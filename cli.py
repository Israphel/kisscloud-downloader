#!/usr/bin/env python3
"""Standalone downloader for myasiantv.es/kisscloud-hosted drama series and movies.

Usage:
    python cli.py <show_url> [--output-dir DIR]

Given a series page (e.g. https://ww19.myasiantv.es/tv/<slug>/), this:
  1. discovers the title, year, and episode/movie page URL(s)
  2. sniffs each video page for its kisscloud video_id + master.txt URL
  3. downloads and muxes each video into an .mkv (Thai audio, English subs)
  4. normalizes that video's audio/subtitle track metadata immediately,
     so a finished episode is Plex-ready right away rather than waiting on
     a final pass over the whole season that an interrupted run might
     never reach

No LLM involved at run time - this only reuses the myasiantv/kisscloud
integration built while doing that process manually.
"""
import argparse
import shutil
import sys
from pathlib import Path

import discovery
import metadata
import sniffer
import streaming


def download_episode(ep_num, page_url, output_dir, scratchpad_root, series_slug, year,
                     filename=None, label=None):
    """Download one video using the existing episode pipeline.

    ``filename`` and ``label`` let movie pages share this path without
    changing the established series naming scheme.
    """
    item_label = label or f"EP{ep_num:02d}"
    out_mkv = output_dir / (filename or f"{series_slug}_({year})_s01e{ep_num:02d}.mkv")
    scratchpad = scratchpad_root / (f"movie" if label == "MOVIE" else f"ep{ep_num:02d}")

    if out_mkv.exists():
        print(f"{item_label}: already exists, skipping", flush=True)
        return

    print(f"==> {item_label}: sniffing {page_url}", flush=True)
    source = sniffer.sniff_episode(page_url)
    referer = source.referer

    scratchpad.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if source.provider == "kisscloud":
        print(f"==> {item_label}: resolving fresh KissCloud stream tokens", flush=True)
        video_m3u8, audio_m3u8 = streaming.resolve_streams(referer, source.master_txt_url)
        video_ts = streaming.download_stream(
            "video", video_m3u8, str(scratchpad / "segs_video"), str(scratchpad), referer
        )
        audio_ts = streaming.download_stream(
            "audio", audio_m3u8, str(scratchpad / "segs_audio"), str(scratchpad), referer
        )
        sub_url = streaming.resolve_subtitle(referer, source.video_id)
    elif source.provider == "vidbasic":
        print(f"==> {item_label}: resolving Vidbasic HLS variants", flush=True)
        media_url, resolution, bandwidth = streaming.resolve_best_variant(referer, source.media_url)
        if resolution:
            print(f"  Selected highest-bandwidth video variant: {resolution} ({bandwidth} bps)", flush=True)
        else:
            print("  Vidbasic returned a single media playlist", flush=True)
        video_ts = streaming.download_stream(
            "media", media_url, str(scratchpad / "segs_media"), str(scratchpad), referer
        )
        audio_ts = None
        sub_url = source.subtitle_url
    else:
        raise RuntimeError(f"{item_label}: unsupported video provider {source.provider!r}")

    sub_path = None
    if source.subtitle_data:
        sub_data = source.subtitle_data.encode()
    elif sub_url:
        print(f"==> {item_label}: resolving subtitle", flush=True)
        sub_data = streaming.fetch_binary(referer, sub_url)
    else:
        sub_data = None
    if sub_data is not None:
        if not sub_data.lstrip().startswith(b"WEBVTT"):
            raise RuntimeError(f"{item_label}: subtitle fetch did not return WebVTT (got {sub_data[:80]!r})")
        sub_path = scratchpad / "en.vtt"
        sub_path.write_bytes(sub_data)

    streaming.mux_episode(video_ts, audio_ts, str(sub_path) if sub_path else None, str(out_mkv))
    shutil.rmtree(scratchpad, ignore_errors=True)
    metadata.fix_file(out_mkv)
    print(f"==> {item_label}: done -> {out_mkv}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "show_url",
        help="myasiantv.es series, episode, or movie page (https://ww19.myasiantv.es/tv/<slug>/, "
        "https://ww19.myasiantv.es/ep/<slug>/, or https://ww19.myasiantv.es/movies/<slug>/)",
    )
    parser.add_argument("--output-dir", default="./downloads", help="root output directory (default: ./downloads)")
    args = parser.parse_args()

    output_root = Path(args.output_dir).expanduser().resolve()

    print(f"==> Discovering series from {args.show_url}", flush=True)
    series_name, year, episode_urls = discovery.discover_series(args.show_url)
    series_slug = discovery.slugify(series_name)
    movie = discovery.is_movie_url(args.show_url)
    kind = "movie" if movie else "episode"
    count_text = "1 movie" if kind == "movie" else f"{len(episode_urls)} episode(s)"
    print(f"==> Title: {series_name} ({year}) - {count_text}", flush=True)


    output_dir = output_root / f"{series_slug}_({year})"
    if not movie:
        output_dir /= "season01"
    scratchpad_root = output_root / f".tmp_{series_slug}_({year})"

    failed = []
    for ep_num, page_url in enumerate(episode_urls, start=1):
        item_label = "MOVIE" if movie else f"EP{ep_num:02d}"
        filename = f"{series_slug}_({year}).mkv" if movie else None
        try:
            download_episode(
                ep_num, page_url, output_dir, scratchpad_root, series_slug, year,
                filename=filename, label=item_label if movie else None,
            )
        except Exception as e:
            print(f"==> {item_label}: FAILED ({e}) - skipping, continuing with remaining episodes", flush=True)
            failed.append(ep_num)

    if failed:
        item_name = "movie" if movie else "episode(s)"
        print(f"Done with {len(failed)} failed {item_name}: {failed}", flush=True)
    else:
        print("Movie done." if movie else "All episodes done.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
