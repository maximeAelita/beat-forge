"""BeatForge project state: tracks, patterns, steps, song arrangement.

The state is a plain dict so it serialises straight to JSON for both the MCP
tools and the browser UI. All mutation goes through Project so the revision
counter stays accurate -- the web UI uses it to detect stale writes.
"""

import copy
import json
import os
import re
import threading
import time

VERSION = 1

# How many past states undo can walk back through.
HISTORY_LIMIT = 60

# ---------------------------------------------------------------------------
# Synth engines available in the browser audio engine (web/audio.js).
# `params` lists the tweakable knobs and their default values.
# ---------------------------------------------------------------------------

DRUM_ENGINES = {
    "kick":       {"tune": 50.0, "decay": 0.42, "punch": 0.55, "drive": 0.25, "click": 0.35},
    "kick808":    {"tune": 42.0, "decay": 0.95, "punch": 0.35, "drive": 0.40, "click": 0.20},
    "snare":      {"tune": 190.0, "decay": 0.20, "snap": 0.60, "tone": 0.45, "drive": 0.15},
    "rimshot":    {"tune": 400.0, "decay": 0.06, "snap": 0.80, "tone": 0.60, "drive": 0.10},
    "clap":       {"tune": 1100.0, "decay": 0.30, "spread": 0.55, "tone": 0.50, "drive": 0.10},
    "hat":        {"tune": 8000.0, "decay": 0.055, "tone": 0.60, "drive": 0.0, "metal": 0.5},
    "openhat":    {"tune": 7400.0, "decay": 0.38, "tone": 0.55, "drive": 0.0, "metal": 0.5},
    "ride":       {"tune": 5200.0, "decay": 0.90, "tone": 0.45, "drive": 0.0, "metal": 0.7},
    "crash":      {"tune": 4200.0, "decay": 1.60, "tone": 0.40, "drive": 0.0, "metal": 0.8},
    "tom":        {"tune": 160.0, "decay": 0.35, "punch": 0.40, "drive": 0.10, "click": 0.15},
    "shaker":     {"tune": 9000.0, "decay": 0.09, "tone": 0.70, "drive": 0.0, "metal": 0.2},
    "cowbell":    {"tune": 560.0, "decay": 0.22, "tone": 0.50, "drive": 0.15, "metal": 0.6},
    "perc":       {"tune": 320.0, "decay": 0.18, "tone": 0.55, "drive": 0.10, "metal": 0.3},
    "noise":      {"tune": 3000.0, "decay": 0.25, "tone": 0.50, "drive": 0.0, "metal": 0.0},
}

MELODIC_ENGINES = {
    "sub808":     {"decay": 1.10, "glide": 0.06, "drive": 0.35, "cutoff": 900.0, "reso": 0.4},
    "bass_saw":   {"decay": 0.45, "glide": 0.0, "drive": 0.30, "cutoff": 1400.0, "reso": 0.6},
    "bass_square": {"decay": 0.40, "glide": 0.0, "drive": 0.25, "cutoff": 1800.0, "reso": 0.5},
    "reese":      {"decay": 0.70, "glide": 0.0, "drive": 0.45, "cutoff": 1100.0, "reso": 0.7},
    "pluck":      {"decay": 0.35, "glide": 0.0, "drive": 0.10, "cutoff": 4200.0, "reso": 0.4},
    "lead_saw":   {"decay": 0.50, "glide": 0.0, "drive": 0.20, "cutoff": 5000.0, "reso": 0.5},
    "keys":       {"decay": 0.80, "glide": 0.0, "drive": 0.05, "cutoff": 6000.0, "reso": 0.2},
    "pad":        {"decay": 2.20, "glide": 0.0, "drive": 0.0, "cutoff": 3200.0, "reso": 0.3},
    "bell":       {"decay": 1.40, "glide": 0.0, "drive": 0.0, "cutoff": 9000.0, "reso": 0.1},
    "organ":      {"decay": 0.60, "glide": 0.0, "drive": 0.15, "cutoff": 5200.0, "reso": 0.2},
}

ALL_ENGINES = {}
ALL_ENGINES.update(DRUM_ENGINES)
ALL_ENGINES.update(MELODIC_ENGINES)


def engine_kind(engine):
    return "drum" if engine in DRUM_ENGINES else "melodic"


# ---------------------------------------------------------------------------
# Step grammar
# ---------------------------------------------------------------------------
#   .  -  _     rest
#   x            normal hit   (velocity 0.80)
#   X            accent       (velocity 1.00)
#   o            soft         (velocity 0.55)
#   s            ghost        (velocity 0.28)
#   1-9          velocity 1..9 -> 0.11 .. 1.00
#   r            roll of 2, R roll of 4 (at velocity 0.7)
#   | and space  ignored (bar separators, purely cosmetic)

