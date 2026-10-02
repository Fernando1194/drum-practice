"""Synthetic drum kit and grooves with known hits, for testing drum transcription."""
from __future__ import annotations

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

SR = 44100
_rng = np.random.default_rng(7)


def _noise(n):
    return _rng.standard_normal(n).astype(np.float32)


def _band(x, lo=None, hi=None, sr=SR):
    if lo and hi:
        sos = butter(4, [lo, hi], btype="band", fs=sr, output="sos")
    elif lo:
        sos = butter(4, lo, btype="high", fs=sr, output="sos")
    else:
        sos = butter(4, hi, btype="low", fs=sr, output="sos")
    return sosfilt(sos, x).astype(np.float32)


def _env(n, rate, sr=SR):
    return np.exp(-np.arange(n) / sr * rate).astype(np.float32)


def kick(sr=SR):
    n = int(0.45 * sr)
    t = np.arange(n) / sr
    f = 50 + 100 * np.exp(-t * 35)
    phase = 2 * np.pi * np.cumsum(f) / sr
    click = _band(_noise(n), 2000, 6000) * _env(n, 300) * 0.15
    return (0.95 * np.sin(phase) * _env(n, 7) + click).astype(np.float32)


def snare(sr=SR):
    n = int(0.3 * sr)
    t = np.arange(n) / sr
    body = 0.45 * np.sin(2 * np.pi * 190 * t) * _env(n, 28)
    wires = 0.7 * _band(_noise(n), 1200, 9000) * _env(n, 17)
    return (body + wires).astype(np.float32)


def hihat(sr=SR):
    n = int(0.12 * sr)
    return (0.45 * _band(_noise(n), 7000, None) * _env(n, 55)).astype(np.float32)


def crash(sr=SR):
    n = int(1.6 * sr)
    return (0.55 * _band(_noise(n), 3500, None) * _env(n, 2.5)).astype(np.float32)


def ride(sr=SR):
    n = int(0.9 * sr)
    t = np.arange(n) / sr
    wash = 0.30 * _band(_noise(n), 4500, None) * _env(n, 5)
    bell = 0.12 * np.sin(2 * np.pi * 2700 * t) * _env(n, 9)
    return (wash + bell).astype(np.float32)


def tom(freq, sr=SR):
    n = int(0.5 * sr)
    t = np.arange(n) / sr
    f = freq * (1 + 0.15 * np.exp(-t * 20))
    phase = 2 * np.pi * np.cumsum(f) / sr
    stick = _band(_noise(n), 1000, 4000) * _env(n, 120) * 0.15
    return (0.8 * np.sin(phase) * _env(n, 7) + stick).astype(np.float32)


SOUNDS = {"kick": kick, "snare": snare, "hihat": hihat, "crash": crash, "ride": ride,
          "tom_high": lambda sr=SR: tom(210, sr), "tom_mid": lambda sr=SR: tom(150, sr),
          "tom_floor": lambda sr=SR: tom(98, sr)}


def groove(bars: int = 16, bpm: float = 110, ride_from: int | None = 9):
    """Rock groove: 8th-note hats (ride from bar `ride_from`), kick on 1 and 3 (+ the '&' of 3),
    snare on 2 and 4, a 16th-note tom fill every 4th bar, crash + kick on the bar after a fill."""
    beat = 60 / bpm
    s16 = beat / 4
    hits: list[tuple[float, str]] = []
    for b in range(bars):
        t0 = b * 4 * beat
        if b % 4 == 3:  # fill: 2 beats of groove, then toms
            hits += [(t0, "hihat"), (t0, "kick"), (t0 + beat, "snare"), (t0 + beat, "hihat")]
            for k, piece in enumerate(["tom_high"] * 2 + ["tom_mid"] * 2 + ["tom_floor"] * 4):
                hits.append((t0 + 2 * beat + k * s16, piece))
            continue
        cym = "ride" if ride_from and b + 1 >= ride_from else "hihat"
        for e in range(8):
            if e == 0 and b > 0 and b % 4 == 0:
                hits.append((t0, "crash"))
            else:
                hits.append((t0 + e * beat / 2, cym))
        hits += [(t0, "kick"), (t0 + 2 * beat, "kick"), (t0 + 2.5 * beat, "kick"),
                 (t0 + beat, "snare"), (t0 + 3 * beat, "snare")]
    return sorted(hits), beat


def render(hits, length_s: float, path=None, sr=SR, lead_in: float = 0.5, kit=None,
           bleed_bpm: float | None = None):
    """Mix the kit sounds at the hit times. Returns (audio, hits shifted by lead_in)."""
    y = np.zeros(int((length_s + lead_in + 2) * sr), np.float32)
    cache = {k: f(sr) for k, f in (kit or SOUNDS).items()}
    shifted = []
    for t, piece in hits:
        s = cache[piece]
        i = int((t + lead_in) * sr)
        y[i:i + len(s)] += s[: len(y) - i]
        shifted.append((round(t + lead_in, 4), piece))
    if bleed_bpm:
        y = y / np.abs(y).max() + bleed(len(y), bleed_bpm, sr=sr)
    y = y / np.abs(y).max() * 0.9
    if path is not None:
        sf.write(path, np.stack([y, y]).T, sr)
    return y, shifted


