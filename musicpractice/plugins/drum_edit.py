"""Fixing a drum transcription by hand: per-family sensitivity and cell edits.

The detector keeps every hit it is even slightly unsure about ("candidates", each with a
score). What the player shows is

    hits = candidates with score >= threshold of their family   (sensitivity sliders)
           - hits you removed  + hits you added                  (edit mode)

so moving a slider never throws away your edits, and your edits never need the model again.

Why sliders help only sometimes (MDB Drums, 23 real recordings, drum stem): picking the best
threshold per song raises F1 by a median of ~0.01 per piece, but by 0.1-0.3 on a few songs
(e.g. one song's hi-hat went from bad to good). Most errors are hits the model never sees
at all, which only edit mode fixes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .drums import GM, LABELS, PIECES, write_midi

FAMILIES = ("kick", "snare", "hihat", "toms", "cymbals")
FAMILY_LABEL = {"kick": "Kick", "snare": "Snare", "hihat": "Hi-hat", "toms": "Toms",
                "cymbals": "Cymbals (crash/ride)"}
FAMILY_OF = {"kick": "kick", "snare": "snare", "hihat": "hihat", "crash": "cymbals",
             "ride": "cymbals", "tom_high": "toms", "tom_mid": "toms", "tom_floor": "toms"}
# ADTOF's own thresholds, toms raised to 0.5 (see drums_adtof.TOM_THRESHOLD)
DEFAULT_THRESHOLDS = {"kick": 0.22, "snare": 0.24, "hihat": 0.22, "toms": 0.5, "cymbals": 0.3}
CANDIDATE_MIN = 0.06    # lowest score kept as a candidate (= sensitivity 100)
STRICTEST = 0.8         # threshold at sensitivity 0
MATCH_S = 0.035         # an edit refers to a hit within this many seconds


def threshold_for(family: str, sensitivity: float) -> float:
    """Sensitivity 0-100 -> score threshold; 50 is the default, higher finds more hits."""
    d = DEFAULT_THRESHOLDS[family]
    s = min(max(float(sensitivity), 0.0), 100.0)
    if s <= 50:
        return round(STRICTEST - (STRICTEST - d) * s / 50, 4)
    return round(d - (d - CANDIDATE_MIN) * (s - 50) / 50, 4)


def sensitivity_for(family: str, threshold: float) -> float:
    d = DEFAULT_THRESHOLDS[family]
    t = float(threshold)
    if t >= d:
        return round(50 * (STRICTEST - t) / (STRICTEST - d), 1)
    return round(50 + 50 * (d - t) / (d - CANDIDATE_MIN), 1)


def thresholds_of(song) -> dict[str, float]:
    return {**DEFAULT_THRESHOLDS, **(song.artifacts.get("drums:thresholds") or {})}


def edits_of(song) -> dict[str, list]:
    e = song.artifacts.get("drums:edits") or {}
    return {"add": list(e.get("add", [])), "remove": list(e.get("remove", []))}


def select(candidates: list[dict], thresholds: dict[str, float]) -> list[dict]:
    """Candidates without a score (basic detector) always pass."""
    return [h for h in candidates
            if h.get("score") is None or h["score"] >= thresholds[FAMILY_OF[h["piece"]]]]


HANDS_AT_ONCE = 2       # a drummer has two hands (the feet play kick and hi-hat pedal)
TOGETHER_S = 0.03       # hits closer than this count as played at the same time


def playable(hits: list[dict], thresholds: dict[str, float]) -> tuple[list[dict], int]:
    """Drop hits no drummer could play: 3+ different hand pieces at the same moment.

    The weakest hit (score relative to its family's threshold) goes first. The hi-hat is
    kept, because a hi-hat "hit" can be the foot pedal, which leaves both hands free; except
    when a ride or crash sounds at the same instant: then it's usually one cymbal reported
    twice, and the weaker of the two goes. (MDB Drums: in every such group the hi-hat was the real
    one, and "drop the weaker" picked right 13 times out of 16.) Hits without a score (basic detector) are kept. On MDB Drums every such group the
    detector produced was wrong (16 of 16, none truly needed 3 hands); they are rare there
    but show up more on separated stems with bleed. Returns (hits, how many were dropped)."""
    out, drop, i = sorted(hits, key=lambda h: h["time"]), set(), 0
    while i < len(out):
        j = i
        while j + 1 < len(out) and out[j + 1]["time"] - out[i]["time"] <= TOGETHER_S:
            j += 1
        group = [k for k in range(i, j + 1) if out[k]["piece"] not in ("kick",)]
        while len({out[k]["piece"] for k in group}) > HANDS_AT_ONCE:
            pieces = {out[k]["piece"] for k in group}
            if "hihat" in pieces and pieces & {"ride", "crash"}:
                # hi-hat AND ride/crash at the same instant, plus another hand: most likely one
                # cymbal heard twice (the model splits it), so the weaker of the two goes
                weak = [k for k in group if out[k]["piece"] in ("hihat", "ride", "crash")
                        and out[k].get("score") is not None]
            else:
                weak = [k for k in group if out[k]["piece"] != "hihat" and out[k].get("score") is not None]
            if not weak:
                break
            k = min(weak, key=lambda k: out[k]["score"] / thresholds[FAMILY_OF[out[k]["piece"]]])
            drop.add(k); group.remove(k)
        i = j + 1
    return [h for k, h in enumerate(out) if k not in drop], len(drop)


def _near(h: dict, t: float, piece: str) -> bool:
    return h["piece"] == piece and abs(h["time"] - t) <= MATCH_S


def apply_edits(hits: list[dict], edits: dict[str, list]) -> list[dict]:
    out = [h for h in hits if not any(_near(h, t, p) for t, p in edits.get("remove", []))]
    for t, p in edits.get("add", []):
        if p in GM and not any(_near(h, t, p) for h in out):
            out.append({"time": round(float(t), 3), "piece": p, "velocity": 0.8, "edited": True})
    return sorted(out, key=lambda h: (h["time"], PIECES.index(h["piece"])))


def merge_edits(old: dict[str, list], add: list, remove: list) -> dict[str, list]:
    """Fold a batch of UI changes into the stored edits. Removing a hit you added earlier
    just forgets the add (and vice versa), so the lists don't grow with back-and-forth."""
    adds, rems = list(old.get("add", [])), list(old.get("remove", []))
    for t, p in remove:
        undo = [a for a in adds if a[1] == p and abs(a[0] - t) <= MATCH_S]
        if undo:
            adds = [a for a in adds if a not in undo]
        elif not any(r[1] == p and abs(r[0] - t) <= MATCH_S for r in rems):
            rems.append([round(float(t), 3), p])
    for t, p in add:
        undo = [r for r in rems if r[1] == p and abs(r[0] - t) <= MATCH_S]
        if undo:
            rems = [r for r in rems if r not in undo]
        elif not any(a[1] == p and abs(a[0] - t) <= MATCH_S for a in adds):
            adds.append([round(float(t), 3), p])
    return {"add": adds, "remove": rems}


def finalize(song, candidates: list[dict], midi_path: Path | str, tempo: float,
             thresholds: dict[str, float] | None = None,
             edits: dict[str, list] | None = None) -> dict[str, Any]:
    """Candidates -> the artifacts everything else reads (grid, kit, highway, MIDI, export)."""
    thr = thresholds or thresholds_of(song)
    sel = select(candidates, thr)
    no_echo = [h for h in sel if not h.get("echo")]      # a kick reported twice (drum_echo.py)
    song.artifacts["drums:echo_dropped"] = len(sel) - len(no_echo)
    kept, n_drop = playable(no_echo, thr)
    song.artifacts["drums:unplayable_dropped"] = n_drop
    hits = apply_edits(kept, edits if edits is not None else edits_of(song))
    hits = [{k: v for k, v in h.items() if k not in ("score", "echo")} for h in hits]
    write_midi(hits, Path(midi_path), tempo)
    notes = [{"start": h["time"], "end": round(h["time"] + 0.1, 3), "pitch": GM[h["piece"]],
              "name": LABELS[h["piece"]], "velocity": h["velocity"]} for h in hits]
    return {"drums:candidates": candidates, "drums:hits": hits, "notes:drums": notes,
            "midi:drums": str(midi_path)}


def rebuild(song, thresholds: dict[str, float] | None = None,
            edits: dict[str, list] | None = None) -> list[dict]:
    """Store new settings and recompute hits from the stored candidates (no model run)."""
    if thresholds is not None:
        song.artifacts["drums:thresholds"] = {f: float(thresholds[f]) for f in FAMILIES}
    if edits is not None:
        song.artifacts["drums:edits"] = edits
    cands = song.artifacts.get("drums:candidates")
    if cands is None:          # transcribed before candidates existed: edits still work
        cands = [h for h in song.get("drums:hits") if not h.get("edited")]
        song.artifacts["drums:candidates"] = cands
    midi = song.artifacts.get("midi:drums") or str(song.dir / "transcription" / "drums.mid")
    song.artifacts.update(finalize(song, cands, midi, float(song.get("tempo"))))
    song.save()
    return song.get("drums:hits")


def counts(song, thresholds: dict[str, float]) -> dict[str, tuple[int, int]]:
    """{family: (hits at these thresholds, hits at the defaults)}, before edits."""
    cands = song.artifacts.get("drums:candidates") or []
    now, dflt = select(cands, thresholds), select(cands, DEFAULT_THRESHOLDS)
    return {f: (sum(FAMILY_OF[h["piece"]] == f for h in now),
                sum(FAMILY_OF[h["piece"]] == f for h in dflt)) for f in FAMILIES}
