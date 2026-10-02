"""Tempo, beat and bar grid using librosa.

librosa has no downbeat model, so bar lines are found with a heuristic:
chords usually change on beat 1, so we try the 4 possible phases of a 4/4 grid
and keep the one where harmonic changes and low-end accents line up best.

Limitations:
- assumes 4/4 (change `beats_per_bar` for 3/4 songs)
- tempo changes and rubato will drift; `bar_offset` in practice tools lets you
  shift the grid manually. A downbeat model (beat_this, madmom) can replace this
  plugin later without touching anything else.
"""
from __future__ import annotations

from typing import Any

import librosa
import numpy as np

from ..core.models import Song
from . import features as F


def extend_beats_back(beats: np.ndarray) -> np.ndarray:
    """The tracker often misses the first beats; extrapolate the grid back to t=0."""
    if len(beats) < 4:
        return beats
    ibi = float(np.median(np.diff(beats)))
    first = beats[0]
    extra = []
    t = first - ibi
    while t > -0.25 * ibi:
        extra.append(max(t, 0.0))
        t -= ibi
    return np.concatenate([np.array(extra[::-1]), beats])


def extend_beats_forward(beats: np.ndarray, end: float) -> np.ndarray:
    """The tracker also stops early: it trims weak beats at the end (fade-outs, a quiet outro,
    a final held chord), and everything after its last beat had no bar to be drawn in, so the
    end of the song showed no transcription. Continue the grid at the median beat spacing up to
    the end of the audio that isn't silence."""
    if len(beats) < 4:
        return beats
    ibi = float(np.median(np.diff(beats)))
    extra = []
    t = beats[-1] + ibi
    while t < end - 0.25 * ibi:
        extra.append(t)
        t += ibi
    return np.concatenate([beats, np.array(extra)]) if extra else beats


def audible_end(y: np.ndarray, sr: int, top_db: float = 50.0) -> float:
    """Time where the audio fades into silence (50 dB below its peak)."""
    _, (start, end) = librosa.effects.trim(y, top_db=top_db)
    return end / sr


def downbeat_phase(y: np.ndarray, sr: int, beats: np.ndarray, beats_per_bar: int) -> int:
    harm, perc = librosa.effects.hpss(y)
    C = librosa.feature.chroma_cqt(y=harm, sr=sr, hop_length=F.HOP)
    bounds = list(beats) + [len(y) / sr]
    Cb = F.segment_means(C, bounds)
    Cb = Cb / np.maximum(np.linalg.norm(Cb, axis=0), 1e-9)
    change = np.zeros(len(beats))
    change[1:] = 1 - np.sum(Cb[:, 1:] * Cb[:, :-1], axis=0)

    low = librosa.onset.onset_strength(y=perc, sr=sr, hop_length=F.HOP, fmax=150, n_mels=32)
    frames = librosa.time_to_frames(beats, sr=sr, hop_length=F.HOP)
    accent = low[np.clip(frames, 0, len(low) - 1)]
    accent = accent / (accent.max() + 1e-9)

    score = change / (change.max() + 1e-9) + 0.5 * accent
    phases = [score[k::beats_per_bar].mean() for k in range(beats_per_bar)]
    return int(np.argmax(phases))


class LibrosaBeatTracker:
    name = "librosa-beats"
    requires = ("audio:mix",)
    produces = ("tempo", "beats", "bars", "duration", "beats:version", "grid:drums")
    VERSION = 2   # 2: grid continues to the end of the song

    def __init__(self, beats_per_bar: int = 4):
        self.beats_per_bar = beats_per_bar

    def available(self) -> bool:
        return True

    def run(self, song: Song, inputs: dict[str, Any]) -> dict[str, Any]:
        sr = F.SR
        y = F.load_mono(inputs["audio:mix"], sr)
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=F.HOP, units="frames")
        tracked = librosa.frames_to_time(beat_frames, sr=sr, hop_length=F.HOP)
        end = audible_end(y, sr)
        beats = extend_beats_forward(extend_beats_back(tracked), end)
        # mean spacing over the whole song averages out the 23 ms frame quantization
        tempo = 60.0 * (len(tracked) - 1) / float(tracked[-1] - tracked[0]) if len(tracked) > 1 else 0.0
        phase = downbeat_phase(y, sr, beats, self.beats_per_bar) if len(beats) >= 8 else 0
        bars = beats[phase:: self.beats_per_bar]
        if phase and bars[0] > 0.05:  # pickup beats before the first bar line become bar 0
            bars = np.concatenate([[0.0], bars])
        if len(beats) > 1:            # no stub bar that starts as the sound ends
            ibi = float(np.median(np.diff(beats)))
            while len(bars) > 1 and bars[-1] > end - ibi:
                bars = bars[:-1]
        return {
            "tempo": round(tempo, 1),
            "beats": [round(float(b), 3) for b in beats],
            "bars": [round(float(b), 3) for b in bars],
            "duration": round(len(y) / sr, 3),
            "beats:version": self.VERSION,
            "grid:drums": 0,          # not yet refined from the drum hits (see drum_grid.py)
        }
