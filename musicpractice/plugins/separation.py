"""Source separation with Demucs (htdemucs_6s: vocals, drums, bass, guitar, piano, other).

Runs Demucs as a subprocess so torch is never imported into the UI process
and a crash in separation can't take the app down. Demucs is optional: if it
isn't installed, `available()` returns False and the UI hides stem features.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..core.models import Song

STEMS_6 = ("vocals", "drums", "bass", "guitar", "piano", "other")


SEEDED_DEMUCS = ("import random, sys; random.seed(0); "
                 "from demucs.separate import main; main(sys.argv[1:])")


class DemucsSeparator:
    name = "demucs"
    requires = ("audio:mix",)
    produces = tuple(f"stem:{s}" for s in STEMS_6)

    def __init__(self, model: str = "htdemucs_6s", device: str | None = None):
        self.model = model
        self.device = device  # "cuda", "mps" or "cpu"; None lets Demucs choose

    def available(self) -> bool:
        return importlib.util.find_spec("demucs") is not None

    def run(self, song: Song, inputs: dict[str, Any]) -> dict[str, Any]:
        mix = Path(inputs["audio:mix"])
        tmp_out = song.dir / "_demucs"
        # Demucs shifts the audio by a RANDOM offset before separating (its "shifts" trick), so
        # the same song gave slightly different stems on every run, and hits near the detection
        # threshold came and went between uploads. A fixed seed makes it repeatable.
        cmd = [sys.executable, "-c", SEEDED_DEMUCS, "-n", self.model, "-o", str(tmp_out), str(mix)]
        if self.device:
            cmd += ["-d", self.device]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Demucs failed:\n{proc.stderr[-2000:]}")

        produced = list((tmp_out / self.model).glob("*/*.wav"))
        stems_dir = song.dir / "stems"
        stems_dir.mkdir(exist_ok=True)
        result: dict[str, Any] = {}
        for wav in produced:
            dest = stems_dir / wav.name
            shutil.move(str(wav), dest)
            result[f"stem:{wav.stem}"] = str(dest)
        shutil.rmtree(tmp_out, ignore_errors=True)
        return result


# The drums of htdemucs_ft: one model of that bag, trained for drums only. Measured on MDB Drums
# (23 full mixes separated, then ADTOF on the drum stem), F1 vs the htdemucs_6s drum stem:
# kick 0.922 -> 0.931, snare 0.766 -> 0.773, hi-hat 0.860 -> 0.857, cymbals 0.839 -> 0.855,
# toms 0.453 -> 0.462; better on 11 songs, worse on 4. The other five stems still come from
# htdemucs_6s (htdemucs_ft has no guitar or piano).
DRUM_MODEL = "f7e0c4bc"
DRUM_MODEL_NAME = "htdemucs_ft"


def separate_drums_ft(song: Song, device: str | None = None) -> Path | None:
    """Replace stems/drums.wav with the htdemucs_ft drum model's output. Returns the new path,
    or None if it couldn't run (the htdemucs_6s drums stay)."""
    mix = Path(song.get("audio:mix"))
    tmp_out = song.dir / "_demucs_drums"
    cmd = [sys.executable, "-c", SEEDED_DEMUCS, "-n", DRUM_MODEL, "--two-stems", "drums",
           "-o", str(tmp_out), str(mix)]
    if device:
        cmd += ["-d", device]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    found = list((tmp_out / DRUM_MODEL).glob("*/drums.wav"))
    if proc.returncode != 0 or not found:
        shutil.rmtree(tmp_out, ignore_errors=True)
        return None
    dest = song.dir / "stems" / "drums.wav"
    dest.parent.mkdir(exist_ok=True)
    shutil.move(str(found[0]), dest)
    shutil.rmtree(tmp_out, ignore_errors=True)
    for old in (song.dir / "isolated" / "drums.wav", song.dir / "isolated" / "minus_drums.wav"):
        old.unlink(missing_ok=True)      # practice mixes made from the old drum stem
    return dest
