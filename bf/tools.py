"""MCP tool definitions and their implementations.

Each tool returns a plain string (what the model reads). Raising an exception
is fine -- the MCP layer converts it into an isError result.
"""

import base64
import copy
import glob
import json
import os
import random
import re
import threading
import time

from . import generators, midi, theory
from .state import (ALL_ENGINES, DRUM_ENGINES, MELODIC_ENGINES, engine_kind,
                    format_steps, midi_to_note, note_to_midi, parse_notes,
                    parse_steps, slugify, default_project, migrate, empty_pattern)

MAX_STEPS = 128

_export_waiters = {}
_analyze_waiters = {}
_export_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _ruler(n):
    out = []
    for i in range(n):
        if i % 4 == 0:
            out.append(str((i // 4) % 10 + 1))
        else:
            out.append("·")
    return "".join(out)


def render_ascii(data, pattern_index=None):
    idx = data.get("current", 0) if pattern_index is None else pattern_index
    if isinstance(idx, str):
        names = [p["name"].lower() for p in data["patterns"]]
        idx = names.index(idx.lower())
    pat = data["patterns"][idx]
    n = pat["steps"]
    solo = any(t["solo"] for t in data["tracks"])

    head = ('BeatForge  "%s"   %s BPM   swing %d%%   %s %s   pattern %s (%d/%d, %d steps)'
            % (data["name"], data["bpm"], round(data["swing"] * 100), data["key"],
               data["scale"], pat["name"], idx + 1, len(data["patterns"]), n))
    lines = [head, "-" * max(len(head), 60)]
    lines.append("%-14s %s" % ("", _ruler(n)))

    melodic = []
    for t in data["tracks"]:
        row = pat["grid"].get(t["id"], [None] * n)
        flag = ""
        if t["mute"]:
            flag = " [M]"
        elif solo and not t["solo"]:
            flag = " [-]"
        elif t["solo"]:
            flag = " [S]"
        if t["kind"] == "drum":
            lines.append("%-14s %s%s" % (t["id"][:14], format_steps(row), flag))
        else:
            melodic.append((t, row, flag))

    for t, row, flag in melodic:
        hits = [(i, s) for i, s in enumerate(row) if s is not None]
        if hits:
            body = "  ".join("%d:%s" % (i + 1, _note_label(s)) for i, s in hits)
        else:
            body = "(empty)"
        lines.append("%-14s %s%s" % (t["id"][:14] + " ♪", body, flag))

    if data.get("song"):
        chain = " ".join("%s×%d" % (data["patterns"][c["pattern"]]["name"], c["repeat"])
                         for c in data["song"] if c["pattern"] < len(data["patterns"]))
        lines.append("song%s: %s" % (" (active)" if data.get("songMode") else "", chain))
    lines.append("legend: X accent  x hit  o soft  s ghost  r/R roll  . rest")
    return "\n".join(lines)


def _note_label(step):
    if step.get("notes"):
        return "+".join(midi_to_note(x) for x in step["notes"])
    if step.get("note") is not None:
        return midi_to_note(step["note"]) + ("~" if step.get("slide") else "")
    return "x"


def _track_summary(data):
    out = []
    for t in data["tracks"]:
        out.append("  %-10s %-11s %-8s gain %.2f pan %+0.2f%s%s rev %.2f dly %.2f"
                   % (t["id"], t["engine"], t["kind"], t["gain"], t["pan"],
                      " MUTE" if t["mute"] else "", " SOLO" if t["solo"] else "",
                      t["reverb"], t["delay"]))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Small internal helpers
# ---------------------------------------------------------------------------

def _resolve_pattern(proj, data, ref):
    if ref is None:
        return data["patterns"][data.get("current", 0)], data.get("current", 0)
    if isinstance(ref, str):
        for i, p in enumerate(data["patterns"]):
            if p["name"].lower() == ref.lower():
                return p, i
        raise ValueError("no pattern named %r (have %s)"
                         % (ref, ", ".join(p["name"] for p in data["patterns"])))
    i = int(ref)
    if not 0 <= i < len(data["patterns"]):
        raise ValueError("pattern index %d out of range 0..%d"
                         % (i, len(data["patterns"]) - 1))
    return data["patterns"][i], i


def _find_track(data, ref):
    ref = str(ref)
    for t in data["tracks"]:
        if t["id"] == ref:
            return t
    low = ref.lower()
    for t in data["tracks"]:
        if t["id"].lower() == low or t["name"].lower() == low:
            return t
    raise ValueError("no track %r (have: %s)"
                     % (ref, ", ".join(t["id"] for t in data["tracks"])))


def _resize_pattern(pat, n):
    n = max(1, min(MAX_STEPS, int(n)))
    for tid, row in pat["grid"].items():
        if len(row) < n:
            row.extend([None] * (n - len(row)))
        else:
            del row[n:]
    pat["steps"] = n


def _write_row(pat, tid, row, mode="replace"):
    n = pat["steps"]
    row = (list(row) + [None] * n)[:n]
    cur = pat["grid"].setdefault(tid, [None] * n)
    if mode == "replace":
        pat["grid"][tid] = row
    elif mode == "merge":
        pat["grid"][tid] = [row[i] if row[i] is not None else cur[i] for i in range(n)]
    elif mode == "erase":
        pat["grid"][tid] = [None if row[i] is not None else cur[i] for i in range(n)]
    else:
        raise ValueError("mode must be replace, merge or erase")
    return pat["grid"][tid]


def _ensure_role_track(proj, data, role, create=True):
    """Map a generator role (kick/snare/hat/...) onto an existing track."""
    candidates = generators.ROLE_ALIASES.get(role, [role])
    for want in candidates:
        for t in data["tracks"]:
            if t["id"] == want:
                return t
    for want in candidates:
        for t in data["tracks"]:
            if t["engine"] == want:
                return t
    if not create:
        return None
    engine = role if role in DRUM_ENGINES else "perc"
    tid = slugify(role, set(t["id"] for t in data["tracks"]))
    from .state import _track
    t = _track(tid, role.replace("_", " ").title(), engine)
    data["tracks"].append(t)
    for p in data["patterns"]:
        p["grid"][tid] = [None] * p["steps"]
    return t


def _count(row):
    return sum(1 for s in row if s is not None)


def _projects_dir(root):
    d = os.path.join(root, "projects")
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_STEPS_DOC = (
    "Pattern string, one character per step: '.' rest, 'x' hit, 'X' accent, "
    "'o' soft, 's' ghost, 'r' 2-roll, 'R' 4-roll, '1'-'9' velocity. Spaces and "
    "'|' are ignored so you can write bars as 'x..x|..x.|x..x|..x.'. A short "
    "string that divides evenly into the pattern length is repeated to fill it."
)

TOOLS = [
    {
        "name": "bf_get_project",
        "description": "Read the current BeatForge project: tempo, key, tracks, and an "
                       "ASCII view of a pattern grid. Call this first, and after edits, "
                       "to see what the beat actually looks like.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"description": "Pattern index or name. Defaults to the selected pattern."},
                "full": {"type": "boolean", "description": "Also dump raw JSON state.", "default": False},
                "all_patterns": {"type": "boolean", "description": "Render every pattern.", "default": False},
            },
        },
    },
    {
        "name": "bf_set_transport",
        "description": "Set tempo, swing, master volume, musical key/scale, project name, "
                       "selected pattern, song mode, and start/stop playback.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bpm": {"type": "number", "minimum": 30, "maximum": 300},
                "swing": {"type": "number", "minimum": 0, "maximum": 0.75,
                          "description": "0 = straight, 0.16 = light shuffle, 0.3 = heavy swing."},
                "master_gain": {"type": "number", "minimum": 0, "maximum": 1.5},
                "key": {"type": "string", "description": "C, F#, Bb ..."},
                "scale": {"type": "string", "description": "minor, major, dorian, minor_pentatonic ..."},
                "name": {"type": "string"},
                "pattern": {"description": "Select a pattern by index or name."},
                "playing": {"type": "boolean", "description": "Start or stop the browser transport."},
                "song_mode": {"type": "boolean", "description": "Play the song chain instead of looping one pattern."},
                "duck_source": {"type": "string", "description": "Track id whose hits drive the sidechain (default 'kick')."},
                "duck_release": {"type": "number", "minimum": 0.02, "maximum": 1.0,
                                 "description": "Seconds for a ducked track to recover. 0.18 is a typical pump."},
                "comp_threshold": {"type": "number", "minimum": -60, "maximum": 0,
                                   "description": "Master compressor threshold in dB. -14 is the default and "
                                                  "levels hard; go to -6 or lower ratio to keep dynamics."},
                "comp_ratio": {"type": "number", "minimum": 1, "maximum": 20,
                               "description": "Master compression ratio. 5 is the default; 1 is off."},
                "comp_release": {"type": "number", "minimum": 0.01, "maximum": 1.0,
                                 "description": "Master compressor release in seconds."},
                "limiter": {"type": "boolean",
                            "description": "Master soft limiter. On by default; turn it off to hear true peaks."},
                "seed": {"type": "integer", "minimum": 0,
                         "description": "Random seed for the audio engine. The same project and seed "
                                        "always render identically; change it to reroll probability "
                                        "steps and noise character."},
            },
        },
    },
    {
        "name": "bf_tracks",
        "description": "List, add, remove or update tracks (mixer + synth parameters). "
                       "Use action='describe_engines' to see every synth engine and its knobs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "add", "remove", "update", "describe_engines"],
                           "default": "list"},
                "track": {"type": "string", "description": "Track id, for remove/update."},
                "name": {"type": "string"},
                "engine": {"type": "string", "description": "Synth engine, e.g. kick, snare, hat, sub808, pluck, pad."},
                "gain": {"type": "number", "minimum": 0, "maximum": 1.5},
                "pan": {"type": "number", "minimum": -1, "maximum": 1},
                "mute": {"type": "boolean"},
                "solo": {"type": "boolean"},
                "reverb": {"type": "number", "minimum": 0, "maximum": 1},
                "delay": {"type": "number", "minimum": 0, "maximum": 1},
                "duck": {"type": "number", "minimum": 0, "maximum": 1,
                         "description": "Sidechain: how far the kick pushes this track "
                                        "down on every hit. 0 off, 0.5 is a clear pump."},
                "params": {"type": "object",
                           "description": "Engine knobs, e.g. {\"tune\": 46, \"decay\": 0.8, \"drive\": 0.4}."},
            },
        },
    },
    {
        "name": "bf_set_steps",
        "description": "Write a drum row from a pattern string. " + _STEPS_DOC,
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string"},
                "steps": {"description": "Pattern string, or an array of step objects/velocities."},
                "pattern": {"description": "Pattern index or name; defaults to selected."},
                "mode": {"type": "string", "enum": ["replace", "merge", "erase"], "default": "replace"},
                "length": {"type": "integer", "minimum": 1, "maximum": MAX_STEPS,
                           "description": "Resize the pattern to this many steps first."},
            },
            "required": ["track", "steps"],
        },
    },
    {
        "name": "bf_set_notes",
        "description": "Write a melodic row (bass, lead, chords). Notes are space separated: "
                       "'C2 . Eb2 - . G2' where '.' is a rest and '-' ties the previous note "
                       "one step longer. Add ':7' or ':0.6' to a note for velocity. "
                       "Chords: use 'C2+Eb2+G2'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string"},
                "notes": {"description": "Note string or array of step objects."},
                "pattern": {"description": "Pattern index or name."},
                "mode": {"type": "string", "enum": ["replace", "merge", "erase"], "default": "replace"},
                "length": {"type": "integer", "minimum": 1, "maximum": MAX_STEPS},
                "octave_shift": {"type": "integer", "description": "Transpose the written line by N octaves."},
            },
            "required": ["track", "notes"],
        },
    },
    {
        "name": "bf_edit_steps",
        "description": "Surgically toggle or tweak individual steps without rewriting the row. "
                       "Steps are 1-indexed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "integer", "minimum": 1},
                            "on": {"type": "boolean", "default": True},
                            "velocity": {"type": "number", "minimum": 0, "maximum": 1},
                            "note": {"type": "string"},
                            "roll": {"type": "integer", "minimum": 1, "maximum": 8},
                            "len": {"type": "integer", "minimum": 1},
                            "slide": {"type": "boolean"},
                            "prob": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["step"],
                    },
                },
                "pattern": {"description": "Pattern index or name."},
            },
            "required": ["track", "edits"],
        },
    },
    {
        "name": "bf_transform",
        "description": "Musical transforms on an existing row: shift, reverse, double/half time, "
                       "thin out, thicken, transpose, velocity scaling, or random variation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string", "description": "Track id, or 'all'."},
                "op": {"type": "string",
                       "enum": ["shift", "reverse", "double_time", "half_time", "thin",
                                "thicken", "transpose", "velocity", "vary", "invert"]},
                "amount": {"type": "number",
                           "description": "shift: steps. transpose: semitones. thin/thicken/vary: 0..1. velocity: multiplier."},
                "pattern": {"description": "Pattern index or name."},
                "seed": {"type": "integer"},
            },
            "required": ["track", "op"],
        },
    },
    {
        "name": "bf_clear",
        "description": "Clear one track's row, or an entire pattern.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string", "description": "Omit to clear every track in the pattern."},
                "pattern": {"description": "Pattern index or name."},
            },
        },
    },
    {
        "name": "bf_pattern",
        "description": "Manage patterns: add, duplicate, delete, select, rename, or resize "
                       "(change how many steps a pattern has).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["add", "duplicate", "delete", "select", "rename", "resize", "list"]},
                "pattern": {"description": "Target pattern index or name."},
                "name": {"type": "string"},
                "steps": {"type": "integer", "minimum": 1, "maximum": MAX_STEPS},
            },
            "required": ["action"],
        },
    },
    {
        "name": "bf_song",
        "description": "Set the song arrangement: an ordered chain of patterns with repeat counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chain": {
                    "type": "array",
                    "description": "e.g. [{\"pattern\":\"A\",\"repeat\":4},{\"pattern\":\"B\",\"repeat\":2}]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pattern": {"description": "Index or name."},
                            "repeat": {"type": "integer", "minimum": 1, "default": 1},
                        },
                        "required": ["pattern"],
                    },
                },
                "song_mode": {"type": "boolean"},
            },
        },
    },
    {
        "name": "bf_generate_drums",
        "description": "Generate a full drum groove in a genre and write it into a pattern. "
                       "Creates any missing drum tracks. Genres: " + ", ".join(generators.list_genres()),
        "inputSchema": {
            "type": "object",
            "properties": {
                "genre": {"type": "string", "enum": generators.list_genres()},
                "bars": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
                "intensity": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5,
                              "description": "0.2 sparse, 0.5 typical, 0.9 busy with rolls and ghosts."},
                "seed": {"type": "integer", "description": "Same seed = same groove."},
                "fill": {"type": "boolean", "default": True, "description": "Add a fill in the last bar."},
                "humanize": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.0},
                "pattern": {"description": "Pattern index or name to write into."},
                "set_tempo": {"type": "boolean", "default": True,
                              "description": "Also apply the genre's suggested BPM and swing."},
                "roles": {"type": "array", "items": {"type": "string"},
                          "description": "Limit generation to these roles, e.g. [\"kick\",\"hat\"]."},
                "keep_others": {"type": "boolean", "default": False,
                                "description": "Leave non-generated tracks alone instead of clearing drums."},
            },
            "required": ["genre"],
        },
    },
    {
        "name": "bf_generate_bass",
        "description": "Generate a bassline in key and write it to a melodic track. "
                       "Styles: " + ", ".join(generators.BASS_STYLES),
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string", "default": "bass"},
                "style": {"type": "string", "enum": list(generators.BASS_STYLES), "default": "follow_kick"},
                "key": {"type": "string"}, "scale": {"type": "string"},
                "octave": {"type": "integer", "minimum": 0, "maximum": 4, "default": 1},
                "bars": {"type": "integer", "minimum": 1, "maximum": 8},
                "progression": {"type": "string",
                                "description": "Named (lofi, trap, drill, jazz, cyberpunk...) or degrees like '1 6 4 5'."},
                "density": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "seed": {"type": "integer"},
                "pattern": {"description": "Pattern index or name."},
                "follow_track": {"type": "string", "default": "kick",
                                 "description": "Track whose rhythm the 'follow_kick' style locks to."},
            },
        },
    },
    {
        "name": "bf_generate_melody",
        "description": "Generate a melody or riff in key. Styles: " + ", ".join(generators.MELODY_STYLES),
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string", "default": "lead"},
                "style": {"type": "string", "enum": list(generators.MELODY_STYLES), "default": "motif"},
                "key": {"type": "string"}, "scale": {"type": "string"},
                "octave": {"type": "integer", "minimum": 1, "maximum": 7, "default": 4},
                "bars": {"type": "integer", "minimum": 1, "maximum": 8},
                "density": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.45},
                "progression": {"type": "string"},
                "seed": {"type": "integer"},
                "pattern": {"description": "Pattern index or name."},
            },
        },
    },
    {
        "name": "bf_generate_chords",
        "description": "Generate a chord progression onto a track. Progressions: "
                       + ", ".join(sorted(theory.PROGRESSIONS)) + ", or degrees like '1 6 4 5'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string", "default": "chords"},
                "progression": {"type": "string", "default": "lofi"},
                "rhythm": {"type": "string", "enum": ["hold", "stab", "offbeat", "pulse", "arp"], "default": "hold"},
                "voicing": {"type": "string", "enum": ["triad", "seventh", "spread"], "default": "seventh"},
                "key": {"type": "string"}, "scale": {"type": "string"},
                "octave": {"type": "integer", "minimum": 1, "maximum": 6, "default": 3},
                "bars": {"type": "integer", "minimum": 1, "maximum": 8},
                "seed": {"type": "integer"},
                "pattern": {"description": "Pattern index or name."},
            },
        },
    },
    {
        "name": "bf_humanize",
        "description": "Add velocity and micro-timing variation so the pattern stops sounding "
                       "quantised. Applies to one track or all of them.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track": {"type": "string", "description": "Omit for all tracks."},
                "amount": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.35},
                "pattern": {"description": "Pattern index or name."},
                "seed": {"type": "integer"},
            },
        },
    },
    {
        "name": "bf_music_reference",
        "description": "Music theory lookup: notes in a scale, and the chords of a progression "
                       "with their MIDI notes. Use before writing melodies by hand.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"}, "scale": {"type": "string"},
                "progression": {"type": "string"},
                "octave": {"type": "integer", "default": 3},
                "list_scales": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "bf_project_io",
        "description": "Save, load, list or start new projects (JSON files in beatforge/projects).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "load", "list", "new"]},
                "name": {"type": "string"},
                "keep_tracks": {"type": "boolean", "default": True,
                                "description": "For action=new: keep the current track/mixer setup."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "bf_export_audio",
        "description": "Render the current pattern or the whole song to a .wav file on disk. "
                       "Requires the BeatForge UI to be open in a browser (it does the rendering). "
                       "Returns the saved file path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Without extension. Defaults to the project name."},
                "repeats": {"type": "integer", "minimum": 1, "maximum": 32, "default": 2,
                            "description": "How many times to loop the pattern (ignored in song mode)."},
                "song": {"type": "boolean", "default": False, "description": "Render the song chain instead."},
                "tail": {"type": "number", "default": 1.5, "description": "Seconds of reverb/decay tail."},
                "timeout": {"type": "number", "default": 90},
            },
        },
    },
    {
        "name": "bf_export_midi",
        "description": "Write the current pattern or the whole song to a .mid file on disk, ready to "
                       "drop into a DAW. Unlike bf_export_audio this needs no browser and returns "
                       "immediately -- drums land on GM channel 10, melodic tracks keep their pitches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Without extension. Defaults to the project name."},
                "song": {"type": "boolean", "default": False, "description": "Write the song chain instead of one pattern."},
            },
        },
    },
    {
        "name": "bf_ui",
        "description": "Check whether the BeatForge browser UI is connected, and get its URL.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "bf_undo",
        "description": "Step the whole project back (or forward) through its edit history. "
                       "Use this the moment a generated part is worse than what it replaced -- "
                       "every tool that writes state can be walked back.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["undo", "redo", "depth"],
                           "default": "undo"},
                "steps": {"type": "integer", "minimum": 1, "default": 1,
                          "description": "How many edits to walk back."},
            },
        },
    },
    {
        "name": "bf_analyze",
        "description": (
            "Measure the rendered audio without writing a file: peak, clipping, overall "
            "and per-band levels, a level-over-time curve, and the level of specific "
            "frequencies at specific moments. Use it to answer questions you cannot "
            "answer by looking at the grid -- is the mix clipping, is the bass masking "
            "the kick, does the drop actually get louder, is that 32.7 Hz note audible. "
            "Renders the same way bf_export_audio does, so it needs the browser open."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "song": {"type": "boolean", "default": False,
                         "description": "Analyse the whole song chain instead of one pattern."},
                "repeats": {"type": "integer", "minimum": 1, "default": 2},
                "tail": {"type": "number", "default": 1.5},
                "slice": {"type": "number", "default": 5,
                          "description": "Seconds per point on the level-over-time curve."},
                "bands": {"type": "array", "items": {"type": "number"},
                          "description": "Band edges in Hz. Default [80, 200, 2000] gives "
                                         "sub / low / mid / high."},
                "probes": {
                    "type": "array",
                    "description": "Measure one exact frequency at one moment, e.g. "
                                   "[{\"t\":3.9,\"freq\":43.65}] to check an F1 landed.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "t": {"type": "number", "description": "Seconds into the render."},
                            "freq": {"type": "number", "description": "Frequency in Hz."},
                            "window": {"type": "number", "default": 0.3},
                        },
                        "required": ["t", "freq"],
                    },
                },
                "timeout": {"type": "number", "default": 300},
            },
        },
    },
    {
        "name": "bf_automate",
        "description": (
            "Per-pattern automation: override a track's gain, mute or synth parameters "
            "for one pattern only, leaving the track's own settings alone. Every value is "
            "either a constant, or [from, to] to sweep it across the pattern -- so "
            "{\"params\":{\"cutoff\":[400,6000]}} is a filter sweep and {\"gain\":[0,1]} "
            "is a fade in. This is how a section gets its own sound without a second track."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "clear", "list"], "default": "set"},
                "pattern": {"description": "Pattern index or name; defaults to selected."},
                "track": {"type": "string",
                          "description": "Track id. Omit with action='clear' to clear the "
                                         "whole pattern."},
                "gain": {"description": "Level multiplier for this pattern: 0.5, or [0,1] "
                                        "to fade in across it."},
                "mute": {"type": "boolean", "description": "Silence this track for this pattern."},
                "params": {"type": "object",
                           "description": "Engine knob overrides, each a number or [from, to], "
                                          "e.g. {\"cutoff\": [400, 6000], \"drive\": 0.8}."},
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

