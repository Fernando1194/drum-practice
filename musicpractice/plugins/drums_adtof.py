"""Drum transcription with ADTOF (a neural network trained on 359 hours of real music),
via the PyTorch port: https://github.com/xavriley/ADTOF-pytorch  (license CC BY-NC-SA 4.0,
fine for personal use).

ADTOF finds hits in five families: kick, snare, hi-hat, toms, cymbals. We then split:
  toms    -> tom 1 / tom 2 / floor tom by pitch (relative to this kit)
  cymbals -> crash / ride by how long they ring

Benchmark on MDB Drums (23 real recordings, ~8000 hand-annotated hits, 50 ms tolerance), F1:

                 drum stem only     full mix
    ADTOF   kick 0.96 / snare 0.80 / hi-hat 0.86 / cymbals 0.87 / toms 0.32    (stem)
            kick 0.85 / snare 0.70 / hi-hat 0.85 / cymbals 0.83 / toms 0.35    (mix)
    NMF     kick 0.93 / snare 0.59 / hi-hat 0.70 / cymbals 0.10 / toms 0.02    (stem)

Crash vs ride on the same data: 92% of cymbal hits labeled right (always guessing "ride"
gets 88%); 61% of crashes are found, and 68% of "crash" labels are right.
Toms remain the weak spot (see TOM_THRESHOLD).
"""
from __future__ import annotations

import importlib.util
from functools import lru_cache
from typing import Any

import librosa
import numpy as np

from ..core.models import Song
from .drum_edit import CANDIDATE_MIN, DEFAULT_THRESHOLDS, finalize
from .drums import PIECES, SR, low_pitch, split_toms

FPS = 100
ADTOF_FAMILY = {35: "kick", 38: "snare", 42: "hihat", 47: "tom", 49: "cymbal"}
CRASH_RING = 0.3   # both ring windows above this -> crash (tuned on MDB Drums)
# ADTOF's default tom threshold (0.32) finds 2/3 of toms but only 1 in 5 "toms" it reports is
# real (MDB Drums). At 0.5, ~9 in 10 are real and ~40% are found. For learning, a tom that
# lights up wrongly misleads more than one that stays dark. Evidence is thin (90 toms in a
# handful of songs), so this is a judgment call, not a proven optimum.
TOM_THRESHOLD = DEFAULT_THRESHOLDS["toms"]   # 0.5


def adtof_available() -> bool:
    return (importlib.util.find_spec("adtof_pytorch") is not None
            and importlib.util.find_spec("torch") is not None)


@lru_cache(maxsize=1)
def _model():
    import adtof_pytorch as A
    model = A.create_frame_rnn_model(A.calculate_n_bins())
    model.eval()
    return A.load_pytorch_weights(model, A.get_default_weights_path(), strict=False)


def detect(path: str) -> tuple[dict[int, list[tuple[float, float]]], np.ndarray]:
    """Run ADTOF: {GM-ish label: [(time, score)]} and frame activations (time, 5) at 100 fps.

    Peaks are picked with a very low threshold and each keeps its score (the value ADTOF's
    peak picker compares with its threshold), so the sensitivity sliders can choose later.
    Filtering these at ADTOF's thresholds gives exactly ADTOF's own output (checked on all
    23 MDB Drums tracks: 0 differences in 7,716 hits)."""
    import adtof_pytorch as A
    import torch
    from ..audio_io import as_wav
    with as_wav(path) as wav:
        x = A.load_audio_for_model(str(wav))
    with torch.no_grad():
        pred = _model()(x).numpy()[0]
    picker = A.PeakPicker(thresholds=CANDIDATE_MIN, fps=FPS)
    proc = picker.processors          # one processor shared by all labels
    w_l, w_r = int(round(proc.pre_avg * FPS)), int(round(proc.post_avg * FPS))
    peaks = {}
    for i, lab in enumerate(A.LABELS_5):
        act = pred[:, i]
        score = np.maximum(0.0, act - proc._moving_average(act, w_l, w_r))
        peaks[lab] = [(t, float(score[min(int(round(t * FPS)), len(score) - 1)]))
                      for t, _ in proc.process(act)]
    return peaks, pred


