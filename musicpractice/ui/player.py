"""Synced practice player: audio + chords + tab that follow playback.

Gradio's audio component doesn't tell Python where playback is, so the player is plain
HTML + JavaScript. Python renders the markup and embeds the analysis as JSON in a data
attribute; one global script (PLAYER_JS, injected in the page <head>) wires up every
player that appears. Speed and looping happen live in the browser (HTML5 playbackRate
with pitch preserved), so nothing has to be re-rendered while practicing.
"""
from __future__ import annotations

import html
import json
import subprocess
import uuid
from pathlib import Path

from ..core.models import Song
from ..plugins import drum_edit as DE
from ..plugins.drum_tab import drum_bar_grids, hit_cells, pieces_used
from ..plugins.drums import COLORS, CYMBALS, LABELS, PIECES, SHORT
from ..plugins.patterns import bar_patterns, beat_figures, runs
from ..plugins.tab import TUNINGS, tab_bar_grids
from .kit import kit_svg

ASSETS = Path(__file__).parent
PLAYER_JS = "\n".join((ASSETS / f).read_text() for f in ("highway.js", "editor.js", "player.js"))
PLAYER_CSS = (ASSETS / "player.css").read_text()
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:'
         'wght@500;600&family=JetBrains+Mono:wght@500;700&display=swap">')
# .mp-hidden: the channel the player uses to send drum fixes to Python (see app.py)
HEAD = (f"{FONTS}<style>{PLAYER_CSS}\n.mp-hidden {{ display: none !important; }}</style>"
        f"<script>{PLAYER_JS}</script>")


def web_audio(path: str | Path) -> Path:
    """Opus copy of an audio file for in-browser playback (~10x smaller than WAV). The player
    downloads every stem fully into memory, so size matters. After a song is compacted its
    audio already is this copy."""
    from ..core.storage import opus_copy
    return opus_copy(path)


def file_url(path: str | Path) -> str:
    """URL Gradio serves a local file under (the folder must be in launch(allowed_paths))."""
    return f"/gradio_api/file={Path(path).resolve()}"


def section_label(s: dict) -> str:
    hint = f" ({s['hint']})" if s.get("hint") else ""
    return f"Bars {s['start_bar']}-{s['end_bar']}: {s['letter']}{hint}"


def _bar_html(i: int, chords: list[str], grid: list[list[str]] | None,
              names: list[str], slots: int, colors: list[str] | None = None,
              cells: dict | None = None, pieces: list[str] | None = None,
              used: set[str] | None = None, tag: tuple | None = None,
              figs: list | None = None) -> str:
    chord_txt = html.escape(" ".join(c for c in chords if c != "N")) or "&nbsp;"
    # tag = (pattern index, short label, color, title): which repeated pattern this bar plays
    badge = (f'<span class="mp-pat-badge" title="{html.escape(tag[3])}">{tag[1]}</span>'
             if tag else "")
    head = (f'<div class="mp-bar-head"><span class="mp-bar-no">{i + 1}</span>{badge}'
            f'<span class="mp-bar-chords">{chord_txt}</span></div>')
    if grid is None:
        body = '<div class="mp-grid mp-grid--chords"><div class="mp-cursor"></div></div>'
    else:
        # drums: every piece has a row; rows of pieces not played only show while editing
        empty = [bool(pieces) and pieces[r] not in (used or set()) for r in range(len(grid))]
        if colors:
            lab_cls = ' class="mp-lab--empty"'
            labels = "".join(f'<span style="--c:{c}"{lab_cls if e else ""}>'
                             f'<b class="mp-sw"></b>{n}</span>'
                             for n, c, e in zip(names, colors, empty))
        else:
            labels = "".join(f"<span>{n}</span>" for n in names)
        def cell(c, r, slot):
            ks = (cells or {}).get((i, r, slot))
            h = f' data-h="{",".join(map(str, ks))}"' if ks else ""
            if c == "x":
                return f'<i class="mp-x"{h}>x</i>'
            if c == "o":
                return f'<i class="mp-o"{h}>\u25cf</i>'
            return f"<i>{c}</i>" if c else "<i></i>"
        def row_open(r):
            if not colors:
                return '<div class="mp-row">'
            cls = "mp-row mp-row--empty" if empty[r] else "mp-row"
            dp = f' data-p="{pieces[r]}"' if pieces else ""
            return f'<div class="{cls}"{dp} style="--c:{colors[r]}">'
        rows = "".join(
            row_open(r)
            + "".join(cell(c, r, slot) for slot, c in enumerate(row)) + "</div>"
            for r, row in enumerate(grid))
        # figs = per beat (figure index, letter, color, title) or None: the one-beat figure
        figrow = ""
        if figs:
            figrow = ('<div class="mp-figrow" style="--beats:%d">' % len(figs) + "".join(
                f'<b class="mp-fig" data-f="{f[0]}" style="--fc:{f[2]}" title="{html.escape(f[3])}">{f[1]}</b>'
                if f else '<b class="mp-fig mp-fig--rest"></b>' for f in figs) + "</div>")
        body = (f'<div class="mp-tab"><div class="mp-strings">{labels}</div>'
                f'<div class="mp-grid" style="--slots:{len(grid[0])};--beats:{len(grid[0]) // 4}">'
                f'{rows}<div class="mp-cursor"></div></div>{figrow}</div>')
    pat = f' data-pat="{tag[0]}" style="--pc:{tag[2]}"' if tag else ""
    return (f'<div class="mp-bar{" has-pat" if tag else ""}" data-i="{i}"{pat} '
            f'title="Bar {i + 1}: click to jump here">{head}{body}</div>')


