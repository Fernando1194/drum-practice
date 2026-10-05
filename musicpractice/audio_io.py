"""Reading audio files of any format.

After a song is analyzed its WAVs are replaced by Opus copies (see core/storage.py), so code that
reads audio may get a .webm. WAV/FLAC go through librosa exactly as before (same samples, same
results); anything else is decoded by ffmpeg, which the app already needs.
"""
from __future__ import annotations

import contextlib
import subprocess
import tempfile
from pathlib import Path

import numpy as np

NATIVE = {".wav", ".flac", ".ogg", ".aiff", ".aif"}


def _ffmpeg(path: Path, sr: int, channels: int) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "f32le",
                          "-ac", str(channels), "-ar", str(sr), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32).reshape(-1, channels).T.copy()


def load(path: str | Path, sr: int, mono: bool = True) -> np.ndarray:
    """Like librosa.load(path, sr=sr, mono=mono)[0]: shape (n,) if mono, else (channels, n)."""
    import librosa
    p = Path(path)
    if p.suffix.lower() in NATIVE:
        y, _ = librosa.load(str(p), sr=sr, mono=mono)
        return y
    y = _ffmpeg(p, sr, 2)
    if mono:
        return y.mean(axis=0)
    return y


@contextlib.contextmanager
def as_wav(path: str | Path):
    """A WAV path for libraries that only read WAV (a temporary decode if needed)."""
    p = Path(path)
    if p.suffix.lower() in NATIVE:
        yield p
        return
    import soundfile as sf
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / (p.stem + ".wav")
        sf.write(str(tmp), load(p, 44100, mono=False).T, 44100)
        yield tmp
