"""Drum mode in headless Chromium: kit pieces must flash in sync with the audio."""
import sys
import shutil
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
from playwright.sync_api import sync_playwright

import synth_drums as SD
from musicpractice.agents import Conductor
from musicpractice.app import build_app, launch
from musicpractice.core import Workspace
from test_pipeline import StandInSeparator

OUT = Path(sys.argv[1])
WS = Path.home() / ".music-practice-browser-check-drums"
shutil.rmtree(WS, ignore_errors=True)
truth, stems = SD.song_with_drums(OUT / "dr_song.wav", OUT / "dr_stems")
ws = Workspace(WS)
c = Conductor(ws)
c.reg._plugins.insert(0, StandInSeparator(stems))
launch(build_app(c), ws, server_port=7866, prevent_thread_lock=True, quiet=True)

results = {}
def check(name, ok, detail=""):
    results[name] = ok
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

P = "document.querySelector('.mp-player--drums')"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1400, "height": 1000})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("http://127.0.0.1:7866/?__theme=dark")
    pg.wait_for_selector("text=Paste a song link above")
    assert pg.get_by_label("I play").input_value() == "drums"   # drums-only app: preselected
    pg.get_by_text("Use an audio file instead").click()
    pg.locator("input[type=file]").set_input_files(str(OUT / "dr_song.wav"))
    time.sleep(1.5)
    pg.get_by_role("button", name="Analyze song").click()
    pg.wait_for_selector(".mp-player--drums[data-ready] .mp-kit-svg", timeout=300000)
    pg.wait_for_function(f"() => {P}.querySelector('.mp-audio').readyState >= 1", timeout=30000)

    info = pg.evaluate(f"""() => {{ const r = {P};
      return {{ pieces: [...r.querySelectorAll('.mp-pc')].map(g => g.dataset.p),
               unused: [...r.querySelectorAll('.mp-pc.is-unused')].map(g => g.dataset.p),
               rows: [...r.querySelector('.mp-bar .mp-strings').children].filter(s => getComputedStyle(s).display !== 'none').map(s => s.textContent),
               src: r.querySelector('.mp-audio').currentSrc }}; }}""")
    check("kit drawn with all 8 pieces", len(info["pieces"]) == 8, ",".join(info["pieces"]))
    from musicpractice.plugins.drums import PIECES, SHORT
    detected = {h["piece"] for h in c.ws.list_songs()[0].get("drums:hits")}
    check("pieces never detected are greyed out, the rest are not",
          set(info["unused"]) == set(PIECES) - detected, f"greyed {info['unused']}, detected {sorted(detected)}")
    check("drum grid has one row per detected piece, in kit order",
          info["rows"] == [SHORT[p] for p in PIECES if p in detected], str(info["rows"]))
    ls = pg.evaluate(f"""() => {{ const r = {P}, bars = [...r.querySelectorAll('.mp-bar')];
        const vis = b => getComputedStyle(b.querySelector('.mp-strings')).visibility === 'visible';
        const starts = bars.filter(b => b.classList.contains('is-line-start'));
        const lineOk = bars.every((b, k) => b.classList.contains('is-line-start') === (k === 0 || b.offsetTop !== bars[k - 1].offsetTop));
        const g0 = bars[0].querySelector('.mp-grid').getBoundingClientRect(), g1 = bars[1].querySelector('.mp-grid').getBoundingClientRect();
        return {{ n: bars.length, starts: starts.length, lineOk, labelsOnlyAtStart: bars.every(b => vis(b) === b.classList.contains('is-line-start')),
                 gap: g1.top === g0.top ? Math.round(g1.left - g0.right) : null }}; }}""")
    check("names appear once per line and bars of a line touch (one continuous staff)",
          ls["lineOk"] and ls["labelsOnlyAtStart"] and 0 < ls["starts"] < ls["n"] and ls["gap"] is not None and abs(ls["gap"]) <= 1, str(ls))
    chord_visible = pg.evaluate(f"() => getComputedStyle({P}.querySelector('.mp-chord-now')).display !== 'none'")
    check("no chord name shown in drum mode", not chord_visible)
    kf = pg.evaluate("""() => { const n = new Set(); const walk = rs => { for (const r of rs) {
        if (r.type === CSSRule.KEYFRAMES_RULE) n.add(r.name); if (r.cssRules) walk(r.cssRules); } };
        for (const sh of document.styleSheets) { try { walk(sh.cssRules); } catch (e) {} } return [...n]; }""")
    needed = {"mp-punch", "mp-flash", "mp-bright", "mp-mark", "mp-mark-x", "mp-beat", "mp-downbeat"}
    check("all player animations are registered by the browser", needed <= set(kf), f"missing {needed - set(kf)}")
    pg.wait_for_function(f"() => {P}.dataset.stemsReady === '1'", timeout=60000)
    g = pg.evaluate(f"() => JSON.parse({P}.dataset.gains)")
    check("hears play-along (drums silent in the live mix)", g.get("drums") == 0 and g.get("guitar") == 1, str(g))

    # the sheet shows exactly two full lines of bars
    LINES = f"""() => {{ const r = {P}, sh = r.querySelector('.mp-sheet'), sr = sh.getBoundingClientRect();
        const bars = [...r.querySelectorAll('.mp-bar')].map(b => b.getBoundingClientRect());
        const full = bars.filter(b => b.top >= sr.top - 1 && b.bottom <= sr.bottom + 1);
        const partial = bars.filter(b => b.bottom > sr.top + 1 && b.top < sr.bottom - 1 && !(b.top >= sr.top - 1 && b.bottom <= sr.bottom + 1));
        const tops = [...new Set(full.map(b => Math.round(b.top)))];
        const now = r.querySelector('.mp-bar.is-now');
        return {{ lines: tops.length, perLine: full.length / Math.max(tops.length, 1), partial: partial.length,
                 nowOnTopLine: now ? Math.abs(now.getBoundingClientRect().top - Math.min(...tops)) < 3 : null,
                 barW: bars[0].width, rowH: parseFloat(getComputedStyle(r.querySelector('.mp-row')).height) }}; }}"""
    time.sleep(1.2)                                   # let the smooth page-turn scroll finish
    lines = pg.evaluate(LINES)
    check("sheet shows exactly two full lines of bars, nothing cut off",
          lines["lines"] == 2 and lines["partial"] == 0, str(lines))
    check("tabs are bigger (bar >= 380 px wide, rows >= 21 px), 2+ bars per line at 1400 px",
          lines["barW"] >= 380 and lines["rowH"] >= 21 and lines["perLine"] >= 2, str(lines))

    # colors: every mark in the grid (also in the bar being played) has its kit piece's color
    pg.evaluate(f"() => {{ {P}.querySelector('.mp-audio').currentTime = 2.3; }}")
    time.sleep(0.4)
    col = pg.evaluate(f"""() => {{ const r = {P};
        const kit = Object.fromEntries([...r.querySelectorAll('.mp-pc')].map(g =>
            [g.dataset.p, getComputedStyle(g.querySelector('.shape')).fill]));
        const order = ['crash','ride','hihat','tom_high','tom_mid','snare','tom_floor','kick'];
        const used = order.filter(p => !r.querySelector(`.mp-pc[data-p="${{p}}"]`).classList.contains('is-unused'));
        const bad = []; let n = 0, nowMarks = 0;
        r.querySelectorAll('.mp-bar').forEach(bar => {{
          bar.querySelectorAll('.mp-row').forEach((row, k) => {{
            row.querySelectorAll('i.mp-o, i.mp-x').forEach(m => {{
              const cs = getComputedStyle(m);
              const c = m.classList.contains('mp-o') ? cs.backgroundColor : cs.color;
              n++; if (bar.classList.contains('is-now')) nowMarks++;
              const pc = row.dataset.p || used[k];
              if (c !== kit[pc]) bad.push([bar.dataset.i, pc, c, kit[pc]]);
            }});
          }});
        }});
        return {{ n, nowMarks, bad: bad.slice(0, 5), nbad: bad.length, kit }}; }}""")
    check("every grid mark has the same color as its kit piece (incl. the current bar)",
          col["nbad"] == 0 and col["n"] > 50 and col["nowMarks"] > 0,
          f"{col['n']} marks, {col['nowMarks']} in current bar, mismatches {col['nbad']} {col['bad']}")
    check("kit pieces are painted with distinct colors",
          len(set(col["kit"].values())) == 8, str(col["kit"]))
    kit_box = pg.locator(".mp-player--drums .mp-body--drums").bounding_box()

    # anticipation: park 0.15 s before a snare hit (beat 2 of bar 2) and read the ring
    beat = 60 / 120
    t_snare = 4 * beat + beat
    pg.evaluate(f"() => {{ {P}.querySelector('.mp-audio').currentTime = {t_snare - 0.15}; }}")
    time.sleep(0.4)
    soon = pg.evaluate(f"() => parseFloat(getComputedStyle({P}.querySelector('[data-p=snare]')).getPropertyValue('--soon') || '0')")
    check("snare ring glows just before its hit", 0.6 <= soon <= 0.8, f"--soon={soon}")

    # play 4 bars from bar 2 without count-in and count flashes per piece
    pg.locator(".mp-player--drums .mp-countin").uncheck()
    start = 4 * beat * 1
    pg.evaluate(f"() => {{ const a = {P}.querySelector('.mp-audio'); a.currentTime = {start}; }}")
    time.sleep(0.3)
    pg.locator(".mp-player--drums .mp-play").click()
    pg.wait_for_timeout(300)
    # the whole piece swells on a hit: sample its on-screen size every frame for 1.5 s
    grow = pg.evaluate(f"""() => new Promise(done => {{
        const r = {P}, gs = [...r.querySelectorAll('.mp-pc:not(.is-unused)')];
        const base = Object.fromEntries(gs.map(g => [g.dataset.p, g.getBoundingClientRect().width]));
        const peak = {{}}; const t0 = performance.now();
        (function f() {{
          gs.forEach(g => {{ const k = g.dataset.p, w = g.getBoundingClientRect().width / base[k];
                             peak[k] = Math.max(peak[k] || 1, w); }});
          if (performance.now() - t0 < 1500) requestAnimationFrame(f); else done(peak);
        }})(); }})""")
    mark_grow = pg.evaluate(f"""() => new Promise(done => {{
        const r = {P}, now = r.querySelector('.mp-bar.is-now') || r.querySelector('.mp-bar');
        const nxt = now.nextElementSibling;
        const ms = [...now.querySelectorAll('.mp-row i[data-h]'), ...(nxt ? nxt.querySelectorAll('.mp-row i[data-h]') : [])];
        const base = ms.map(m => m.getBoundingClientRect().width); let peak = 1; const t0 = performance.now();
        (function f() {{ ms.forEach((m, k) => {{ peak = Math.max(peak, m.getBoundingClientRect().width / base[k]); }});
          if (performance.now() - t0 < 1200) requestAnimationFrame(f); else done(peak); }})(); }})""")
    check("grid: the mark being played visibly pops (>= 1.4x at peak)", mark_grow >= 1.4, f"peak {mark_grow:.2f}x")
    check("kit: the whole piece swells on its hits (>= 6% bigger at peak)",
          all(grow.get(k, 1) >= 1.06 for k in ("hihat", "kick")), str({k: round(v, 3) for k, v in grow.items()}))
    pg.screenshot(path=str(OUT / "d1_playing.png"))
    pg.wait_for_timeout(40)
    pg.screenshot(path=str(OUT / "d1_body.png"), clip=kit_box)
    time.sleep(1.2)
    pg.screenshot(path=str(OUT / "d2_playing.png"))
    pg.wait_for_function(f"() => {P}.querySelector('.mp-audio').currentTime >= {start + 8 * beat}", timeout=20000)
    t_end = pg.evaluate(f"() => {{ const a = {P}.querySelector('.mp-audio'); a.pause(); return a.currentTime; }}")
    counts = pg.evaluate(f"() => Object.fromEntries(['kick','snare','hihat','crash','ride','tom_high','tom_mid','tom_floor'].map(p => [p, {P}.mpKit.hitCount(p)]))")
    hits = c.ws.get(c.ws.list_songs()[0].id).get("drums:hits")
    def tally(items, hi):
        out = {}
        for t, piece in items:
            if start - 1e-3 <= t <= hi:
                out[piece] = out.get(piece, 0) + 1
        return out
    # the pause can land between two animation frames: hits in the last 50 ms may or may not
    # have flashed yet. Everything before that, including hits exactly at the start, must.
    lo = tally([(h["time"], h["piece"]) for h in hits], t_end - 0.05)
    hi = tally([(h["time"], h["piece"]) for h in hits], t_end)
    ok = all(lo.get(k, 0) <= counts.get(k, 0) <= hi.get(k, 0) for k in set(lo) | set(hi) | {k for k, v in counts.items() if v})
    check("every detected hit flashed once while playing (incl. the downbeat we started on)", ok,
          f"flashed {dict((k, v) for k, v in counts.items() if v)} expected {lo}..{hi}")
    pops = pg.evaluate(f"""() => {{ const r = {P};
        const marks = [...r.querySelectorAll('.mp-row i[data-h]')];
        let n = 0; marks.forEach(m => n += parseInt(m.dataset.pops || '0', 10));
        return n; }}""")
    kit_total = sum(counts.values())
    check("grid marks pulse together with the kit (one pop per hit)", abs(pops - kit_total) <= 1,
          f"{pops} mark pops vs {kit_total} kit flashes")
    truth_win = {}
    for t, piece in truth:
        if start - 1e-3 <= t <= t_end:
            truth_win[piece] = truth_win.get(piece, 0) + 1
    check("flashes match the real groove in this window",
          all(abs(counts.get(k, 0) - v) <= 1 for k, v in truth_win.items()),
          f"truth {truth_win}")
    # loop bar 3 (no fill there): its downbeat kick must flash on every repeat
    k0 = pg.evaluate(f"() => {P}.mpKit.hitCount('kick')")
    pg.evaluate(f"""() => {{ const r = {P};
        r.querySelector('.mp-loop-on').checked = true;
        r.querySelector('.mp-from').value = 3; r.querySelector('.mp-to').value = 3;
        r.querySelector('.mp-to').dispatchEvent(new Event('change')); }}""")
    pg.locator(".mp-player--drums .mp-bar").nth(2).click()   # enter bar 3 like a user (with pre-roll)
    time.sleep(0.3)
    pg.locator(".mp-player--drums .mp-play").click()
    time.sleep(3 * 4 * beat + 0.3)          # three passes of a 2-second bar
    pg.evaluate(f"() => {P}.querySelector('.mp-audio').pause()")
    loops_kicks = pg.evaluate(f"() => {P}.mpKit.hitCount('kick')") - k0
    check("loop: kick on the downbeat flashes every pass", 9 <= loops_kicks <= 12,
          f"{loops_kicks} kicks in ~3 passes of a bar with 3 kicks")
    # follow: once playback crosses into the next line, that line moves to the top
    pg.evaluate(f"() => {{ const r = {P}; r.querySelector('.mp-loop-on').checked = false; }}")
    per = int(lines["perLine"])
    line2_start = per * 4 * beat                     # first bar of line 2 (2 s per bar)
    pg.evaluate(f"() => {{ {P}.querySelector('.mp-audio').currentTime = {line2_start - 0.6}; }}")
    pg.locator(".mp-player--drums .mp-play").click()
    time.sleep(1.6)
    after = pg.evaluate(LINES)
    first_visible = pg.evaluate(f"() => {{ const r = {P}, sh = r.querySelector('.mp-sheet'), st = sh.getBoundingClientRect().top;"
                                f" return +[...r.querySelectorAll('.mp-bar')].find(b => b.getBoundingClientRect().top >= st - 1).dataset.i + 1; }}")
    check("follow turns the page: the line being played is the top line",
          after["nowOnTopLine"] and after["lines"] == 2 and first_visible == per + 1,
          f"first visible bar {first_visible}, expected {per + 1}; {after}")
    pg.evaluate(f"() => {P}.querySelector('.mp-audio').pause()")

    # re-analyze a DIFFERENT song (other tempo) while playing: only one player may survive
    truth2, stems2 = SD.song_with_drums(OUT / "dr_song2.wav", OUT / "dr_stems2", bpm=95)
    c.reg._plugins[0] = StandInSeparator(stems2)
    pg.evaluate(f"() => {P}.querySelector('.mp-audio').play()")
    time.sleep(0.5)
    pg.get_by_label("Clear").first.click()            # Gradio hides the file input once a file is set
    time.sleep(0.5)
    pg.locator("input[type=file]").set_input_files(str(OUT / "dr_song2.wav"))
    time.sleep(1.5)
    old_key = pg.evaluate(f"() => {P}.dataset.key")
    pg.get_by_role("button", name="Analyze song").click()
    pg.wait_for_function(f"() => {P} && {P}.dataset.key !== '{old_key}' && {P}.querySelector('.mp-audio').readyState >= 1", timeout=300000)
    time.sleep(0.5)
    pg.evaluate(f"() => {{ {P}.querySelector('.mp-countin').checked = false; }}")
    pg.locator(".mp-player--drums .mp-play").click()
    samples = []
    for _ in range(12):
        time.sleep(0.25)
        samples.append(pg.evaluate("""() => [document.querySelectorAll('.mp-bar.is-now').length,
            [...document.querySelectorAll('audio')].filter(a => !a.paused).length]"""))
    check("after analyzing another song: one highlighted bar, one audio playing",
          all(n == 1 and a == 1 for n, a in samples), str(samples))
    # ---- layout: divider, show/hide, tab size, remembered after reload ----
    pg.evaluate(f"() => {P}.querySelector('.mp-audio').pause()")
    W = lambda sel: pg.evaluate(f"() => {P}.querySelector('{sel}').getBoundingClientRect().width")
    kit0, sheet0 = W(".mp-kit"), W(".mp-sheet")
    pg.locator(".mp-player--drums .mp-split").scroll_into_view_if_needed()
    sp = pg.locator(".mp-player--drums .mp-split").bounding_box()
    pg.mouse.move(sp["x"] + sp["width"] / 2, sp["y"] + 100)
    pg.mouse.down(); pg.mouse.move(sp["x"] + 200, sp["y"] + 100, steps=8); pg.mouse.up()
    kit1, sheet1 = W(".mp-kit"), W(".mp-sheet")
    check("dragging the divider makes the kit bigger and the grid smaller",
          kit1 > kit0 + 150 and sheet1 < sheet0 - 150, f"kit {kit0:.0f}->{kit1:.0f}, grid {sheet0:.0f}->{sheet1:.0f}")
    time.sleep(0.6)
    lines_after = pg.evaluate(LINES)
    check("grid still shows two full lines after resizing", lines_after["lines"] == 2 and lines_after["partial"] == 0, str(lines_after))
    pg.locator(".mp-show-kit").click(); time.sleep(0.4)
    vis = lambda sel: pg.evaluate(f"() => getComputedStyle({P}.querySelector('{sel}')).display !== 'none'")
    check("hiding the kit gives the grid the full width", not vis(".mp-kit") and W(".mp-sheet") > sheet0 + 250,
          f"grid {W('.mp-sheet'):.0f}px")
    kit_btn_disabled = pg.evaluate("() => document.querySelector('.mp-show-sheet').disabled")
    check("the last visible panel can't be hidden", kit_btn_disabled)
    pg.locator(".mp-show-kit").click(); pg.locator(".mp-show-sheet").click(); time.sleep(0.4)
    check("hiding the grid shows a big kit", not vis(".mp-sheet") and W(".mp-kit") > kit1 + 100, f"kit {W('.mp-kit'):.0f}px")
    pg.locator(".mp-show-sheet").click(); time.sleep(0.4)
    rowh = lambda: pg.evaluate(f"() => parseFloat(getComputedStyle({P}.querySelector('.mp-row')).height)")
    r0 = rowh()
    pg.locator(".mp-zoom-in").click(); pg.locator(".mp-zoom-in").click(); time.sleep(0.6)
    r1 = rowh(); lz = pg.evaluate(LINES)
    check("A+ makes the grid bigger and it still fits two lines", r1 > r0 * 1.2 and lz["lines"] == 2 and lz["partial"] == 0,
          f"row {r0:.1f}->{r1:.1f}px, {lz}")
    kit_before_reload = W(".mp-kit")
    pg.reload()
    assert pg.get_by_label("I play").input_value() == "drums"
    pg.get_by_text("Use an audio file instead").click()
    pg.locator("input[type=file]").set_input_files(str(OUT / "dr_song2.wav")); time.sleep(1.5)
    pg.get_by_role("button", name="Analyze song").click()
    pg.wait_for_selector(".mp-player--drums[data-ready] .mp-kit-svg", timeout=300000); time.sleep(1.0)
    check("layout is remembered after reloading the page",
          abs(W(".mp-kit") - kit_before_reload) < 3 and abs(rowh() - r1) < 0.5,
          f"kit {W('.mp-kit'):.0f} (was {kit_before_reload:.0f}), row {rowh():.1f} (was {r1:.1f})")
    pg.screenshot(path=str(OUT / "d_layout.png"))
    check("no JavaScript errors", not errors, "; ".join(errors)[:300])
    pg.screenshot(path=str(OUT / "d3_full.png"), full_page=True)
    b.close()

print(f"\n{sum(results.values())}/{len(results)} checks passed")
