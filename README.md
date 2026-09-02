# kisscloud-downloader

Standalone downloader for drama series and movies hosted on myasiantv.es
via the kisscloud.online HLS player. Give it a series or movie page URL and it
downloads the video(s), muxes Thai audio + English subtitles into an `.mkv`, and
normalizes that episode's track metadata (audio language/default, subtitle
language/default and format) as soon as it's muxed - no LLM or manual
per-episode steps required.

## Why this exists

myasiantv.es episode pages embed a kisscloud player whose stream URLs are
signed, tokenized, and obfuscated (fake `.html`/`.js`/`.css` extensions on
what are actually HLS segments) - `yt-dlp` doesn't support the site, and
ffmpeg refuses to open the segment URLs directly. This tool sniffs the
kisscloud iframe with a real (headless) browser to capture the stable
identifiers it needs, then does the actual segment fetching/muxing itself.

## Setup

```sh
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/playwright install chromium
```

Also requires `curl` and `ffmpeg`/`ffprobe` on `PATH` (both are already
present on macOS/most Linux setups; install ffmpeg via your package manager
if `ffprobe -version` fails).

## Usage

```sh
./.venv/bin/python cli.py "https://ww19.myasiantv.es/tv/<series-slug>/" --output-dir ./downloads
# Or a single movie:
./.venv/bin/python cli.py "https://ww19.myasiantv.es/movies/<movie-slug>/" --output-dir ./downloads
```

A series produces:

```
downloads/
└── <series_name>_(<year>)/
    └── season01/
        ├── <series_name>_(<year>)_s01e01.mkv
        ├── <series_name>_(<year>)_s01e02.mkv
        └── ...
```

A movie produces the same title/year directory with the movie directly inside it:

```
downloads/
└── <movie_name>_(<year>)/
    └── <movie_name>_(<year>).mkv
```

Re-running the same command skips any existing `.mkv` that already exists,
so an interrupted run resumes cheaply instead of re-downloading everything.
Since metadata is fixed per-episode as soon as it's muxed (see below), every
episode already on disk is already Plex-ready - an interrupted run never
leaves a "downloaded but not yet fixed" episode behind.

## How it works

1. **`discovery.py`** - loads the series or movie page, parses the title/year
   out of the page `<title>` (format: `<Name> (<Year>) ...`), and collects
   every `Episode N` link for a series. A movie page is represented as a
   one-item URL list so the download pipeline remains shared.
2. **`sniffer.py`** - loads each episode page in a headless browser and
   captures the kisscloud `video_id` (from the embedded iframe's `src`) and
   the stable `/cdn/hls/<hash>/master.txt` URL from network traffic. The
   actual `/m3/...` stream URLs in that request stream rotate/expire within
   minutes, so only the stable master.txt reference is kept.
3. **`streaming.py`** - re-resolves fresh stream tokens from master.txt at
   download time, fetches the English subtitle from kisscloud's player API,
   downloads video/audio segments concurrently over `curl`, joins them with
   `ffmpeg concat`, and muxes video + audio + subtitle into the final `.mkv`.
4. **`metadata.py`** - immediately after each episode is muxed, validates and
   (if needed) remuxes that `.mkv` so Thai audio is marked as the default
   track and English subtitles are marked default (not forced) and
   transcoded to SRT. This last part matters: most Plex clients (and
   possibly other players) can't direct-play an embedded WebVTT subtitle
   track and instead burn it in, which forces a full video re-encode even
   when the client's hardware could otherwise play the source codec
   natively. SRT avoids that. Fixing metadata per-episode (rather than once
   at the end of the whole series) means an interrupted run never leaves an
   already-downloaded episode stuck without this fix.

## Known limitations / fragility

- **This is a scraper, not an API client.** It depends on myasiantv.es's
  current page structure (title format, link markup) and kisscloud's
  current player/network behavior. If either site changes meaningfully,
  `discovery.py` or `sniffer.py` may need updating - there's no vendor
  contract keeping this stable.
- **Series title parsing assumes the `<Name> (<Year>)` pattern** seen in
  every myasiantv page title sampled during development. A title that
  doesn't follow this shape will raise rather than guess.
- **No split-episode ("part 1/2/3") support.** Every myasiantv episode page
  observed so far maps to exactly one kisscloud video, unlike some YouTube
  uploads that split a single episode across multiple videos. If a series
  turns out to need that, it isn't handled here.
- **Headless browser dependency.** Playwright launches a real (headless)
  Chromium to render pages and observe network traffic, since the relevant
  URLs are only ever generated client-side by the page's own JS player -
  there's no simpler HTTP-only path to get them.
- **Personal-use tool.** This downloads copyrighted content from an
  unofficial source; it's intended for personal archival/offline viewing,
  not redistribution.