STEM_ORDER = ("vocals", "drums", "bass", "guitar", "piano", "other")
STEM_LABEL = {"vocals": "Vocals", "drums": "Drums", "bass": "Bass", "guitar": "Guitar",
              "piano": "Piano", "other": "Other"}


def _mixer_html(stems: list[str], mine: str | None) -> str:
    strips = []
    for name in stems:
        you = '<i class="mp-you">you</i>' if name == mine else ""
        label = STEM_LABEL.get(name, name.title())
        strips.append(
            f'<div class="mp-strip" data-stem="{name}">'
            f'<span class="mp-strip-name">{label}{you}</span>'
            f'<input type="range" min="0" max="100" step="1" value="100" aria-label="{label} volume">'
            f'<input type="number" class="mp-num mp-strip-val" min="0" max="100" step="1" value="100" '
            f'aria-label="{label} volume, type a value">'
            f'<button type="button" class="mp-m" aria-pressed="false" title="Mute {label}">M</button>'
            f'<button type="button" class="mp-s" aria-pressed="false" title="Solo {label} (hear only this)">S</button>'
            f'</div>')
    return ('<details class="mp-mixer" open><summary><b class="mp-mix-title">Mixer</b>'
            '<span class="mp-mix-hint">raise an instrument to follow it, lower it to play over it</span>'
            '<span class="mp-mix-status">(loading tracks...)</span>'
            '<span class="mp-fold-state" aria-hidden="true"></span></summary>'
            '<div class="mp-strips">' + "".join(strips) + '</div></details>')


# lanes left to right as a drummer sees the kit; the kick is a bar across all lanes
HW_LANES = ("hihat", "snare", "tom_high", "tom_mid", "tom_floor", "ride", "crash")
UNRELIABLE = {"tom_high", "tom_mid", "tom_floor"}


