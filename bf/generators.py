"""Pattern generators: genre drum kits, basslines, melodies and chords.

Everything here is pure -- it takes parameters and returns step lists, so the
MCP tool layer can decide where to write them.
"""

import random

from . import theory
from .state import parse_steps, parse_notes, note_to_midi, midi_to_note

# ---------------------------------------------------------------------------
# Genre templates. Each role holds interchangeable one-bar (16 step) variants.
# `core` roles always play; `spice` roles come in as intensity rises.
# ---------------------------------------------------------------------------

GENRES = {
    "boom_bap": {
        "bpm": (86, 94), "swing": 0.16,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X..x..X...x.....", "X.....X..x..X...", "X..x....X.x.....", "X...x..X....x..x"],
            "snare":   ["....X.......X...", "....X..s....X..s", "....X.......X.s.", "....X..s....X..."],
            "hat":     ["x.x.x.x.x.x.x.x.", "x.xsx.x.xsx.x.x.", "xsx.x.xsx.x.xsx.", "x.x.x.x.x.x.xsxs"],
            "openhat": ["..............x.", "......x.......x.", "................"],
            "perc":    ["...s........s...", "..........s.....", "................"],
            "ride":    ["................"],
        },
    },
    "trap": {
        "bpm": (132, 148), "swing": 0.0,
        "core": ["kick", "clap", "hat"],
        "roles": {
            "kick":    ["X.....x..X...x..", "X....x..X.....x.", "X..x....X..x...x", "X......x.X..x..."],
            "snare":   ["........X.......", "........X.....s."],
            "clap":    ["........X.......", "........X......x"],
            "hat":     ["xxxxxxxxxxxxRRRR", "xxxxxxrxxxxxxxRR", "x.xxx.xxx.xxx.RR", "xxxxRRxxxxxxrrxx"],
            "openhat": ["......x.......x.", "..............x.", "................"],
            "perc":    ["..x.......x.....", "................"],
        },
    },
    "drill": {
        "bpm": (140, 146), "swing": 0.08,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X.......x...X...", "X....x......X...", "X.......x..X..x.", "X..x........X..."],
            "snare":   ["........X.......", "........X....s..", "....s...X......."],
            "clap":    ["........X.......", "................"],
            "hat":     ["x.xx.xx.x.xx.xx.", "x.xx.xrx.xx.xRR.", "xx.xx.xx.xx.xRRR"],
            "openhat": ["..........x.....", "................"],
            "perc":    ["..............x.", "................"],
        },
    },
    "house": {
        "bpm": (122, 128), "swing": 0.04,
        "core": ["kick", "clap", "hat"],
        "roles": {
            "kick":    ["X...X...X...X...", "X...X...X...X..x"],
            "snare":   ["................"],
            "clap":    ["....X.......X...", "....X.......X..o"],
            "hat":     ["..x...x...x...x.", "..x...x...x...xx", "x.x.x.x.x.x.x.x."],
            "openhat": ["..x...x...x...x.", "................"],
            "perc":    ["...x..x...x..x..", "..o...o...o...o.", "................"],
            "ride":    ["................"],
        },
    },
    "deep_house": {
        "bpm": (118, 124), "swing": 0.12,
        "core": ["kick", "clap", "hat"],
        "roles": {
            "kick":    ["X...X...X...X...", "X...X...X...X.x."],
            "clap":    ["....o.......o...", "....X.......o..."],
            "hat":     ["..o...o...o...o.", "..o.s.o...o.s.o."],
            "openhat": ["..x...x...x...x.", "................"],
            "perc":    ["...s..s...s..s..", "................"],
        },
    },
    "techno": {
        "bpm": (128, 140), "swing": 0.0,
        "core": ["kick", "hat", "perc"],
        "roles": {
            "kick":    ["X...X...X...X...", "X...X...X...X..x", "X...X...X..xX..."],
            "clap":    ["....X.......X...", "............X..."],
            "hat":     ["..x...x...x...x.", "x.x.x.x.x.x.x.x.", "xxxxxxxxxxxxxxxx"],
            "openhat": ["..x...x...x...x.", "................"],
            "perc":    ["..x..x..x..x..x.", "...x...x...x...x", "..s.s..s..s.s..s"],
            "ride":    ["................"],
        },
    },
    "dnb": {
        "bpm": (172, 176), "swing": 0.0,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X......x..X.....", "X.........X...x.", "X....x....X....."],
            "snare":   ["....X.......X...", "....X..s..s.X...", "....X.....s.X..s"],
            "hat":     ["x.x.x.x.x.x.x.x.", "xsx.xsx.xsx.xsx.", "x.xxx.xxx.xxx.xx"],
            "openhat": ["......x.........", "................"],
            "perc":    ["..........s.....", "................"],
            "ride":    ["x.x.x.x.x.x.x.x.", "................"],
        },
    },
    "jungle": {
        "bpm": (160, 172), "swing": 0.10,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X.....x...X...x.", "X..x......X.....", "X.......x.X..x.."],
            "snare":   ["....X..s.s..X.s.", "....X.s...s.X..s", "..s.X.s.x...X.s."],
            "hat":     ["x.xsx.xsx.xsx.xs", "x.x.xsx.x.x.xsx."],
            "openhat": ["......x.......x.", "................"],
        },
    },
    "lofi": {
        "bpm": (70, 84), "swing": 0.22,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X.......x..X....", "X.....x....X....", "X..x.......X...."],
            "snare":   ["....o.......o...", "....o.......o.s.", "....o..s....o..."],
            "hat":     ["o.o.o.o.o.o.o.o.", "o.oso.o.o.oso.o.", "o...o...o...o..s"],
            "openhat": ["................", "..............s."],
            "perc":    ["..........s.....", "................"],
        },
    },
    "reggaeton": {
        "bpm": (88, 100), "swing": 0.06,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X...X...X...X...", "X...X...X..xX..."],
            "snare":   ["...x..x....x..x.", "...x..x.x..x..x.", "...X..o....X..o."],
            "clap":    ["...x..x....x..x.", "................"],
            "hat":     ["x.x.x.x.x.x.x.x.", "..x...x...x...x."],
            "perc":    ["..s...s...s...s.", "................"],
        },
    },
    "afrobeat": {
        "bpm": (102, 114), "swing": 0.14,
        "core": ["kick", "perc", "hat"],
        "roles": {
            "kick":    ["X.....x.X...x...", "X...x...X.x.....", "X.....x.X..x...."],
            "snare":   ["....o.......o...", "................"],
            "clap":    ["............X...", "................"],
            "hat":     ["x.xx.x.xx.xx.x.x", "..x.x..x..x.x..x"],
            "perc":    ["..x..x..x..x.x..", "x..x..x..x..x..x", "..s.x..s.x..s.x."],
            "openhat": ["......x.........", "................"],
        },
    },
    "amapiano": {
        "bpm": (110, 116), "swing": 0.18,
        "core": ["kick", "perc", "hat"],
        "roles": {
            "kick":    ["X...X...X...X...", "X...X...X..xX..."],
            "snare":   ["................", "............o..."],
            "clap":    ["............X...", "....o.......X..."],
            "hat":     ["..o...o...o...o.", "..o.s.o.s.o...o."],
            "perc":    ["..x.x..x.x..x.x.", "x..x..x...x..x..", "..s..s.x..s..s.x"],
            "openhat": ["......x.......x.", "................"],
        },
    },
    "uk_garage": {
        "bpm": (130, 138), "swing": 0.30,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X.....x.....X...", "X.......x...X...", "X.....x...X....."],
            "snare":   ["....X.......X...", "....X.....s.X..."],
            "clap":    ["....X.......X...", "................"],
            "hat":     ["x.xsx.xsx.xsx.xs", "..x...x...x...x.", "x.x.xsx.x.x.xsx."],
            "openhat": ["......x.......x.", "................"],
            "perc":    ["..........s.....", "................"],
        },
    },
    "breakbeat": {
        "bpm": (128, 140), "swing": 0.08,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X.....x...X.....", "X..x......X...x.", "X.......x.X....."],
            "snare":   ["....X..s....X...", "....X.....s.X.s.", "....X...s...X..."],
            "hat":     ["x.x.x.x.x.x.x.x.", "xsx.x.xsx.x.xsx."],
            "openhat": ["......x.......x.", "................"],
        },
    },
    "funk": {
        "bpm": (98, 114), "swing": 0.12,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X..x..x.X..x....", "X.x...x.X...x..x", "X..x....X.x.x..."],
            "snare":   ["....X..s....X.s.", "....X.s.s...X..s", "..s.X...s.s.X..."],
            "hat":     ["xsxsxsxsxsxsxsxs", "x.xsx.xsx.xsx.xs"],
            "openhat": ["......x.........", "................"],
            "perc":    ["...s....s...s...", "................"],
        },
    },
    "disco": {
        "bpm": (116, 126), "swing": 0.06,
        "core": ["kick", "snare", "openhat"],
        "roles": {
            "kick":    ["X...X...X...X...", "X...X...X..xX..."],
            "snare":   ["....X.......X...", "....X.......X..o"],
            "clap":    ["....o.......o...", "................"],
            "hat":     ["x.x.x.x.x.x.x.x.", "xxxxxxxxxxxxxxxx"],
            "openhat": ["..x...x...x...x.", "..x...x...x...xx"],
            "perc":    ["..s..s..s..s..s.", "................"],
        },
    },
    "rock": {
        "bpm": (110, 150), "swing": 0.0,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X...x...X.x.....", "X.......X...x...", "X..x....X..x...."],
            "snare":   ["....X.......X...", "....X.......X..o"],
            "hat":     ["x.x.x.x.x.x.x.x.", "xxxxxxxxxxxxxxxx", "x...x...x...x..."],
            "crash":   ["X...............", "................"],
            "ride":    ["................"],
        },
    },
    "phonk": {
        "bpm": (130, 160), "swing": 0.05,
        "core": ["kick", "snare", "hat", "cowbell"],
        "roles": {
            "kick":    ["X.....x..X...x..", "X....x..X.....x."],
            "snare":   ["........X.......", "....X.......X..."],
            "clap":    ["........X.......", "................"],
            "hat":     ["x.x.x.x.x.x.xRRR", "xxxxxxxxxxxxrrxx"],
            "cowbell": ["x..x..x...x..x..", "x...x...x.x.x..."],
            "perc":    ["................"],
        },
    },
    "dubstep": {
        "bpm": (138, 144), "swing": 0.0,
        "core": ["kick", "snare", "hat"],
        "roles": {
            "kick":    ["X.......x.......", "X..........x....", "X.....x........."],
            "snare":   ["........X.......", "........X.....X."],
            "hat":     ["..x...x...x...x.", "..x.x.x...x.x.x."],
            "openhat": ["..............x.", "................"],
            "perc":    ["............s...", "................"],
        },
    },
    "ambient": {
        "bpm": (60, 80), "swing": 0.0,
        "core": ["perc"],
        "roles": {
            "kick":    ["X...............", "X.......o.......", "................"],
            "snare":   ["................", "........s......."],
            "hat":     ["..s...s...s...s.", "................"],
            "perc":    ["....s.......s...", "..s.....s......s"],
            "ride":    ["................"],
        },
    },
    "industrial": {
        "bpm": (124, 136), "swing": 0.0,
        "core": ["kick", "snare", "perc"],
        "roles": {
            "kick":    ["X...X...X...X...", "X..xX...X.x.X..."],
            "snare":   ["....X.......X...", "....X...x...X..x"],
            "hat":     ["xxxxxxxxxxxxxxxx", "..x...x...x...x."],
            "perc":    ["..x..x..x..x..x.", "x..x..x..x..x..x"],
            "crash":   ["X...............", "................"],
        },
    },
}