_VEL_CHARS = {"x": 0.80, "X": 1.0, "o": 0.55, "s": 0.28, "r": 0.70, "R": 0.70}
_REST_CHARS = ".-_"
_IGNORED = " |\t\n"


def parse_steps(spec, length=None):
    """Parse a pattern string (or list) into a list of step dicts / None."""
    if isinstance(spec, list):
        out = []
        for item in spec:
            out.append(_coerce_step(item))
    else:
        out = []
        for ch in str(spec):
            if ch in _IGNORED:
                continue
            if ch in _REST_CHARS:
                out.append(None)
            elif ch in _VEL_CHARS:
                step = {"v": _VEL_CHARS[ch]}
                if ch == "r":
                    step["roll"] = 2
                elif ch == "R":
                    step["roll"] = 4
                out.append(step)
            elif ch.isdigit():
                d = int(ch)
                out.append(None if d == 0 else {"v": round(d / 9.0, 3)})
            else:
                raise ValueError(
                    "unknown step character %r -- use . x X o s r R or digits 0-9" % ch
                )
    if length is not None:
        if len(out) < length:
            # repeat short patterns to fill (so "x..." fills a 16 step bar)
            if out and length % len(out) == 0:
                reps = length // len(out)
                out = [copy.deepcopy(s) for _ in range(reps) for s in out]
            else:
                out = out + [None] * (length - len(out))
        out = out[:length]
    return out


_NOTE_RE = re.compile(r"^([A-Ga-g])([#b]{0,2})(-?\d)$")
_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_to_midi(name):
    """'C2' -> 36, 'F#3' -> 54. Octave 4 contains middle C (MIDI 60)."""
    if isinstance(name, (int, float)):
        return int(name)
    m = _NOTE_RE.match(str(name).strip())
    if not m:
        raise ValueError("bad note name %r (expected like C2, F#3, Bb1)" % name)
    letter, accidental, octave = m.groups()
    val = _PITCH_CLASS[letter.upper()]
    for ch in accidental:
        val += 1 if ch == "#" else -1
    return val + (int(octave) + 1) * 12