def _highway_html(used: list[str]) -> str:
    chips = "".join(
        f'<label class="mp-lane-chip" style="--c:{COLORS[p]}" data-p="{p}">'
        f'<input type="checkbox" class="mp-score-on" data-p="{p}"{"" if p in UNRELIABLE else " checked"}>'
        f'<b class="mp-sw"></b>{LABELS[p]}'
        f'<button type="button" class="mp-learn" data-p="{p}" title="Map a pad: click, then hit the pad">learn</button></label>'
        for p in PIECES if p in used)
    return f"""<div class="mp-hw" hidden>
  <div class="mp-hw-bar">
    <button type="button" class="mp-hw-play" aria-label="Play or pause (space)">Play / pause</button>
    <span class="mp-hw-where"></span>
    <button type="button" class="mp-midi-connect">Connect MIDI drums</button>
    <select class="mp-midi-in" aria-label="MIDI input" hidden></select>
    <span class="mp-midi-status">Not connected</span>
    <span class="mp-midi-mon" title="What your kit sends: note number per pad, and the hi-hat pedal position">Last pad: --</span>
    <button type="button" class="mp-calibrate" title="Hit any pad along with 8 clicks">Calibrate</button>
    <label class="mp-hw-field">Latency <input type="range" class="mp-offset" min="-150" max="150" step="1" value="0">
      <input type="number" class="mp-num" min="-150" max="150" step="1" value="0" aria-label="Latency in ms, type a value">
      <output class="mp-offset-out">ms</output></label>
  </div>
  <div class="mp-hw-bar mp-hw-lanes">
    <span class="mp-hw-k">Scored</span>{chips}
    <label class="mp-check"><input type="checkbox" class="mp-cym-merge" checked> Crash and ride count as one</label>
  </div>
  <div class="mp-hw-main">
    <canvas class="mp-hw-canvas" aria-label="Note highway: notes fall to the line, hit them on your kit"></canvas>
    <aside class="mp-hw-score" aria-live="polite">
      <div class="mp-hw-acc"><b>--</b><span>accuracy</span></div>
      <div class="mp-hw-row"><span>Combo</span><b class="mp-hw-combo">0</b></div>
      <div class="mp-hw-row"><span>Perfect</span><b class="mp-hw-n-perfect">0</b></div>
      <div class="mp-hw-row"><span>Good</span><b class="mp-hw-n-good">0</b></div>
      <div class="mp-hw-row"><span>OK</span><b class="mp-hw-n-ok">0</b></div>
      <div class="mp-hw-row"><span>Missed</span><b class="mp-hw-n-miss">0</b></div>
      <div class="mp-hw-row"><span>Extra hits</span><b class="mp-hw-n-extra">0</b></div>
      <div class="mp-hw-tend">Timing: <b class="mp-hw-mean">--</b></div>
      <ol class="mp-hw-passes" aria-label="Loop passes"></ol>
      <button type="button" class="mp-hw-reset">Reset score</button>
    </aside>
  </div>
  <p class="mp-hw-help">Notes fall toward the line; hit the pad as a note crosses it. The kick is the bar across
  all lanes. Perfect is within 30 ms, Good 60 ms, OK 100 ms (real time, at any speed).</p>
</div>"""


