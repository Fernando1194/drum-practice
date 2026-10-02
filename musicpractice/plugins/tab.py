"""Notes -> playable tablature.

The same pitch can be played in several places on the neck (E4 = open high E, 5th fret B,
9th fret G...). We choose with dynamic programming (Viterbi) over the whole song. Each
state is a fingering plus a hand position (the fret under the index finger), so playing
frets 5 then 8 in one position costs nothing, while sliding the hand up the neck does.
Costs:
- fret stretch inside a chord (more than 4 frets is very hard)
- hand movement between consecutive events (weighted more in fast passages)
- playing high up the neck when a lower position works

Before arranging, overtones that Basic Pitch reports as extra notes (an octave, octave+5th,
two octaves above a louder note) are removed, and unplayable chords lose their weakest notes.

The result is *a* playable tab, not necessarily the one the artist used.
Bends, slides, hammer-ons and palm mutes are not detected.
"""
from __future__ import annotations

import itertools
from typing import Any

import numpy as np

from ..core.models import Song

TUNINGS = {
    "guitar": {"pitches": [40, 45, 50, 55, 59, 64], "names": ["E", "A", "D", "G", "B", "e"],
               "max_fret": 22},
    "bass": {"pitches": [28, 33, 38, 43], "names": ["E", "A", "D", "G"], "max_fret": 20},
}
CHORD_WINDOW_S = 0.04
OVERTONE_INTERVALS = (12, 19, 24, 28, 31)
OVERTONE_RATIO = 0.75
HAND_SPAN = 3          # index finger at fret p covers p..p+3 without stretching
MAX_FINGERINGS = 10


# ---------- cleanup ----------

def group_events(notes: list[dict]) -> list[dict]:
    """Notes starting within 40 ms of each other are played together."""
    events: list[dict] = []
    for n in sorted(notes, key=lambda n: n["start"]):
        if events and n["start"] - events[-1]["time"] <= CHORD_WINDOW_S:
            if n["pitch"] not in [x["pitch"] for x in events[-1]["notes"]]:
                events[-1]["notes"].append(n)
        else:
            events.append({"time": n["start"], "notes": [n]})
    return events


def remove_overtones(event_notes: list[dict]) -> list[dict]:
    keep = []
    for n in event_notes:
        is_overtone = any(
            m["pitch"] == n["pitch"] - iv and n["velocity"] < OVERTONE_RATIO * m["velocity"]
            for m in event_notes for iv in OVERTONE_INTERVALS)
        if not is_overtone:
            keep.append(n)
    return keep or event_notes


def fit_pitch(pitch: int, tuning: list[int], max_fret: int) -> tuple[int, bool]:
    """Move notes outside the instrument's range by octaves (flagged as transposed)."""
    p, moved = pitch, False
    while p < tuning[0]:
        p, moved = p + 12, True
    while p > tuning[-1] + max_fret:
        p, moved = p - 12, True
    return p, moved


# ---------- fingering search ----------

def stretch_cost(fing) -> float:
    fretted = [f for _, f in fing if f > 0]
    if not fretted:
        return 0.0
    span = max(fretted) - min(fretted)
    return 0.0 if span <= HAND_SPAN else (span - HAND_SPAN) * 1.0 + (10.0 if span > 5 else 0.0)


def fingerings(pitches: list[int], tuning: list[int], max_fret: int) -> list[tuple]:
    per_note = []
    for p in pitches:
        opts = [(s, p - o) for s, o in enumerate(tuning) if 0 <= p - o <= max_fret]
        per_note.append(sorted(opts, key=lambda sf: sf[1])[:5])
    combos = [c for c in itertools.product(*per_note) if len({s for s, _ in c}) == len(c)]
    return sorted(combos, key=stretch_cost)[:MAX_FINGERINGS]


def playable_fingerings(notes: list[dict], tuning: list[int], max_fret: int):
    """Drop the weakest notes until the chord can actually be played."""
    notes = sorted(notes, key=lambda n: -n["velocity"])[: len(tuning)]
    while notes:
        fitted = sorted({fit_pitch(n["pitch"], tuning, max_fret)[0] for n in notes})
        fings = fingerings(fitted, tuning, max_fret)
        if fings and stretch_cost(fings[0]) < 5:
            return fings, any(fit_pitch(n["pitch"], tuning, max_fret)[1] for n in notes)
        notes = notes[:-1]
    return [()], False


def states_for(fings: list[tuple], max_fret: int) -> list[tuple[tuple, int]]:
    """(fingering, hand position) pairs. Open-string-only events can happen with the hand
    at any position, so they get one state per position and the hand stays put."""
    states = []
    for f in fings:
        fretted = [fr for _, fr in f if fr > 0]
        if not fretted:
            states.extend((f, pos) for pos in range(1, max_fret - HAND_SPAN + 1))
            continue
        lo, hi = min(fretted), max(fretted)
        # wide chords (span > 4 frets) still need a hand position: anchor at the lowest fret
        first = max(1, min(lo, hi - HAND_SPAN))
        for pos in range(first, lo + 1):
            states.append((f, pos))
    return states or [((), 1)]