class ToolRunner(object):
    def __init__(self, project, root, url):
        self.proj = project
        self.root = root
        self.url = url

    def call(self, name, args):
        fn = getattr(self, "_" + name, None)
        if fn is None:
            raise ValueError("unknown tool %r" % name)
        return fn(args or {})

    # -- read ---------------------------------------------------------------
    def _bf_get_project(self, a):
        data = self.proj.snapshot()
        parts = []
        if a.get("all_patterns"):
            for i in range(len(data["patterns"])):
                parts.append(render_ascii(data, i))
        else:
            parts.append(render_ascii(data, a.get("pattern")))
        parts.append("tracks:\n" + _track_summary(data))
        if a.get("full"):
            parts.append("raw:\n" + json.dumps(data, indent=1))
        return "\n\n".join(parts)

    def _bf_ui(self, a):
        n = len(self.proj._listeners)
        return ("UI %s (%d connected client%s). Open %s in a browser."
                % ("connected" if n else "NOT connected", n, "" if n == 1 else "s", self.url))

    def _bf_undo(self, a):
        action = a.get("action") or "undo"
        if action == "depth":
            u, r = self.proj.history_depth()
            return "%d edit%s can be undone, %d redone." % (u, "" if u == 1 else "s", r)
        steps = int(a.get("steps") or 1)
        n = self.proj.undo(steps) if action == "undo" else self.proj.redo(steps)
        u, r = self.proj.history_depth()
        if not n:
            return "nothing to %s." % action
        return "%s %d edit%s (%d undo / %d redo left)." % (
            "undid" if action == "undo" else "redid", n, "" if n == 1 else "s", u, r)

    # -- transport ----------------------------------------------------------
    def _bf_set_transport(self, a):
        changed = []

        def fn(data):
            if a.get("bpm") is not None:
                data["bpm"] = max(30, min(300, float(a["bpm"])))
                changed.append("bpm=%g" % data["bpm"])
            if a.get("swing") is not None:
                data["swing"] = max(0.0, min(0.75, float(a["swing"])))
                changed.append("swing=%.2f" % data["swing"])
            if a.get("master_gain") is not None:
                data["masterGain"] = max(0.0, min(1.5, float(a["master_gain"])))
                changed.append("master=%.2f" % data["masterGain"])
            if a.get("key"):
                theory.root_pc(a["key"])
                data["key"] = a["key"]
                changed.append("key=%s" % a["key"])
            if a.get("scale"):
                if a["scale"] not in theory.SCALES:
                    raise ValueError("unknown scale %r -- %s"
                                     % (a["scale"], ", ".join(sorted(theory.SCALES))))
                data["scale"] = a["scale"]
                changed.append("scale=%s" % a["scale"])
            if a.get("name"):
                data["name"] = a["name"]
                changed.append("name=%s" % a["name"])
            if a.get("pattern") is not None:
                _, i = _resolve_pattern(self.proj, data, a["pattern"])
                data["current"] = i
                changed.append("pattern=%s" % data["patterns"][i]["name"])
            if a.get("song_mode") is not None:
                data["songMode"] = bool(a["song_mode"])
                changed.append("song_mode=%s" % data["songMode"])
            if a.get("duck_source"):
                self.proj.track(data, a["duck_source"])
                data["duckSource"] = a["duck_source"]
                changed.append("duck_source=%s" % data["duckSource"])
            if a.get("duck_release") is not None:
                data["duckRelease"] = max(0.02, min(1.0, float(a["duck_release"])))
                changed.append("duck_release=%.2f" % data["duckRelease"])
            for key, field, lo, hi in (("comp_threshold", "compThreshold", -60, 0),
                                       ("comp_ratio", "compRatio", 1, 20),
                                       ("comp_release", "compRelease", 0.01, 1.0)):
                if a.get(key) is not None:
                    data[field] = max(lo, min(hi, float(a[key])))
                    changed.append("%s=%g" % (key, data[field]))
            if a.get("limiter") is not None:
                data["limiter"] = bool(a["limiter"])
                changed.append("limiter=%s" % data["limiter"])
            if a.get("seed") is not None:
                data["seed"] = max(0, int(a["seed"]))
                changed.append("seed=%d" % data["seed"])
            if a.get("playing") is not None:
                data["playing"] = bool(a["playing"])
                changed.append("playing=%s" % data["playing"])
        self.proj.mutate(fn)
        if a.get("playing") is not None:
            self.proj.push_command({"cmd": "play" if a["playing"] else "stop"})
        if not changed:
            return "nothing changed"
        return "transport: " + ", ".join(changed)

    # -- tracks -------------------------------------------------------------
    def _bf_tracks(self, a):
        action = a.get("action", "list")
        if action == "describe_engines":
            lines = ["drum engines:"]
            for e, p in sorted(DRUM_ENGINES.items()):
                lines.append("  %-9s %s" % (e, ", ".join("%s=%g" % kv for kv in sorted(p.items()))))
            lines.append("melodic engines:")
            for e, p in sorted(MELODIC_ENGINES.items()):
                lines.append("  %-12s %s" % (e, ", ".join("%s=%g" % kv for kv in sorted(p.items()))))
            return "\n".join(lines)

        if action == "list":
            return "tracks:\n" + _track_summary(self.proj.snapshot())

        result = {}

        def fn(data):
            if action == "add":
                engine = a.get("engine", "perc")
                if engine not in ALL_ENGINES:
                    raise ValueError("unknown engine %r -- run bf_tracks "
                                     "action=describe_engines" % engine)
                name = a.get("name") or engine.replace("_", " ").title()
                tid = slugify(a.get("track") or name, set(t["id"] for t in data["tracks"]))
                from .state import _track
                t = _track(tid, name, engine,
                           gain=float(a.get("gain", 0.8)), pan=float(a.get("pan", 0.0)))
                if a.get("params"):
                    t["params"].update({k: float(v) for k, v in a["params"].items()})
                for key in ("reverb", "delay"):
                    if a.get(key) is not None:
                        t[key] = float(a[key])
                data["tracks"].append(t)
                for p in data["patterns"]:
                    p["grid"][tid] = [None] * p["steps"]
                result["msg"] = "added track '%s' (%s, %s)" % (tid, engine, t["kind"])
            elif action == "remove":
                t = _find_track(data, a["track"])
                data["tracks"] = [x for x in data["tracks"] if x["id"] != t["id"]]
                for p in data["patterns"]:
                    p["grid"].pop(t["id"], None)
                result["msg"] = "removed track '%s'" % t["id"]
            elif action == "update":
                t = _find_track(data, a["track"])
                bits = []
                if a.get("engine"):
                    if a["engine"] not in ALL_ENGINES:
                        raise ValueError("unknown engine %r" % a["engine"])
                    t["engine"] = a["engine"]
                    t["kind"] = engine_kind(a["engine"])
                    merged = dict(ALL_ENGINES[a["engine"]])
                    merged.update({k: v for k, v in t["params"].items() if k in merged})
                    t["params"] = merged
                    bits.append("engine=" + a["engine"])
                if a.get("name"):
                    t["name"] = a["name"]
                    bits.append("name")
                for key, lo, hi in (("gain", 0, 1.5), ("pan", -1, 1),
                                    ("reverb", 0, 1), ("delay", 0, 1),
                                    ("duck", 0, 1)):
                    if a.get(key) is not None:
                        t[key] = max(lo, min(hi, float(a[key])))
                        bits.append("%s=%.2f" % (key, t[key]))
                for key in ("mute", "solo"):
                    if a.get(key) is not None:
                        t[key] = bool(a[key])
                        bits.append("%s=%s" % (key, t[key]))
                if a.get("params"):
                    for k, v in a["params"].items():
                        t["params"][k] = float(v)
                    bits.append("params(" + ",".join(sorted(a["params"])) + ")")
                result["msg"] = "track '%s': %s" % (t["id"], ", ".join(bits) or "no change")
            else:
                raise ValueError("unknown action %r" % action)
        self.proj.mutate(fn)
        return result["msg"]

    # -- step editing -------------------------------------------------------
    def _bf_set_steps(self, a):
        out = {}

        def fn(data):
            pat, pi = _resolve_pattern(self.proj, data, a.get("pattern"))
            if a.get("length"):
                _resize_pattern(pat, a["length"])
            t = _find_track(data, a["track"])
            row = parse_steps(a["steps"], pat["steps"])
            final = _write_row(pat, t["id"], row, a.get("mode", "replace"))
            out["msg"] = ("%s [%s] %s  (%d hits)"
                          % (t["id"], pat["name"], format_steps(final), _count(final)))
        self.proj.mutate(fn)
        return out["msg"]

    def _bf_set_notes(self, a):
        out = {}

        def fn(data):
            pat, pi = _resolve_pattern(self.proj, data, a.get("pattern"))
            if a.get("length"):
                _resize_pattern(pat, a["length"])
            t = _find_track(data, a["track"])
            row = _parse_note_line(a["notes"], pat["steps"])
            shift = int(a.get("octave_shift") or 0) * 12
            if shift:
                for s in row:
                    if s is None:
                        continue
                    if s.get("note") is not None:
                        s["note"] += shift
                    if s.get("notes"):
                        s["notes"] = [n + shift for n in s["notes"]]
            final = _write_row(pat, t["id"], row, a.get("mode", "replace"))
            if t["kind"] != "melodic":
                out["warn"] = ("  note: track '%s' uses drum engine '%s' -- pitches will "
                               "retune the drum rather than play a scale."
                               % (t["id"], t["engine"]))
            out["msg"] = "%s [%s] %s  (%d notes)" % (t["id"], pat["name"],
                                                     format_steps(final, True), _count(final))
        self.proj.mutate(fn)
        return out["msg"] + out.get("warn", "")

    def _bf_edit_steps(self, a):
        out = {}

        def fn(data):
            pat, _ = _resolve_pattern(self.proj, data, a.get("pattern"))
            t = _find_track(data, a["track"])
            row = pat["grid"].setdefault(t["id"], [None] * pat["steps"])
            for e in a["edits"]:
                i = int(e["step"]) - 1
                if not 0 <= i < pat["steps"]:
                    raise ValueError("step %d out of range 1..%d" % (i + 1, pat["steps"]))
                if e.get("on") is False:
                    row[i] = None
                    continue
                step = dict(row[i] or {})
                step["v"] = float(e.get("velocity", step.get("v", 0.8)))
                if e.get("note") is not None:
                    step["note"] = note_to_midi(e["note"])
                for key in ("roll", "len"):
                    if e.get(key) is not None:
                        step[key] = int(e[key])
                if e.get("prob") is not None:
                    step["prob"] = float(e["prob"])
                if e.get("slide") is not None:
                    if e["slide"]:
                        step["slide"] = True
                    else:
                        step.pop("slide", None)
                row[i] = step
            melodic = t["kind"] == "melodic"
            out["msg"] = ("%s [%s] %s  (%d edits, %d hits)"
                          % (t["id"], pat["name"], format_steps(row, melodic),
                             len(a["edits"]), _count(row)))
        self.proj.mutate(fn)
        return out["msg"]

    def _bf_clear(self, a):
        out = {}

        def fn(data):
            pat, _ = _resolve_pattern(self.proj, data, a.get("pattern"))
            if a.get("track"):
                t = _find_track(data, a["track"])
                pat["grid"][t["id"]] = [None] * pat["steps"]
                out["msg"] = "cleared %s in pattern %s" % (t["id"], pat["name"])
            else:
                for tid in pat["grid"]:
                    pat["grid"][tid] = [None] * pat["steps"]
                out["msg"] = "cleared all tracks in pattern %s" % pat["name"]
        self.proj.mutate(fn)
        return out["msg"]

    def _bf_transform(self, a):
        out = {}
        rng = random.Random(a.get("seed"))
        op = a["op"]
        amt = a.get("amount")

        def fn(data):
            pat, _ = _resolve_pattern(self.proj, data, a.get("pattern"))
            if str(a["track"]).lower() == "all":
                targets = list(data["tracks"])
            else:
                targets = [_find_track(data, a["track"])]
            names = []
            for t in targets:
                row = pat["grid"].setdefault(t["id"], [None] * pat["steps"])
                pat["grid"][t["id"]] = _transform_row(row, op, amt, pat["steps"], rng)
                names.append(t["id"])
            out["msg"] = ("%s(%s) on %s [%s]\n%s"
                          % (op, "" if amt is None else amt, ", ".join(names), pat["name"],
                             render_ascii(data, data["patterns"].index(pat))))
        self.proj.mutate(fn)
        return out["msg"]

    # -- patterns / song ----------------------------------------------------
    def _bf_pattern(self, a):
        out = {}
        action = a["action"]

        def fn(data):
            if action == "list":
                out["msg"] = "\n".join(
                    "  %d %-8s %2d steps  %d hits%s"
                    % (i, p["name"], p["steps"],
                       sum(_count(r) for r in p["grid"].values()),
                       "  <- selected" if i == data["current"] else "")
                    for i, p in enumerate(data["patterns"]))
                return
            if action == "add":
                steps = int(a.get("steps") or data["patterns"][data["current"]]["steps"])
                name = a.get("name") or _next_name(data)
                data["patterns"].append(empty_pattern(name, data["tracks"], steps))
                data["current"] = len(data["patterns"]) - 1
                out["msg"] = "added pattern '%s' (%d steps), now selected" % (name, steps)
                return
            pat, i = _resolve_pattern(self.proj, data, a.get("pattern"))
            if action == "duplicate":
                new = copy.deepcopy(pat)
                new["name"] = a.get("name") or _next_name(data)
                data["patterns"].insert(i + 1, new)
                data["current"] = i + 1
                out["msg"] = "duplicated '%s' as '%s'" % (pat["name"], new["name"])
            elif action == "delete":
                if len(data["patterns"]) == 1:
                    raise ValueError("cannot delete the last remaining pattern")
                data["patterns"].pop(i)
                data["song"] = [c for c in data["song"] if c["pattern"] != i]
                for c in data["song"]:
                    if c["pattern"] > i:
                        c["pattern"] -= 1
                data["current"] = max(0, min(data["current"], len(data["patterns"]) - 1))
                out["msg"] = "deleted pattern '%s'" % pat["name"]
            elif action == "select":
                data["current"] = i
                out["msg"] = "selected pattern '%s'" % pat["name"]
            elif action == "rename":
                old = pat["name"]
                pat["name"] = a["name"]
                out["msg"] = "renamed '%s' -> '%s'" % (old, pat["name"])
            elif action == "resize":
                _resize_pattern(pat, a["steps"])
                out["msg"] = "pattern '%s' is now %d steps" % (pat["name"], pat["steps"])
            else:
                raise ValueError("unknown action %r" % action)
        self.proj.mutate(fn)
        return out["msg"]

    def _bf_song(self, a):
        out = {}

        def fn(data):
            if a.get("chain") is not None:
                chain = []
                for c in a["chain"]:
                    _, i = _resolve_pattern(self.proj, data, c["pattern"])
                    chain.append({"pattern": i, "repeat": max(1, int(c.get("repeat", 1)))})
                data["song"] = chain
            if a.get("song_mode") is not None:
                data["songMode"] = bool(a["song_mode"])
            bars = sum(c["repeat"] for c in data["song"])
            out["msg"] = ("song (%s): %s  = %d pattern-plays"
                          % ("active" if data["songMode"] else "inactive",
                             " -> ".join("%s×%d" % (data["patterns"][c["pattern"]]["name"], c["repeat"])
                                         for c in data["song"]) or "(empty)", bars))
        self.proj.mutate(fn)
        return out["msg"]

    # -- generators ---------------------------------------------------------
    def _bf_generate_drums(self, a):
        out = {}

        def fn(data):
            pat, pi = _resolve_pattern(self.proj, data, a.get("pattern"))
            bars = int(a.get("bars", 1))
            spb = 16
            _resize_pattern(pat, bars * spb)
            res = generators.generate_drums(
                a["genre"], bars=bars, steps_per_bar=spb,
                intensity=float(a.get("intensity", 0.5)), seed=a.get("seed"),
                fill=a.get("fill", True), humanize=float(a.get("humanize", 0.0)))
            wanted = set(a["roles"]) if a.get("roles") else None
            if not a.get("keep_others"):
                for t in data["tracks"]:
                    if t["kind"] == "drum" and (wanted is None or t["id"] in wanted):
                        pat["grid"][t["id"]] = [None] * pat["steps"]
            written = []
            for role, row in res["roles"].items():
                if wanted is not None and role not in wanted:
                    continue
                t = _ensure_role_track(self.proj, data, role)
                pat["grid"][t["id"]] = (list(row) + [None] * pat["steps"])[:pat["steps"]]
                written.append("%s(%d)" % (t["id"], _count(row)))
            if a.get("set_tempo", True):
                data["bpm"] = res["bpm"]
                data["swing"] = res["swing"]
            out["msg"] = ("generated %s: %d bar(s), %s @ %s BPM swing %.2f\n\n%s"
                          % (a["genre"], bars, " ".join(written), data["bpm"], data["swing"],
                             render_ascii(data, pi)))
        self.proj.mutate(fn)
        return out["msg"]

    def _bf_generate_bass(self, a):
        out = {}

        def fn(data):
            pat, pi = _resolve_pattern(self.proj, data, a.get("pattern"))
            tid = a.get("track", "bass")
            try:
                t = _find_track(data, tid)
            except ValueError:
                from .state import _track
                new_id = slugify(tid, set(x["id"] for x in data["tracks"]))
                t = _track(new_id, "Bass", "sub808", gain=0.85)
                data["tracks"].append(t)
                for p in data["patterns"]:
                    p["grid"][new_id] = [None] * p["steps"]
            bars = int(a.get("bars") or max(1, pat["steps"] // 16))
            spb = max(4, pat["steps"] // max(1, bars))
            follow = None
            try:
                follow = pat["grid"].get(_find_track(data, a.get("follow_track", "kick"))["id"])
            except ValueError:
                pass
            row = generators.generate_bassline(
                key=a.get("key") or data["key"], scale=a.get("scale") or data["scale"],
                style=a.get("style", "follow_kick"), bars=bars, steps_per_bar=spb,
                octave=int(a.get("octave", 1)), kick_row=follow,
                progression=a.get("progression"), seed=a.get("seed"),
                density=float(a.get("density", 0.5)))
            final = _write_row(pat, t["id"], row, "replace")
            out["msg"] = ("bass '%s' [%s] style=%s key=%s %s\n%s"
                          % (t["id"], pat["name"], a.get("style", "follow_kick"),
                             a.get("key") or data["key"], a.get("scale") or data["scale"],
                             format_steps(final, True)))
        self.proj.mutate(fn)
        return out["msg"]

    def _bf_generate_melody(self, a):
        out = {}

        def fn(data):
            pat, pi = _resolve_pattern(self.proj, data, a.get("pattern"))
            t = _ensure_melodic(data, a.get("track", "lead"), "pluck", "Lead")
            bars = int(a.get("bars") or max(1, pat["steps"] // 16))
            spb = max(4, pat["steps"] // max(1, bars))
            row = generators.generate_melody(
                key=a.get("key") or data["key"], scale=a.get("scale") or data["scale"],
                style=a.get("style", "motif"), bars=bars, steps_per_bar=spb,
                octave=int(a.get("octave", 4)), seed=a.get("seed"),
                density=float(a.get("density", 0.45)), progression=a.get("progression"))
            final = _write_row(pat, t["id"], row, "replace")
            out["msg"] = ("melody '%s' [%s] style=%s\n%s"
                          % (t["id"], pat["name"], a.get("style", "motif"),
                             format_steps(final, True)))
        self.proj.mutate(fn)
        return out["msg"]

    def _bf_generate_chords(self, a):
        out = {}

        def fn(data):
            pat, pi = _resolve_pattern(self.proj, data, a.get("pattern"))
            t = _ensure_melodic(data, a.get("track", "chords"), "pad", "Chords")
            bars = int(a.get("bars") or max(1, pat["steps"] // 16))
            spb = max(4, pat["steps"] // max(1, bars))
            voicing = a.get("voicing", "seventh")
            row, labels = generators.generate_chords(
                key=a.get("key") or data["key"], scale=a.get("scale") or data["scale"],
                progression=a.get("progression", "lofi"), bars=bars, steps_per_bar=spb,
                octave=int(a.get("octave", 3)),
                voicing="triad" if voicing == "triad" else voicing,
                rhythm=a.get("rhythm", "hold"), seed=a.get("seed"))
            final = _write_row(pat, t["id"], row, "replace")
            out["msg"] = ("chords '%s' [%s] %s -> %s\n%s"
                          % (t["id"], pat["name"], a.get("progression", "lofi"),
                             " ".join(labels), format_steps(final, True)))
        self.proj.mutate(fn)
        return out["msg"]

    def _bf_humanize(self, a):
        out = {}
        rng = random.Random(a.get("seed"))
        amount = float(a.get("amount", 0.35))

        def fn(data):
            pat, pi = _resolve_pattern(self.proj, data, a.get("pattern"))
            targets = [_find_track(data, a["track"])] if a.get("track") else data["tracks"]
            for t in targets:
                row = pat["grid"].get(t["id"])
                if row:
                    pat["grid"][t["id"]] = generators.humanize_row(row, amount, rng)
            out["msg"] = ("humanized %d track(s) by %.2f in pattern %s"
                          % (len(targets), amount, pat["name"]))
        self.proj.mutate(fn)
        return out["msg"]

    # -- reference ----------------------------------------------------------
    def _bf_music_reference(self, a):
        data = self.proj.snapshot()
        key = a.get("key") or data["key"]
        scale = a.get("scale") or data["scale"]
        if a.get("list_scales"):
            return ("scales: " + ", ".join(sorted(theory.SCALES))
                    + "\nprogressions: " + ", ".join(sorted(theory.PROGRESSIONS)))
        octave = int(a.get("octave", 3))
        lines = [theory.describe(key, scale)]
        low = theory.degree_note(key, scale, 1, 1)
        notes = theory.scale_notes(key, scale, low, low + 24)
        lines.append("playable (2 octaves from %s): %s"
                     % (midi_to_note(low), " ".join(midi_to_note(n) for n in notes)))
        prog = a.get("progression")
        if prog:
            lines.append("progression '%s' in %s %s (octave %d):" % (prog, key, scale, octave))
            for notes_, label in theory.progression_notes(key, scale, prog, octave, "seventh"):
                lines.append("  %-5s %s" % (label, " ".join(midi_to_note(n) for n in notes_)))
        else:
            lines.append("named progressions: " + ", ".join(sorted(theory.PROGRESSIONS)))
        return "\n".join(lines)

    # -- io -----------------------------------------------------------------
    def _bf_project_io(self, a):
        action = a["action"]
        pdir = _projects_dir(self.root)
        if action == "list":
            files = sorted(glob.glob(os.path.join(pdir, "*.json")))
            if not files:
                return "no saved projects in %s" % pdir
            return "saved projects:\n" + "\n".join(
                "  %-28s %s" % (os.path.basename(f)[:-5],
                                time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(f))))
                for f in files if not os.path.basename(f).startswith("_"))
        if action == "save":
            data = self.proj.snapshot()
            name = a.get("name") or data["name"]
            safe = re.sub(r"[^A-Za-z0-9 _-]", "_", name).strip() or "untitled"
            path = os.path.join(pdir, safe + ".json")
            with open(path, "w") as fh:
                json.dump(data, fh, indent=1)
            if name != data["name"]:
                self.proj.mutate(lambda d: d.update({"name": name}))
            return "saved to %s" % path
        if action == "load":
            name = a["name"]
            path = os.path.join(pdir, re.sub(r"\.json$", "", name) + ".json")
            if not os.path.exists(path):
                raise ValueError("no such project %r in %s" % (name, pdir))
            with open(path) as fh:
                loaded = migrate(json.load(fh))
            self.proj.mutate(lambda d: (d.clear(), d.update(loaded)))
            self.proj.push_command({"cmd": "reload"})
            return "loaded %s\n\n%s" % (path, render_ascii(self.proj.snapshot()))
        if action == "new":
            keep = a.get("keep_tracks", True)
            cur = self.proj.snapshot()
            fresh = default_project(a.get("name") or "Untitled")
            if keep:
                fresh["tracks"] = cur["tracks"]
                fresh["patterns"] = [empty_pattern("A", cur["tracks"])]
                fresh["bpm"] = cur["bpm"]
                fresh["key"] = cur["key"]
                fresh["scale"] = cur["scale"]
            self.proj.mutate(lambda d: (d.clear(), d.update(fresh)))
            self.proj.push_command({"cmd": "reload"})
            return "new project '%s'%s" % (fresh["name"], " (kept tracks)" if keep else "")
        raise ValueError("unknown action %r" % action)

    def _bf_export_audio(self, a):
        if not self.proj._listeners:
            raise ValueError(
                "no browser connected -- open %s first, the UI does the audio rendering"
                % self.url)
        data = self.proj.snapshot()
        name = a.get("filename") or data["name"]
        safe = re.sub(r"[^A-Za-z0-9 _-]", "_", str(name)).strip() or "beatforge"
        job = "%s-%d" % (safe, int(time.time() * 1000) % 100000)
        ev = threading.Event()
        with _export_lock:
            _export_waiters[job] = {"event": ev, "path": None, "error": None}
        self.proj.push_command({
            "cmd": "export", "job": job, "filename": safe,
            "repeats": int(a.get("repeats", 2)), "song": bool(a.get("song", False)),
            "tail": float(a.get("tail", 1.5)),
        })
        timeout = float(a.get("timeout", 90))
        if not ev.wait(timeout):
            with _export_lock:
                _export_waiters.pop(job, None)
            raise ValueError("export timed out after %gs -- is the browser tab in the "
                             "foreground? Background tabs throttle audio rendering." % timeout)
        with _export_lock:
            info = _export_waiters.pop(job, {})
        if info.get("error"):
            raise ValueError("browser reported: %s" % info["error"])
        return "exported %s (%.1f KB)" % (info["path"], info.get("bytes", 0) / 1024.0)

    def _bf_analyze(self, a):
        if not self.proj._listeners:
            raise ValueError(
                "no browser connected -- open %s first, the UI does the rendering"
                % self.url)
        job = "analyze-%d" % (int(time.time() * 1000) % 100000)
        ev = threading.Event()
        with _export_lock:
            _analyze_waiters[job] = {"event": ev, "report": None, "error": None}
        cmd = {
            "cmd": "analyze", "job": job,
            "repeats": int(a.get("repeats", 2)), "song": bool(a.get("song", False)),
            "tail": float(a.get("tail", 1.5)),
            "slice": float(a.get("slice", 5)),
        }
        if a.get("bands"):
            cmd["bands"] = [float(b) for b in a["bands"]]
        if a.get("probes"):
            cmd["probes"] = [{"t": float(p["t"]), "freq": float(p["freq"]),
                              "window": float(p.get("window", 0.3))} for p in a["probes"]]
        self.proj.push_command(cmd)
        timeout = float(a.get("timeout", 300))
        if not ev.wait(timeout):
            with _export_lock:
                _analyze_waiters.pop(job, None)
            raise ValueError("analysis timed out after %gs" % timeout)
        with _export_lock:
            info = _analyze_waiters.pop(job, {})
        if info.get("error"):
            raise ValueError("browser reported: %s" % info["error"])
        return _format_analysis(info["report"])

    def _bf_automate(self, a):
        out = {}

        def fn(data):
            pat, _ = _resolve_pattern(self.proj, data, a.get("pattern"))
            mix = pat.setdefault("mix", {})
            action = a.get("action") or "set"
            if action == "clear":
                if a.get("track"):
                    mix.pop(_find_track(data, a["track"])["id"], None)
                else:
                    pat["mix"] = {}
                out["msg"] = "cleared automation on %s" % pat["name"]
                return
            if action == "list":
                out["msg"] = ("automation on %s:\n%s" % (pat["name"], json.dumps(mix, indent=1))
                              if mix else "no automation on %s" % pat["name"])
                return
            track = _find_track(data, a["track"])
            entry = mix.setdefault(track["id"], {})
            if a.get("gain") is not None:
                entry["gain"] = _ramp_value(a["gain"])
            if a.get("mute") is not None:
                entry["mute"] = bool(a["mute"])
            if a.get("params"):
                p = entry.setdefault("params", {})
                for k, v in a["params"].items():
                    p[k] = _ramp_value(v)
            out["msg"] = "%s on %s: %s" % (track["id"], pat["name"], json.dumps(entry))
        self.proj.mutate(fn)
        return out["msg"]

    def _bf_export_midi(self, a):
        data = self.proj.snapshot()
        name = a.get("filename") or data["name"]
        safe = re.sub(r"[^A-Za-z0-9 _-]", "_", str(name)).strip() or "beatforge"
        out_dir = os.path.join(self.root, "exports")
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        path = os.path.join(out_dir, "%s.mid" % safe)
        n = 2
        while os.path.exists(path):
            path = os.path.join(out_dir, "%s-%d.mid" % (safe, n))
            n += 1
        tracks, notes = midi.write(data, path, song=bool(a.get("song", False)))
        return "wrote %s -- %d tracks, %d notes, %.1f KB" % (
            path, tracks, notes, os.path.getsize(path) / 1024.0)


def _ensure_melodic(data, tid, engine, label):
    try:
        return _find_track(data, tid)
    except ValueError:
        pass
    from .state import _track
    new_id = slugify(tid, set(x["id"] for x in data["tracks"]))
    t = _track(new_id, label, engine, gain=0.55, reverb=0.25)
    data["tracks"].append(t)
    for p in data["patterns"]:
        p["grid"][new_id] = [None] * p["steps"]
    return t


def _parse_note_line(spec, length):
    """Like parse_notes but also understands 'C3+Eb3+G3' chord tokens."""
    if not isinstance(spec, str):
        return parse_notes(spec, length)
    tokens = [t for t in re.split(r"[\s,|]+", spec) if t]
    out = []
    for tok in tokens:
        if "+" in tok and tok not in (".", "-"):
            head, _, vtxt = tok.partition(":")
            vel = 0.7
            if vtxt:
                v = float(vtxt)
                vel = round(v / 9.0, 3) if v > 1.0 else v
            out.append({"v": vel, "notes": [note_to_midi(n) for n in head.split("+")], "len": 1})
        else:
            out.append(tok)
    # hand the mixed list to parse_notes, which handles '.' / '-' / plain notes
    return parse_notes(out, length)


def _next_name(data):
    used = set(p["name"] for p in data["patterns"])
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ch not in used:
            return ch
    return "P%d" % (len(data["patterns"]) + 1)


def _transform_row(row, op, amt, n, rng):
    row = [None if s is None else dict(s) for s in row]
    if op == "shift":
        k = int(amt or 1) % n
        return row[-k:] + row[:-k]
    if op == "reverse":
        return row[::-1]
    if op == "double_time":
        half = row[: n // 2]
        return (half + half)[:n]
    if op == "half_time":
        out = [None] * n
        for i, s in enumerate(row):
            if s is not None and i * 2 < n:
                out[i * 2] = s
        return out
    if op == "thin":
        p = float(amt if amt is not None else 0.4)
        return [None if (s is not None and rng.random() < p) else s for s in row]
    if op == "thicken":
        p = float(amt if amt is not None else 0.3)
        for i, s in enumerate(row):
            if s is None and rng.random() < p:
                nearest = next((x for x in row if x is not None), {"v": 0.6})
                new = {"v": round(max(0.15, nearest.get("v", 0.6) * 0.5), 3)}
                if nearest.get("note") is not None:
                    new["note"] = nearest["note"]
                row[i] = new
        return row
    if op == "transpose":
        k = int(amt or 0)
        for s in row:
            if s is None:
                continue
            if s.get("note") is not None:
                s["note"] += k
            if s.get("notes"):
                s["notes"] = [x + k for x in s["notes"]]
        return row
    if op == "velocity":
        m = float(amt if amt is not None else 1.0)
        for s in row:
            if s is not None:
                s["v"] = round(max(0.02, min(1.0, s.get("v", 0.8) * m)), 3)
        return row
    if op == "invert":
        for s in row:
            if s is not None:
                s["v"] = round(max(0.05, min(1.0, 1.05 - s.get("v", 0.8))), 3)
        return row
    if op == "vary":
        p = float(amt if amt is not None else 0.25)
        for i in range(n):
            if rng.random() < p:
                j = rng.randrange(n)
                row[i], row[j] = row[j], row[i]
        return row
    raise ValueError("unknown op %r" % op)


def _ramp_value(v):
    """A parameter override is a constant, or [from, to] swept over the pattern."""
    if isinstance(v, (list, tuple)):
        if len(v) != 2:
            raise ValueError("a ramp must be [from, to], got %r" % (v,))
        return [float(v[0]), float(v[1])]
    return float(v)


def _format_analysis(r):
    if not r:
        return "no report returned"
    lines = ["%.3f s at %d Hz" % (r["seconds"], r["sampleRate"]),
             "peak %.1f dBFS (%.1f%% FS)   clipped samples %d   overall %.1f dBFS RMS"
             % (r["peak"], r["peakPct"], r["clipped"], r["rms"])]
    lines.append("")
    lines.append("bands:")
    for b in r["bands"]:
        lo, hi = b["from"], b["to"]
        label = ("below %g Hz" % hi if not lo else
                 "%g Hz and up" % lo if not hi else "%g-%g Hz" % (lo, hi))
        lines.append("  %-16s %7.1f dBFS" % (label, b["rms"]))
    if r.get("timeline"):
        lines.append("")
        lines.append("level over time:")
        for s in r["timeline"]:
            bar = "#" * max(0, int((s["rms"] + 40) / 1.5))
            lines.append("  %6.1fs %7.1f dBFS  %s" % (s["t"], s["rms"], bar))
    if r.get("probes"):
        lines.append("")
        lines.append("frequency probes:")
        for p in r["probes"]:
            lines.append("  %6.2fs %8.2f Hz %7.1f dBFS" % (p["t"], p["freq"], p["level"]))
    return "\n".join(lines)


def complete_analyze(job, report, error=None):
    with _export_lock:
        info = _analyze_waiters.get(job)
        if not info:
            return False
        info["report"] = report
        info["error"] = error
        info["event"].set()
    return True


def complete_export(job, path, nbytes, error=None):
    with _export_lock:
        info = _export_waiters.get(job)
        if not info:
            return False
        info["path"] = path
        info["bytes"] = nbytes
        info["error"] = error
        info["event"].set()
    return True
