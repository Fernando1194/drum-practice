"""The same sound reported twice: a kick that the model also calls a weak snare or hi-hat.

Found on a real song (Audioslave, "Gasoline"): the intro is one kick sound repeated, yet some of
those kicks also came out as a snare or hi-hat, barely over the threshold. Their sound was
indistinguishable from the kicks that came out alone (within +-2 dB in every band). A snare or
a hi-hat played on top of a kick adds energy where they live (snare wires 2-6 kHz, hi-hat
above 6 kHz); an echo of the kick doesn't.

Rule: a weak snare/hi-hat (score under 2x its threshold) that sits on a kick (within 30 ms) is
an echo when, in its band, it is no louder than the kicks heard alone nearby (8 nearest; less
than max(3 dB, 2 robust standard deviations) above their median).

On MDB Drums it is neutral (drum stem: hi-hat F1 0.862 -> 0.869, snare 0.797 -> 0.796; full
mix: 0.849 -> 0.842 and 0.701 -> 0.698): that data rarely has the problem. Applied to toms and
cymbals it hurt (cymbals 0.836 -> 0.799 on the mix), so it is limited to snare and hi-hat.
"""
from __future__ import annotations

import numpy as np

BANDS = {"snare": (2000.0, 6000.0), "hihat": (6000.0, 16000.0)}
WEAK = 2.0          # score / threshold below this counts as weak
TOGETHER_S = 0.03
NEAR = 8
MIN_DB = 3.0
VERSION = 1


def mark(candidates: list[dict], audio_path: str, thresholds: dict[str, float]) -> int:
    """Set "echo": True on candidate snare/hi-hat hits that are echoes of a kick. Returns how
    many were marked. Judged on the hits at the given thresholds."""
    import librosa

    from .drum_edit import FAMILY_OF, select
    for c in candidates:
        c.pop("echo", None)
    hits = select(candidates, thresholds)
    kicks = np.array([h["time"] for h in hits if h["piece"] == "kick"])
    others = np.array([h["time"] for h in hits if h["piece"] != "kick"])
    if len(kicks) < 4:
        return 0
    alone = np.array([t for t in kicks if not len(others) or np.min(np.abs(others - t)) > 0.05])
    if len(alone) < 4:
        return 0
    sus = [h for h in hits if h["piece"] in BANDS and h.get("score") is not None
           and h["score"] / thresholds[FAMILY_OF[h["piece"]]] < WEAK
           and np.min(np.abs(kicks - h["time"])) <= TOGETHER_S]
    if not sus:
        return 0
    y, sr = librosa.load(str(audio_path), sr=44100, mono=True)
    hop = 256
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop)) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=2048)

    def band_db(t, lo, hi):
        i = int(t * sr / hop)
        return 10 * np.log10(S[(f >= lo) & (f < hi), i:i + 8].mean(axis=1).sum() + 1e-12)

    n = 0
    for h in sus:
        lo, hi = BANDS[h["piece"]]
        refs = alone[np.argsort(np.abs(alone - h["time"]))[:NEAR]]
        r = np.array([band_db(t, lo, hi) for t in refs])
        med = float(np.median(r))
        spread = float(np.median(np.abs(r - med)) * 1.4826)
        if band_db(h["time"], lo, hi) - med < max(MIN_DB, 2 * spread):
            h["echo"] = True              # h is the candidate dict itself (select keeps them)
            n += 1
    return n