def _patterns(rows, grids, bars, duration) -> tuple[list, str, dict, str]:
    """Per-bar pattern tags + the 'Patterns' strip: one chip per repeated pattern and a band
    with one cell per bar in its pattern's color, so repeats show at a glance."""
    res = bar_patterns(rows, grids)
    n_g = n_f = 0
    names = []
    for p in res["patterns"]:
        if p["fill"]:
            n_f += 1; names.append((f"F{n_f}", f"Fill {n_f}"))
        else:
            n_g += 1; names.append((f"G{n_g}", f"Groove {n_g}"))
    one_offs = [b for b, k in enumerate(res["ids"]) if k == -2]
    tags = []
    for b, k in enumerate(res["ids"]):
        if k >= 0:
            p = res["patterns"][k]
            tags.append((k, names[k][0], p["color"],
                         f'{names[k][1]}: played {p["n"]} times (bars {runs(p["bars"])})'))
        elif k == -2:
            tags.append((-2, "1×", res["one_off_color"], "Played only once in the song"))
        else:
            tags.append(None)
    if not res["patterns"]:
        return tags, "", {}, ""
    chips = "".join(
        f'<span class="mp-pat-chip" data-pat="{k}" style="--pc:{p["color"]}">'
        f'<button type="button" class="mp-pat-show" aria-pressed="false" '
        f'title="Show where {names[k][1]} is played (bars {runs(p["bars"])})">'
        f'<b>{names[k][0]}</b> {names[k][1]} <i>×{p["n"]}</i></button>'
        f'<button type="button" class="mp-pat-loop" data-bar="{p["rep"]}" '
        f'title="Loop bar {p["rep"] + 1}, the most typical {names[k][1]}">loop</button></span>'
        for k, p in enumerate(res["patterns"]))
    if one_offs:
        chips += (f'<span class="mp-pat-chip" data-pat="-2" style="--pc:{res["one_off_color"]}">'
                  f'<button type="button" class="mp-pat-show" aria-pressed="false" '
                  f'title="Bars played only once: {runs(one_offs)}"><b>1×</b> One-offs '
                  f'<i>×{len(one_offs)}</i></button></span>')
    ends = list(bars[1:]) + [duration]
    band = "".join(
        f'<i data-i="{b}"{f" data-pat={chr(34)}{t[0]}{chr(34)}" if t else ""} '
        f'style="flex:{max(e - s, 0.05):.3f};--pc:{t[2] if t else "transparent"}" '
        f'title="Bar {b + 1}{": " + t[3] if t else ""}"></i>'
        for b, (s, e, t) in enumerate(zip(bars, ends, tags)))
    learn, n_steps = len(res["patterns"]), len(res["steps"])
    html_ = (f'<div class="mp-pats"><div class="mp-pats-row"><span class="mp-pats-k">'
             f'{len(grids)} bars = <b>{n_steps} step{"s" if n_steps != 1 else ""}</b> built from '
             f'{learn} pattern{"s" if learn != 1 else ""}</span>{chips}</div>'
             f'<div class="mp-patband" aria-label="Pattern of every bar">{band}</div></div>')

    def step_name(st):
        if st["pattern"] is None:
            return "Intro" if st["bars"][0] == 0 else "Free part"
        nm = names[st["pattern"]][1]
        extra = len(st["bars"]) // st["times"] - st["reps"]
        return f'{nm} ×{st["reps"]}' + (" + fill" if extra == 1 else f" + {extra} bars" if extra else "")
    js = {"ids": res["ids"],
          "pats": [{"short": names[k][0], "name": names[k][1], "color": p["color"], "rep": p["rep"],
                    "fill": p["fill"]} for k, p in enumerate(res["patterns"])],
          "steps": [{"p": -1 if st["pattern"] is None else st["pattern"], "a": st["bars"][0],
                     "b": st["bars"][-1], "phrase": st["phrase"], "reps": st["reps"],
                     "times": st["times"], "name": step_name(st)} for st in res["steps"]]}
    step_chips = ""
    for k, st in enumerate(res["steps"]):
        color = (res["patterns"][st["pattern"]]["color"] if st["pattern"] is not None
                 else res["one_off_color"])
        times = f'<i>×{st["times"]}</i>' if st["times"] > 1 else ""
        step_chips += (f'<button type="button" class="mp-step" data-s="{k}" style="--pc:{color}" '
                       f'title="Bars {st["bars"][0] + 1}-{st["bars"][-1] + 1}: click to go there">'
                       f'<b>{k + 1}</b><span>{html.escape(js["steps"][k]["name"])}</span>{times}</button>')
    steps_html = f"""<div class="mp-steps" hidden>
  <div class="mp-steps-list" aria-label="Practice steps">{step_chips}</div>
  <div class="mp-steps-head">
    <div><span class="mp-steps-no">Step 1 of {n_steps}</span><span class="mp-steps-name"></span></div>
    <div class="mp-steps-count"><span class="mp-steps-rep"></span><span class="mp-steps-dots"></span>
      <span class="mp-steps-pass"></span></div>
  </div>
  <div class="mp-steps-stage mp-sheet mp-sheet--drums" aria-live="off"></div>
  <p class="mp-steps-next"></p>
</div>"""
    return tags, html_, js, steps_html


FIG_PIECES = ("crash", "ride", "hihat", "tom_high", "tom_mid", "tom_floor", "snare", "kick")


def _fig_glyph(cells: list[list[str]], rows: list[str]) -> str:
    """A tiny drawing of a one-beat figure: one line per piece played, 4 sixteenth columns."""
    used = [p for p in FIG_PIECES if p in rows and any(cells[rows.index(p)])]
    if not used:
        return ""
    h = 5 * len(used) + 1
    dots = "".join(
        f'<circle cx="{4 + 8 * s}" cy="{3 + 5 * y}" r="2.2" fill="{COLORS[p]}"/>'
        for y, p in enumerate(used) for s, c in enumerate(cells[rows.index(p)]) if c)
    ticks = "".join(f'<line x1="{4 + 8 * s}" x2="{4 + 8 * s}" y1="0" y2="{h}" stroke="currentColor" '
                    f'stroke-opacity="{.35 if s == 0 else .12}"/>' for s in range(4))
    return (f'<svg class="mp-fig-glyph" viewBox="0 0 32 {h}" width="32" height="{h}" aria-hidden="true">'
            f'{ticks}{dots}</svg>')