def score(found: list[tuple[float, str]], truth: list[tuple[float, str]], tol: float = 0.035):
    """Per-piece precision/recall/F1 with one-to-one matching inside `tol` seconds."""
    out = {}
    for piece in sorted({p for _, p in truth} | {p for _, p in found}):
        tt = sorted(t for t, p in truth if p == piece)
        ff = sorted(t for t, p in found if p == piece)
        used, tp, j = set(), 0, 0
        for t in tt:
            best = None
            for k, f in enumerate(ff):
                if k not in used and abs(f - t) <= tol and (best is None or abs(f - t) < abs(ff[best] - t)):
                    best = k
            if best is not None:
                used.add(best)
                tp += 1
        p = tp / len(ff) if ff else (1.0 if not tt else 0.0)
        r = tp / len(tt) if tt else 1.0
        out[piece] = {"precision": round(p, 3), "recall": round(r, 3),
                      "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
                      "truth": len(tt), "found": len(ff)}
    return out


def kit_b(sr=SR):
    """A differently tuned, differently voiced kit to check the detector isn't overfit."""
    def kick_b(sr=sr):
        n = int(0.5 * sr); t = np.arange(n) / sr
        f = 42 + 70 * np.exp(-t * 25)
        return (0.9 * np.sin(2 * np.pi * np.cumsum(f) / sr) * _env(n, 5)
                + 0.25 * _band(_noise(n), 1500, 5000) * _env(n, 200)).astype(np.float32)

    def snare_b(sr=sr):
        n = int(0.35 * sr); t = np.arange(n) / sr
        return (0.35 * np.sin(2 * np.pi * 240 * t) * _env(n, 20)
                + 0.5 * np.sin(2 * np.pi * 330 * t) * _env(n, 35) * 0.4
                + 0.6 * _band(_noise(n), 800, 12000) * _env(n, 12)).astype(np.float32)

    def hat_b(sr=sr):
        n = int(0.15 * sr)
        return (0.35 * _band(_noise(n), 5500, None) * _env(n, 40)).astype(np.float32)

    def crash_b(sr=sr):
        n = int(2.0 * sr)
        return (0.5 * _band(_noise(n), 2500, None) * _env(n, 1.8)).astype(np.float32)

    def ride_b(sr=sr):
        n = int(1.0 * sr); t = np.arange(n) / sr
        return (0.25 * _band(_noise(n), 3500, None) * _env(n, 6)
                + 0.15 * np.sin(2 * np.pi * 3100 * t) * _env(n, 6)).astype(np.float32)

    return {"kick": kick_b, "snare": snare_b, "hihat": hat_b, "crash": crash_b, "ride": ride_b,
            "tom_high": lambda sr=sr: tom(185, sr), "tom_mid": lambda sr=sr: tom(128, sr),
            "tom_floor": lambda sr=sr: tom(82, sr)}


def bleed(n: int, bpm: float, level: float = 0.08, sr=SR) -> np.ndarray:
    """Quiet guitar chords + bass notes, like what leaks into a separated drum stem."""
    y = np.zeros(n, np.float32)
    beat = 60 / bpm
    t = np.arange(int(beat * 2 * sr)) / sr
    roots = [110.0, 98.0, 87.3, 82.4]
    for k, start in enumerate(np.arange(0, n / sr - 2 * beat, 2 * beat)):
        r = roots[k % 4]
        seg = sum(np.sin(2 * np.pi * f * t) for f in (r, r * 1.26, r * 1.5, r * 2, r / 2))
        seg = seg * np.exp(-t * 1.5)
        i = int(start * sr)
        y[i:i + len(seg)] += seg[: n - i].astype(np.float32)
    return level * y / (np.abs(y).max() + 1e-9)


def song_with_drums(path, stems_dir, bpm: float = 120, bars: int = 32, kit=None):
    """Full synthetic song: guitar/bass/vocals from synth.make_song + this drum groove.
    Returns (drum truth hits, stems dict)."""
    from pathlib import Path

    from synth import make_song
    stems_dir = Path(stems_dir)
    _, stems = make_song(Path(path), stems_dir=stems_dir, bpm=bpm)
    hits, beat = groove(bars, bpm)
    drums, truth = render(hits, bars * 4 * beat, kit=kit, lead_in=0.0)
    parts = []
    for name in ("guitar", "bass", "vocals"):
        y, _ = sf.read(stems[name])
        parts.append(y[:, 0] if y.ndim > 1 else y)
    n = max(len(p) for p in parts + [drums])
    drums = np.pad(drums, (0, n - len(drums))) * 0.8
    sf.write(stems_dir / "drums.wav", np.stack([drums, drums]).T, SR)
    stems["drums"] = str(stems_dir / "drums.wav")
    mix = sum(np.pad(p, (0, n - len(p))) for p in parts) + drums
    mix = mix / np.abs(mix).max() * 0.9
    sf.write(path, np.stack([mix, mix]).T, SR)
    return truth, stems
