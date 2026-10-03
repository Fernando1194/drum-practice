"""Breaking a drum part into the patterns it is made of, and into practice steps.

A song of 120 bars is not 120 things to learn: it is a few grooves, each played many times,
with fills and small variations. This module finds them so the player can say "step 3 of 12:
Groove 1, repetition 5 of 8" instead of "bar 37 of 120".

Patterns. Each bar is a set of marks (piece, 16th slot), weighted by how much they define the
groove: kick and snare 1, toms 0.7, hi-hat and ride 0.5 (timekeeping detail varies most, and
is where the transcription is noisiest). The crash is ignored (an accent on beat 1 doesn't make
a new groove). Distance between bars = weighted Jaccard distance; bars are grouped by
average-linkage clustering, cut at 0.45.

Steps. A phrase is one groove played a number of times, ending with the fill or variation
that closes it: "Groove 1 x7 + fill". A new phrase starts when a different groove starts, or
after a fill once the phrase has at least 4 bars. The same phrase played again right away is
the same step, repeated: "(Groove 2 x3 + fill) x6" is one step.

Measured on MDB Drums (23 real recordings, 680 bars, beats from the annotations), comparing
the grouping made from the AI transcription with the grouping made from the hand annotations
(adjusted Rand index, 1 = identical): the previous exact-match rule (v18) scored 0.47 on the
drum stem and 0.25 on the full mix; this one 0.73 and 0.70. Steps: 113 from the transcription
of the drum stem vs 112 from the annotations (680 bars, about one step per 6 bars). Rock/pop excerpts of 16-20 bars come out as 1-3 steps;
improvised jazz stays fragmented, as it should.
"""
from __future__ import annotations

import numpy as np

# muted hues that stay distinct from each other and readable as a thin stripe on paper
PATTERN_COLORS = ("#3b6fd8", "#d9822b", "#2f9e6e", "#9b59b6", "#c0392b", "#1f9bb0",
                  "#8a6d3b", "#d4589c")
ONE_OFF_COLOR = "#9aa3ae"
WEIGHT = {"kick": 1.0, "snare": 1.0, "tom_high": 0.7, "tom_mid": 0.7, "tom_floor": 0.7,
          "hihat": 0.5, "ride": 0.5}
GROUP = {"tom_high": "tom", "tom_mid": "tom", "tom_floor": "tom"}   # which tom is a pitch guess
CUT = 0.45          # clustering threshold (weighted Jaccard distance)
MAIN_MIN = 3        # a pattern played this many times is a groove a step can be built on
PHRASE_MIN = 4      # a fill ends a step once the step has this many bars


def _marks(grid: list[list[str]], rows: list[str], weight: dict = WEIGHT) -> dict:
    return {(GROUP.get(rows[r], rows[r]), s): weight[rows[r]]
            for r, row in enumerate(grid) if rows[r] in weight
            for s, c in enumerate(row) if c}


def distance(a: dict, b: dict) -> float:
    keys = a.keys() | b.keys()
    if not keys:
        return 0.0
    inter = sum(min(a.get(k, 0.0), b.get(k, 0.0)) for k in keys)
    union = sum(max(a.get(k, 0.0), b.get(k, 0.0)) for k in keys)
    return 1.0 - inter / union


def _clusters(marks: list[dict]) -> list[list[int]]:
    idx = [i for i, m in enumerate(marks) if m]
    if len(idx) < 2:
        return [[i] for i in idx]
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    n = len(idx)
    D = np.zeros((n, n))
    for a in range(n):
        for b in range(a + 1, n):
            D[a, b] = D[b, a] = distance(marks[idx[a]], marks[idx[b]])
    lab = fcluster(linkage(squareform(D, checks=False), method="average"), t=CUT,
                   criterion="distance")
    groups: dict[int, list[int]] = {}
    for k, l in zip(idx, lab):
        groups.setdefault(int(l), []).append(k)
    return sorted(groups.values(), key=min)