ROLE_ALIASES = {
    "clap": ["clap", "snare"],
    "snare": ["snare", "clap", "rimshot"],
    "openhat": ["openhat", "hat"],
    "cowbell": ["cowbell", "perc"],
    "ride": ["ride", "hat"],
    "crash": ["crash", "ride"],
}


def list_genres():
    return sorted(GENRES)


def generate_drums(genre, bars=1, steps_per_bar=16, intensity=0.5, seed=None,
                   fill=True, humanize=0.0):
    """Return {role: [steps]} covering `bars` bars, plus suggested tempo."""
    if genre not in GENRES:
        raise ValueError(
            "unknown genre %r -- available: %s" % (genre, ", ".join(list_genres()))
        )
    spec = GENRES[genre]
    rng = random.Random(seed)
    intensity = max(0.0, min(1.0, float(intensity)))
    total = bars * steps_per_bar

    out = {}
    for role, variants in spec["roles"].items():
        is_core = role in spec["core"]
        # Below ~0.3 intensity only core roles survive; spice fades in above.
        if not is_core and intensity < 0.28:
            continue
        if not is_core and rng.random() > 0.35 + intensity * 0.8:
            continue

        row = []
        prev = None
        for bar in range(bars):
            # Bar 0 always uses the most characteristic variant; later bars vary.
            if bar == 0:
                choice = variants[0]
            elif rng.random() < 0.25 + intensity * 0.45:
                pool = [v for v in variants if v != prev] or variants
                choice = rng.choice(pool)
            else:
                choice = prev if prev is not None else variants[0]
            prev = choice
            row.extend(parse_steps(choice, steps_per_bar))

        row = _apply_intensity(row, role, intensity, rng, steps_per_bar)
        if humanize:
            row = humanize_row(row, humanize, rng)
        if any(s is not None for s in row):
            out[role] = row[:total]

    if fill and bars >= 2:
        _add_fill(out, bars, steps_per_bar, rng, intensity)

    lo, hi = spec["bpm"]
    return {
        "roles": out,
        "bpm": rng.randint(lo, hi),
        "swing": spec["swing"],
        "bars": bars,
        "steps_per_bar": steps_per_bar,
    }


