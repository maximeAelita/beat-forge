"""Standard MIDI File writer.

BeatForge state is already MIDI-shaped -- notes are MIDI numbers, velocities are
0..1, `len` is a tie count -- so a project exports to a type 1 SMF with no
dependencies. Drum tracks land on channel 10 with General MIDI note numbers;
melodic tracks get a channel each and keep their own pitches.
"""

import struct

from .state import engine_kind

TPQN = 480                      # ticks per quarter note
TICKS_PER_STEP = TPQN // 4      # a step is a sixteenth

# Drum engine -> General MIDI percussion note.
GM_DRUMS = {
    "kick": 36, "kick808": 36, "snare": 38, "rimshot": 37, "clap": 39,
    "hat": 42, "openhat": 46, "ride": 51, "crash": 49, "tom": 45,
    "shaker": 70, "cowbell": 56, "perc": 64, "noise": 75,
}


def _vlq(n):
    """MIDI variable-length quantity."""
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def _chunk(tag, body):
    return tag + struct.pack(">I", len(body)) + body


def _track_chunk(events, name=None):
    """events: list of (abs_tick, order, status, data_bytes)."""
    body = bytearray()
    if name:
        text = name.encode("utf-8")[:127]
        body += _vlq(0) + b"\xff\x03" + _vlq(len(text)) + text
    last = 0
    for tick, _order, status, data in sorted(events, key=lambda e: (e[0], e[1])):
        body += _vlq(max(0, tick - last)) + bytes([status]) + data
        last = tick
    body += _vlq(0) + b"\xff\x2f\x00"
    return _chunk(b"MTrk", bytes(body))


def _sequence(data, song):
    """The (pattern, step) play order, matching the audio engine."""
    seq = []
    if song and data.get("song"):
        for entry in data["song"]:
            pat = data["patterns"][min(entry["pattern"], len(data["patterns"]) - 1)]
            for _ in range(entry.get("repeat", 1) or 1):
                for i in range(pat["steps"]):
                    seq.append((pat, i))
    else:
        pat = data["patterns"][min(data.get("current", 0), len(data["patterns"]) - 1)]
        for i in range(pat["steps"]):
            seq.append((pat, i))
    return seq


def _channels(tracks):
    """Drums share channel 9; melodic tracks take the rest, skipping it."""
    out, nxt = {}, 0
    for t in tracks:
        if engine_kind(t.get("engine", "kick")) == "drum":
            out[t["id"]] = 9
        else:
            if nxt == 9:
                nxt += 1
            out[t["id"]] = min(nxt, 15)
            nxt += 1
    return out


def write(data, path, song=False):
    """Write the project to `path` as a type 1 SMF. Returns (tracks, notes)."""
    seq = _sequence(data, song)
    if not seq:
        raise ValueError("nothing to export -- the pattern is empty")

    swing = data.get("swing") or 0
    chans = _channels(data["tracks"])

    # Track 0 carries tempo only, as is conventional for type 1.
    us_per_beat = int(round(60000000.0 / max(1.0, float(data.get("bpm", 120)))))
    tempo = struct.pack(">I", us_per_beat)[1:]
    head = [(0, 0, 0xFF, b"\x51\x03" + tempo)]
    chunks = [_track_chunk(head, data.get("name") or "BeatForge")]

    total_notes = 0
    for track in data["tracks"]:
        ch = chans[track["id"]]
        drum = engine_kind(track.get("engine", "kick")) == "drum"
        gm = GM_DRUMS.get(track.get("engine"), 64)
        events = []

        for pos, (pat, index) in enumerate(seq):
            step = (pat["grid"].get(track["id"]) or [None] * pat["steps"])[index]
            if not step:
                continue
            base = pos * TICKS_PER_STEP
            if index % 2 == 1:
                base += int(swing * TICKS_PER_STEP * 0.5)
            if step.get("nudge"):
                base += int(step["nudge"] * TICKS_PER_STEP)

            vel = max(1, min(127, int(round((step.get("v", 0.8)) * 127))))
            notes = step.get("notes") or ([step["note"]] if step.get("note") is not None else [gm])
            rolls = max(1, int(step.get("roll") or 1))
            span = (step.get("len", 1) if not drum else 1) * TICKS_PER_STEP

            for r in range(rolls):
                start = base + (r * TICKS_PER_STEP) // rolls
                dur = max(1, (span // rolls) - 2)   # small gap so notes retrigger
                rv = vel if rolls == 1 else max(1, int(vel * (0.55 + 0.45 * r / max(1, rolls - 1))))
                for n in notes:
                    n = max(0, min(127, int(n)))
                    events.append((start, 1, 0x90 | ch, bytes([n, rv])))
                    events.append((start + dur, 0, 0x80 | ch, bytes([n, 0])))
                    total_notes += 1

        if events:
            chunks.append(_track_chunk(events, track.get("name") or track["id"]))

    header = struct.pack(">HHH", 1, len(chunks), TPQN)
    with open(path, "wb") as fh:
        fh.write(_chunk(b"MThd", header))
        for c in chunks:
            fh.write(c)
    return len(chunks), total_notes