def bar_patterns(rows: list[str], grids: list[list[list[str]]]) -> dict:
    """Returns {"ids": pattern index per bar (-1 = empty bar, -2 = played once),
                "patterns": [{"n", "bars", "rep", "color", "fill"}...] in order of first use,
                "steps": [{"pattern", "bars", "phrase", "reps", "times"}...]}.
    A step plays `times` phrases of `phrase` bars, each with `reps` bars of the groove.
    rep = the most typical bar of the pattern (closest to all others): the one to practice."""
    marks = [_marks(g, rows) for g in grids]
    ids = [-1] * len(grids)
    patterns = []
    for members in _clusters(marks):
        if len(members) < 2:
            ids[members[0]] = -2
            continue
        rep = min(members, key=lambda b: sum(distance(marks[b], marks[k]) for k in members))
        toms = sum(1 for (p, _) in marks[rep] if p == "tom")
        k = len(patterns)
        patterns.append({"n": len(members), "bars": members, "rep": rep,
                         "color": PATTERN_COLORS[k % len(PATTERN_COLORS)], "fill": toms >= 3})
        for b in members:
            ids[b] = k
    # A pattern that (almost) always sits alone between bars of other patterns, like a bar every
    # 4th or 8th bar, is a fill/turnaround even when the toms weren't detected.
    biggest = max((p["n"] for p in patterns), default=0)
    for k, p in enumerate(patterns):
        alone = sum(1 for b in p["bars"]
                    if (b == 0 or ids[b - 1] != k) and (b + 1 >= len(ids) or ids[b + 1] != k))
        if alone >= 0.75 * p["n"] and p["n"] < biggest:
            p["fill"] = True
    return {"ids": ids, "patterns": patterns, "steps": steps(ids, patterns),
            "one_off_color": ONE_OFF_COLOR}


def steps(ids: list[int], patterns: list[dict]) -> list[dict]:
    """Practice steps: one groove repeated, plus the fill/variation that closes the phrase."""
    main = {k for k, p in enumerate(patterns) if p["n"] >= MAIN_MIN and not p["fill"]}
    out: list[dict] = []
    cur = None
    for b, k in enumerate(ids):
        if cur is None:
            cur = {"pattern": k if k in main else None, "bars": [b]}
            out.append(cur)
        elif k in main and k != cur["pattern"]:
            if cur["pattern"] is None:            # intro bars before the first groove
                cur["pattern"] = k
                cur["bars"].append(b)
            else:
                cur = {"pattern": k, "bars": [b]}
                out.append(cur)
        else:
            cur["bars"].append(b)
            if k not in main and k != -1 and len(cur["bars"]) >= PHRASE_MIN:
                cur = None                        # the phrase ended with a fill
    for s in out:
        s["reps"] = sum(1 for b in s["bars"] if s["pattern"] is not None and ids[b] == s["pattern"])
    # the same phrase played again right away (same groove, same length) is the same step,
    # repeated: "(Groove 2 x3 + fill) x6" is one thing to learn, not six
    merged: list[dict] = []
    for ph in out:
        last = merged[-1] if merged else None
        if (last and ph["pattern"] is not None and last["pattern"] == ph["pattern"]
                and last["phrase"] == len(ph["bars"]) and last["reps"] == ph["reps"]):
            last["bars"] += ph["bars"]
            last["times"] += 1
        else:
            merged.append({"pattern": ph["pattern"], "bars": list(ph["bars"]),
                           "phrase": len(ph["bars"]), "reps": ph["reps"], "times": 1})
    return merged


FIG_WEIGHT = {**WEIGHT, "crash": 0.5}   # within one beat, "kick + crash" is its own thing to play
FIG_CUT = 0.3       # beat figures: clustering threshold (see beat_figures)
FIG_COLORS = ("#f2c14e", "#6ec1e4", "#f08a5d", "#9bd26a", "#c49bf0", "#f27aa9", "#4fd1b4",
              "#e0a96d")