def _fig_words(cells: list[list[str]], rows: list[str]) -> str:
    """'kick on 1, hi-hat on 1 and &' for a figure's tooltip."""
    pos = ("the beat", "e", "&", "a")
    parts = []
    for p in FIG_PIECES[::-1]:
        if p in rows:
            on = [pos[s] for s, c in enumerate(cells[rows.index(p)]) if c]
            if on:
                parts.append(f'{LABELS[p].lower()} on {", ".join(on)}')
    return "; ".join(parts) or "rest"


def _figures(rows, grids) -> tuple[list, str]:
    """Per-bar list of beat figures + the 'Beats' strip: one chip per figure (letter, drawing,
    count). Click a chip to see where that figure is played."""
    res = beat_figures(rows, grids)
    figs = res["figures"]
    info = []
    for f in figs:
        b, k = f["rep"]
        cells = [row[4 * k:4 * k + 4] for row in grids[b]]
        info.append((cells, _fig_words(cells, rows)))
    per_bar = []
    for ids in res["ids"]:
        out = []
        for fid in ids:
            if fid >= 0:
                out.append((fid, figs[fid]["letter"], figs[fid]["color"],
                            f'Beat figure {figs[fid]["letter"]}: {info[fid][1]} (played {figs[fid]["n"]} times)'))
            elif fid == -2:
                out.append((-2, "·", "#9aa3ae", "A beat played only once"))
            else:
                out.append(None)
        per_bar.append(out)
    if not figs:
        return per_bar, ""
    total = sum(f["n"] for f in figs)
    top = sum(f["n"] for f in figs[:6])
    chips = "".join(
        f'<button type="button" class="mp-fig-chip" data-f="{k}" aria-pressed="false" style="--fc:{f["color"]}" '
        f'title="{html.escape(info[k][1])}: click to see where it is played">'
        f'<b>{f["letter"]}</b>{_fig_glyph(info[k][0], rows)}<i>×{f["n"]}</i></button>'
        for k, f in enumerate(figs))
    lead = (f'{len(figs)} beat figure{"s" if len(figs) != 1 else ""}'
            + (f', the first 6 are {100 * top / total:.0f}% of the song' if len(figs) > 6 else ""))
    return per_bar, (f'<div class="mp-pats-row mp-figs"><span class="mp-pats-k">{lead}</span>{chips}</div>')


def _fix_data(song: Song) -> dict:
    """What the 'Fix transcription' panel needs: candidates with scores (for live hit counts
    while a slider moves) and the thresholds/edits in force."""
    cands = song.artifacts.get("drums:candidates") or []
    scored = any(h.get("score") is not None for h in cands)
    edits = DE.edits_of(song)
    return {
        "scored": scored,
        "families": list(DE.FAMILIES), "of": DE.FAMILY_OF, "cymbals": sorted(CYMBALS),
        "thr": DE.thresholds_of(song), "defaults": DE.DEFAULT_THRESHOLDS,
        "min": DE.CANDIDATE_MIN, "max": DE.STRICTEST,
        "cands": ([[h["time"], DE.FAMILIES.index(DE.FAMILY_OF[h["piece"]]), h["score"]]
                   for h in cands if h.get("score") is not None] if scored else []),
        "edits": len(edits["add"]) + len(edits["remove"]),
        "dropped": _dropped_by_family(song, cands) if scored else {},
    }


def _dropped_by_family(song: Song, cands: list[dict]) -> dict[str, int]:
    """Hits the 'three hands' rule removes at the saved thresholds, per family, so the live
    counts next to the sliders match what the grid shows."""
    thr = DE.thresholds_of(song)
    sel = DE.select(cands, thr)
    kept, _ = DE.playable(sel, thr)
    out = {f: 0 for f in DE.FAMILIES}
    for h in sel:
        out[DE.FAMILY_OF[h["piece"]]] += 1
    for h in kept:
        out[DE.FAMILY_OF[h["piece"]]] -= 1
    return out


