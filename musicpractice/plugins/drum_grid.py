"""A bar grid that follows the drummer.

The beat tracker (librosa) works from the whole mix and knows nothing about drums. Measured on
MDB Drums (14 rock/pop/funk recordings with hand-annotated beats), its grid had three problems
that make the same groove come out drawn differently from song to song and upload to upload:

  1. beats ~40 ms late (median, every song), so hits on the beat sat a third of a 16th early
     and flipped between two cells;
  2. bar line on the wrong beat in 4 of 14 songs (beat 2 or 4 drawn as beat 1);
  3. half speed in 1 of 14 (the backbeat landed on every beat), like the "43 BPM" song.

Once the drums are transcribed we know where the drummer actually plays, so:

  * speed: if the snare is in (almost) every beat, always at the same spot, and the kick at the
    other spot (on the beat / exactly between two), the tracker is at half speed: put a beat
    between every two;
  * timing: smooth the beat times (they jitter on a 23 ms frame grid), then shift each beat by
    the median distance of nearby kick/snare/hat hits to their nearest 16th (at most 45% of a
    16th), so the grid sits where the drummer is;
  * bar line: the kick is on 1 and 3 and the snare on 2 and 4; that decides which pair of beats
    can be beat 1, and the chord-change guess from before picks between the two (crash and
    kick strength if it can't).

Result on those 14 recordings, comparing the drum grid with one built from the hand
annotations (Jaccard of the marks, 1 = identical): 0.50 before, 0.71 after; bar lines on the
right beat: 65% -> 88% of bars. The same song re-uploaded shifted by 30 ms and 3 dB quieter
gives the same grid 91% of the time (90% before). Not fixed: songs the tracker gets badly
wrong (one at 1.6x the tempo), shuffle/triplet feels (the grid is 16ths).
"""
from __future__ import annotations

import numpy as np

VERSION = 1
WEIGHT = {"kick": 1.0, "snare": 1.0, "hihat": 0.6, "ride": 0.6}


def beat_pos(beats, times) -> np.ndarray:
    """Times -> position in beats (2.5 = halfway between beat 2 and beat 3, 0-based)."""
    b = np.asarray(beats, float)
    ibi = float(np.median(np.diff(b)))
    g = np.concatenate([b, [b[-1] + ibi]])
    t = np.asarray(times, float)
    j = np.clip(np.searchsorted(g, t, side="right") - 1, 0, len(g) - 2)
    return j + (t - g[j]) / (g[j + 1] - g[j])


def _times(hits, piece):
    return np.array([h["time"] for h in hits if h["piece"] == piece])


def is_half_speed(beats, hits) -> bool:
    """At half speed every tracker beat holds a whole backbeat cycle: the snare falls on
    (almost) every beat, always at the same place (on the beat or exactly between two), and the
    kick sits at the other place."""
    sn, kk = _times(hits, "snare"), _times(hits, "kick")
    if len(sn) < 8 or len(kk) < 4:
        return False
    ps, pk = beat_pos(beats, sn), beat_pos(beats, kk)
    fs, fk = ps - np.floor(ps), pk - np.floor(pk)
    for snare_at, kick_at in ((0.0, 0.5), (0.5, 0.0)):
        on = np.abs(((fs - snare_at + 0.5) % 1.0) - 0.5) < 0.15
        if on.mean() < 0.6:
            continue
        beats_with_snare = len(set(np.floor(ps[on] + 0.5 - snare_at).astype(int))) / len(beats)
        kick_other = np.mean(np.abs(((fk - kick_at + 0.5) % 1.0) - 0.5) < 0.15)
        if beats_with_snare > 0.6 and kick_other > 0.25:
            return True
    return False


