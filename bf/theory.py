"""Scales, chords and progressions -- just enough theory to keep generated
basslines and melodies in key."""

from .state import note_to_midi, midi_to_note

SCALES = {
    "major":            [0, 2, 4, 5, 7, 9, 11],
    "minor":            [0, 2, 3, 5, 7, 8, 10],
    "natural_minor":    [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor":   [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor":    [0, 2, 3, 5, 7, 9, 11],
    "dorian":           [0, 2, 3, 5, 7, 9, 10],
    "phrygian":         [0, 1, 3, 5, 7, 8, 10],
    "phrygian_dominant": [0, 1, 4, 5, 7, 8, 10],
    "lydian":           [0, 2, 4, 6, 7, 9, 11],
    "mixolydian":       [0, 2, 4, 5, 7, 9, 10],
    "locrian":          [0, 1, 3, 5, 6, 8, 10],
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "major_pentatonic": [0, 2, 4, 7, 9],
    "blues":            [0, 3, 5, 6, 7, 10],
    "japanese":         [0, 1, 5, 7, 8],
    "hungarian_minor":  [0, 2, 3, 6, 7, 8, 11],
    "chromatic":        list(range(12)),
}

CHORD_SHAPES = {
    "maj":   [0, 4, 7],
    "min":   [0, 3, 7],
    "dim":   [0, 3, 6],
    "aug":   [0, 4, 8],
    "sus2":  [0, 2, 7],
    "sus4":  [0, 5, 7],
    "maj7":  [0, 4, 7, 11],
    "min7":  [0, 3, 7, 10],
    "dom7":  [0, 4, 7, 10],
    "min9":  [0, 3, 7, 10, 14],
    "maj9":  [0, 4, 7, 11, 14],
    "min11": [0, 3, 7, 10, 14, 17],
    "add9":  [0, 4, 7, 14],
    "m7b5":  [0, 3, 6, 10],
    "dim7":  [0, 3, 6, 9],
}

# Diatonic chord quality per scale degree.
TRIADS_MAJOR = ["maj7", "min7", "min7", "maj7", "dom7", "min7", "m7b5"]
TRIADS_MINOR = ["min7", "m7b5", "maj7", "min7", "min7", "maj7", "dom7"]

PROGRESSIONS = {
    "lofi":        [1, 4, 6, 5],
    "boom_bap":    [1, 6, 4, 5],
    "trap":        [1, 6, 7, 5],
    "drill":       [1, 7, 6, 5],
    "sad":         [6, 4, 1, 5],
    "epic":        [1, 5, 6, 4],
    "house":       [1, 5, 6, 4],
    "deep":        [2, 5, 1, 1],
    "jazz":        [2, 5, 1, 6],
    "dark":        [1, 1, 6, 7],
    "cyberpunk":   [1, 7, 1, 6],
    "andalusian":  [1, 7, 6, 5],
    "pop":         [1, 5, 6, 4],
    "minor_climb": [1, 3, 4, 6],
}

ROOTS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
         "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10,
         "Bb": 10, "B": 11}


def root_pc(key):
    """Pitch class 0-11 for a key name like 'C' or 'F#'."""
    key = str(key).strip()
    if key in ROOTS:
        return ROOTS[key]
    key = key[0].upper() + key[1:]
    if key in ROOTS:
        return ROOTS[key]
    raise ValueError("unknown key %r" % key)


def scale_notes(key, scale, low_midi, high_midi):
    """Every MIDI note of `key scale` within [low, high]."""
    if scale not in SCALES:
        raise ValueError(
            "unknown scale %r -- available: %s" % (scale, ", ".join(sorted(SCALES)))
        )
    pcs = set((root_pc(key) + iv) % 12 for iv in SCALES[scale])
    return [m for m in range(int(low_midi), int(high_midi) + 1) if m % 12 in pcs]


def degree_note(key, scale, degree, octave=2):
    """Scale degree (1-based, may exceed the scale length) -> MIDI note."""
    ivs = SCALES[scale]
    idx = int(degree) - 1
    oct_shift, within = divmod(idx, len(ivs))
    base = note_to_midi("%s%d" % (_spell(root_pc(key)), octave))
    return base + ivs[within] + 12 * oct_shift


def _spell(pc):
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return names[pc % 12]


def chord_for_degree(key, scale, degree, octave=3, voicing="triad"):
    """Diatonic chord on a scale degree, returned as MIDI notes."""
    ivs = SCALES.get(scale, SCALES["minor"])
    seventh = TRIADS_MAJOR if scale in ("major", "lydian", "mixolydian") else TRIADS_MINOR
    quality = seventh[(int(degree) - 1) % len(seventh)]
    if voicing == "triad":
        quality = {"maj7": "maj", "min7": "min", "dom7": "maj",
                   "m7b5": "dim"}.get(quality, quality)
    root = degree_note(key, scale, degree, octave)
    shape = CHORD_SHAPES.get(quality, CHORD_SHAPES["min"])
    if voicing == "spread":
        shape = [shape[0]] + [n + 12 if i % 2 else n for i, n in enumerate(shape[1:])]
    return [root + iv for iv in shape], quality


def progression_notes(key, scale, progression, octave=3, voicing="triad"):
    """['1','6','4','5'] or a named progression -> list of (notes, label)."""
    if isinstance(progression, str):
        if progression in PROGRESSIONS:
            degrees = PROGRESSIONS[progression]
        else:
            degrees = [int(d) for d in progression.replace("-", " ").split()]
    else:
        degrees = [int(d) for d in progression]
    out = []
    for d in degrees:
        notes, quality = chord_for_degree(key, scale, d, octave, voicing)
        out.append((notes, "%s%s" % (_roman(d, quality), "")))
    return out


def _roman(degree, quality):
    numerals = ["I", "II", "III", "IV", "V", "VI", "VII"]
    n = numerals[(int(degree) - 1) % 7]
    if quality.startswith("min") or quality.startswith("dim") or quality == "m7b5":
        n = n.lower()
    return n


def snap_to_scale(midi, key, scale):
    """Move a MIDI note to the nearest tone in the given scale."""
    pcs = sorted(set((root_pc(key) + iv) % 12 for iv in SCALES.get(scale, SCALES["minor"])))
    best, best_dist = midi, 99
    for delta in range(-6, 7):
        cand = midi + delta
        if cand % 12 in pcs and abs(delta) < best_dist:
            best, best_dist = cand, abs(delta)
    return best


def describe(key, scale):
    notes = [_spell(root_pc(key) + iv) for iv in SCALES.get(scale, SCALES["minor"])]
    return "%s %s: %s" % (key, scale, " ".join(notes))
