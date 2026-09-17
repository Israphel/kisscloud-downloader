# kisscloud-downloader

Standalone downloader for drama series and movies hosted on myasiantv.es
via its KissCloud or Vidbasic HLS video providers. Give it a series or movie page URL and it
downloads the video(s), muxes Thai audio + English subtitles into an `.mkv`, and
normalizes that episode's track metadata (audio language/default, subtitle
language/default and format) as soon as it's muxed - no LLM or manual
per-episode steps required.

## Why this exists

myasiantv.es episode pages embed third-party players whose stream URLs are
signed, tokenized, encrypted, and sometimes served behind fake `.html`/`.js`/`.css`
extensions. `yt-dlp` doesn't support the site, and ffmpeg refuses to open some
of the segment URLs directly. This tool detects the provider in a real
(headless) browser, extracts the provider's current media source, then reuses
the same segment fetching/muxing pipeline for both providers.

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
   detects the embedded provider. KissCloud returns a stable `video_id` and
   `master.txt` reference; Vidbasic's JWPlayer exposes a current HLS source and
   subtitle URL after the player initializes. Vidbasic subtitle cues are
   decrypted in the browser context using the player-provided CryptoJS setup.
4. **`streaming.py`** - downloads provider-independent HLS segments concurrently
   over `curl` (including HLS byte-range segments), selects the highest-bandwidth
   variant when a provider returns a master playlist, joins segments with
   `ffmpeg` concat, and muxes video/audio/subtitle tracks into the final `.mkv`.
5. **`metadata.py`** - immediately after each episode is muxed, validates and
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
