#!/usr/bin/env python3
"""Standalone downloader for myasiantv.es/kisscloud-hosted drama series.

Usage:
    python cli.py <show_url> [--output-dir DIR]

Given a series page (e.g. https://ww19.myasiantv.es/tv/<slug>/), this:
  1. discovers the series name, year, and every episode page URL
  2. sniffs each episode page for its kisscloud video_id + master.txt URL
  3. downloads and muxes each episode into an .mkv (Thai audio, English subs)
  4. normalizes that episode's audio/subtitle track metadata immediately,
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


def download_episode(ep_num, page_url, output_dir, scratchpad_root, series_slug, year):
    out_mkv = output_dir / f"{series_slug}_({year})_s01e{ep_num:02d}.mkv"
    scratchpad = scratchpad_root / f"ep{ep_num:02d}"

    if out_mkv.exists():
        print(f"EP{ep_num:02d}: already exists, skipping", flush=True)
        return

    print(f"==> EP{ep_num:02d}: sniffing {page_url}", flush=True)
    video_id, master_txt = sniffer.sniff_episode(page_url)
    referer = f"https://kisscloud.online/video/{video_id}"

    scratchpad.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"==> EP{ep_num:02d}: resolving fresh stream tokens from master.txt", flush=True)
    video_m3u8, audio_m3u8 = streaming.resolve_streams(referer, master_txt)

    print(f"==> EP{ep_num:02d}: resolving fresh subtitle URL", flush=True)
    sub_url = streaming.resolve_subtitle(referer, video_id)
    sub_data = streaming.fetch_binary(referer, sub_url)
    if not sub_data.lstrip().startswith(b"WEBVTT"):
        raise RuntimeError(f"EP{ep_num:02d}: subtitle fetch did not return WebVTT (got {sub_data[:80]!r})")
    sub_path = scratchpad / "en.vtt"
    sub_path.write_bytes(sub_data)

    video_ts = streaming.download_stream("video", video_m3u8, str(scratchpad / "segs_video"), str(scratchpad), referer)
    audio_ts = streaming.download_stream("audio", audio_m3u8, str(scratchpad / "segs_audio"), str(scratchpad), referer)

    streaming.mux_episode(video_ts, audio_ts, str(sub_path), str(out_mkv))
    shutil.rmtree(scratchpad, ignore_errors=True)
    metadata.fix_file(out_mkv)
    print(f"==> EP{ep_num:02d}: done -> {out_mkv}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "show_url",
        help="myasiantv.es series page (https://ww19.myasiantv.es/tv/<slug>/) "
        "or any one of its episode pages (https://ww19.myasiantv.es/ep/<slug>/)",
    )
    parser.add_argument("--output-dir", default="./downloads", help="root output directory (default: ./downloads)")
    args = parser.parse_args()

    output_root = Path(args.output_dir).expanduser().resolve()

    print(f"==> Discovering series from {args.show_url}", flush=True)
    series_name, year, episode_urls = discovery.discover_series(args.show_url)
    series_slug = discovery.slugify(series_name)
    print(f"==> Series: {series_name} ({year}) - {len(episode_urls)} episode(s)", flush=True)

    output_dir = output_root / f"{series_slug}_({year})" / "season01"
    scratchpad_root = output_root / f".tmp_{series_slug}_({year})"

    failed = []
    for ep_num, page_url in enumerate(episode_urls, start=1):
        try:
            download_episode(ep_num, page_url, output_dir, scratchpad_root, series_slug, year)
        except Exception as e:
            print(f"==> EP{ep_num:02d}: FAILED ({e}) - skipping, continuing with remaining episodes", flush=True)
            failed.append(ep_num)

    if failed:
        print(f"Done with {len(failed)} failed episode(s): {failed}", flush=True)
    else:
        print("All episodes done.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
