"""Note transcription with Spotify's Basic Pitch (open source, runs on CPU).

Produces, per instrument:
    notes:<instrument>   list of {start, end, pitch (MIDI), name, velocity}
    midi:<instrument>    path to a .mid file you can open in MuseScore, Guitar Pro, a DAW...

Works best on an isolated stem. On a full mix it will pick up every instrument.
Drums are not supported (Basic Pitch transcribes pitched notes only).
"""
from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..core.models import Song

# basic-pitch imports TensorFlow on load if it is installed, even when we run the ONNX
# model, and a TensorFlow built for another NumPy crashes that import. When ONNX Runtime
# is available we hide TensorFlow from it, so reinstalling dependencies can't break this.
if importlib.util.find_spec("onnxruntime") is not None and "tensorflow" not in sys.modules:
    sys.modules["tensorflow"] = None  # type: ignore[assignment]

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Frequency limits cut out-of-range junk (e.g. bass harmonics showing up as guitar notes)
RANGES = {
    "guitar": (75.0, 1400.0),
    "bass": (28.0, 400.0),
    "piano": (27.0, 4200.0),
    "vocals": (80.0, 1100.0),
    "other": (27.0, 4200.0),
}
PITCHED = tuple(RANGES)


def note_name(pitch: int) -> str:
    return f"{NAMES[pitch % 12]}{pitch // 12 - 1}"


@lru_cache(maxsize=1)
def _model():
    """Prefer the ONNX model: lighter than TensorFlow and avoids TF/NumPy version clashes."""
    import basic_pitch
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import Model
    onnx = Path(basic_pitch.__file__).parent / "saved_models" / "icassp_2022" / "nmp.onnx"
    if onnx.exists() and importlib.util.find_spec("onnxruntime") is not None:
        return Model(onnx)
    return Model(ICASSP_2022_MODEL_PATH)


PAD_S = 0.25  # Basic Pitch misses notes that start exactly at 0.0s; pad and shift back


def transcribe_file(audio_path: str, instrument: str, out_midi: Path,
                    onset_threshold: float = 0.5, frame_threshold: float = 0.3,
                    min_note_ms: float = 80.0) -> list[dict[str, Any]]:
    import tempfile

    import librosa
    import numpy as np
    import soundfile as sf
    from basic_pitch.inference import predict

    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        sf.write(tmp.name, np.concatenate([np.zeros(int(PAD_S * sr), np.float32), y]), sr)
        padded = tmp.name
    fmin, fmax = RANGES.get(instrument, (None, None))
    try:
        _, midi, events = predict(
            padded, _model(),
            onset_threshold=onset_threshold, frame_threshold=frame_threshold,
            minimum_note_length=min_note_ms, minimum_frequency=fmin, maximum_frequency=fmax,
            melodia_trick=instrument in ("vocals", "bass"),  # favors one clear line
        )
    finally:
        Path(padded).unlink(missing_ok=True)
    for inst in midi.instruments:
        for n in inst.notes:
            n.start, n.end = max(n.start - PAD_S, 0.0), max(n.end - PAD_S, 0.0)
    out_midi.parent.mkdir(parents=True, exist_ok=True)
    midi.write(str(out_midi))
    notes = [{"start": round(max(float(s) - PAD_S, 0.0), 3),
              "end": round(max(float(e) - PAD_S, 0.0), 3), "pitch": int(p),
              "name": note_name(int(p)), "velocity": round(float(a), 3)}
             for s, e, p, a, *_ in events]
    return sorted(notes, key=lambda n: (n["start"], n["pitch"]))


class BasicPitchTranscriber:
    """One instance per instrument, so the registry can resolve notes:<instrument>."""

    def __init__(self, instrument: str):
        if instrument not in PITCHED:
            raise ValueError(f"{instrument} is not a pitched instrument")
        self.instrument = instrument
        self.name = f"basic-pitch:{instrument}"
        self.requires = (f"stem:{instrument}",)
        self.produces = (f"notes:{instrument}", f"midi:{instrument}")

    def available(self) -> bool:
        return importlib.util.find_spec("basic_pitch") is not None

    def run(self, song: Song, inputs: dict[str, Any]) -> dict[str, Any]:
        midi_path = song.dir / "transcription" / f"{self.instrument}.mid"
        notes = transcribe_file(inputs[f"stem:{self.instrument}"], self.instrument, midi_path)
        return {f"notes:{self.instrument}": notes, f"midi:{self.instrument}": str(midi_path)}
