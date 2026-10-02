"""Shared feature extraction for analysis plugins."""
from __future__ import annotations

import librosa
import numpy as np

from ..core.models import Song

SR = 22050
HOP = 512
PITCH_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PITCH_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def pc_name(pc: int, flats: bool = False) -> str:
    return (PITCH_FLAT if flats else PITCH_SHARP)[pc % 12]


def load_mono(path: str, sr: int = SR) -> np.ndarray:
    y, _ = librosa.load(path, sr=sr, mono=True)
    return y


def harmonic_audio(song: Song, sr: int = SR) -> np.ndarray:
    """Audio with drums and vocals removed when stems exist, else HPSS harmonic part."""
    stems = song.stems()
    pitched = [stems[s] for s in ("bass", "guitar", "piano", "other") if s in stems]
    if pitched:
        arrays = [load_mono(p, sr) for p in pitched]
        n = max(len(a) for a in arrays)
        return sum(np.pad(a, (0, n - len(a))) for a in arrays)
    return librosa.effects.harmonic(load_mono(song.get("audio:mix"), sr), margin=3.0)


def chroma(y: np.ndarray, sr: int = SR) -> np.ndarray:
    return librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP)


def segment_means(feat: np.ndarray, boundaries_s: list[float], sr: int = SR) -> np.ndarray:
    """Mean feature over each [b_i, b_i+1) time span. Returns (d, len(boundaries)-1)."""
    frames = librosa.time_to_frames(np.asarray(boundaries_s), sr=sr, hop_length=HOP)
    frames = np.clip(frames, 0, feat.shape[1])
    out = np.zeros((feat.shape[0], len(frames) - 1), dtype=np.float32)
    for i, (a, b) in enumerate(zip(frames[:-1], frames[1:])):
        out[:, i] = feat[:, a:max(b, a + 1)].mean(axis=1) if a < feat.shape[1] else 0
    return out
