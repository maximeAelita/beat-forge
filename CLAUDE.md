# Working on BeatForge with Claude Code

Notes from a session that built a 90 s lofi track end to end. Read this before
starting a beat — most of it was learned the hard way.

The README is the reference for the tools, the step/note grammar and the file
layout; this file is only the part that isn't obvious from reading the code.

## Setup (a fresh cloud container has none of this)

```bash
git clone https://github.com/maximeAelita/beat-forge /workspace/maximeaelita/beat-forge
claude mcp add beatforge --scope user -- python3 /abs/path/beatforge.py --mcp
```

The `bf_*` tools only appear **after a session restart** — MCP servers load at
startup. Running locally instead? Then `projects/` persists between sessions and
none of this re-setup applies.

## Rendering audio with no human at a browser

`bf_export_audio` needs a browser tab: the page does the offline Web Audio render
and POSTs the WAV back. Headless Chromium is enough.

```bash
npm install playwright-core          # PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
# launch with executablePath '/opt/pw-browsers/chromium', goto the UI, click #gate-btn
```

The gate click is load-bearing — `doExport` bails with "audio not started" without
it. Keep the process alive for the whole session; the tab drops out sometimes, so
check `bf_ui` before every export and relaunch if it says NOT connected.

**`bf_export_audio` can exceed the 60 s MCP client timeout on long songs and still
succeed.** On a timeout, check `exports/` before retrying — the file is usually there.

## Duration is exact arithmetic, not trimming

One bar (16 steps) = `4 * 60 / BPM` seconds. At **80 BPM a bar is exactly 3.0 s**,
which makes lofi lengths land dead on:

| target | bars | note |
|---|---|---|
| 15 s | 5 | 1 intro + 4 main |
| 90 s | 30 | the arrangement below |

`tail` is added *on top* of the music. Use `tail: 0` for a seamless loop (exact
duration), 1–2 s for a track meant to end.

## Verify renders, don't assume

No numpy in the container; pure-stdlib `wave` + a hand-rolled FFT is plenty.
Worth measuring every time:

- **per-bar RMS** — confirms the arrangement actually moves (breaks drop, etc.)
- **peak dBFS** — should sit near −1, not 0
- **crest factor** — rises when a mix is *less* squashed
- **energy >5 kHz** — the real measure of "soft" vs "bright"

This caught two mistakes that sounded fine in theory: a first "softer" pass that
cut the top end 13× (muffled, hats gone), and a "peak" section that measured
identical to the main section.

## What "softer" actually means here

Level and brightness, not arrangement. The wins, in order of impact:

1. **Bell lead filter 9000 → 4600 Hz.** The single harshest element by far.
2. **Kick `click` 0.35 → 0.08, `punch` 0.55 → 0.3.** The beater click is the hardness.
3. **Snare `snap` 0.6 → 0.32**, more reverb, lower gain.
4. **Gentler master comp** (−6 dB / 2:1 instead of −14 / 5) — raises crest, lets it breathe.
5. **Remove accents** — `X` → `x` in drum rows; soften the closing fill.

Don't stack four treble cuts on the hats at once. Cut the lead's filter first and
re-measure; the hats usually need far less than you think.

## Arrangement beats repetition

Six copies of a 4-bar loop is not a 90 s track. Duplicate the main pattern and
subtract from each copy:

```
intro  2 bars  hats + one held tonic chord
groove 4 bars  main minus the lead        (withhold the melody)
main   4 bars  everything
lift   4 bars  + open hats
break  4 bars  drums and bass cleared     (the one real dynamic move)
main   4 bars  return
peak   4 bars  + clap, perc, 16th hats
outro  4 bars  drums fall away over 2 bars, chords ring
```

**Sections differ by density, not loudness.** Adding a clap to the peak did not
move its RMS at all — kick and bass dominate it. The lift reads as +2.9 dB above
5 kHz instead. That is correct for lofi; don't chase a volume swing.

For a loop, end the outro on the **v** chord (Em7 in A minor) so it pulls back
into the tonic at bar 1. Check the seam: the final sample should be near silence.

## Licensing, for anything posted publicly

Everything is synthesised — no samples, so no clearance issue. But *not
infringing* is not the same as *owned*: machine-generated output generally isn't
protectable, so "free to use" (a grant of your own rights) is honest where
"non-copyrighted" (a claim that no rights exist) is not.
