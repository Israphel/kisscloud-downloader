"""HTTP fetch helpers and kisscloud stream resolution/download/mux.

kisscloud serves segments over plain HLS but with signed/tokenized URLs that
rotate across sessions. Callers pass in the stable identifiers (video_id,
master.txt hash) and this module re-resolves the actual stream URLs at run
time via resolve_streams()/resolve_subtitle().
"""
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
WORKERS = 20


def curl_headers(referer):
    return [
        "curl", "-sL", "--max-time", "30",
        "-H", f"Referer: {referer}",
        "-H", f"User-Agent: {USER_AGENT}",
        "-H", "Accept: */*",
    ]


def fetch_text(referer, url):
    return subprocess.run(curl_headers(referer) + [url], capture_output=True, check=True).stdout.decode()


def fetch_binary(referer, url, byte_range=None):
    command = curl_headers(referer)
    if byte_range:
        start, end = byte_range
        command += ["-H", f"Range: bytes={start}-{end}"]
    return subprocess.run(command + [url], capture_output=True, check=True).stdout


def resolve_streams(referer, master_txt_url):
    """Fetch master.txt fresh at run time - /m3/ stream tokens rotate.

    Picks the video variant with the highest BANDWIDTH from its
    #EXT-X-STREAM-INF line rather than assuming line order - master.txt
    doesn't always list variants lowest-to-highest, and some episodes offer
    a 1080p tier above the usual 360p/720p."""
    text = fetch_text(referer, master_txt_url)
    lines = text.splitlines()
    audio = None
    best_bandwidth = -1
    best_uri = None
    best_resolution = None
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith('#EXT-X-MEDIA') and 'TYPE=AUDIO' in line:
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                audio = "https://kisscloud.online" + m.group(1)
        elif line.startswith('#EXT-X-STREAM-INF'):
            bw_match = re.search(r'BANDWIDTH=(\d+)', line)
            bandwidth = int(bw_match.group(1)) if bw_match else 0
            uri = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if uri.startswith('/m3/') and bandwidth > best_bandwidth:
                best_bandwidth = bandwidth
                best_uri = uri
                res_match = re.search(r'RESOLUTION=([0-9x]+)', line)
                best_resolution = res_match.group(1) if res_match else None
    if not audio or not best_uri:
        raise RuntimeError(f"Could not parse master.txt:\n{text}")
    if best_resolution:
        print(f"  Selected highest-bandwidth video variant: {best_resolution} ({best_bandwidth} bps)", flush=True)
    return "https://kisscloud.online" + best_uri, audio


def resolve_best_variant(referer, playlist_url):
    """Select the highest-bandwidth variant when a provider returns a master playlist."""
    text = fetch_text(referer, playlist_url)
    lines = text.splitlines()
    variants = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line.startswith("#EXT-X-STREAM-INF:") or i + 1 >= len(lines):
            continue
        bandwidth = re.search(r"BANDWIDTH=(\d+)", line)
        if not bandwidth:
            continue
        resolution = re.search(r"RESOLUTION=([0-9x]+)", line)
        variants.append((int(bandwidth.group(1)), resolution.group(1) if resolution else None,
                         urljoin(playlist_url, lines[i + 1].strip())))
    if not variants:
        return playlist_url, None, None
    bandwidth, resolution, variant_url = max(variants, key=lambda item: item[0])
    return variant_url, resolution, bandwidth


def resolve_subtitle(referer, video_id):
    """Subtitle URLs are signed/tokenized like the stream URLs, so fetch a fresh
    one from the kisscloud player API right before use instead of storing it."""
    text = subprocess.run(
        curl_headers(referer) + [
            "-H", "X-Requested-With: XMLHttpRequest",
            f"https://kisscloud.online/player/index.php?data={video_id}&do=getVideo",
        ],
        capture_output=True, check=True
    ).stdout.decode()
    marker = 'playerjsSubtitle = "'
    start = text.find(marker)
    if start == -1:
        raise RuntimeError("Could not find playerjsSubtitle in getVideo response")
    raw = text[start + len(marker):text.find('"', start + len(marker))]
    for entry in raw.split(','):
        if entry.startswith('[English]'):
            return entry[len('[English]'):]
    raise RuntimeError(f"No [English] subtitle entry found in: {raw}")


def download_segment(args):
    idx, url, path, referer, byte_range = args
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return idx
    for attempt in range(3):
        try:
            data = fetch_binary(referer, url, byte_range)
            with open(path, 'wb') as f:
                f.write(data)
            return idx
        except Exception as e:
            if attempt == 2:
                print(f"  FAILED seg {idx}: {e}", flush=True)
    return idx


def download_stream(name, m3u8_url, seg_dir, scratchpad, referer):
    os.makedirs(seg_dir, exist_ok=True)
    text = fetch_text(referer, m3u8_url)
    segments = []
    pending_range = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-BYTERANGE:"):
            value = line.split(":", 1)[1]
            length, _, offset = value.partition("@")
            if offset:
                start = int(offset)
            elif segments:
                _, previous_end = segments[-1][1] or (0, -1)
                start = previous_end + 1
            else:
                start = 0
            pending_range = (start, start + int(length) - 1)
        elif not line.startswith("#"):
            segments.append((urljoin(m3u8_url, line), pending_range))
            pending_range = None
    total = len(segments)
    print(f"[{name}] {total} segments", flush=True)
    tasks = [
        (i, url, os.path.join(seg_dir, f"{i:05d}.ts"), referer, byte_range)
        for i, (url, byte_range) in enumerate(segments)
    ]
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for _ in as_completed(ex.submit(download_segment, t) for t in tasks):
            done += 1
            if done % 200 == 0 or done == total:
                print(f"  [{name}] {done}/{total}", flush=True)
    list_path = os.path.join(scratchpad, f"{name}_list.txt")
    joined = os.path.join(scratchpad, f"{name}.ts")
    with open(list_path, 'w') as lf:
        for i in range(total):
            lf.write(f"file '{os.path.join(seg_dir, f'{i:05d}.ts')}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", joined],
                   check=True, capture_output=True)
    os.unlink(list_path)
    return joined


def mux_episode(video_ts, audio_ts, sub_path, out_mkv):
    """Mux either separate video/audio streams or one combined media stream."""
    tmp_mkv = out_mkv + ".tmp.mkv"
    inputs = ["-i", video_ts]
    maps = ["-map", "0:v"]
    metadata = []
    if audio_ts:
        inputs += ["-i", audio_ts]
        maps += ["-map", "1:a"]
        metadata += ["-disposition:a:0", "default", "-metadata:s:a:0", "language=tha"]
    else:
        maps += ["-map", "0:a?"]
        metadata += ["-disposition:a:0", "default", "-metadata:s:a:0", "language=tha"]
    if sub_path:
        inputs += ["-i", sub_path]
        maps += ["-map", f"{2 if audio_ts else 1}"]
        metadata += ["-disposition:s:0", "default", "-metadata:s:s:0", "language=eng"]
    subprocess.run([
        "ffmpeg", "-y", *inputs, *maps, "-c", "copy",
        "-disposition:v:0", "default", *metadata, tmp_mkv,
    ], check=True)
    os.rename(tmp_mkv, out_mkv)