def cymbal_rings(y: np.ndarray, times: list[float], sr: int = SR, hop: int = 256) -> list[tuple[float, float]]:
    """High-band (>5 kHz) energy left 70-110 ms and 200-300 ms after each hit, relative to the
    attack, minus what was already ringing before it. Crashes keep ringing, rides don't."""
    if not times:
        return []
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop)) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=2048)
    hi = S[f > 5000].sum(axis=0)
    fs = hop / sr
    out = []
    for t in times:
        i = int(round(t / fs))
        pre = float(hi[max(0, i - 4):i].mean()) if i > 0 else 0.0
        peak = float(hi[i:i + 5].max()) if i < len(hi) else 0.0
        vals = []
        for a, b in ((0.07, 0.11), (0.2, 0.3)):
            seg = hi[i + int(a / fs):i + int(b / fs)]
            vals.append(float((seg.mean() - pre) / (peak - pre + 1e-9)) if len(seg) else 0.0)
        out.append((vals[0], vals[1]))
    return out


def transcribe_adtof(audio_path: str) -> list[dict[str, Any]]:
    """Candidate hits, each with a score; drum_edit.select() turns them into hits."""
    peaks, act = detect(audio_path)
    from ..audio_io import load
    y = load(audio_path, SR)
    col = {lab: k for k, lab in enumerate((35, 38, 47, 42, 49))}

    def vel(lab, t):
        i = min(int(round(t * FPS)), len(act) - 1)
        a = float(act[max(0, i - 2):i + 3, col[lab]].max())
        return round(min(1.0, max(0.05, a)), 3)

    def hit(t, piece, lab, s):
        return {"time": round(float(t), 3), "piece": piece, "velocity": vel(lab, t),
                "score": round(s, 4)}

    hits: list[dict[str, Any]] = []
    for lab in (35, 38, 42):
        hits += [hit(t, ADTOF_FAMILY[lab], lab, s) for t, s in peaks.get(lab, [])]

    toms = sorted(peaks.get(47, []))
    nexts = [t for t, _ in toms[1:]] + [None]
    pitches = [low_pitch(y, t, SR, until=n) for (t, _), n in zip(toms, nexts)]
    # which tom is which is decided by the confident hits, so lowering the tom threshold
    # doesn't relabel the toms you already see
    sure = [p for p, (_, s) in zip(pitches, toms) if s >= TOM_THRESHOLD]
    names = split_toms(pitches, ref=sure or None)
    hits += [hit(t, p, 47, s) for (t, s), p in zip(toms, names)]

    cym = sorted(peaks.get(49, []))
    for (t, s), (r1, r2) in zip(cym, cymbal_rings(y, [t for t, _ in cym])):
        piece = "crash" if r1 >= CRASH_RING and r2 >= CRASH_RING else "ride"
        hits.append(hit(t, piece, 49, s))
    return sorted(hits, key=lambda h: (h["time"], PIECES.index(h["piece"])))


def result_for_path(song: Song, audio_path: str, tempo: float, suffix: str = "") -> dict[str, Any]:
    cands = transcribe_adtof(audio_path)
    from . import drum_echo
    drum_echo.mark(cands, audio_path, DEFAULT_THRESHOLDS)
    song.artifacts["drums:echo"] = drum_echo.VERSION
    midi = song.dir / "transcription" / f"drums{suffix}.mid"
    return {**finalize(song, cands, midi, tempo), "drums:detector": "adtof"}


class ADTOFDrumTranscriber:
    name = "drums-adtof"
    requires = ("stem:drums", "tempo")
    produces = ("drums:hits", "drums:candidates", "notes:drums", "midi:drums")

    def available(self) -> bool:
        return adtof_available()

    def run(self, song: Song, inputs: dict[str, Any]) -> dict[str, Any]:
        return result_for_path(song, inputs["stem:drums"], float(inputs["tempo"]))