def _fix_html(song: Song) -> str:
    fix = _fix_data(song)
    if fix["scored"]:
        sliders = "".join(
            f'<label class="mp-sens-row" data-f="{f}"><span class="mp-sens-name">{DE.FAMILY_LABEL[f]}</span>'
            f'<span class="mp-sens-k">fewer</span>'
            f'<input type="range" class="mp-sens" data-f="{f}" min="0" max="100" step="1" '
            f'value="{DE.sensitivity_for(f, fix["thr"][f]):.0f}" aria-label="{DE.FAMILY_LABEL[f]} sensitivity">'
            f'<span class="mp-sens-k">more</span>'
            f'<input type="number" class="mp-num" min="0" max="100" step="1" '
            f'value="{DE.sensitivity_for(f, fix["thr"][f]):.0f}" aria-label="{DE.FAMILY_LABEL[f]} sensitivity, type a value">'
            f'<output class="mp-sens-n" data-f="{f}"></output></label>'
            for f in DE.FAMILIES)
        n_echo = song.artifacts.get("drums:echo_dropped") or 0
        n_drop = song.artifacts.get("drums:unplayable_dropped") or 0
        dropped = (f'<p class="mp-fix-help mp-fix-dropped">{n_drop} hit{"s" if n_drop != 1 else ""} '
                   'left out because they would need three hands at once (the weakest one goes; '
                   'the hi-hat stays, it can be the pedal, unless a ride or crash sounds with it: '
                   'then it is one cymbal heard twice). Add one back by hand if it is real.</p>'
                   if n_drop else "")
        if n_echo:
            dropped += (f'<p class="mp-fix-help mp-fix-dropped">{n_echo} snare/hi-hat hit'
                        f'{"s" if n_echo != 1 else ""} left out because they were the kick heard twice '
                        '(on a kick, weak, and sounding exactly like the kicks alone). '
                        'Add one back by hand if it is real.</p>')
        sens = (f'<div class="mp-fix-col"><h4>Sensitivity</h4>{sliders}{dropped}'
                '<p class="mp-fix-help">Hits the AI is missing: move right. Hits that aren\'t '
                'there: move left. The middle is the AI\'s default. Counts update as you drag; '
                'press Save to see the result.</p></div>')
    else:
        sens = ('<div class="mp-fix-col"><h4>Sensitivity</h4><p class="mp-fix-help">Needs the ADTOF '
                'drum model (see README, "Drums"). Editing by hand works without it.</p></div>')
    return f"""<div class="mp-fix" hidden>
  {sens}
  <div class="mp-fix-col"><h4>Edit by hand</h4>
    <p class="mp-fix-help">Click an empty cell in the grid to add a hit, click a mark to remove it.
    To change a piece (snare that should be a tom), remove it and add it on the right row.
    Rows for pieces the AI didn't find are shown while this panel is open.
    Click a changed cell again to undo it.</p>
    <p class="mp-fix-status" aria-live="polite"></p>
    <div class="mp-fix-btns">
      <button type="button" class="mp-fix-save" disabled>Save</button>
      <button type="button" class="mp-fix-discard" disabled>Discard changes</button>
      <button type="button" class="mp-fix-reset" title="Forget all your edits and slider settings">Back to the AI's version</button>
    </div>
  </div>
</div>"""


