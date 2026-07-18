"""Normalizes audio/subtitle track metadata on finished mkvs.

Ensures: Thai audio marked default, English subtitle marked default (and not
forced), and the subtitle track transcoded to SRT.

WebVTT subs get burned in by most Plex clients (forcing a full video
transcode, e.g. VP9->H264, even when the client could otherwise direct-play
the source codec), while SRT direct-plays. So the subtitle track is always
transcoded to SRT here rather than copied through as-is.
"""
import subprocess
from pathlib import Path


def _probe(path, stream_selector, entries):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream_selector,
         "-show_entries", entries, "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def _validate(path):
    audio_lang = _probe(path, "a:0", "stream_tags=language")
    audio_default = _probe(path, "a:0", "stream_disposition=default")
    sub_lang = _probe(path, "s:0", "stream_tags=language")
    sub_default = _probe(path, "s:0", "stream_disposition=default")
    sub_forced = _probe(path, "s:0", "stream_disposition=forced")
    sub_codec = _probe(path, "s:0", "stream=codec_name")

    if audio_lang != "tha" or audio_default != "1":
        return False
    if sub_lang:
        return sub_lang == "eng" and sub_default == "1" and sub_forced == "0" and sub_codec == "subrip"
    return True


def fix_file(f):
    """Validate a single .mkv and remux it in place if it fails validation.

    Called right after each episode is muxed (not batched at the end of a
    run) so a finished episode is immediately Plex-ready - an interrupted
    run doesn't leave already-downloaded episodes stuck with WebVTT subs
    waiting on a final pass that may never happen.
    """
    f = Path(f)
    print(f"Checking: {f}", flush=True)
    if _validate(f):
        print("  -> OK", flush=True)
        return

    print("  -> Fixing metadata...", flush=True)
    tmp = f.with_name(f.name + ".tmp.mkv")
    subprocess.run([
        "ffmpeg", "-loglevel", "error", "-i", str(f),
        "-map", "0", "-c:v", "copy", "-c:a", "copy", "-c:s", "srt",
        "-disposition:v:0", "default",
        "-disposition:a:0", "default",
        "-disposition:s:0", "default",
        "-metadata:s:a:0", "language=tha",
        str(tmp),
    ], check=True)
    tmp.rename(f)

    if _validate(f):
        print("  -> Fixed OK", flush=True)
    else:
        print(f"  -> WARNING: validation still failing after fix on {f}", flush=True)


def fix_metadata(directory):
    """Validate every .mkv in `directory` and remux any that fail.

    Useful for normalizing an existing directory (e.g. episodes downloaded
    by an older version of this tool); a fresh run fixes each episode via
    fix_file() as soon as it's muxed instead of calling this.
    """
    for f in sorted(Path(directory).glob("*.mkv")):
        fix_file(f)
