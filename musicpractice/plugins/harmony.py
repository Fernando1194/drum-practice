"""Key and chord recognition from chroma.

Method (classic, no deep learning):
- Key: correlate the song's average chroma with Krumhansl-Kessler key profiles.
- Chords: beat-synchronous chroma matched against 24 major/minor triad templates,
  smoothed with Viterbi so chords don't flicker every beat.

Expect good results on pop/rock/folk harmony. Sevenths, sus and extended chords are
reported as their underlying triad; jazz harmony will be simplified a lot.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ..core.models import Song
from . import features as F

KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
FLAT_KEYS = {"F major", "Bb major", "Eb major", "Ab major", "Db major", "Gb major",
             "D minor", "G minor", "C minor", "F minor", "Bb minor", "Eb minor"}


def estimate_key(mean_chroma: np.ndarray) -> tuple[str, float, str]:
    scores = []
    for pc in range(12):
        for mode, prof in (("major", KK_MAJOR), ("minor", KK_MINOR)):
            r = np.corrcoef(mean_chroma, np.roll(prof, pc))[0, 1]
            scores.append((r, pc, mode))
    scores.sort(reverse=True)
    (r1, pc1, m1), (r2, pc2, m2) = scores[0], scores[1]
    name = f"{F.pc_name(pc1, flats=False)} {m1}"
    flats = name in FLAT_KEYS or f"{F.pc_name(pc1, True)} {m1}" in FLAT_KEYS
    name = f"{F.pc_name(pc1, flats)} {m1}"
    runner_up = f"{F.pc_name(pc2, flats)} {m2}"
    return name, round(float(r1 - r2), 3), runner_up


def chord_templates() -> tuple[np.ndarray, list[tuple[int, str]]]:
    temps, labels = [], []
    for pc in range(12):
        for quality, intervals in (("", (0, 4, 7)), ("m", (0, 3, 7))):
            t = np.zeros(12)
            t[[(pc + i) % 12 for i in intervals]] = [1.0, 0.8, 0.9]
            temps.append(t / np.linalg.norm(t))
            labels.append((pc, quality))
    return np.array(temps), labels


def viterbi(scores: np.ndarray, switch_penalty: float) -> np.ndarray:
    """scores: (n_states, n_steps), higher is better. Returns best state per step."""
    n_states, n = scores.shape
    acc = scores[:, 0].copy()
    back = np.zeros((n_states, n), dtype=int)
    for t in range(1, n):
        stay = acc
        best_prev = int(acc.argmax())
        switch = acc[best_prev] - switch_penalty
        choose_switch = switch > stay
        back[:, t] = np.where(choose_switch, best_prev, np.arange(n_states))
        acc = np.maximum(stay, switch) + scores[:, t]
    path = np.zeros(n, dtype=int)
    path[-1] = int(acc.argmax())
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[path[t], t]
    return path


class HarmonyAnalyzer:
    name = "harmony"
    requires = ("beats", "bars", "duration")
    produces = ("key", "chords", "chord_chart")

    def __init__(self, switch_penalty: float = 0.08, silence_ratio: float = 0.15):
        self.switch_penalty = switch_penalty
        self.silence_ratio = silence_ratio

    def available(self) -> bool:
        return True

    def run(self, song: Song, inputs: dict[str, Any]) -> dict[str, Any]:
        y = F.harmonic_audio(song)
        C = F.chroma(y)
        key, conf, runner_up = estimate_key(C.mean(axis=1))
        flats = key in FLAT_KEYS

        beats, duration = inputs["beats"], inputs["duration"]
        bounds = [0.0] + [b for b in beats if b > 0] + [duration]
        B = F.segment_means(C, bounds)                                # (12, n_beats)
        energy = np.linalg.norm(B, axis=0)
        Bn = B / np.maximum(energy, 1e-9)

        T, labels = chord_templates()
        scores = T @ Bn                                               # (24, n_beats)
        no_chord = np.where(energy < self.silence_ratio * np.median(energy), 1.0, 0.0)
        scores = np.vstack([scores, 0.55 + 0.5 * no_chord])           # state 24 = "N"
        scores[:24, no_chord > 0] -= 1.0
        path = viterbi(scores, self.switch_penalty)

        def label(s: int) -> str:
            if s == 24:
                return "N"
            pc, q = labels[s]
            return F.pc_name(pc, flats) + q

        chords: list[dict[str, Any]] = []
        for i, s in enumerate(path):
            lab = label(int(s))
            start, end = bounds[i], bounds[i + 1]
            if chords and chords[-1]["label"] == lab:
                chords[-1]["end"] = round(end, 3)
            else:
                chords.append({"start": round(start, 3), "end": round(end, 3), "label": lab})

        return {"key": {"name": key, "confidence": conf, "runner_up": runner_up},
                "chords": chords,
                "chord_chart": chord_chart(chords, inputs["bars"], duration)}


def chord_chart(chords: list[dict], bars: list[float], duration: float,
                min_share: float = 0.2) -> list[list[str]]:
    """Chords per bar: every chord covering at least `min_share` of the bar, in order."""
    edges = list(bars) + [duration]
    chart = []
    for a, b in zip(edges[:-1], edges[1:]):
        length = max(b - a, 1e-6)
        in_bar = []
        for c in chords:
            overlap = min(b, c["end"]) - max(a, c["start"])
            if overlap / length >= min_share and (not in_bar or in_bar[-1] != c["label"]):
                in_bar.append(c["label"])
        chart.append(in_bar or ["N"])
    return chart