def build_player(song: Song, instrument: str, sources: list[tuple[str, str, str]],
                 start_source: str | None = None, stems: dict[str, str] | None = None) -> str:
    """sources: (id, label, audio path), used when there are no stems.
    stems: {instrument: path}; when given, the player mixes them live (per-instrument volume)."""
    bars, duration = song.get("bars"), song.get("duration")
    chart = song.get("chord_chart")
    is_drums = instrument == "drums" and song.has("drums:hits")
    drum_hits = song.get("drums:hits") if is_drums else []
    rows, used = None, None
    if is_drums:
        rows, used = list(PIECES), set(pieces_used(drum_hits))
        _, grids = drum_bar_grids(drum_hits, song.get("beats"), bars, duration, rows=rows)
        names = [SHORT[p] for p in rows]
        row_colors = [COLORS[p] for p in rows]
        cells = hit_cells(drum_hits, song.get("beats"), bars, duration, rows=rows)
        has_tab = bool(grids)
        chart = [[] for _ in bars]          # drummers read the grid, not chord names
    else:
        row_colors = None
        cells = None
        has_tab = song.has(f"tab:{instrument}") and instrument in TUNINGS
        grids = (tab_bar_grids(song.get(f"tab:{instrument}"), instrument, song.get("beats"),
                               bars, duration) if has_tab else [])
        names = list(reversed(TUNINGS[instrument]["names"])) if has_tab else []

    stem_names = [n for n in STEM_ORDER if stems and n in stems] + \
        [n for n in (stems or {}) if n not in STEM_ORDER]
    sections = song.get("sections") if song.has("sections") else []
    data = {
        "bars": bars, "duration": duration, "beats": song.get("beats"),
        "tempo": song.get("tempo"), "chords": song.get("chords"),
        "sections": sections,
        "sources": [{"id": i, "label": lab, "url": file_url(p)} for i, lab, p in sources],
        "stems": ([{"name": n, "url": file_url(web_audio(stems[n]))} for n in stem_names] if stem_names else None),
        "mine": instrument if instrument in stem_names else None,
        "hasTab": has_tab,
        "drums": ({"pieces": list(PIECES),
                   "hits": [[h["time"], PIECES.index(h["piece"]), h["velocity"]] for h in drum_hits],
                   "fix": _fix_data(song)}
                  if is_drums else None),
    }
    start = start_source or (sources[0][0] if sources else "")

    if stem_names:   # live mix: the "Hear" menu becomes mixer presets
        mine = instrument if instrument in stem_names else None
        presets = ([("along", f"Play along (no {instrument})")] if mine else []) + \
            [("full", "Full song")] + ([("solo", f"Only {instrument}")] if mine else []) + \
            [("custom", "Custom mix")]
        first = "along" if mine else "full"
        src_opts = "".join(f'<option value="{i}"{" selected" if i == first else ""}>{html.escape(lab)}'
                           f'</option>' for i, lab in presets)
        mixer = _mixer_html(stem_names, mine)
    else:
        src_opts = "".join(f'<option value="{i}"{" selected" if i == start else ""}>{html.escape(lab)}'
                           f'</option>' for i, lab, _ in sources)
        mixer = ""
    sec_opts = '<option value="">Choose a section</option>' + "".join(
        f'<option value="{k}">{html.escape(section_label(s))}</option>'
        for k, s in enumerate(sections))
    n = len(bars)
    timeline = "".join(
        f'<button class="mp-sec mp-sec--{s["letter"]}" data-k="{k}" '
        f'style="flex:{max(s["end"] - s["start"], 0.1):.2f}" '
        f'title="{html.escape(section_label(s))}: click to practice this part">'
        f'<b>{s["letter"]}</b><span>{html.escape(s["hint"] or "")}</span></button>'
        for k, s in enumerate(sections))

    tags, pats_html, steps_html = [None] * n, "", ""
    bar_figs: list = [None] * n
    if is_drums and grids:
        tags, pats_html, pat_js, steps_html = _patterns(rows, grids, bars, duration)
        data["drums"]["steps"] = pat_js or None
        bar_figs, figs_html = _figures(rows, grids)
        pats_html = (pats_html[:-len("</div>")] + figs_html + "</div>"
                     if pats_html else (f'<div class="mp-pats">{figs_html}</div>' if figs_html else ""))
    sheet = "".join(
        _bar_html(i, chart[i] if i < len(chart) else [], grids[i] if has_tab else None,
                  names, 16, row_colors, cells, rows, used, tags[i] if i < len(tags) else None,
                  bar_figs[i] if i < len(bar_figs) else None)
        for i in range(n))
    sheet_cls = "mp-sheet" + ("" if has_tab else " mp-sheet--chords") + \
        (" mp-sheet--drums" if is_drums else "")
    grip = ('<div class="mp-sheet-grip" role="slider" tabindex="0" aria-orientation="vertical" '
            'aria-label="Lines of music shown: drag down to see more ahead, arrow keys to change, '
            'double-click to reset"><span class="mp-grip-bar"></span>'
            '<span class="mp-grip-n">2 lines</span><span class="mp-grip-bar"></span></div>'
            if has_tab else "")
    sheet_html = f'<div class="{sheet_cls}">{sheet}</div>{grip}'
    if is_drums:
        kit = kit_svg({h["piece"] for h in drum_hits})
        body = (f'<div class="mp-body mp-body--drums"><div class="mp-kit" title="Each piece lights '
                f'up when it is hit; a ring in its color grows during the beat before its next hit">{kit}'
                f'</div><div class="mp-split" role="separator" aria-orientation="vertical" tabindex="0" '
                f'aria-label="Drag to resize the kit and the grid (double-click to reset)"></div>'
                f'{sheet_html}{steps_html}{_highway_html(sorted({h["piece"] for h in drum_hits}, key=PIECES.index))}</div>')
        dur = int(duration)
        songhead = (f'<div class="mp-songhead"><div class="mp-song-title">{html.escape(song.title)}</div>'
                    f'<div class="mp-song-meta">{song.get("tempo"):.0f} BPM &middot; {n} bars &middot; '
                    f'{dur // 60}:{dur % 60:02d}</div></div>')
        stage = (f'<div class="mp-stage mp-stage--drums">{songhead}<div class="mp-where">'
                 f'<span class="mp-k">Bar</span><span class="mp-bar-now">1</span>'
                 f'<span class="mp-of">of {n}</span><span class="mp-time">0:00</span></div>'
                 f'<span class="mp-chord-now" hidden></span><span class="mp-chord-next" hidden>'
                 f'</span><span class="mp-next-in" hidden></span></div>')
    else:
        body = sheet_html
        stage = f"""<div class="mp-stage">
    <div class="mp-now"><span class="mp-k">Now</span><span class="mp-chord-now">&nbsp;</span></div>
    <div class="mp-next"><span class="mp-k">Next</span><span class="mp-chord-next">&nbsp;</span>
      <span class="mp-next-in"></span></div>
    <div class="mp-where"><span class="mp-k">Bar</span><span class="mp-bar-now">1</span>
      <span class="mp-of">of {n}</span><span class="mp-time">0:00</span></div>
  </div>"""

    view_toggles = ('<button type="button" class="mp-show-kit" aria-pressed="true">Kit</button>'
                    '<button type="button" class="mp-show-sheet" aria-pressed="true">Grid</button>'
                    '<button type="button" class="mp-show-steps" aria-pressed="false" title="The song as a few steps that repeat">Steps</button>'
                    '<button type="button" class="mp-show-hw" aria-pressed="false">Highway</button>'
                    '<button type="button" class="mp-show-fix" aria-pressed="false" '
                    'title="Adjust how many hits the AI finds, and add or remove hits by hand">'
                    'Fix transcription</button>'
                    if is_drums else "")
    return f"""
<div class="mp-player{' mp-player--drums' if is_drums else ''}" data-key="{uuid.uuid4().hex}" data-mp="{html.escape(json.dumps(data))}">
  <audio class="mp-audio" preload="auto"></audio>
  {stage}
  <div class="mp-timeline" aria-label="Song sections">{timeline}<div class="mp-head"></div></div>
  {pats_html}
  <div class="mp-controls">
    <button class="mp-play" aria-label="Play or pause (space)">
      <svg viewBox="0 0 24 24" class="i-play"><path d="M7 4.5v15l13-7.5z"/></svg>
      <svg viewBox="0 0 24 24" class="i-pause"><path d="M6 4h4.5v16H6zM13.5 4H18v16h-4.5z"/></svg>
    </button>
    <label class="mp-field">Hear
      <select class="mp-src">{src_opts}</select></label>
    <label class="mp-field mp-speed-field">Speed
      <input type="range" class="mp-speed" min="40" max="125" step="1" value="100">
      <input type="number" class="mp-num" min="40" max="125" step="1" value="100" aria-label="Speed in percent, type a value">
      <output class="mp-speed-out">%</output></label>
    <div class="mp-loop">
      <label class="mp-check"><input type="checkbox" class="mp-loop-on"> Loop bars</label>
      <input type="number" class="mp-from" min="1" max="{n}" value="1" aria-label="Loop from bar">
      <span>to</span>
      <input type="number" class="mp-to" min="1" max="{n}" value="{min(4, n)}" aria-label="Loop to bar">
      <select class="mp-sec-sel" aria-label="Loop a section">{sec_opts}</select>
    </div>
    <div class="mp-opts">
      <label class="mp-check"><input type="checkbox" class="mp-countin" checked> Count-in</label>
      <label class="mp-check"><input type="checkbox" class="mp-click"> Click track</label>
      <label class="mp-check"><input type="checkbox" class="mp-follow" checked> Follow</label>
    </div>
    <div class="mp-view" role="group" aria-label="Layout">
      {view_toggles}<span class="mp-view-k">Tab size</span>
      <button type="button" class="mp-zoom-out" aria-label="Smaller tab">A&minus;</button>
      <button type="button" class="mp-zoom-in" aria-label="Bigger tab">A+</button>
    </div>
  </div>
  {mixer}
  {_fix_html(song) if is_drums else ""}
  {body}
  <p class="mp-keys">Space play/pause, arrows jump a bar, L toggles the loop. Click any bar to jump to it.</p>
</div>"""
