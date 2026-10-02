"""Drum hits -> per-bar grid (the drummer's equivalent of tablature).

One row per kit piece used in the song, one column per 16th note. Cymbals are marked 'x',
drums 'o', the usual drum-tab convention:

    CC |x---------------|
    HH |--x-x-x-x-x-x-x-|
    SD |----o-------o---|
    BD |o-------o-o-----|
"""
from __future__ import annotations

import numpy as np

from .drums import CYMBALS, PIECES, SHORT
from .tab import quantize


def pieces_used(hits: list[dict]) -> list[str]:
    used = {h["piece"] for h in hits}
    return [p for p in PIECES if p in used]


def drum_bar_grids(hits: list[dict], beats: list[float], bars: list[float], duration: float,
                   slots_per_beat: int = 4, rows: list[str] | None = None,
                   ) -> tuple[list[str], list[list[list[str]]]]:
    """Returns (pieces in row order, per bar: rows of cells '', 'x' or 'o').
    rows: which pieces get a row (default: the ones played), e.g. all of them for editing."""
    rows = list(rows) if rows else pieces_used(hits)
    if len(beats) < 2 or not len(bars) or not rows:
        return rows, []
    beats_arr = np.array(beats, dtype=float)
    bar_beat = [int(np.argmin(np.abs(beats_arr - b))) for b in bars] + [len(beats_arr)]
    grids = [[[""] * (max(bar_beat[b + 1] - bar_beat[b], 1) * slots_per_beat) for _ in rows]
             for b in range(len(bars))]
    events = [{"time": h["time"], "piece": h["piece"], "k": k} for k, h in enumerate(hits)]
    for bar, slot, ev in quantize(events, beats, bars, duration, slots_per_beat):
        if 0 <= bar < len(grids) and 0 <= slot < len(grids[bar][0]):
            grids[bar][rows.index(ev["piece"])][slot] = "x" if ev["piece"] in CYMBALS else "o"
    return rows, grids


def hit_cells(hits: list[dict], beats: list[float], bars: list[float], duration: float,
              slots_per_beat: int = 4, rows: list[str] | None = None,
              ) -> dict[tuple[int, int, int], list[int]]:
    """{(bar, row, slot): [indices into hits]}: which grid mark each hit is drawn as, so the
    player can make that exact mark pulse when the hit sounds."""
    rows = list(rows) if rows else pieces_used(hits)
    events = [{"time": h["time"], "piece": h["piece"], "k": k} for k, h in enumerate(hits)]
    out: dict[tuple[int, int, int], list[int]] = {}
    for bar, slot, ev in quantize(events, beats, bars, duration, slots_per_beat):
        out.setdefault((bar, rows.index(ev["piece"]), slot), []).append(ev["k"])
    return out


def render_drum_tab(hits: list[dict], beats: list[float], bars: list[float], duration: float,
                    start_bar: int = 1, end_bar: int | None = None, bars_per_line: int = 4) -> str:
    rows, grids = drum_bar_grids(hits, beats, bars, duration)
    if not grids:
        return ""
    end_bar = min(end_bar or len(bars), len(bars))
    lines = []
    for i in range(start_bar - 1, end_bar, bars_per_line):
        chunk = list(range(i, min(i + bars_per_line, end_bar)))
        header = "   " + "".join(f"{b + 1:<{len(grids[b][0]) + 1}}" for b in chunk)
        lines.append(header.rstrip())
        for r, piece in enumerate(rows):
            cells = "".join("".join(c or "-" for c in grids[b][r]) + "|" for b in chunk)
            lines.append(f"{SHORT[piece]}|{cells}")
        lines.append("")
    return "\n".join(lines)