_MIDI_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_note(midi):
    midi = int(round(midi))
    return "%s%d" % (_MIDI_NAMES[midi % 12], midi // 12 - 1)


def parse_notes(spec, length=None):
    """Parse a melodic line: 'C2 . Eb2 - . G2 ...' into step dicts.

    '.' is a rest, '-' extends the previous note by one step (a tie).
    A token may carry a velocity suffix: 'C2:0.6' or 'C2:7'.
    """
    if isinstance(spec, list):
        tokens = [("." if t is None else t) for t in spec]
    else:
        tokens = [t for t in re.split(r"[\s,|]+", str(spec)) if t]

    out = []
    for tok in tokens:
        if isinstance(tok, dict):
            out.append(_coerce_step(tok))
            continue
        tok = str(tok)
        if tok in (".", "_", "0"):
            out.append(None)
            continue
        if tok == "-":
            # tie: lengthen the most recent note
            for prev in reversed(out):
                if prev is not None:
                    prev["len"] = prev.get("len", 1) + 1
                    break
            out.append(None)
            continue
        vel = 0.85
        if ":" in tok:
            tok, _, vtxt = tok.partition(":")
            v = float(vtxt)
            vel = round(v / 9.0, 3) if v > 1.0 else v
        out.append({"v": vel, "note": note_to_midi(tok), "len": 1})

    if length is not None:
        if len(out) < length and out and length % len(out) == 0:
            reps = length // len(out)
            out = [copy.deepcopy(s) for _ in range(reps) for s in out]
        out = (out + [None] * length)[:length]
    return out


def _coerce_step(item):
    if item is None or item is False or item == 0 or item == "":
        return None
    if item is True:
        return {"v": 0.8}
    if isinstance(item, (int, float)):
        return {"v": max(0.0, min(1.0, float(item)))} if item > 0 else None
    if isinstance(item, str):
        got = parse_steps(item)
        return got[0] if got else None
    if isinstance(item, dict):
        step = {"v": max(0.0, min(1.0, float(item.get("v", 0.8))))}
        if item.get("note") is not None:
            step["note"] = note_to_midi(item["note"])
        if item.get("notes"):
            step["notes"] = [note_to_midi(n) for n in item["notes"]]
        for key in ("len", "roll"):
            if item.get(key):
                step[key] = int(item[key])
        if item.get("prob") is not None:
            step["prob"] = max(0.0, min(1.0, float(item["prob"])))
        if item.get("nudge"):
            step["nudge"] = round(float(item["nudge"]), 3)
        if item.get("slide"):
            step["slide"] = True
        return step
    raise ValueError("cannot interpret step %r" % (item,))


def format_steps(steps, melodic=False):
    """Render steps back to a compact string for display / LLM inspection."""
    if melodic:
        out = []
        for step in steps:
            if step is None:
                out.append("..")
            elif step.get("notes"):
                names = [midi_to_note(n) for n in step["notes"]]
                out.append("[%s]" % "+".join(names))
            elif "note" in step:
                out.append(midi_to_note(step["note"]) + ("~" if step.get("slide") else ""))
            else:
                out.append("x")
        return " ".join(out)
    chars = []
    for step in steps:
        if step is None:
            chars.append(".")
            continue
        if step.get("roll"):
            chars.append("R" if step["roll"] >= 4 else "r")
            continue
        v = step.get("v", 0.8)
        if v >= 0.95:
            chars.append("X")
        elif v >= 0.68:
            chars.append("x")
        elif v >= 0.42:
            chars.append("o")
        else:
            chars.append("s")
    return "".join(chars)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def _track(tid, name, engine, gain=0.8, pan=0.0, **params):
    base = dict(ALL_ENGINES[engine])
    base.update(params)
    return {
        "id": tid,
        "name": name,
        "engine": engine,
        "kind": engine_kind(engine),
        "gain": gain,
        "pan": pan,
        "mute": False,
        "solo": False,
        "reverb": 0.0,
        "delay": 0.0,
        "duck": 0.0,
        "params": base,
    }


def default_tracks():
    return [
        _track("kick", "Kick", "kick", gain=0.95),
        _track("snare", "Snare", "snare", gain=0.80),
        _track("clap", "Clap", "clap", gain=0.62, pan=0.12),
        _track("hat", "Hat", "hat", gain=0.52, pan=-0.15),
        _track("openhat", "Open Hat", "openhat", gain=0.42, pan=0.18),
        _track("perc", "Perc", "perc", gain=0.50, pan=-0.30, reverb=0.20),
        _track("bass", "808 Bass", "sub808", gain=0.85),
        _track("lead", "Lead", "pluck", gain=0.55, reverb=0.28, delay=0.18),
        _track("chords", "Chords", "pad", gain=0.40, reverb=0.45),
    ]


def empty_pattern(name, tracks, steps=16):
    return {
        "name": name,
        "steps": steps,
        "grid": {t["id"]: [None] * steps for t in tracks},
    }


def default_project(name="Untitled"):
    tracks = default_tracks()
    return {
        "version": VERSION,
        "name": name,
        "bpm": 90,
        "swing": 0.0,
        "masterGain": 0.85,
        "key": "C",
        "scale": "minor",
        "duckSource": "kick",
        "duckRelease": 0.18,
        # Master bus. The defaults are what the engine used to hardcode; loosen
        # the compressor when a mix needs to keep its dynamics (sidechain pump,
        # quiet intros) instead of being levelled flat.
        "compThreshold": -14.0,
        "compRatio": 5.0,
        "compRelease": 0.18,
        "limiter": True,
        # Everything random in the audio engine draws from this seed, so the
        # same project always renders to the same audio.
        "seed": 1,
        "tracks": tracks,
        "patterns": [empty_pattern("A", tracks)],
        "current": 0,
        "song": [],
        "songMode": False,
        "playing": False,
        "rev": 0,
    }


# ---------------------------------------------------------------------------
# Project wrapper
# ---------------------------------------------------------------------------

class Project(object):
    """Thread-safe holder for the project dict, shared by MCP + web server."""

    def __init__(self, autosave_path=None):
        self.lock = threading.RLock()
        self.data = default_project()
        self.autosave_path = autosave_path
        self._listeners = []
        self._pending_cmds = []
        self._undo = []
        self._redo = []
        if autosave_path and os.path.exists(autosave_path):
            try:
                with open(autosave_path, "r") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict) and "patterns" in loaded:
                    self.data = migrate(loaded)
            except Exception:
                pass

    # -- listener plumbing (SSE clients) ------------------------------------
    def subscribe(self, client_id=None):
        entry = {"id": client_id, "queue": [], "event": threading.Event()}
        with self.lock:
            self._listeners.append(entry)
        return entry

    def unsubscribe(self, entry):
        with self.lock:
            if entry in self._listeners:
                self._listeners.remove(entry)

    def _broadcast(self, event, payload, exclude=None):
        for entry in list(self._listeners):
            if exclude is not None and entry["id"] == exclude:
                continue
            entry["queue"].append((event, payload))
            entry["event"].set()

    def push_command(self, cmd):
        """Send a one-shot instruction to any connected browser."""
        with self.lock:
            self._broadcast("cmd", cmd)

    # -- state access -------------------------------------------------------
    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def mutate(self, fn, origin=None):
        """Apply fn(data) under the lock, bump rev, notify listeners."""
        with self.lock:
            before = copy.deepcopy(self.data)
            result = fn(self.data)
            # Only remember mutations that actually changed something -- the UI
            # posts the whole project on every click, most of which are no-ops.
            if _sig(before) != _sig(self.data):
                self._undo.append(before)
                del self._undo[:-HISTORY_LIMIT]
                self._redo = []
            self.data["rev"] = self.data.get("rev", 0) + 1
            self.data["mtime"] = time.time()
            payload = copy.deepcopy(self.data)
            self._broadcast("state", payload, exclude=origin)
            self._autosave()
        return result

    # -- undo / redo --------------------------------------------------------
    def _restore(self, src, dst):
        """Pop one snapshot off `src`, pushing the current state onto `dst`."""
        with self.lock:
            if not src:
                return None
            dst.append(copy.deepcopy(self.data))
            del dst[:-HISTORY_LIMIT]
            rev = self.data.get("rev", 0)
            self.data = src.pop()
            self.data["rev"] = rev + 1          # revisions only ever go forward
            self.data["mtime"] = time.time()
            self._broadcast("state", copy.deepcopy(self.data))
            self._autosave()
            return self.data

    def undo(self, steps=1):
        n = 0
        for _ in range(max(1, int(steps))):
            if self._restore(self._undo, self._redo) is None:
                break
            n += 1
        return n

    def redo(self, steps=1):
        n = 0
        for _ in range(max(1, int(steps))):
            if self._restore(self._redo, self._undo) is None:
                break
            n += 1
        return n

    def history_depth(self):
        with self.lock:
            return len(self._undo), len(self._redo)

    def _autosave(self):
        if not self.autosave_path:
            return
        try:
            tmp = self.autosave_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(self.data, fh)
            os.replace(tmp, self.autosave_path)
        except Exception:
            pass

    # -- convenience lookups (call inside mutate/lock) ----------------------
    def track(self, data, tid):
        for t in data["tracks"]:
            if t["id"] == tid:
                return t
        raise KeyError(
            "no track %r (have: %s)" % (tid, ", ".join(t["id"] for t in data["tracks"]))
        )

    def pattern(self, data, index=None):
        if index is None:
            index = data.get("current", 0)
        if isinstance(index, str):
            for i, p in enumerate(data["patterns"]):
                if p["name"].lower() == index.lower():
                    return p
            raise KeyError("no pattern named %r" % index)
        if not 0 <= index < len(data["patterns"]):
            raise KeyError(
                "pattern index %s out of range (0..%d)" % (index, len(data["patterns"]) - 1)
            )
        return data["patterns"][index]


