"""Drum transcription: which piece of the kit is hit, and when.

No deep-learning model (those pull in TensorFlow). Instead, classic signal processing:

1. Mel spectrogram of the drum stem (or the percussive part of the mix if no stem).
2. Non-negative matrix factorization (NMF) with one spectral template per drum family
   (kick, snare, toms, metal = hi-hats and cymbals) plus two free components that soak up
   bleed from other instruments. The templates start as generic shapes and adapt to the
   actual kit in the recording. This is the "partially fixed NMF" idea from drum
   transcription research.
3. Hits = peaks in each family's onset curve.
4. Refinement:
   - metal hits: short ring = hi-hat, long ring = cymbal; cymbals played in a steady
     pattern = ride, isolated ones = crash.
   - tom hits: split into high / mid / floor by their pitch, relative to this kit.

Honest expectations: kick, snare and hi-hat are the reliable ones. Toms are often confused
with each other, crash vs ride is a pattern guess, open hi-hat, ghost notes, rimshots and
cymbal chokes are not detected.

Outputs:
    drums:hits   [{time, piece, velocity}]  piece in PIECES
    notes:drums  same hits as General MIDI drum notes (shared format with other instruments)
    midi:drums   .mid file on MIDI channel 10 (opens as drums in MuseScore, DAWs, Guitar Pro)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import librosa
import numpy as np

from ..core.models import Song

PIECES = ("crash", "ride", "hihat", "tom_high", "tom_mid", "snare", "tom_floor", "kick")
GM = {"kick": 36, "snare": 38, "hihat": 42, "crash": 49, "ride": 51,
      "tom_high": 48, "tom_mid": 45, "tom_floor": 41}
LABELS = {"kick": "Kick", "snare": "Snare", "hihat": "Hi-hat", "crash": "Crash", "ride": "Ride",
          "tom_high": "Tom 1", "tom_mid": "Tom 2", "tom_floor": "Floor tom"}
SHORT = {"kick": "BD", "snare": "SD", "hihat": "HH", "crash": "CC", "ride": "RD",
         "tom_high": "T1", "tom_mid": "T2", "tom_floor": "FT"}
CYMBALS = {"crash", "ride", "hihat"}
# One color per piece, used for the kit drawing AND the marks in the drum grid, so the eye
# links them. Checked with a color-vision validator: neighboring grid rows stay distinct for
# normal and color-blind vision. Color is never the only cue: each piece also has its own
# row, symbol (x cymbal, dot drum) and name.
COLORS = {"crash": "#e87ba4", "ride": "#4a3aa7", "hihat": "#2a78d6", "tom_high": "#eb6834",
          "tom_mid": "#1baf7a", "snare": "#e34948", "tom_floor": "#eda100", "kick": "#008300"}

SR = 44100
HOP = 256
N_MELS = 96
FAMILIES = ("kick", "snare", "tom", "metal")
HAT_RING = 0.05      # isolated metal hit below this: hi-hat
KEEPER_SPLIT = 0.25  # time-keeping cymbal: local median ring below = hi-hat, above = ride
CRASH_RING = 0.7     # crash: rings at least this much (and more than its neighbors)


# ---------------------------------------------------------------- spectrogram + NMF

def _gauss_oct(f, center, width_oct):
    return np.exp(-0.5 * (np.log2(np.maximum(f, 1) / center) / width_oct) ** 2)


def templates(freqs: np.ndarray) -> np.ndarray:
    """Generic starting spectra (columns) for each family, on the mel bin center freqs."""
    kick = _gauss_oct(freqs, 60, 0.55) + 0.03 * _gauss_oct(freqs, 3500, 0.6)
    snare = 0.8 * _gauss_oct(freqs, 200, 0.4) + 0.5 * ((freqs > 1200) & (freqs < 9000))
    tom = _gauss_oct(freqs, 140, 0.7) + 0.04 * _gauss_oct(freqs, 2500, 0.8)
    metal = np.clip((freqs - 4000) / 4000, 0, 1) + 0.15 * ((freqs > 2500) & (freqs < 4000))
    W = np.stack([kick, snare, tom, metal], axis=1) + 1e-3
    return W / W.sum(axis=0, keepdims=True)


def nmf(V: np.ndarray, W0: np.ndarray, n_free: int = 2, iters: int = 80,
        adapt_from: int = 20, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """KL-divergence NMF. Template columns are held fixed for `adapt_from` iterations,
    then allowed to adapt; free columns adapt from the start."""
    rng = np.random.default_rng(seed)
    k_fixed = W0.shape[1]
    W = np.hstack([W0, rng.random((V.shape[0], n_free)) + 0.1])
    W /= W.sum(axis=0, keepdims=True)
    H = rng.random((W.shape[1], V.shape[1])) * V.mean() + 1e-6
    eps = 1e-9
    for it in range(iters):
        WH = W @ H + eps
        H *= (W.T @ (V / WH)) / (W.sum(axis=0)[:, None] + eps)
        WH = W @ H + eps
        upd = ((V / WH) @ H.T) / (H.sum(axis=1)[None, :] + eps)
        if it < adapt_from:
            upd[:, :k_fixed] = 1.0
        W *= upd
        s = W.sum(axis=0, keepdims=True) + eps
        W /= s
        H *= s.T
    return W, H


# ---------------------------------------------------------------- peak picking

def onset_curve(h: np.ndarray) -> np.ndarray:
    d = np.diff(h, prepend=h[:1])
    return np.maximum(d, 0)


def pick_peaks(nov: np.ndarray, frame_s: float, min_gap_s: float = 0.06,
               rel: float = 0.12, k_std: float = 1.0, echo_s: float = 0.075,
               echo_ratio: float = 0.5) -> np.ndarray:
    """Peaks of an onset curve. A peak shortly after a stronger one of the same family must be
    at least `echo_ratio` as strong: drums decay in stages and would otherwise double-trigger."""
    if nov.max() <= 0:
        return np.array([], dtype=int)
    w = max(1, int(round(0.03 / frame_s)))
    local_max = nov == np.array([nov[max(0, i - w):i + w + 1].max() for i in range(len(nov))])
    floor = max(rel * np.percentile(nov[nov > 0], 99), nov.mean() + k_std * nov.std())
    cand = np.where(local_max & (nov > floor))[0]
    keep: list[int] = []
    gap = int(round(min_gap_s / frame_s))
    for c in cand[np.argsort(-nov[cand])]:
        if all(abs(c - k) >= gap for k in keep):
            keep.append(int(c))
    keep.sort()
    echo = int(round(echo_s / frame_s))
    out: list[int] = []
    for c in keep:
        prev = [k for k in out if 0 < c - k <= echo]
        if prev and nov[c] < echo_ratio * max(nov[k] for k in prev):
            continue
        out.append(c)
    return np.array(out, dtype=int)


# ---------------------------------------------------------------- refinement

def ring_ratio(act: np.ndarray, i: int, frame_s: float) -> float:
    """How much a metal hit is still ringing 70-110 ms later, relative to its attack, after
    removing what was already ringing before it (a crash's tail, a snare's wires).
    Hi-hats ~0 or below, rides ~0.1-0.6, crashes higher."""
    pre = float(act[max(0, i - 4):i].mean()) if i >= 1 else 0.0
    peak = float(act[i:i + int(0.02 / frame_s) + 1].max())
    a, b = i + int(0.07 / frame_s), i + int(0.11 / frame_s)
    if b >= len(act):
        return 1.0
    return float((act[a:b].mean() - pre) / (peak - pre + 1e-9))


def classify_metal(times: list[float], vels: list[float], rings: list[float],
                   beat_s: float) -> list[str]:
    """Hi-hat / ride / crash.

    Drummers keep time on the hi-hat OR the ride for whole passages, so the time-keeping
    cymbal is decided by the median ring of the metal hits within about a bar, not hit by
    hit. That way a hi-hat that coincides with a snare (whose wires keep ringing) doesn't
    flip to "cymbal". A crash must ring long in absolute terms and clearly more than its
    neighbors, or be an accent / an isolated hit that rings."""
    t = np.asarray(times)
    r = np.asarray(rings)
    v = np.asarray(vels)
    out = []
    for k in range(len(t)):
        near = np.where((np.abs(t - t[k]) <= 4 * beat_s) & (np.arange(len(t)) != k))[0]
        keepers = near[r[near] < CRASH_RING] if len(near) else near
        local = float(np.median(r[keepers])) if len(keepers) >= 3 else float(min(r[k], HAT_RING))
        close = near[np.abs(t[near] - t[k]) <= 1.05 * beat_s] if len(near) else near
        typical_v = float(np.median(v[close])) if len(close) else 0.0
        if r[k] >= CRASH_RING and r[k] >= local + 0.3:
            out.append("crash")
        elif not len(close) and r[k] >= 0.4:
            out.append("crash")                           # isolated, ringing cymbal
        elif len(close) and v[k] >= 1.6 * typical_v and r[k] >= local + 0.3:
            out.append("crash")                           # accent that rings over the pattern
        else:
            out.append("hihat" if local < KEEPER_SPLIT else "ride")
    return out


def low_pitch(y: np.ndarray, t: float, sr: int = SR, until: float | None = None) -> float:
    """Dominant frequency 35-450 Hz from 30 ms after a hit until 150 ms or the next low hit
    (whichever comes first): the drum's 'note'."""
    end = t + 0.15 if until is None else min(t + 0.15, until - 0.005)
    seg = y[int((t + 0.03) * sr): int(max(end, t + 0.06) * sr)]
    if len(seg) < 256:
        return 0.0
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n=8192))
    f = np.fft.rfftfreq(8192, 1 / sr)
    band = (f >= 35) & (f <= 450)
    return float(f[band][spec[band].argmax()])


