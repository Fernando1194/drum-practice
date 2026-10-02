"""Practice tools: stem mixing, bar-range looping, slow-down, count-in, speed trainer.

All audio is handled as float32 arrays shaped (channels, samples).
"""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from ..core.models import Song

SR = 44100


# ---------- I/O ----------

def load(path: str | Path, sr: int = SR) -> np.ndarray:
    y, _ = librosa.load(str(path), sr=sr, mono=False)
    return np.atleast_2d(y).astype(np.float32)


def save(y: np.ndarray, path: str | Path, sr: int = SR) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), y.T, sr)
    return path


# ---------- Mixing ----------

def _match_channels(arrays: list[np.ndarray]) -> list[np.ndarray]:
    ch = max(a.shape[0] for a in arrays)
    return [np.repeat(a, ch, axis=0) if a.shape[0] == 1 and ch > 1 else a for a in arrays]


def mix(stems: dict[str, str], gains: dict[str, float], sr: int = SR) -> np.ndarray:
    """Sum stems with per-stem gain (0 = muted, 1 = original). Missing gain = 1."""
    active = {n: p for n, p in stems.items() if gains.get(n, 1.0) > 0}
    if not active:
        raise ValueError("All stems are muted")
    arrays = _match_channels([load(p, sr) * gains.get(n, 1.0) for n, p in active.items()])
    n = max(a.shape[1] for a in arrays)
    out = np.zeros((arrays[0].shape[0], n), dtype=np.float32)
    for a in arrays:
        out[:, : a.shape[1]] += a
    peak = np.abs(out).max()
    return out / peak * 0.98 if peak > 1.0 else out


# ---------- Time ----------

def bar_range_to_seconds(song: Song, start_bar: int, end_bar: int,
                         bar_offset: int = 0) -> tuple[float, float]:
    """Bars are 1-indexed and inclusive. `bar_offset` shifts the grid (pickup bars)."""
    bars: list[float] = song.get("bars")
    duration: float = song.get("duration")
    s, e = start_bar - 1 + bar_offset, end_bar + bar_offset
    if start_bar < 1 or end_bar < start_bar or s >= len(bars):
        raise ValueError(f"Bar range {start_bar}-{end_bar} invalid; song has {len(bars)} bars")
    return bars[s], (bars[e] if e < len(bars) else duration)


def cut(y: np.ndarray, start_s: float, end_s: float, sr: int = SR) -> np.ndarray:
    return y[:, int(start_s * sr): int(end_s * sr)].copy()


def _rubberband_ok() -> bool:
    return importlib.util.find_spec("pyrubberband") is not None and shutil.which("rubberband") is not None


def time_stretch(y: np.ndarray, speed: float, sr: int = SR) -> np.ndarray:
    """Change speed without changing pitch. speed=0.7 means 70% tempo."""
    if abs(speed - 1.0) < 1e-3:
        return y
    if _rubberband_ok():
        import pyrubberband as pyrb
        return pyrb.time_stretch(y.T, sr, speed).T.astype(np.float32)
    # Fallback: phase vocoder. Audibly "phasey" below ~70%; install rubberband for better quality.
    return np.stack([librosa.effects.time_stretch(c, rate=speed) for c in y]).astype(np.float32)


def fade(y: np.ndarray, ms: float = 10, sr: int = SR) -> np.ndarray:
    """Short fade in/out so loop boundaries don't click."""
    n = min(int(sr * ms / 1000), y.shape[1] // 2)
    if n > 0:
        ramp = np.linspace(0, 1, n, dtype=np.float32)
        y = y.copy()
        y[:, :n] *= ramp
        y[:, -n:] *= ramp[::-1]
    return y


# ---------- Practice building blocks ----------

def click(sr: int = SR, accent: bool = False, channels: int = 2) -> np.ndarray:
    dur, freq = 0.05, 1500 if accent else 1000
    t = np.arange(int(sr * dur)) / sr
    c = (np.sin(2 * np.pi * freq * t) * np.exp(-t * 60) * 0.6).astype(np.float32)
    return np.tile(c, (channels, 1))


def count_in(tempo: float, beats: int = 4, sr: int = SR, channels: int = 2) -> np.ndarray:
    beat_len = int(sr * 60.0 / tempo)
    out = np.zeros((channels, beat_len * beats), dtype=np.float32)
    for i in range(beats):
        c = click(sr, accent=(i == 0), channels=channels)
        out[:, i * beat_len: i * beat_len + c.shape[1]] += c
    return out


def silence(seconds: float, channels: int = 2, sr: int = SR) -> np.ndarray:
    return np.zeros((channels, int(sr * seconds)), dtype=np.float32)


@dataclass
class PracticeRequest:
    start_bar: int
    end_bar: int
    gains: dict[str, float] = field(default_factory=dict)  # stem -> gain; empty = original mix
    speed: float = 1.0               # used when ladder is off
    loops: int = 4
    gap_s: float = 1.0               # pause between repetitions
    use_count_in: bool = True
    ladder: bool = False             # speed trainer: start_speed -> end_speed
    start_speed: float = 0.6
    end_speed: float = 1.0
    step: float = 0.1
    bar_offset: int = 0

    def speeds(self) -> list[float]:
        if not self.ladder:
            return [self.speed]
        n = int(round((self.end_speed - self.start_speed) / self.step)) + 1
        return [round(self.start_speed + i * self.step, 3) for i in range(max(n, 1))]


def render_practice(song: Song, req: PracticeRequest, sr: int = SR) -> Path:
    """Build one practice audio file from a bar range, stem mix and speed plan."""
    stems = song.stems()
    if req.gains and stems:
        source = mix(stems, req.gains, sr)
    else:
        source = load(song.get("audio:mix"), sr)
    channels = source.shape[0]

    start_s, end_s = bar_range_to_seconds(song, req.start_bar, req.end_bar, req.bar_offset)
    segment = fade(cut(source, start_s, end_s, sr), sr=sr)
    tempo = float(song.get("tempo"))

    parts: list[np.ndarray] = []
    for speed in req.speeds():
        stretched = fade(time_stretch(segment, speed, sr), sr=sr)
        if req.use_count_in:
            parts.append(count_in(tempo * speed, sr=sr, channels=channels))
        for i in range(req.loops):
            parts.append(stretched)
            if i < req.loops - 1:
                parts.append(silence(req.gap_s, channels, sr))
        parts.append(silence(req.gap_s * 2, channels, sr))

    speeds_tag = "-".join(f"{int(s * 100)}" for s in req.speeds())
    name = (f"bars{req.start_bar}-{req.end_bar}_{mix_tag(req.gains)}"
            f"_speed{speeds_tag}_x{req.loops}.wav")
    return save(np.concatenate(parts, axis=1), song.dir / "practice" / name, sr)


def mix_tag(gains: dict[str, float]) -> str:
    if not gains:
        return "fullmix"
    on = [k for k, g in gains.items() if g > 0]
    off = [k for k, g in gains.items() if g == 0]
    if len(on) == 1 and len(off) >= 2:
        return f"only-{on[0]}"
    if off and all(g == 1.0 for g in gains.values() if g > 0):
        return "minus-" + "-".join(sorted(off))
    return "custom"
