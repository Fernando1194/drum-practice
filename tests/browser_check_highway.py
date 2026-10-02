"""Highway + MIDI scoring in headless Chromium, driven by a simulated electronic drum kit.

The page's Web MIDI is replaced by a fake kit. A "robot drummer" inside the page hits each
note at an exact, chosen timing offset (in real milliseconds), so we can check that the judge
says Perfect / Good / OK / Missed / Extra, early vs late, at full and half speed.
"""
import shutil
import sys
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
WS = Path.home() / ".music-practice-browser-check-highway"
shutil.rmtree(WS, ignore_errors=True)
truth, stems = SD.song_with_drums(OUT / "hw_song.wav", OUT / "hw_stems")
ws = Workspace(WS)
c = Conductor(ws)
c.reg._plugins.insert(0, StandInSeparator(stems))
launch(build_app(c), ws, server_port=7874, prevent_thread_lock=True, quiet=True)

FAKE_MIDI = """
(() => {
  const input = { id: "fake", name: "Simulated e-kit", onmidimessage: null };
  const inputs = new Map([["fake", input]]);
  navigator.requestMIDIAccess = async () => ({ inputs, outputs: new Map(), onstatechange: null });
  window.__kitHit = (note, vel, ts) => input.onmidimessage && input.onmidimessage({ data: [0x99, note, vel], timeStamp: ts });
  window.__kitCC = (cc, val) => input.onmidimessage && input.onmidimessage({ data: [0xB9, cc, val], timeStamp: performance.now() });
  // robot drummer: hits upcoming notes at audio time n.t + offset (offset given in REAL ms)
  window.__robot = null;
  window.__startRobot = (cfg) => {
    const r = document.querySelector('.mp-player--drums'), hw = r.__mpHW, a = r.querySelector('.mp-audio');
    const NOTE = { kick: 36, snare: 38, hihat: 42, crash: 49, ride: 51, tom_high: 48, tom_mid: 45, tom_floor: 43 };
    const scored = (p) => { const el = r.querySelector(`.mp-score-on[data-p="${p}"]`); return el ? el.checked : true; };
    let k = hw.notes.findIndex((n) => n.t >= a.currentTime + 0.05), stop = false;
    let count = 0;
    const rate = () => a.playbackRate;
    let seen = a.currentTime;
    (function loop() {
      if (stop) return;
      if (a.currentTime < seen - 0.2) k = hw.notes.findIndex((n) => n.t >= a.currentTime - 0.01);   // loop wrapped
      seen = a.currentTime;
      while (k >= 0 && k < hw.notes.length) {
        const n = hw.notes[k];
        if (!scored(n.piece) || (cfg.skip && cfg.skip(n, count))) { k++; count++; continue; }
        const due = n.t + (cfg.offsetMs / 1000) * rate();
        if (a.currentTime < due) break;
        const lateBy = (a.currentTime - due) / rate() * 1000;            // how late this frame is
        const piece = cfg.wrong && cfg.wrong(n, count) ? cfg.wrong(n, count) : n.piece;
        window.__kitHit(cfg.note && cfg.note[piece] || NOTE[piece], 100, performance.now() - lateBy);
        k++; count++;
      }
      requestAnimationFrame(loop);
    })();
    window.__robot = { stop: () => { stop = true; } };
  };
})();
"""

results = {}
def check(name, ok, detail=""):
    results[name] = ok
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