def _apply_intensity(row, role, intensity, rng, spb):
    """Thin out at low intensity, add ghosts and rolls at high intensity."""
    row = [None if s is None else dict(s) for s in row]
    if intensity < 0.5:
        drop = (0.5 - intensity) * 0.9
        for i, s in enumerate(row):
            if s is None:
                continue
            downbeat = (i % (spb // 4)) == 0
            if not downbeat and s.get("v", 0.8) < 0.7 and rng.random() < drop:
                row[i] = None
    else:
        add = (intensity - 0.5) * 0.9
        for i, s in enumerate(row):
            if s is not None:
                continue
            if role in ("hat", "perc", "shaker") and rng.random() < add * 0.35:
                row[i] = {"v": round(0.22 + rng.random() * 0.16, 3)}
            elif role == "snare" and rng.random() < add * 0.10:
                row[i] = {"v": round(0.20 + rng.random() * 0.12, 3)}
        if role == "hat":
            for i, s in enumerate(row):
                if s is not None and rng.random() < add * 0.16 and i % (spb // 4) != 0:
                    s["roll"] = rng.choice([2, 2, 3, 4])
    return row


def _add_fill(roles, bars, spb, rng, intensity):
    """Overwrite the tail of the last bar with a simple fill."""
    total = bars * spb
    start = total - max(4, spb // 2)
    style = rng.choice(["snare_roll", "tom_run", "hat_stop", "reverse"])

    if style == "hat_stop":
        for role in ("hat", "openhat"):
            if role in roles:
                for i in range(start, total):
                    roles[role][i] = None
        return

    target = "snare" if "snare" in roles else ("clap" if "clap" in roles else None)
    if style == "tom_run":
        target = "tom"
        roles.setdefault("tom", [None] * total)
    if target is None:
        return
    roles.setdefault(target, [None] * total)
    row = roles[target]
    n = total - start
    for k, i in enumerate(range(start, total)):
        if style == "reverse":
            v = 0.30 + 0.65 * (1.0 - k / float(max(1, n - 1)))
        else:
            v = 0.35 + 0.60 * (k / float(max(1, n - 1)))
        step = {"v": round(v, 3)}
        if style == "tom_run":
            step["note"] = 48 - k * 2
        if rng.random() < intensity * 0.4:
            step["roll"] = 2
        row[i] = step
    # keep the kick out of the way of the fill
    if "kick" in roles:
        for i in range(start + 1, total):
            if rng.random() < 0.7:
                roles["kick"][i] = None


def humanize_row(row, amount, rng=None):
    """Scatter velocities (and micro-timing hints) by `amount` (0..1)."""
    rng = rng or random.Random()
    out = []
    for s in row:
        if s is None:
            out.append(None)
            continue
        s = dict(s)
        v = s.get("v", 0.8) * (1.0 + (rng.random() - 0.5) * 0.5 * amount)
        s["v"] = round(max(0.05, min(1.0, v)), 3)
        nudge = round((rng.random() - 0.5) * 0.5 * amount, 3)
        if abs(nudge) > 0.01:
            s["nudge"] = nudge  # in steps, applied by the audio scheduler
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Bass
# ---------------------------------------------------------------------------

BASS_STYLES = ("follow_kick", "root_hold", "offbeat", "octave", "walking",
               "arp", "rolling", "slide", "stab", "acid")


def generate_bassline(key="C", scale="minor", style="follow_kick", bars=1,
                      steps_per_bar=16, octave=1, kick_row=None,
                      progression=None, seed=None, density=0.5):
    """Return a list of melodic steps for a bass track."""
    rng = random.Random(seed)
    total = bars * steps_per_bar
    root = theory.degree_note(key, scale, 1, octave)
    pool = theory.scale_notes(key, scale, root - 2, root + 14)

    # A chord per bar drives which root the bass lands on.
    if progression:
        chords = theory.progression_notes(key, scale, progression, octave)
        roots = [c[0][0] for c in chords]
    else:
        roots = [root]
    bar_root = lambda b: roots[b % len(roots)]

    row = [None] * total

    if style == "follow_kick":
        anchors = []
        if kick_row:
            anchors = [i for i, s in enumerate(kick_row[:total]) if s is not None]
        if not anchors:
            anchors = [i for i in range(0, total, steps_per_bar // 2)]
        for i in anchors:
            b = i // steps_per_bar
            n = bar_root(b)
            if i % steps_per_bar != 0 and rng.random() < 0.35:
                n = _nearest(pool, n + rng.choice([3, 5, 7, -5]))
            row[i] = {"v": 0.9 if i % steps_per_bar == 0 else 0.75,
                      "note": n, "len": 2}
        _extend_lengths(row, total)

    elif style == "root_hold":
        for b in range(bars):
            i = b * steps_per_bar
            row[i] = {"v": 0.9, "note": bar_root(b), "len": steps_per_bar}

    elif style == "offbeat":
        for i in range(steps_per_bar // 4, total, steps_per_bar // 4):
            if (i // (steps_per_bar // 4)) % 2 == 1:
                b = i // steps_per_bar
                row[i] = {"v": 0.8, "note": bar_root(b), "len": 1}

    elif style == "octave":
        step = max(1, steps_per_bar // 8)
        for k, i in enumerate(range(0, total, step)):
            b = i // steps_per_bar
            n = bar_root(b) + (12 if k % 2 else 0)
            row[i] = {"v": 0.85 if k % 2 == 0 else 0.65, "note": n, "len": 1}

    elif style == "walking":
        step = max(1, steps_per_bar // 4)
        cur = 0
        for i in range(0, total, step):
            b = i // steps_per_bar
            base = bar_root(b)
            n = _nearest(pool, base + [0, 2, 3, 5, 7][cur % 5])
            row[i] = {"v": 0.8, "note": n, "len": step}
            cur += 1

    elif style == "arp":
        shape = [0, 7, 12, 7, 3, 7, 12, 15]
        step = max(1, steps_per_bar // 8)
        for k, i in enumerate(range(0, total, step)):
            b = i // steps_per_bar
            row[i] = {"v": 0.75 if k % 2 else 0.88,
                      "note": _nearest(pool, bar_root(b) + shape[k % len(shape)]),
                      "len": 1}

    elif style == "rolling":
        for i in range(total):
            if rng.random() < 0.25 + density * 0.5 or i % (steps_per_bar // 4) == 0:
                b = i // steps_per_bar
                n = bar_root(b)
                if rng.random() < 0.3:
                    n = _nearest(pool, n + rng.choice([3, 5, 7, 10, 12]))
                row[i] = {"v": round(0.55 + rng.random() * 0.35, 3), "note": n, "len": 1}

    elif style == "slide":
        for b in range(bars):
            i = b * steps_per_bar
            row[i] = {"v": 0.95, "note": bar_root(b), "len": steps_per_bar // 2}
            j = i + steps_per_bar // 2 + rng.choice([0, 2])
            if j < total:
                target = _nearest(pool, bar_root(b) + rng.choice([-2, 3, 5, 7, -5]))
                row[j] = {"v": 0.85, "note": target, "len": steps_per_bar // 2,
                          "slide": True}

    elif style == "stab":
        hits = [0, steps_per_bar // 2, steps_per_bar - 2]
        for b in range(bars):
            for h in hits:
                i = b * steps_per_bar + h
                if i < total and rng.random() < 0.45 + density * 0.5:
                    row[i] = {"v": 0.85, "note": bar_root(b), "len": 1}

    elif style == "acid":
        for i in range(total):
            if rng.random() < 0.35 + density * 0.45:
                b = i // steps_per_bar
                n = _nearest(pool, bar_root(b) + rng.choice([0, 0, 0, 12, 3, 7, -12, 10]))
                s = {"v": round(0.5 + rng.random() * 0.5, 3), "note": n, "len": 1}
                if rng.random() < 0.35:
                    s["slide"] = True
                row[i] = s
    else:
        raise ValueError("unknown bass style %r -- try: %s"
                         % (style, ", ".join(BASS_STYLES)))

    return row


def _extend_lengths(row, total):
    """Stretch each note until the next one starts (legato)."""
    idxs = [i for i, s in enumerate(row) if s is not None]
    for k, i in enumerate(idxs):
        end = idxs[k + 1] if k + 1 < len(idxs) else total
        row[i]["len"] = max(1, min(end - i, 16))


def _nearest(pool, midi):
    if not pool:
        return midi
    return min(pool, key=lambda n: abs(n - midi))


# ---------------------------------------------------------------------------
# Melody
# ---------------------------------------------------------------------------

MELODY_STYLES = ("motif", "arp_up", "arp_down", "arp_updown", "call_response",
                 "riff", "pad_swell", "random_walk", "stabs")


def generate_melody(key="C", scale="minor", style="motif", bars=2,
                    steps_per_bar=16, octave=4, seed=None, density=0.45,
                    progression=None):
    rng = random.Random(seed)
    total = bars * steps_per_bar
    low = theory.degree_note(key, scale, 1, octave)
    pool = theory.scale_notes(key, scale, low - 5, low + 19)
    row = [None] * total

    if style in ("arp_up", "arp_down", "arp_updown"):
        chords = (theory.progression_notes(key, scale, progression, octave)
                  if progression else None)
        step = max(1, steps_per_bar // 8)
        seq_idx = 0
        for i in range(0, total, step):
            b = i // steps_per_bar
            notes = chords[b % len(chords)][0] if chords else [
                _nearest(pool, low), _nearest(pool, low + 3), _nearest(pool, low + 7),
                _nearest(pool, low + 12)]
            if style == "arp_up":
                n = notes[seq_idx % len(notes)]
            elif style == "arp_down":
                n = notes[-1 - (seq_idx % len(notes))]
            else:
                cycle = notes + notes[-2:0:-1]
                n = cycle[seq_idx % len(cycle)]
            row[i] = {"v": round(0.55 + rng.random() * 0.3, 3), "note": n, "len": step}
            seq_idx += 1

    elif style == "motif":
        # Build one bar, then repeat it with a small variation.
        motif = _make_motif(pool, low, steps_per_bar, rng, density)
        for b in range(bars):
            for i, s in enumerate(motif):
                if s is None:
                    continue
                s = dict(s)
                if b > 0 and rng.random() < 0.30:
                    s["note"] = _nearest(pool, s["note"] + rng.choice([-2, 2, 3, -3]))
                if b > 0 and rng.random() < 0.15:
                    continue
                row[b * steps_per_bar + i] = s

    elif style == "call_response":
        call = _make_motif(pool, low, steps_per_bar, rng, density)
        resp = _make_motif(pool, low + 5, steps_per_bar, rng, density * 0.8)
        for b in range(bars):
            src = call if b % 2 == 0 else resp
            for i, s in enumerate(src):
                if s is not None:
                    row[b * steps_per_bar + i] = dict(s)

    elif style == "riff":
        shape = [0, 0, 3, 0, 5, 3, 0, -2]
        step = max(1, steps_per_bar // 8)
        for k, i in enumerate(range(0, total, step)):
            if rng.random() > 0.15:
                row[i] = {"v": round(0.6 + rng.random() * 0.35, 3),
                          "note": _nearest(pool, low + shape[k % len(shape)]),
                          "len": step}

    elif style == "pad_swell":
        chords = theory.progression_notes(key, scale, progression or "lofi", octave - 1)
        for b in range(bars):
            notes, _ = chords[b % len(chords)]
            row[b * steps_per_bar] = {"v": 0.6, "notes": notes, "len": steps_per_bar}

    elif style == "random_walk":
        cur = _nearest(pool, low + 7)
        for i in range(total):
            if rng.random() < density:
                cur = _nearest(pool, cur + rng.choice([-3, -2, -1, 1, 2, 3, 5, -5]))
                cur = max(pool[0], min(pool[-1], cur))
                row[i] = {"v": round(0.45 + rng.random() * 0.45, 3), "note": cur, "len": 1}

    elif style == "stabs":
        chords = theory.progression_notes(key, scale, progression or "trap", octave - 1)
        for b in range(bars):
            notes, _ = chords[b % len(chords)]
            for h in (0, steps_per_bar // 2, steps_per_bar - 4):
                if rng.random() < 0.4 + density * 0.6:
                    row[b * steps_per_bar + h] = {"v": 0.7, "notes": notes, "len": 2}
    else:
        raise ValueError("unknown melody style %r -- try: %s"
                         % (style, ", ".join(MELODY_STYLES)))
    return row


def _make_motif(pool, center, length, rng, density):
    row = [None] * length
    cur = _nearest(pool, center)
    positions = [0] + sorted(rng.sample(range(1, length),
                                        max(1, int(length * density))))
    for i in positions:
        row[i] = {"v": round(0.55 + rng.random() * 0.35, 3), "note": cur,
                  "len": max(1, length // 8)}
        cur = _nearest(pool, cur + rng.choice([-5, -3, -2, 2, 3, 4, 5, 7]))
        cur = max(pool[0], min(pool[-1], cur))
    return row


def generate_chords(key="C", scale="minor", progression="lofi", bars=4,
                    steps_per_bar=16, octave=3, voicing="triad", rhythm="hold",
                    seed=None):
    rng = random.Random(seed)
    chords = theory.progression_notes(key, scale, progression, octave, voicing)
    total = bars * steps_per_bar
    row = [None] * total
    labels = []
    for b in range(bars):
        notes, label = chords[b % len(chords)]
        labels.append(label)
        base = b * steps_per_bar
        if rhythm == "hold":
            row[base] = {"v": 0.65, "notes": notes, "len": steps_per_bar}
        elif rhythm == "stab":
            for h in (0, steps_per_bar // 2):
                row[base + h] = {"v": 0.7, "notes": notes, "len": 2}
        elif rhythm == "offbeat":
            for h in range(steps_per_bar // 4, steps_per_bar, steps_per_bar // 4):
                if (h // (steps_per_bar // 4)) % 2 == 1:
                    row[base + h] = {"v": 0.6, "notes": notes, "len": 2}
        elif rhythm == "pulse":
            for h in range(0, steps_per_bar, steps_per_bar // 4):
                row[base + h] = {"v": 0.55 if h else 0.7, "notes": notes,
                                 "len": steps_per_bar // 4}
        elif rhythm == "arp":
            step = max(1, steps_per_bar // 8)
            for k, h in enumerate(range(0, steps_per_bar, step)):
                row[base + h] = {"v": round(0.5 + rng.random() * 0.3, 3),
                                 "note": notes[k % len(notes)], "len": step}
        else:
            raise ValueError("unknown chord rhythm %r "
                             "(hold, stab, offbeat, pulse, arp)" % rhythm)
    return row, labels
