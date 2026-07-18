# AGENTS.md

Guidance for agents (and humans) working on this codebase.

## What this is

A standalone CLI that downloads an entire drama series from myasiantv.es
(kisscloud-hosted) given only the series page URL - no per-episode manual
steps, no LLM in the loop at run time. See `README.md` for the pipeline
overview and usage.

## Module layout

- `discovery.py` - series page -> (name, year, ordered episode URLs)
- `sniffer.py` - episode page -> (kisscloud video_id, master.txt URL), via a
  headless Playwright browser watching network requests
- `streaming.py` - master.txt -> resolved stream URLs -> downloaded segments
  -> joined/muxed `.mkv` (curl for fetching, ffmpeg for concat/mux)
- `metadata.py` - validates/fixes audio+subtitle track metadata on a finished
  `.mkv`. `cli.py` calls `fix_file()` on each episode right after muxing it,
  not batched at the end - `fix_metadata(directory)` (iterates `fix_file`
  over a whole directory) exists only for normalizing episodes downloaded by
  an older version of this tool, not for the normal run path
- `cli.py` - argument parsing and orchestration; the only file that should
  ever create directories, decide output paths, or loop over episodes

Keep this separation: `streaming.py` and `metadata.py` should stay
free of anything specific to myasiantv/kisscloud page structure, and
`discovery.py`/`sniffer.py` should stay free of anything about local
filesystem layout. That split is what makes it plausible to reuse
`streaming.py`/`metadata.py` if another kisscloud-hosted source ever needs
supporting.

## Conventions

- No third-party HTTP libraries for the actual segment/API fetching -
  `subprocess` + `curl` is intentional (matches how the reference
  implementation this was built from works, and avoids TLS/fingerprinting
  differences between `curl` and Python HTTP clients that some CDNs are
  picky about).
- Playwright is only used for the two pages that need real JS execution to
  observe (`discovery.py`, `sniffer.py`). Don't reach for it elsewhere.
- Every network identifier that's *stable* across sessions (video_id,
  master.txt hash) gets stored; every identifier that *rotates* (the
  `/m3/...` stream URLs, subtitle URLs) gets re-resolved at the point of use
  rather than cached. If you're tempted to save a `/m3/` URL "to save a
  request," don't - it expires within minutes.
- `fix_file`/`_validate` in `metadata.py` must tolerate episodes with no
  subtitle stream at all (some episodes may be burned-in/no soft subs) -
  don't add a check that assumes a subtitle track always exists.
- Subtitle tracks must end up as SRT (`subrip`), never WebVTT - see the
  "Known limitations" note in README for why this specifically matters for
  Plex playback.

## Testing changes

There's no unit test suite (the interesting logic is almost entirely "did a
real website respond the way we expect," which unit tests don't verify
well). Instead, when changing `discovery.py` or `sniffer.py`, sanity-check
against a real, currently-airing series page before considering the change
done - a change that only satisfies your priors about the page structure
without checking the live page is untested. When changing `streaming.py`
or `metadata.py`, downloading one real episode end-to-end and inspecting
the output with `ffprobe` is the equivalent check.

## If the site changes

`discovery.py`'s title regex and episode-link filtering, and `sniffer.py`'s
network-request pattern matching, are the two most likely things to break
if myasiantv/kisscloud change their markup or player. Re-run the
inspection steps that produced them in the first place: load the page
in a real browser (or a throwaway Playwright script), check the page
`<title>`, and check what requests actually fire when the player loads
before assuming the existing regexes still apply.
