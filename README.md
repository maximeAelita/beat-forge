# BeatForge

A step-sequencer beat maker with a **built-in MCP server**, so Claude can write
beats, basslines, melodies and chords directly into the same session you're
clicking around in. Everything is synthesised live in the browser — no samples,
no downloads, no build step, and **zero dependencies** (system Python 3 + any
modern browser).

```
┌──────────────┐   stdio JSON-RPC   ┌──────────────────┐   SSE + fetch   ┌───────────┐
│ Claude Code  │ ◀────────────────▶ │  beatforge.py    │ ◀─────────────▶ │  browser  │
│ (21 MCP      │                    │  one process,    │                 │  Web Audio│
│  tools)      │                    │  one shared state│                 │  engine   │
└──────────────┘                    └──────────────────┘                 └───────────┘
```

Anything Claude writes appears in the browser instantly. Anything you click is
visible to Claude on its next read. There is one project state, not two.

---

## Running it

Requirements: Python 3.8+ (the system `python3` on macOS is enough) and any
modern browser. There is nothing to install.

**As a studio you drive yourself:**

```bash
python3 beatforge.py --open
```

Then click **ENTER STUDIO** (browsers block audio until you click something).

**As Claude's instrument** — register the MCP server once (use the absolute path
to wherever you cloned it):

```bash
claude mcp add beatforge --scope user -- python3 "$PWD/beatforge.py" --mcp
```

No `claude` CLI? Add the same thing to `~/.claude.json` by hand:

```json
{ "mcpServers": { "beatforge": {
    "type": "stdio", "command": "python3",
    "args": ["/absolute/path/to/beatforge/beatforge.py", "--mcp"] } } }
```

Restart Claude Code, and the `bf_*` tools appear. Starting the MCP server also
starts the web UI on <http://127.0.0.1:8787> — open that tab and you'll watch
Claude build the beat in real time.

If port 8787 is busy it walks up to the next free one; the exact URL is printed
to stderr and returned by the `bf_ui` tool.

---

## The interface

| | |
|---|---|
| **click** a step | toggle it on/off |
| **shift+click** | cycle velocity: ghost → soft → normal → accent |
| **alt+click** | drums: cycle roll (×2, ×4) · melodic: toggle slide/portamento |
| **wheel** over a step | drums: velocity · melodic: pitch (shift = octaves, ⌘/ctrl = note length) |
| **drag** across steps | paint or erase a run |
| **right-click** | erase |
| **space** | play / stop |
| **1–9** | jump to pattern |
| **ctrl/⌘+Z** | undo · **shift** to redo — walks back anything you *or* Claude changed |
| double-click a track name | audition it |

The left column is the mixer (mute, solo, volume, pan). The right panel is the
**inspector**: swap the synth engine and tweak its parameters — every drum and
instrument is a synth, so a kick can become an 808, a hat can become a ride.
**DUCK** sets how far the kick sidechains that track on every hit.

The header's **COMP** / **RATIO** / **LIMIT** control the master bus. The
defaults level hard; raise COMP or drop RATIO when a mix needs to keep its
dynamics — a heavy master compressor will otherwise undo the sidechain pump.

Patterns are 8–64 steps. Chain them into a song with **+CHAIN**, tick **SONG**,
and the transport plays the arrangement instead of looping one pattern.

**EXPORT** renders the pattern (or the whole song) offline and drops a 16-bit
44.1 kHz stereo WAV into `beatforge/exports/`.

---

## The MCP tools

| Tool | What it does |
|---|---|
| `bf_get_project` | Read tempo, key, mixer and an ASCII picture of the grid. **Start here.** |
| `bf_set_transport` | BPM, swing, master volume, key/scale, pattern select, play/stop |
| `bf_tracks` | List / add / remove / update tracks; `describe_engines` lists every synth |
| `bf_set_steps` | Write a drum row from a pattern string |
| `bf_set_notes` | Write a melodic row from note names |
| `bf_edit_steps` | Surgically change individual steps |
| `bf_transform` | shift, reverse, double/half time, thin, thicken, transpose, humanised variation |
| `bf_clear` | Clear a track or a whole pattern |
| `bf_pattern` | Add / duplicate / delete / rename / resize / select patterns |
| `bf_song` | Build the song chain |
| `bf_generate_drums` | A full groove in one of 21 genres |
| `bf_generate_bass` | 10 bassline styles, locked to the kick and to the key |
| `bf_generate_melody` | 9 melody styles |
| `bf_generate_chords` | Diatonic progressions with 5 rhythmic feels |
| `bf_humanize` | Velocity and micro-timing scatter |
| `bf_music_reference` | Scale notes and chord spellings |
| `bf_project_io` | Save / load / list projects |
| `bf_export_audio` | Render a WAV to disk (the browser does the rendering) |
| `bf_export_midi` | Write a .mid for a DAW — no browser needed, returns immediately |
| `bf_undo` | Walk the whole project back (or forward) through its edit history |
| `bf_ui` | Is a browser connected, and where |

### Step grammar

One character per step, so a whole bar fits in a single argument:

```
.  rest        x  hit (0.80)      X  accent (1.0)
o  soft (0.55) s  ghost (0.28)    r  roll ×2   R  roll ×4
1-9 velocity   |  and spaces are ignored — write bars: "x..x|..x.|x..x|..x."
```

A short string that divides evenly into the pattern is repeated to fill it, so
`"x..."` fills 16 steps with four-on-the-floor.

### Note grammar

```
C2 . Eb2 - . G2        '.' rest, '-' ties the previous note one step longer
F#3:0.6                velocity suffix (0-1, or 1-9)
C3+Eb3+G3              a chord on one step
```

### Genres

`boom_bap · trap · drill · house · deep_house · techno · dnb · jungle · lofi ·
reggaeton · afrobeat · amapiano · uk_garage · breakbeat · funk · disco · rock ·
phonk · dubstep · ambient · industrial`

### Synth engines

**Drums** — kick, kick808, snare, rimshot, clap, hat, openhat, ride, crash,
tom, shaker, cowbell, perc, noise.
**Melodic** — sub808, bass_saw, bass_square, reese, pluck, lead_saw, keys, pad,
bell, organ.

---

## Files

```
beatforge.py          entry point (--mcp for Claude, plain for you)
bf/state.py           project model, step/note grammar, autosave
bf/theory.py          scales, chords, progressions
bf/generators.py      genre grooves, basslines, melodies
bf/midi.py            Standard MIDI File writer
bf/tools.py           the 21 MCP tools
bf/mcp.py             MCP stdio JSON-RPC server
bf/web.py             HTTP + SSE server
web/                  the studio UI (audio.js is the whole synth engine)
projects/             saved projects, plus _autosave.json
exports/              rendered WAVs
```

The project autosaves to `projects/_autosave.json` on every change, so closing
the tab or restarting the server never loses work.

## Notes

- **WAV** export needs the browser tab open and **not** minimised — background
  tabs throttle audio rendering, and the export tool will tell you if that
  happens. **MIDI** export is pure Python and needs no browser at all.
- The master bus has a compressor and a soft limiter, so exports don't clip.
  Both are project settings now — see COMP / RATIO / LIMIT above.
- Every edit goes through one undo history, so anything Claude writes can be
  walked back with ctrl+Z or `bf_undo`.
- The audio engine's randomness runs off the project `seed`, so the same
  project always renders the same audio. Change the seed to reroll.
- Everything is synthesis, so a whole project file is a few KB of JSON.