def migrate(data):
    """Fill in anything a loaded/older project is missing."""
    base = default_project()
    for key, val in base.items():
        data.setdefault(key, val)
    known = set()
    for t in data["tracks"]:
        t.setdefault("kind", engine_kind(t.get("engine", "kick")))
        t.setdefault("params", dict(ALL_ENGINES.get(t.get("engine", "kick"), {})))
        for key, val in ALL_ENGINES.get(t.get("engine", "kick"), {}).items():
            t["params"].setdefault(key, val)
        for key, val in (("gain", 0.8), ("pan", 0.0), ("mute", False),
                         ("solo", False), ("reverb", 0.0), ("delay", 0.0),
                         ("duck", 0.0)):
            t.setdefault(key, val)
        known.add(t["id"])
    for p in data["patterns"]:
        p.setdefault("steps", 16)
        grid = p.setdefault("grid", {})
        for tid in known:
            row = grid.get(tid) or []
            grid[tid] = (list(row) + [None] * p["steps"])[: p["steps"]]
        for tid in list(grid):
            if tid not in known:
                del grid[tid]
    data["current"] = max(0, min(int(data.get("current", 0)), len(data["patterns"]) - 1))
    return data


_VOLATILE = ("rev", "mtime", "playing", "current")


def _sig(data):
    """Content fingerprint: ignores transport and view state, so hitting play
    or switching pattern does not land on the undo stack."""
    return json.dumps({k: v for k, v in data.items() if k not in _VOLATILE},
                      sort_keys=True, separators=(",", ":"))


def slugify(name, taken):
    base = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "track"
    tid = base
    n = 2
    while tid in taken:
        tid = "%s%d" % (base, n)
        n += 1
    return tid
