"""'Fix transcription' panel in headless Chromium: sensitivity counts, add/remove hits on the
grid, save through Gradio, the player comes back where it was, reset."""
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
WS = Path.home() / ".music-practice-browser-check-fix"
shutil.rmtree(WS, ignore_errors=True)
truth, stems = SD.song_with_drums(OUT / "fx_song.wav", OUT / "fx_stems")
ws = Workspace(WS)
c = Conductor(ws)
c.reg._plugins.insert(0, StandInSeparator(stems))
launch(build_app(c), ws, server_port=7869, prevent_thread_lock=True, quiet=True)

results = {}
def check(name, ok, detail=""):
    results[name] = ok
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

def hits():
    return [(h["time"], h["piece"]) for h in ws.list_songs()[0].get("drums:hits")]

P = "document.querySelector('.mp-player--drums')"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1400, "height": 1000})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("http://127.0.0.1:7869/")
    pg.wait_for_selector("text=Paste a song link above")
    pg.get_by_text("Or upload an audio file").click()
    pg.locator("input[type=file]").set_input_files(str(OUT / "fx_song.wav"))
    time.sleep(1.5)
    pg.get_by_role("button", name="Analyze song").click()
    pg.wait_for_selector(".mp-player--drums[data-ready] .mp-kit-svg", timeout=300000)
    pg.wait_for_function(f"() => {P}.dataset.stemsReady === '1'", timeout=60000)
    original = hits()

    VIS_ROWS = f"() => [...{P}.querySelector('.mp-bar .mp-strings').children].filter(s => getComputedStyle(s).display !== 'none').length"
    n_closed = pg.evaluate(VIS_ROWS)
    check("panel starts closed", pg.evaluate(f"() => {P}.querySelector('.mp-fix').hidden"))
    pg.locator(".mp-show-fix").click()
    check("panel opens", not pg.evaluate(f"() => {P}.querySelector('.mp-fix').hidden"))
    n_open = pg.evaluate(VIS_ROWS)
    check("all 8 rows while editing, only played ones otherwise", n_open == 8 and n_closed < 8, f"{n_closed} -> {n_open}")
    check("hidden-grid layouts switch to the grid", pg.evaluate(f"() => !{P}.querySelector('.mp-body--drums').classList.contains('no-sheet')"))

    # sensitivity: counts follow the slider
    def count(f):
        txt = pg.evaluate(f"() => {P}.querySelector('.mp-sens-n[data-f={f}]').textContent")
        return int(txt.split()[0]), txt
    def slide(f, v):
        pg.evaluate(f"() => {{ const s = {P}.querySelector('.mp-sens[data-f={f}]'); s.value = {v}; s.dispatchEvent(new Event('input')); }}")
    n50, t50 = count("hihat"); slide("hihat", 0); n0, t0 = count("hihat"); slide("hihat", 100); n100, t100 = count("hihat")
    real_hh = sum(p == "hihat" for _, p in original)
    check("hit count at the default matches the grid", n50 == real_hh, f"{t50} vs {real_hh}")
    check("left finds fewer, right finds more", n0 <= n50 <= n100 and n0 < n100, f"{t0} | {t50} | {t100}")
    check("unsaved state shows", pg.evaluate(f"() => {P}.classList.contains('has-unsaved')") and
          "not saved" in pg.locator(".mp-fix-status").inner_text())
    pg.locator(".mp-fix-discard").click()
    check("discard puts the slider back", count("hihat")[0] == n50 and pg.locator(".mp-fix-save").is_disabled())

    # edit cells: remove a snare in bar 2, add a floor tom on beat 1 of bar 2
    t_before = pg.evaluate(f"() => {P}.querySelector('.mp-audio').currentTime")
    snare = pg.locator(".mp-bar[data-i='1'] .mp-row[data-p=snare] i[data-h]").first
    snare_k = int(snare.get_attribute("data-h").split(",")[0])
    snare_t = pg.evaluate(f"() => JSON.parse({P}.dataset.mp).drums.hits[{snare_k}][0]")
    snare.click()
    tom = pg.locator(".mp-bar[data-i='1'] .mp-row[data-p=tom_floor] i").nth(0)
    tom.click()
    t_after = pg.evaluate(f"() => {P}.querySelector('.mp-audio').currentTime")
    check("clicking cells while editing doesn't jump", abs(t_after - t_before) < 1e-3, f"{t_before} -> {t_after}")
    check("removed mark and added mark are shown as pending",
          "mp-del" in snare.get_attribute("class") and "mp-add" in tom.get_attribute("class"))
    check("status counts 2 changes", "2 cells changed" in pg.locator(".mp-fix-status").inner_text())
    tom_t = pg.evaluate(f"() => {P}.__mpFix.slotTime(1, 0)")
    # click again undoes; click once more redoes
    tom.click(); check("clicking a changed cell again undoes it", "mp-add" not in (tom.get_attribute("class") or ""))
    tom.click()

    # remember a spot, then save
    pg.evaluate(f"() => {{ const r = {P}; r.querySelector('.mp-speed').value = 80; r.querySelector('.mp-speed').dispatchEvent(new Event('input')); r.querySelector('.mp-audio').currentTime = 5.0; }}")
    key0 = pg.evaluate(f"() => {P}.dataset.key")
    pg.locator(".mp-fix-save").click()
    pg.wait_for_function(f"() => {P}.dataset.key !== '{key0}' && {P}.dataset.ready", timeout=60000)
    pg.wait_for_function(f"() => {P}.dataset.stemsReady === '1'", timeout=60000)
    time.sleep(0.5)
    new = hits()
    check("snare removed in the saved song", (snare_t, "snare") not in new and (snare_t, "snare") in original)
    check("floor tom added at the cell's time", ("tom_floor" in {p for _, p in new})
          and any(abs(t - tom_t) < 0.002 and p == "tom_floor" for t, p in new), f"tom at {tom_t}")
    check("everything else unchanged", set(new) - {(round(tom_t, 3), "tom_floor")} == set(original) - {(snare_t, "snare")})
    st = pg.evaluate(f"""() => {{ const r = {P}; return {{ open: !r.querySelector('.mp-fix').hidden,
        speed: r.querySelector('.mp-speed').value, t: r.querySelector('.mp-audio').currentTime,
        mark: !!r.querySelector(".mp-bar[data-i='1'] .mp-row[data-p=tom_floor] i.mp-o[data-h]"),
        status: r.querySelector('.mp-fix-status').textContent }}; }}""")
    check("player comes back with the panel open, same speed and spot",
          st["open"] and st["speed"] == "80" and abs(st["t"] - 5.0) < 0.05, str(st))
    check("new mark is a real hit in the grid (kit/highway use it)", st["mark"])
    check("saved edits are counted", "2 hand edits saved" in st["status"], st["status"])
    check("Gradio channel stays invisible", pg.evaluate("() => getComputedStyle(document.querySelector('#mp-cmd')).display === 'none'"))

    # back to the AI's version: needs two clicks
    key1 = pg.evaluate(f"() => {P}.dataset.key")
    pg.locator(".mp-fix-reset").click()
    time.sleep(0.4)
    check("reset asks for a second click", pg.evaluate(f"() => {P}.dataset.key") == key1 and "again" in pg.locator(".mp-fix-reset").inner_text())
    pg.locator(".mp-fix-reset").click()
    pg.wait_for_function(f"() => {P}.dataset.key !== '{key1}' && {P}.dataset.ready", timeout=60000)
    time.sleep(0.3)
    check("reset restores the AI's hits", hits() == original)
    # ---- playhead crosses each grid mark when its hit sounds ----
    pg.locator(".mp-show-fix").click()        # close the panel (rows back to normal)
    al = pg.evaluate(f"""async () => {{ const r = {P}, a = r.querySelector('.mp-audio');
        const D = JSON.parse(r.dataset.mp); a.pause();
        const marks = [...r.querySelectorAll('.mp-row i[data-h]')].filter((m, k) => k % 7 === 0).slice(0, 12);
        const out = [];
        for (const m of marks) {{
          const k = +m.dataset.h.split(',')[0], t = D.drums.hits[k][0];
          a.currentTime = t; await new Promise(res => setTimeout(res, 120));
          const bar = m.closest('.mp-bar'), cur = bar.querySelector('.mp-cursor');
          if (!bar.classList.contains('is-now')) {{ out.push({{ skip: true }}); continue; }}
          const g = bar.querySelector('.mp-grid').getBoundingClientRect();
          const mc = m.getBoundingClientRect(), cc = cur.getBoundingClientRect();
          const i = +bar.dataset.i, slot = g.width / m.parentElement.children.length;
          const lin = (t - D.bars[i]) / ((D.bars[i + 1] || D.duration) - D.bars[i]) * g.width + g.left;
          out.push({{ q: r.mpCursorFrac(i, t), mx: (mc.left + mc.width / 2 - g.left) / g.width, dx: (cc.left + cc.width / 2) - (mc.left + mc.width / 2), old: lin - (mc.left + mc.width / 2), slot }});
        }}
        return out; }}""")
    al = [x for x in al if not x.get("skip")]
    worst = max(abs(x["dx"]) for x in al) if al else 99
    slot = al[0]["slot"] if al else 1
    old = sorted(abs(x["old"]) for x in al)[len(al) // 2] if al else 0
    check("playhead is on the mark when the hit sounds (within 1/4 of a 16th)",
          len(al) >= 6 and worst < 0.25 * slot,
          f"worst {worst:.1f}px, 16th = {slot:.1f}px, n={len(al)}, grid lead {pg.evaluate(f'() => {P}.dataset.gridLead')} ms")
    check("the old straight-line playhead was behind by half a 16th or more", 0.4 * slot < old,
          f"median old lag {old:.1f}px")

    # ---- typed values next to sliders ----
    def num(sel):
        return pg.locator(sel).locator("xpath=..").locator("input.mp-num")
    sp = num(".mp-speed")
    sp.click(); sp.fill("87"); sp.press("Enter")
    st = pg.evaluate(f"() => ({{ v: {P}.querySelector('.mp-speed').value, r: {P}.querySelector('.mp-audio').playbackRate, p: {P}.querySelector('.mp-audio').paused }})")
    check("typing 87 sets the speed to 87%", st["v"] == "87" and abs(st["r"] - 0.87) < 1e-6, str(st))
    sp.fill("300"); sp.press("Enter")
    check("out-of-range typed value is clamped", pg.evaluate(f"() => {P}.querySelector('.mp-speed').value") == "125")
    sp.fill("9"); sp.press(" ")
    check("space inside the box doesn't start playback", pg.evaluate(f"() => {P}.querySelector('.mp-audio').paused") and not
          pg.evaluate(f"() => {P}.classList.contains('is-counting')"))
    sp.press("Escape")
    check("Escape restores the slider's value", sp.input_value() == "125")
    sp.fill("100"); sp.press("Enter")
    g = num(".mp-strip[data-stem=guitar] input[type=range]")
    g.fill("35"); g.press("Tab")
    gains = pg.evaluate(f"() => JSON.parse({P}.dataset.gains)")
    check("typing a mixer volume sets it (applies when leaving the box)", abs(gains.get("guitar", 0) - 0.35) < 1e-6, str(gains))
    pg.locator(".mp-mixer select, .mp-src").first.select_option("full")
    time.sleep(0.2)
    check("presets update the typed boxes too", g.input_value() == "100", g.input_value())
    pg.locator(".mp-show-fix").click()
    sn = num(".mp-sens[data-f=snare]")
    before = pg.evaluate(f"() => {P}.querySelector('.mp-sens-n[data-f=snare]').textContent")
    sn.fill("100"); sn.press("Enter")
    after = pg.evaluate(f"() => {P}.querySelector('.mp-sens-n[data-f=snare]').textContent")
    check("typing a sensitivity updates the count", before != after and not pg.locator(".mp-fix-save").is_disabled(), f"{before} -> {after}")
    pg.locator(".mp-fix-discard").click()
    check("discard puts the typed box back too", (time.sleep(0.1) or sn.input_value()) == "50", sn.input_value())

    # ---- repeated patterns ----
    pt = pg.evaluate(f"""() => {{ const r = {P};
        return {{ chips: [...r.querySelectorAll('.mp-pat-chip')].map(c => c.textContent.trim()),
                 band: r.querySelectorAll('.mp-patband i').length, bars: r.querySelectorAll('.mp-bar').length,
                 badges: r.querySelectorAll('.mp-bar .mp-pat-badge').length }}; }}""")
    check("patterns found: chips, one band cell and one badge per bar", len(pt["chips"]) >= 2 and pt["band"] == pt["bars"]
          and pt["badges"] >= pt["bars"] - 2, str(pt))
    pg.locator(".mp-pat-chip .mp-pat-show").first.click()
    fo = pg.evaluate(f"""() => {{ const r = {P}, k = r.dataset.patFocus;
        const on = [...r.querySelectorAll('.mp-bar')].filter(b => b.dataset.pat === k);
        const off = [...r.querySelectorAll('.mp-bar')].filter(b => b.dataset.pat !== k);
        return {{ k, on: on.length, dimOff: off.every(b => getComputedStyle(b.querySelector('.mp-tab')).opacity < 0.5),
                 brightOn: on.every(b => b.classList.contains('is-pat')) }}; }}""")
    time.sleep(0.3)
    fo["dimOff"] = pg.evaluate(f"""() => {{ const r = {P}, k = r.dataset.patFocus;
        return [...r.querySelectorAll('.mp-bar')].filter(b => b.dataset.pat !== k).every(b => parseFloat(getComputedStyle(b.querySelector('.mp-tab')).opacity) < 0.5); }}""")
    check("showing a pattern dims every other bar", fo["k"] is not None and fo["on"] >= 2 and fo["dimOff"] and fo["brightOn"], str(fo))
    pg.locator(".mp-pat-chip .mp-pat-show").first.click()
    check("clicking again clears it", pg.evaluate(f"() => !{P}.dataset.patFocus"))
    lb = int(pg.locator(".mp-pat-chip .mp-pat-loop").first.get_attribute("data-bar"))
    pg.locator(".mp-pat-chip .mp-pat-loop").first.click()
    lp = pg.evaluate(f"() => {{ const r = {P}; return [r.querySelector('.mp-loop-on').checked, r.querySelector('.mp-from').value, r.querySelector('.mp-to').value]; }}")
    check("loop button loops the pattern's typical bar", lp == [True, str(lb + 1), str(lb + 1)], str(lp))
    pg.evaluate(f"() => {{ {P}.querySelector('.mp-loop-on').checked = false; }}")
    pg.screenshot(path=str(OUT / "patterns.png"))

    # ---- steps view: one bar per groove, the playhead comes round again ----
    pg.locator(".mp-show-steps").click()
    time.sleep(0.3)
    sv = pg.evaluate(f"""async () => {{ const r = {P}, a = r.querySelector('.mp-audio'), D = JSON.parse(r.dataset.mp);
        const S = D.drums.steps, st = S.steps.find(s => s.p >= 0 && s.reps >= 2);
        const vis = !r.querySelector('.mp-steps').hidden && getComputedStyle(r.querySelector('.mp-sheet')).display === 'none';
        // two consecutive bars of the groove
        let b = st.a; while (!(S.ids[b] === st.p && S.ids[b + 1] === st.p)) b++;
        a.currentTime = D.bars[b] + 0.05; await new Promise(res => setTimeout(res, 150));
        const s1 = r.__mpSteps.state();
        const again0 = r.querySelector('.mp-steps-stage').classList.contains('is-again');
        await a.play();
        await new Promise(res => {{ const f = () => {{ if (a.currentTime < D.bars[b + 1] + 0.3) requestAnimationFrame(f); else res(); }}; requestAnimationFrame(f); }});
        a.pause();
        const s2 = r.__mpSteps.state(), stage = r.querySelector('.mp-steps-stage');
        const pops = [...stage.querySelectorAll('.mp-row i')].filter(i => i.classList.contains('is-hit')).length;
        return {{ vis, n: S.steps.length, bars: D.bars.length, s1, s2, again: stage.classList.contains('is-again'), again0, pops,
                 chips: r.querySelectorAll('.mp-step').length, nowChip: !!r.querySelector('.mp-step.is-now'),
                 dots: r.querySelectorAll('.mp-steps-dots i').length }}; }}""")
    check("steps view replaces the grid and lists the steps", sv["vis"] and sv["chips"] == sv["n"] and sv["nowChip"]
          and sv["n"] <= sv["bars"] / 3, f"{sv['n']} steps for {sv['bars']} bars")
    check("the next repetition shows the SAME bar again, counter goes up, sweep-back animation",
          sv["s1"]["shown"] == sv["s2"]["shown"] and sv["s2"]["atBar"] == sv["s1"]["atBar"] + 1
          and sv["s1"]["rep"] != sv["s2"]["rep"] and sv["again"], str({k: sv[k] for k in ("s1", "s2", "again")}))
    check("hits pop on the shown pattern while it plays", sv["pops"] >= 1 and sv["dots"] >= 2, f"{sv['pops']} marks popping")
    pg.locator(".mp-steps").screenshot(path=str(OUT / "steps.png"))
    pg.locator(".mp-show-sheet").click()
    time.sleep(0.3)

    # ---- grip under the sheet: choose how many lines ahead are visible ----
    VIS = f"""() => {{ const r = {P}, sh = r.querySelector('.mp-sheet'), sr = sh.getBoundingClientRect();
        const tops = new Set([...sh.querySelectorAll(':scope > .mp-bar')].map(b => b.getBoundingClientRect())
          .filter(b => b.top >= sr.top - 1 && b.bottom <= sr.bottom + 1).map(b => Math.round(b.top)));
        return {{ full: tops.size, lines: +r.dataset.lines, lineH: +r.dataset.lineHeight }}; }}"""
    v0 = pg.evaluate(VIS)
    gap = pg.evaluate(f"() => {{ const r = {P}; return r.querySelector('.mp-sheet-grip').getBoundingClientRect().top - r.querySelector('.mp-sheet').getBoundingClientRect().bottom; }}")
    check("the grip is glued to the bottom edge of the sheet", abs(gap) <= 1, f"gap {gap:.1f}px")
    g = pg.locator(".mp-sheet-grip"); g.scroll_into_view_if_needed(); gb = g.bounding_box()
    pg.mouse.move(gb["x"] + gb["width"] / 2, gb["y"] + gb["height"] / 2); pg.mouse.down()
    pg.mouse.move(gb["x"] + gb["width"] / 2, gb["y"] + gb["height"] / 2 + 1.6 * v0["lineH"], steps=10); pg.mouse.up()
    time.sleep(0.3)
    v1 = pg.evaluate(VIS)
    check("dragging the grip down shows more whole lines (snaps to lines)", v0["full"] == v0["lines"] == 2
          and v1["lines"] in (3, 4) and v1["full"] == v1["lines"], f"{v0} -> {v1}")
    g.focus(); g.press("ArrowDown"); time.sleep(0.2)
    v2 = pg.evaluate(VIS)
    check("arrow keys add a line", v2["lines"] == v1["lines"] + 1 and v2["full"] == v2["lines"], str(v2))
    saved = pg.evaluate("() => JSON.parse(localStorage.getItem('music-practice-layout') || '{}').lines")
    check("the choice is remembered", saved == v2["lines"], str(saved))
    pg.locator(".mp-body--drums").screenshot(path=str(OUT / "grip.png"))
    g.dblclick(); time.sleep(0.2)
    check("double-click goes back to 2 lines", pg.evaluate(VIS)["lines"] == 2)

    # ---- follow: the page glides to the next line during the last beat ----
    if pg.evaluate(f"() => !{P}.querySelector('.mp-fix').hidden"):
        pg.locator(".mp-show-fix").click()
    time.sleep(0.3)
    gl = pg.evaluate(f"""async () => {{ const r = {P}, a = r.querySelector('.mp-audio'), sh = r.querySelector('.mp-sheet');
        const D = JSON.parse(r.dataset.mp), L = r.mpLines();
        const n = L.nextLine.find((x, k) => k >= 4 && x > 0);         // a line change past the intro
        a.currentTime = D.bars[n] - 1.5; await new Promise(res => setTimeout(res, 400));
        const out = [];
        await a.play();
        await new Promise(res => {{ const f = () => {{ out.push([a.currentTime, sh.scrollTop]);
          if (a.currentTime < D.bars[n] + 0.6) requestAnimationFrame(f); else res(); }}; requestAnimationFrame(f); }});
        a.pause();
        return {{ out, tn: D.bars[n], from: L.lineTop[n - 1], to: L.lineTop[n], beat: 60 / D.tempo,
                 lineH: parseFloat(r.dataset.lineHeight) }}; }}""")
    pts, tn = gl["out"], gl["tn"]
    ys = [y for _, y in pts]
    steps = [b - a for a, b in zip(ys, ys[1:])]
    moving = [t for (t, y), d in zip(pts[1:], steps) if abs(d) > 0.5]
    arrive = [y for t, y in pts if t >= tn]
    check("follow glides (many small steps, never backwards, no big jump)",
          len(moving) >= 8 and min(steps) >= -0.5 and max(steps) < 0.35 * gl["lineH"],
          f"{len(moving)} moving frames, biggest step {max(steps):.0f}px of a {gl['lineH']:.0f}px line")
    check("glide starts in the last beat, not earlier",
          bool(moving) and moving[0] >= tn - max(gl["beat"], 0.7) - 0.05, f"starts {tn - moving[0]:.2f}s before the line")
    check("the new line is on top when its first bar starts",
          bool(arrive) and abs(arrive[0] - gl["to"]) <= 3, f"at line start {arrive[0] if arrive else None} vs {gl['to']}")

    fr = pg.evaluate(f"""async () => {{ const r = {P}, a = r.querySelector('.mp-audio');
        a.currentTime = 3; await a.play(); const d = []; let last = performance.now();
        await new Promise(res => {{ const f = (now) => {{ d.push(now - last); last = now;
          if (d.length < 240) requestAnimationFrame(f); else res(); }}; requestAnimationFrame(f); }});
        a.pause(); d.shift(); d.sort((x, y) => x - y);
        return {{ p50: d[d.length >> 1], p95: d[Math.floor(d.length * .95)], max: d[d.length - 1] }}; }}""")
    check("playback stays smooth (95% of frames under 25 ms)", fr["p95"] < 25, str({k: round(v, 1) for k, v in fr.items()}))

    check("no page errors", not errors, "; ".join(errors[:3]))
    pg.screenshot(path=str(OUT / "fix-panel.png"), full_page=False)
    b.close()

ok = all(results.values())
print(f"\n{sum(results.values())}/{len(results)} checks passed")
sys.exit(0 if ok else 1)