tom_pitch = low_pitch  # backwards-compatible name


def split_low(pitches: list[float], kick_votes: list[bool]) -> list[str]:
    """Kick vs toms by pitch. The kick is the lowest, most frequent low drum: its pitch is
    the median of hits the kick template claimed. Anything > 4 semitones above is a tom."""
    if not pitches:
        return []
    claimed = [p for p, v in zip(pitches, kick_votes) if v and p > 0] or [min(p for p in pitches if p > 0)
                                                                          if any(pitches) else 60.0]
    kick_f = float(np.median(claimed))
    limit = kick_f * 2 ** (4 / 12)
    out = ["kick" if 0 < p <= limit else "tom" for p in pitches]
    toms = [p for p, o in zip(pitches, out) if o == "tom"]
    tom_names = iter(split_toms(toms))
    return [next(tom_names) if o == "tom" else o for o in out]


def split_toms(pitches: list[float], ref: list[float] | None = None) -> list[str]:
    """Group tom hits into up to three drums by pitch (gaps > ~2 semitones split groups).
    The groups are built from `ref` (default: all pitches); every pitch goes to the nearest."""
    if not pitches:
        return []
    order = sorted(set(round(p) for p in (ref if ref is not None else pitches) if p > 0))
    if not order:
        order = sorted(set(round(p) for p in pitches if p > 0)) or [1]
    groups: list[list[int]] = []
    for p in order:
        if groups and 12 * np.log2(p / groups[-1][-1]) < 2.0:
            groups[-1].append(p)
        else:
            groups.append([p])
    while len(groups) > 3:  # merge the two closest neighbors
        gaps = [np.log2(groups[i + 1][0] / groups[i][-1]) for i in range(len(groups) - 1)]
        i = int(np.argmin(gaps))
        groups[i:i + 2] = [groups[i] + groups[i + 1]]
    centers = [float(np.median(g)) for g in groups]
    names = {1: ["tom_mid"], 2: ["tom_floor", "tom_high"],
             3: ["tom_floor", "tom_mid", "tom_high"]}[len(centers)]
    out = []
    for p in pitches:
        idx = int(np.argmin([abs(np.log2(max(p, 1) / c)) for c in centers]))
        out.append(names[idx])
    return out