P = "document.querySelector('.mp-player--drums')"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1600, "height": 1100})
    ctx.add_init_script(FAKE_MIDI)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("http://127.0.0.1:7874/?__theme=dark")
    pg.evaluate("() => localStorage.clear()")
    assert pg.get_by_label("I play").input_value() == "drums"
    pg.get_by_text("Use an audio file instead").click()
    pg.locator("input[type=file]").set_input_files(str(OUT / "hw_song.wav")); time.sleep(1.5)
    pg.get_by_role("button", name="Analyze song").click()
    pg.wait_for_selector(".mp-player--drums[data-ready] .mp-hw", state="attached", timeout=300000)
    pg.wait_for_function(f"() => {P}.dataset.stemsReady === '1'", timeout=60000)
    pg.wait_for_function(f"() => {P}.querySelector('.mp-audio').readyState >= 2"); time.sleep(0.8)

    pg.locator(".mp-show-hw").click(); time.sleep(0.3)
    vis = pg.evaluate(f"() => ({{ hw: !{P}.querySelector('.mp-hw').hidden, grid: getComputedStyle({P}.querySelector('.mp-sheet')).display }})")
    check("Highway button swaps the grid for the highway", vis["hw"] and vis["grid"] == "none", str(vis))
    time.sleep(0.8)
    inview = pg.evaluate(f"() => {{ const r = {P}.querySelector('.mp-hw-canvas').getBoundingClientRect(); return [r.top, r.bottom, innerHeight]; }}")
    check("opening the highway scrolls it fully into view", inview[0] >= 0 and inview[1] <= inview[2] + 1, str([round(x) for x in inview]))
    pg.locator(".mp-hw-play").click(); time.sleep(2.6)
    playing = pg.evaluate(f"() => !{P}.querySelector('.mp-audio').paused")
    pg.locator(".mp-hw-play").click(); time.sleep(0.2)
    paused = pg.evaluate(f"() => {P}.querySelector('.mp-audio').paused")
    check("the highway's own play/pause button works", playing and paused, f"playing={playing} paused={paused}")
    pg.locator(".mp-midi-connect").click(); time.sleep(0.3)
    st = pg.evaluate(f"() => {P}.querySelector('.mp-midi-status').textContent")
    check("connects to the MIDI kit", "Simulated e-kit" in st, st)

    stats = lambda: pg.evaluate(f"() => {P}.__mpHW.stats()")
    def run(offset_ms, seconds=7.0, start=2.0, **extra):
        pg.evaluate(f"() => {{ {P}.__mpHW.reset(); const a = {P}.querySelector('.mp-audio'); a.pause(); a.currentTime = {start}; }}")
        pg.evaluate(f"() => {{ {P}.querySelector('.mp-countin').checked = false; }}")
        time.sleep(0.4)
        cfg = "{offsetMs: %s%s}" % (offset_ms, "".join(f", {k}: {v}" for k, v in extra.items()))
        pg.evaluate(f"() => {{ {P}.querySelector('.mp-audio').play(); window.__startRobot({cfg}); }}")
        time.sleep(seconds)
        pg.evaluate(f"() => {{ window.__robot.stop(); {P}.querySelector('.mp-audio').pause(); }}")
        time.sleep(0.2)
        return stats()

    s = run(0)
    pg.screenshot(path=str(OUT / "hw_play.png"))
    n = s["perfect"] + s["good"] + s["ok"]
    check("on-time hits are Perfect (no misses, no extras)",
          s["perfect"] >= 0.95 * n and s["miss"] <= 1 and s["extra"] == 0 and n >= 20,
          f"{s['perfect']} perfect / {n} hit, {s['miss']} missed, {s['extra']} extra, acc {s['acc']:.0f}%")
    check("accuracy near 100% for a perfect player", s["acc"] >= 97, f"{s['acc']:.1f}%")

    s = run(45)
    check("45 ms late -> Good, and the tendency says dragging ~45 ms",
          s["good"] >= 0.9 * (s["perfect"] + s["good"] + s["ok"]) and 38 <= s["mean"] <= 52,
          f"good {s['good']} perfect {s['perfect']} ok {s['ok']}, mean {s['mean']:.1f} ms")
    s = run(-80)
    check("80 ms early -> OK, tendency says rushing ~80 ms",
          s["ok"] >= 0.9 * (s["perfect"] + s["good"] + s["ok"]) and -88 <= s["mean"] <= -72,
          f"ok {s['ok']}, mean {s['mean']:.1f} ms")
    s = run(220, skip="(n, i) => n.piece !== 'snare'")   # snares are 1 s apart: no neighbor to confuse
    snares = pg.evaluate(f"() => {P}.__mpHW.notes.filter(n => n.piece === 'snare' && n.t > 2.05 && n.t < 2 + 6.6).length")
    check("220 ms late is outside the window: no note counts as played, each late hit is 'extra'",
          s["perfect"] + s["good"] + s["ok"] == 0 and abs(s["extra"] - snares) <= 1,
          f"played 0, extra {s['extra']} for {snares} late snares, missed {s['miss']}")
    s = run(0, skip="(n, i) => n.piece === 'snare'")
    snares = pg.evaluate(f"() => {P}.__mpHW.notes.filter(n => n.piece === 'snare' && n.t > 2.05 && n.t < 2 + 6.6).length")
    check("skipped snares are counted as missed", abs(s["miss"] - snares) <= 1 and s["extra"] == 0,
          f"missed {s['miss']}, snares in window {snares}")
    s = run(0, wrong="(n, i) => n.piece === 'hihat' && i % 2 ? 'snare' : null")
    check("hitting the wrong pad: a miss on the hi-hat and an extra hit",
          s["miss"] >= 4 and s["extra"] >= 4, f"missed {s['miss']}, extra {s['extra']}")

    # half speed: 40 ms late in REAL time must still read ~40 ms (judged in real time)
    pg.locator(".mp-player--drums .mp-speed").fill("50"); pg.locator(".mp-player--drums .mp-speed").dispatch_event("input")
    s = run(40, seconds=9.0)
    check("at 50% speed, timing is judged in real time (40 ms late reads ~40 ms)",
          s["good"] >= 0.85 * (s["perfect"] + s["good"] + s["ok"]) and 33 <= s["mean"] <= 47,
          f"good {s['good']} perfect {s['perfect']}, mean {s['mean']:.1f} ms")
    pg.locator(".mp-player--drums .mp-speed").fill("100"); pg.locator(".mp-player--drums .mp-speed").dispatch_event("input")

    # latency compensation: the kit reports 30 ms late, offset 30 ms makes it Perfect
    pg.evaluate(f"() => {{ const o = {P}.querySelector('.mp-offset'); o.value = 30; o.dispatchEvent(new Event('input')); }}")
    s = run(30)
    check("latency offset compensates a late-reporting kit (30 ms late + 30 ms offset = Perfect)",
          s["perfect"] >= 0.9 * (s["perfect"] + s["good"] + s["ok"]) and abs(s["mean"]) < 8, f"mean {s['mean']:.1f} ms")
    pg.evaluate(f"() => {{ const o = {P}.querySelector('.mp-offset'); o.value = 0; o.dispatchEvent(new Event('input')); }}")

    # calibration: hit 25 ms after each of the 8 clicks -> offset becomes +25 ms
    pg.locator(".mp-calibrate").click(); time.sleep(0.1)
    pg.evaluate(f"""() => {{ const cl = {P}.__mpCalibClicks;
        cl.forEach((c) => setTimeout(() => window.__kitHit(38, 100, c + 25), Math.max(0, c + 25 - performance.now()) + 5)); }}""")
    time.sleep(6.0)
    off = pg.evaluate(f"() => parseInt({P}.querySelector('.mp-offset').value, 10)")
    msg = pg.evaluate(f"() => {P}.querySelector('.mp-midi-status').textContent")
    check("calibration measures the kit's latency from 8 clicks", abs(off - 25) <= 2, f"offset {off} ms; '{msg}'")
    pg.evaluate(f"() => {{ const o = {P}.querySelector('.mp-offset'); o.value = 0; o.dispatchEvent(new Event('input')); }}")

    # learn: map an unusual pad number to the snare
    pg.locator('.mp-learn[data-p="snare"]').click()
    pg.evaluate("() => window.__kitHit(99, 100, performance.now())")
    learned = pg.evaluate("() => JSON.parse(localStorage.getItem('music-practice-midi')).map['99']")
    s = run(0, note="{snare: 99}")
    check("learn maps a new pad to a lane and it scores", learned == "snare" and s["miss"] <= 1 and s["extra"] == 0,
          f"pad 99 -> {learned}; missed {s['miss']}")

    # loop passes: loop bars 2-3, perfect robot -> a list of passes near 100%
    pg.evaluate(f"""() => {{ const r = {P}; r.__mpHW.reset(); r.querySelector('.mp-loop-on').checked = true;
        r.querySelector('.mp-from').value = 2; r.querySelector('.mp-to').value = 3;
        r.querySelector('.mp-to').dispatchEvent(new Event('change')); r.querySelector('.mp-countin').checked = false; }}""")
    pg.evaluate(f"() => {{ {P}.querySelector('.mp-audio').currentTime = 30; }}"); time.sleep(0.3)
    pg.evaluate("() => window.__startRobot({offsetMs: 0})")               # robot follows the jump
    pg.locator(".mp-player--drums .mp-play").click()                     # loop on: play jumps to bar 2
    time.sleep(14.5)                                     # ~3.5 passes of a 4 s loop
    pg.evaluate(f"() => {{ window.__robot.stop(); {P}.querySelector('.mp-audio').pause(); }}")
    s = stats()
    passes = s["passes"]
    check("each loop pass gets its own score", len(passes) >= 2 and all(x["acc"] >= 90 for x in passes),
          str([round(x["acc"]) for x in passes]))
    shown = pg.evaluate(f"() => {P}.querySelectorAll('.mp-hw-passes li').length")
    check("pass results are listed in the score panel", shown == len(passes), f"{shown} listed")
    pg.screenshot(path=str(OUT / "hw_full.png"))
    pg.evaluate("() => { window.__kitHit(77, 90, performance.now()); window.__kitCC(4, 64); }")
    mon = pg.evaluate(f"() => {P}.querySelector('.mp-midi-mon').textContent")
    check("MIDI monitor shows the note and the hi-hat pedal", "note 77" in mon and "velocity 90" in mon and "CC4) 64" in mon, mon)
    check("no JavaScript errors", not errors, "; ".join(errors)[:300])
    b.close()

print(f"\n{sum(results.values())}/{len(results)} checks passed")