def arrange(notes: list[dict], instrument: str) -> list[dict]:
    cfg = TUNINGS[instrument]
    tuning, max_fret = cfg["pitches"], cfg["max_fret"]
    events = group_events(notes)
    if not events:
        return []

    all_states, transposed = [], []
    for e in events:
        e["notes"] = remove_overtones(e["notes"])
        fings, moved = playable_fingerings(e["notes"], tuning, max_fret)
        all_states.append(states_for(fings, max_fret))
        transposed.append(moved)

    def local_cost(states):
        # open strings are natural in low positions, awkward when the hand is up the neck
        return np.array([stretch_cost(f)
                         + (0.03 * pos if any(fr for _, fr in f) else 0.0)
                         + 0.3 * sum(1 for _, fr in f if fr == 0) * (pos >= 5)
                         for f, pos in states])

    def positions(states):
        return np.array([pos for _, pos in states], dtype=float)

    cost = local_cost(all_states[0])
    back: list[np.ndarray] = []
    for i in range(1, len(events)):
        pa, pb = positions(all_states[i - 1]), positions(all_states[i])
        gap = events[i]["time"] - events[i - 1]["time"]
        w = 0.4 if gap < 0.5 else 0.15
        move = np.abs(pa[:, None] - pb[None, :])
        total = cost[:, None] + w * move
        back.append(total.argmin(axis=0))
        cost = total.min(axis=0) + local_cost(all_states[i])

    choice = [int(cost.argmin())]
    for bp in reversed(back):
        choice.append(int(bp[choice[-1]]))
    choice.reverse()

    return [{"time": e["time"],
             "positions": [list(sf) for sf in all_states[i][choice[i]][0]],
             "transposed": transposed[i]}
            for i, e in enumerate(events)]


# ---------- rendering ----------

def quantize(tab: list[dict], beats: list[float], bars: list[float], duration: float,
             slots_per_beat: int = 4) -> list[tuple[int, int, dict]]:
    """Snap each event to the nearest 16th note: returns (bar_index, slot_in_bar, event)."""
    beats_arr = np.array(beats, dtype=float)
    if len(beats_arr) < 2 or not len(bars):
        return []
    ibi = float(np.median(np.diff(beats_arr)))
    grid = np.concatenate([beats_arr, [beats_arr[-1] + ibi]])
    bar_beat = [int(np.argmin(np.abs(beats_arr - b))) for b in bars]
    out = []
    for ev in tab:
        j = int(np.clip(np.searchsorted(grid, ev["time"], side="right") - 1, 0, len(grid) - 2))
        frac = (ev["time"] - grid[j]) / max(grid[j + 1] - grid[j], 1e-3)
        q = j * slots_per_beat + int(round(frac * slots_per_beat))   # global 16th index
        beat_idx = q // slots_per_beat
        bar = int(np.searchsorted(bar_beat, beat_idx, side="right") - 1)
        if bar < 0:
            continue
        slot = q - bar_beat[bar] * slots_per_beat
        out.append((bar, slot, ev))
    return out


def tab_bar_grids(tab: list[dict], instrument: str, beats: list[float], bars: list[float],
                  duration: float, slots_per_beat: int = 4) -> list[list[list[str]]]:
    """Per bar: rows (highest string first) of slot cells; '' = nothing played."""
    n_str = len(TUNINGS[instrument]["names"])
    if len(beats) < 2 or not len(bars):
        return []
    beats_arr = np.array(beats, dtype=float)
    bar_beat = [int(np.argmin(np.abs(beats_arr - b))) for b in bars] + [len(beats_arr)]
    grids = []
    for b in range(len(bars)):
        n_slots = max(bar_beat[b + 1] - bar_beat[b], 1) * slots_per_beat
        grids.append([[""] * n_slots for _ in range(n_str)])
    for bar, slot, ev in quantize(tab, beats, bars, duration, slots_per_beat):
        if not 0 <= bar < len(grids) or not 0 <= slot < len(grids[bar][0]):
            continue
        for s, f in ev["positions"]:
            row = n_str - 1 - s
            if grids[bar][row][slot] == "":
                grids[bar][row][slot] = str(f)
    return grids


def render_tab(tab: list[dict], instrument: str, beats: list[float], bars: list[float],
               duration: float, chord_chart: list[list[str]] | None = None,
               start_bar: int = 1, end_bar: int | None = None,
               slots_per_beat: int = 4, bars_per_line: int = 4) -> str:
    """ASCII tab, one 16th note per column, chord names and bar numbers above each bar."""
    names = TUNINGS[instrument]["names"]
    n_str = len(names)
    grids = tab_bar_grids(tab, instrument, beats, bars, duration, slots_per_beat)
    if not grids:
        return ""
    end_bar = min(end_bar or len(bars), len(bars))

    blocks = []
    for b in range(start_bar - 1, end_bar):
        rows = ["".join((f"-{c}-" if len(c) == 1 else f"-{c}") if c else "---" for c in r) + "|"
                for r in grids[b]]
        chords = " ".join(chord_chart[b]) if chord_chart and b < len(chord_chart) else ""
        width = len(rows[0])
        blocks.append((f"{b + 1} {chords}"[: width - 1].ljust(width), rows))

    lines = []
    for i in range(0, len(blocks), bars_per_line):
        chunk = blocks[i:i + bars_per_line]
        lines.append("   " + "".join(h for h, _ in chunk).rstrip())
        for r in range(n_str):
            lines.append(f"{names[n_str - 1 - r]:<2}|" + "".join(rows[r] for _, rows in chunk))
        lines.append("")
    return "\n".join(lines)


class TabArranger:
    def __init__(self, instrument: str):
        if instrument not in TUNINGS:
            raise ValueError(f"No tab support for {instrument}")
        self.instrument = instrument
        self.name = f"tab:{instrument}"
        self.requires = (f"notes:{instrument}",)
        self.produces = (f"tab:{instrument}",)

    def available(self) -> bool:
        return True

    def run(self, song: Song, inputs: dict[str, Any]) -> dict[str, Any]:
        return {f"tab:{self.instrument}": arrange(inputs[f"notes:{self.instrument}"],
                                                   self.instrument)}