# ---------------------------------------------------------------- main entry

PAD_S = 0.1  # silence added in front so a hit at 0:00 still has an attack to detect


def transcribe_drums(y: np.ndarray, sr: int = SR, beat_s: float = 0.5) -> list[dict[str, Any]]:
    pad = int(PAD_S * sr)
    hits = _transcribe(np.concatenate([np.zeros(pad, dtype=np.float32), y.astype(np.float32)]),
                       sr, beat_s)
    out = []
    for h in hits:
        t = round(h["time"] - PAD_S, 3)
        if t >= -0.02:
            out.append({**h, "time": max(t, 0.0)})
    return out


def _transcribe(y: np.ndarray, sr: int, beat_s: float) -> list[dict[str, Any]]:
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    mel_fb = librosa.filters.mel(sr=sr, n_fft=2048, n_mels=N_MELS, fmin=30, fmax=16000)
    V = mel_fb @ S + 1e-9
    freqs = librosa.mel_frequencies(n_mels=N_MELS, fmin=30, fmax=16000)
    frame_s = HOP / sr

    W, H = nmf(V, templates(freqs))
    fam = {name: H[i] * W[:, i].sum() for i, name in enumerate(FAMILIES)}
    nov = {name: onset_curve(h) for name, h in fam.items()}
    # kick and toms overlap spectrally, so they are detected together as "low" hits and
    # told apart by pitch afterwards
    nov["low"] = nov["kick"] + nov["tom"]

    def strong(n):
        return float(np.percentile(n[n > 0], 99)) if n.max() > 0 else 0.0

    loudest = max(strong(nov[k]) for k in ("low", "snare", "metal"))
    metal_act = fam["metal"]
    hits: list[dict[str, Any]] = []
    metal: list[tuple[float, float, float]] = []      # time, velocity, ring
    low: list[tuple[float, float, bool, float]] = []  # time, velocity, kick template won, kick onset

    def win(name, p):
        return float(nov[name][max(0, p - 2):p + 3].max())

    for name in ("low", "snare", "metal"):
        n = nov[name]
        own = strong(n)
        if own < 0.08 * loudest:
            continue                                   # this family is basically absent
        for p in pick_peaks(n, frame_s):
            t = round(float(p * frame_s), 3)
            vel = float(min(1.0, n[p] / own))
            if name == "metal":
                metal.append((t, vel, ring_ratio(metal_act, p, frame_s)))
            elif name == "snare":
                if n[p] < 0.5 * own and (n[p] < 0.35 * win("low", p) or n[p] < 0.35 * win("metal", p)):
                    continue                           # bleed from a loud kick/tom or cymbal
                if vel < 0.3:
                    continue                           # too faint: ghost notes aren't reliable
                hits.append({"time": t, "piece": "snare", "velocity": round(vel, 3),
                             "_vs_metal": n[p] / (win("metal", p) + 1e-9)})
            else:
                low.append((t, vel, win("kick", p) >= win("tom", p), win("kick", p)))

    nexts = [low[k + 1][0] if k + 1 < len(low) else None for k in range(len(low))]
    pitches = [low_pitch(y, t, sr, until=nx) for (t, *_), nx in zip(low, nexts)]
    names = split_low(pitches, [k for _, _, k, _ in low])
    kick_onsets = [ko for (_, _, _, ko), nm in zip(low, names) if nm == "kick"]
    kick_ref = float(np.median(kick_onsets)) if kick_onsets else 0.0
    snare_times = np.array([h["time"] for h in hits if h["piece"] == "snare"])
    for (t, v, _, ko), piece in zip(low, names):
        near_snare = len(snare_times) and np.abs(snare_times - t).min() <= 0.04
        if near_snare and piece != "kick":
            continue                                   # the snare's own body, not a tom
        if near_snare and ko < 0.3 * kick_ref:
            continue                                   # a previous kick still ringing under a snare
        hits.append({"time": t, "piece": piece, "velocity": round(v, 3)})

    kinds = classify_metal([m[0] for m in metal], [m[1] for m in metal], [m[2] for m in metal],
                           beat_s)
    for (t, v, _), piece in zip(metal, kinds):
        hits.append({"time": t, "piece": piece, "velocity": round(v, 3)})

    # a crash's wash overlaps the snare wires: a "snare" on a crash must clearly out-hit it
    crashes = np.array([h["time"] for h in hits if h["piece"] == "crash"])
    kept = []
    for h in hits:
        ratio = h.pop("_vs_metal", None)
        if ratio is not None and len(crashes) and np.abs(crashes - h["time"]).min() <= 0.04 \
                and ratio < 0.65:
            continue
        kept.append(h)
    return sorted(kept, key=lambda h: (h["time"], PIECES.index(h["piece"])))