def align(beats, hits, smooth: int = 4, window: float = 2.0, max_frac: float = 0.45) -> np.ndarray:
    b = np.asarray(beats, float)
    n = len(b)
    if n < 8:
        return b
    sm = b.copy()
    for i in range(n):                      # local straight line through +-4 beats
        a, e = max(0, i - smooth), min(n, i + smooth + 1)
        k = np.arange(a, e)
        p = np.polyfit(k, b[a:e], 1)
        sm[i] = p[0] * i + p[1]
    sel = [h for h in hits if h["piece"] in WEIGHT]
    ht = np.array([h["time"] for h in sel])
    hw = np.array([WEIGHT[h["piece"]] for h in sel])
    out = sm.copy()
    for i in range(n):
        lo_i, hi_i = max(i - 1, 0), min(i + 1, n - 1)
        ibi = (sm[hi_i] - sm[lo_i]) / max(hi_i - lo_i, 1)
        s16 = ibi / 4
        m = (ht >= sm[i] - window * ibi) & (ht <= sm[i] + window * ibi)
        if m.sum() < 3:
            continue
        rel = (ht[m] - sm[i]) / s16
        res = (rel - np.round(rel)) * s16
        order = np.argsort(res)
        cw = np.cumsum(hw[m][order])
        med = res[order][np.searchsorted(cw, cw[-1] / 2)]          # weighted median
        out[i] = sm[i] + np.clip(med, -max_frac * s16, max_frac * s16)
    return out


def phase_scores(beats, hits, bpb: int = 4) -> np.ndarray:
    pos = {p: beat_pos(beats, _times(hits, p)) for p in ("kick", "snare", "crash")}
    sc = np.zeros(bpb)
    for ph in range(bpb):
        for piece, w, on in (("kick", 1.0, (0, 2)), ("snare", 1.0, (1, 3)), ("crash", 1.5, (0,))):
            x = pos[piece]
            if not len(x):
                continue
            near = np.abs(x - np.round(x)) < 0.15
            k = (np.round(x[near]).astype(int) - ph) % bpb
            if not len(k):
                continue
            sc[ph] += w * np.isin(k, on).mean()
            if piece != "crash":
                sc[ph] -= 0.5 * w * np.isin(k, [(q + 1) % bpb for q in on]).mean()
    return sc


def choose_phase(beats, hits, harmonic_bars, bpb: int = 4) -> int:
    sc = phase_scores(beats, hits, bpb)
    half = bpb // 2
    pair = max(range(half), key=lambda k: sc[k] + sc[k + half])
    cands = [pair, pair + half]
    if harmonic_bars is not None and len(harmonic_bars):
        b = np.asarray(beats)
        idx = [int(np.argmin(np.abs(b - t))) % bpb for t in harmonic_bars]
        votes = {c: idx.count(c) for c in cands}
        if votes[cands[0]] != votes[cands[1]]:
            return max(cands, key=lambda c: votes[c])
    return max(cands, key=lambda c: sc[c])


def refine(beats, bars, hits, bpb: int = 4) -> dict:
    """New beats/bars/tempo from the drum hits. `bars` = the tracker's bar lines (its chord-
    change guess is kept as the tie-breaker for beat 1)."""
    b = np.asarray(beats, float)
    if len(b) < 8 or len([h for h in hits if h["piece"] in WEIGHT]) < 8:
        return {"beats": list(beats), "bars": list(bars), "doubled": False, "changed": False}
    doubled = is_half_speed(b, hits)
    if doubled:
        b = np.sort(np.concatenate([b, (b[:-1] + b[1:]) / 2]))
    b = align(b, hits)
    ph = choose_phase(b, hits, bars, bpb)
    new_bars = list(b[ph::bpb])
    if ph and new_bars and new_bars[0] > 0.05:      # pickup beats before the first bar line
        new_bars = [0.0] + new_bars
    while len(new_bars) > 1 and new_bars[-1] >= b[-1] - 1e-6:   # no bar that starts on the last beat
        new_bars = new_bars[:-1]
    b = np.maximum(b, 0.0)
    return {"beats": [round(float(x), 3) for x in b], "bars": [round(float(x), 3) for x in new_bars],
            "tempo": round(60.0 / float(np.median(np.diff(b))), 1), "doubled": doubled, "changed": True}
