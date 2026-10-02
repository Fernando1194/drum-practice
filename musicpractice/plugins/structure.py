"""Song structure: section boundaries (snapped to bars), repeat letters, and label hints.

Boundaries come from a bar-level self-similarity matrix (chroma + timbre) with a
checkerboard novelty kernel, the classic Foote method. Sections that sound alike get
the same letter (A, B, A, C...).

Names like "Chorus" or "Solo" are HINTS from simple rules:
- with stems: vocals present or not, plus guitar/keys energy
- without stems: only "Intro"/"Outro" from loudness
They will often be wrong on unusual song forms.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import librosa
import numpy as np

from ..core.models import Song
from . import features as F


def bar_features(song: Song, bars: list[float], duration: float) -> tuple[np.ndarray, np.ndarray]:
    y = F.load_mono(song.get("audio:mix"))
    harm = librosa.effects.harmonic(y, margin=2.0)
    C = F.chroma(harm)
    M = librosa.feature.mfcc(y=y, sr=F.SR, hop_length=F.HOP, n_mfcc=13)[1:]
    rms = librosa.feature.rms(y=y, hop_length=F.HOP)
    edges = list(bars) + [duration]
    Cb, Mb, Rb = (F.segment_means(x, edges) for x in (C, M, rms))
    Cb = Cb / np.maximum(np.linalg.norm(Cb, axis=0), 1e-9)
    Mb = (Mb - Mb.mean(axis=1, keepdims=True)) / (Mb.std(axis=1, keepdims=True) + 1e-9)
    X = np.vstack([Cb * 2.0, Mb / np.sqrt(Mb.shape[0])])
    return X, Rb[0]


def self_similarity(X: np.ndarray) -> np.ndarray:
    Xn = X / np.maximum(np.linalg.norm(X, axis=0), 1e-9)
    return (Xn.T @ Xn + 1) / 2


def novelty_boundaries(S: np.ndarray, width: int = 4, min_len: int = 4) -> list[int]:
    n = S.shape[0]
    if n < 2 * min_len:
        return [0, n]
    sign = np.sign(np.arange(-width, width) + 0.5)
    g = np.exp(-0.5 * (np.arange(-width, width) + 0.5) ** 2 / (width / 2) ** 2)
    K = np.outer(sign * g, sign * g)
    pad = np.pad(S, width, mode="edge")
    nov = np.array([(K * pad[i:i + 2 * width, i:i + 2 * width]).sum() for i in range(n)])
    nov = np.maximum(nov, 0)
    thresh = nov.mean() + 0.5 * nov.std()
    cands = sorted((i for i in range(min_len, n - min_len + 1)
                    if nov[i] >= thresh and nov[i] == nov[max(0, i - 2):i + 3].max()),
                   key=lambda i: -nov[i])
    chosen: list[int] = []
    for c in cands:
        if all(abs(c - o) >= min_len for o in chosen):
            chosen.append(c)
    return [0] + sorted(chosen) + [n]


def assign_letters(S: np.ndarray, bounds: list[int], ratio: float = 0.93) -> list[str]:
    segs = list(zip(bounds[:-1], bounds[1:]))
    self_sim = [S[a:b, a:b].mean() for a, b in segs]
    letters: list[str] = []
    reps: dict[str, int] = {}
    for i, (a, b) in enumerate(segs):
        best, best_score = None, 0.0
        for letter, j in reps.items():
            c, d = segs[j]
            score = S[a:b, c:d].mean() / min(self_sim[i], self_sim[j])
            if score > best_score:
                best, best_score = letter, score
        if best is not None and best_score >= ratio:
            letters.append(best)
        else:
            new = chr(ord("A") + len(reps))
            reps[new] = i
            letters.append(new)
    return letters


def stem_activity(song: Song, bars: list[float], duration: float) -> dict[str, np.ndarray]:
    """Per-bar loudness for each stem, scaled so 1.0 = that stem's loud parts."""
    out = {}
    edges = list(bars) + [duration]
    for name, path in song.stems().items():
        rms = librosa.feature.rms(y=F.load_mono(path), hop_length=F.HOP)
        per_bar = F.segment_means(rms, edges)[0]
        out[name] = per_bar / (np.percentile(per_bar, 90) + 1e-9)
    return out


def hint_labels(letters: list[str], segs: list[tuple[int, int]],
                activity: dict[str, np.ndarray], loudness: np.ndarray) -> list[str]:
    n = len(segs)
    hints = [""] * n
    seg_mean = lambda arr, s: float(arr[s[0]:s[1]].mean())  # noqa: E731
    loud = [seg_mean(loudness, s) for s in segs]
    med = float(np.median(loudness)) + 1e-9

    if "vocals" not in activity:
        if n > 1 and loud[0] < 0.6 * med:
            hints[0] = "Intro"
        if n > 2 and loud[-1] < 0.6 * med:
            hints[-1] = "Outro"
        return hints

    vocal = [seg_mean(activity["vocals"], s) > 0.25 for s in segs]
    lead = np.maximum.reduce([activity[k] for k in ("guitar", "piano", "other") if k in activity]
                             or [np.zeros_like(loudness)])
    first_v = next((i for i, v in enumerate(vocal) if v), None)
    last_v = max((i for i, v in enumerate(vocal) if v), default=None)
    if first_v is None:
        return ["Instrumental"] * n

    vocal_letters = Counter(letters[i] for i in range(n) if vocal[i])
    by_strength = sorted(vocal_letters, key=lambda L: (
        vocal_letters[L], np.mean([loud[i] for i in range(n) if letters[i] == L])), reverse=True)
    chorus = by_strength[0] if vocal_letters[by_strength[0]] > 1 else None
    if chorus and len(by_strength) > 1 and vocal_letters[by_strength[1]] == vocal_letters[chorus]:
        a, b = by_strength[0], by_strength[1]
        la = np.mean([loud[i] for i in range(n) if letters[i] == a])
        lb = np.mean([loud[i] for i in range(n) if letters[i] == b])
        chorus = a if la >= lb else b
    first_chorus = next((i for i in range(n) if letters[i] == chorus and vocal[i]), n)

    for i in range(n):
        if i < first_v:
            hints[i] = "Intro"
        elif last_v is not None and i > last_v:
            hints[i] = "Outro"
        elif not vocal[i]:
            hints[i] = "Solo / instrumental" if seg_mean(lead, segs[i]) > 0.7 else "Interlude"
        elif letters[i] == chorus:
            hints[i] = "Chorus"
        elif vocal_letters[letters[i]] == 1 and i > first_chorus:
            hints[i] = "Bridge"
        else:
            hints[i] = "Verse"
    return hints


class StructureAnalyzer:
    name = "structure"
    requires = ("bars", "duration")
    produces = ("sections", "sections_stem_hints")

    def available(self) -> bool:
        return True

    def run(self, song: Song, inputs: dict[str, Any]) -> dict[str, Any]:
        bars, duration = inputs["bars"], inputs["duration"]
        X, loudness = bar_features(song, bars, duration)
        S = self_similarity(X)
        bounds = novelty_boundaries(S)
        letters = assign_letters(S, bounds)
        segs = list(zip(bounds[:-1], bounds[1:]))
        activity = stem_activity(song, bars, duration)
        hints = hint_labels(letters, segs, activity, loudness)
        edges = list(bars) + [duration]
        sections = [{"start_bar": a + 1, "end_bar": b, "start": edges[a], "end": edges[b],
                     "letter": letters[i], "hint": hints[i]}
                    for i, (a, b) in enumerate(segs)]
        return {"sections": sections, "sections_stem_hints": bool(activity)}