def beat_figures(rows: list[str], grids: list[list[list[str]]], cut: float = FIG_CUT) -> dict:
    """The same idea one level down: every beat (4 sixteenths) of every bar is grouped with the
    beats that sound like it, so a bar of plain rock reads "A B A B" (A = kick + hi-hat, B =
    snare + hi-hat) and the whole song is a handful of figures repeated.

    Same marks and weights as the bar patterns (toms as one), except that the crash counts
    here: in a bar it is an accent on the same groove, but a beat with kick + crash is a
    different motion from kick + hi-hat. Measured on the
    rock/pop/funk recordings of MDB Drums (beats from the annotations), grouping of the
    transcription vs grouping of the hand annotations (adjusted Rand index): 0.83 on the drum
    stem and 0.72 on the full mix at a cut of 0.3 (0.83 / 0.73 at 0.4, 0.83 / 0.63 at 0.2); about
    7 figures per 30 s excerpt, the six most common covering 95% of the beats. 0.3 rather than 0.4
    because 0.4 merges "kick on the beat" with "kick on the beat and on the &" (distance 0.33),
    a difference the drummer has to play; a missed hi-hat 8th (0.25) still stays in its figure.

    Returns {"ids": [[figure per beat] per bar] (-1 = nothing played, -2 = heard only once),
             "figures": [{"n", "rep": (bar, beat), "letter", "color"}...] most common first}."""
    marks, where = [], []
    for b, g in enumerate(grids):
        n_beats = len(g[0]) // 4 if g and g[0] else 0
        for k in range(n_beats):
            sub = [row[4 * k:4 * k + 4] for row in g]
            marks.append(_marks(sub, rows, FIG_WEIGHT))
            where.append((b, k))
    ids = [[-1] * (len(g[0]) // 4 if g and g[0] else 0) for g in grids]
    if not marks:
        return {"ids": ids, "figures": []}
    from scipy.cluster.hierarchy import fcluster, linkage
    nz = [i for i, m in enumerate(marks) if m]
    # identical beats are the rule: cluster the distinct ones, weighted by how often they occur
    keyed: dict[tuple, list[int]] = {}
    for i in nz:
        keyed.setdefault(tuple(sorted(marks[i].items())), []).append(i)
    uniq = list(keyed.values())
    groups: list[list[int]] = []
    if len(uniq) == 1:
        groups = [uniq[0]]
    elif uniq:
        # average linkage on the full set = UPGMA; repeat each distinct beat by its count would be
        # O(n^2) on a long song, so weight it instead (same result for average linkage)
        lab = _weighted_upgma([marks[u[0]] for u in uniq], [len(u) for u in uniq], cut)
        by: dict[int, list[int]] = {}
        for u, l in zip(uniq, lab):
            by.setdefault(l, []).extend(u)
        groups = list(by.values())
    groups.sort(key=lambda g: (-len(g), min(g)))
    figures = []
    for members in groups:
        if len(members) < 2:
            b, k = where[members[0]]
            ids[b][k] = -2
            continue
        sample = members if len(members) <= 400 else members[::len(members) // 400 + 1]
        rep = min(sample, key=lambda i: sum(distance(marks[i], marks[j]) for j in sample))
        f = len(figures)
        figures.append({"n": len(members), "rep": where[rep],
                        "letter": chr(65 + f) if f < 26 else f"Z{f - 25}",
                        "color": FIG_COLORS[f % len(FIG_COLORS)]})
        for i in members:
            b, k = where[i]
            ids[b][k] = f
    return {"ids": ids, "figures": figures}


def _weighted_upgma(points: list[dict], weights: list[int], cut: float) -> list[int]:
    """Average-linkage clustering where point i stands for weights[i] identical copies, cut at
    `cut`. Returns a cluster label per point. Equivalent to scipy's 'average' on the expanded
    set, without building an n x n matrix for every beat of the song."""
    n = len(points)
    D = np.zeros((n, n))
    for a in range(n):
        for b in range(a + 1, n):
            D[a, b] = D[b, a] = distance(points[a], points[b])
    W = np.asarray(weights, float)
    S = D * np.outer(W, W)                 # sum of distances between members of two clusters
    size = W.copy()
    alive = np.ones(n, bool)
    label = np.arange(n)
    while alive.sum() > 1:
        A = S / np.outer(size, size)
        A[~alive, :] = np.inf
        A[:, ~alive] = np.inf
        np.fill_diagonal(A, np.inf)
        i, j = np.unravel_index(int(np.argmin(A)), A.shape)
        if A[i, j] > cut:
            break
        S[i, :] += S[j, :]
        S[:, i] += S[:, j]
        S[i, i] = 0.0
        size[i] += size[j]
        alive[j] = False
        label[label == j] = i
    return [int(x) for x in label]


def runs(bars: list[int]) -> str:
    """[4, 5, 6, 9] -> '5-7, 10' (1-based bar numbers)."""
    out, start, prev = [], None, None
    for b in sorted(bars):
        if start is None:
            start = prev = b
        elif b == prev + 1:
            prev = b
        else:
            out.append(f"{start + 1}" if start == prev else f"{start + 1}-{prev + 1}")
            start = prev = b
    if start is not None:
        out.append(f"{start + 1}" if start == prev else f"{start + 1}-{prev + 1}")
    return ", ".join(out)
