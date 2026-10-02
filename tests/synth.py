"""Synthetic songs with known ground truth, for tests."""
from __future__ import annotations

import numpy as np
import soundfile as sf

SR = 44100
NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8,
        "A": 9, "A#": 10, "B": 11}


def midi_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def tone(freq: float, dur: float, sr: int = SR, decay: float = 2.0, amp: float = 0.2):
    t = np.arange(int(sr * dur)) / sr
    y = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 5))
    return (amp * y * np.exp(-t * decay)).astype(np.float32)


def chord_notes(label: str) -> list[int]:
    minor = label.endswith("m")
    root = NOTE[label[:-1] if minor else label]
    return [48 + root, 48 + root + (3 if minor else 4), 48 + root + 7]


def make_song(path, form=(("A", 8), ("B", 8), ("A", 8), ("B", 8)),
              progressions=None, bpm=120, sr=SR, stems_dir=None):
    """Each bar holds one chord (strummed on each beat) + bass root + kick.
    Section B also adds a lead melody line so it sounds different in timbre."""
    progressions = progressions or {"A": ["C", "G"], "B": ["Am", "F"]}
    beat = 60 / bpm
    bars = sum(n for _, n in form)
    n = int(sr * beat * 4 * bars) + sr
    parts = {k: np.zeros(n, np.float32) for k in ("guitar", "bass", "drums", "vocals")}
    truth = []
    bar = 0
    for letter, count in form:
        prog = progressions[letter]
        for i in range(count):
            label = prog[i % len(prog)]
            truth.append(label)
            start = int(bar * 4 * beat * sr)
            for b in range(4):
                s = start + int(b * beat * sr)
                for m in chord_notes(label):
                    seg = tone(midi_hz(m), beat, sr)
                    parts["guitar"][s:s + len(seg)] += seg
                kick = tone(55, 0.12, sr, decay=30, amp=0.8)
                parts["drums"][s:s + len(kick)] += kick
            bass = tone(midi_hz(chord_notes(label)[0] - 12), 4 * beat, sr, decay=0.8, amp=0.3)
            parts["bass"][start:start + len(bass)] += bass
            if letter == "B":
                for b in range(4):
                    m = chord_notes(label)[b % 3] + 24
                    seg = tone(midi_hz(m), beat, sr, decay=1.0, amp=0.15)
                    s = start + int(b * beat * sr)
                    parts["vocals"][s:s + len(seg)] += seg
            bar += 1
    mix = sum(parts.values())
    mix = mix / np.abs(mix).max() * 0.9
    sf.write(path, np.stack([mix, mix]).T, sr)
    stems = {}
    if stems_dir is not None:
        stems_dir.mkdir(parents=True, exist_ok=True)
        for k, y in parts.items():
            p = stems_dir / f"{k}.wav"
            sf.write(p, np.stack([y, y]).T, sr)
            stems[k] = str(p)
    return truth, stems