def write_midi(hits: list[dict], path: Path, tempo: float) -> None:
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo) or 120.0)
    kit = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    for h in hits:
        kit.notes.append(pretty_midi.Note(velocity=int(40 + 87 * h["velocity"]),
                                          pitch=GM[h["piece"]], start=h["time"],
                                          end=h["time"] + 0.1))
    pm.instruments.append(kit)
    path.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(path))


class DrumTranscriber:
    name = "drums-nmf"
    requires = ("stem:drums", "tempo")
    produces = ("drums:hits", "drums:candidates", "notes:drums", "midi:drums")

    def available(self) -> bool:
        return True

    def run(self, song: Song, inputs: dict[str, Any]) -> dict[str, Any]:
        from ..audio_io import load
        y = load(inputs["stem:drums"], SR)
        return result_for(song, y, float(inputs["tempo"]))


def result_for(song: Song, y: np.ndarray, tempo: float, suffix: str = "") -> dict[str, Any]:
    from .drum_edit import finalize
    hits = transcribe_drums(y, SR, beat_s=60.0 / tempo if tempo else 0.5)
    midi = song.dir / "transcription" / f"drums{suffix}.mid"
    return {**finalize(song, hits, midi, tempo), "drums:detector": "nmf"}


def transcribe_from_mix(song: Song) -> dict[str, Any]:
    """No drum stem: use the percussive part of the full mix (less accurate)."""
    from ..audio_io import load
    y = load(song.get("audio:mix"), SR)
    _, perc = librosa.effects.hpss(y, margin=2.0)
    return result_for(song, perc, float(song.get("tempo")), suffix="_from_mix")
