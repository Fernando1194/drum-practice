"""Browser check for the synced player (needs: pip install playwright && playwright install chromium).
Run: python tests/browser_check.py <output folder for screenshots>"""
import json
import sys
import threading
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
import os
os.environ["MUSIC_PRACTICE_ALL_INSTRUMENTS"] = "1"   # this check plays a guitar song
from playwright.sync_api import sync_playwright

from musicpractice.agents import Conductor
from musicpractice.app import build_app, launch
from musicpractice.core import Workspace
from synth import make_song
from test_pipeline import StandInSeparator

OUT = Path(sys.argv[1])
WS = Path.home() / ".music-practice-browser-check"
import shutil
shutil.rmtree(WS, ignore_errors=True)
truth, stems = make_song(OUT / "pl_song.wav", stems_dir=OUT / "pl_stems")
ws = Workspace(WS)
c = Conductor(ws)
c.reg._plugins.insert(0, StandInSeparator(stems))
launch(build_app(c), ws, server_port=7865, prevent_thread_lock=True, quiet=True)

results = {}
def check(name, ok, detail=""):
    results[name] = ok
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

JS_STATE = """() => { const r = document.querySelector('.mp-player[data-ready]:not(.mp-empty)');
  const a = r.querySelector('.mp-audio'); const D = JSON.parse(r.dataset.mp);
  const now = [...r.querySelectorAll('.mp-bar')].findIndex(b => b.classList.contains('is-now'));
  let expect = -1; D.bars.forEach((b, i) => { if (b <= a.currentTime + 0.04) expect = i; });  // player pre-roll
  return { t: a.currentTime, paused: a.paused, rate: a.playbackRate, src: a.currentSrc,
           dur: a.duration, ready: a.readyState, nowBar: now, expectBar: expect,
           chordNow: r.querySelector('.mp-chord-now').textContent,
           nextChord: r.querySelector('.mp-chord-next').textContent,
           nextIn: r.querySelector('.mp-next-in').textContent,
           from: r.querySelector('.mp-from').value, to: r.querySelector('.mp-to').value,
           speedOut: r.querySelector('.mp-speed-out').textContent,
           bars: D.bars.length, tabRows: r.querySelectorAll('.mp-bar .mp-row').length }; }"""

with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1400, "height": 1000})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("http://127.0.0.1:7865/?__theme=dark")
    pg.wait_for_selector("text=Paste a song link above")
    pg.get_by_label("I play").click(); pg.get_by_role("option", name="guitar").click()
    pg.wait_for_selector("text=Paste a song link above")
    pg.screenshot(path=str(OUT / "p0_empty.png"), full_page=True)

    pg.get_by_text("Use an audio file instead").click()
    pg.locator("input[type=file]").set_input_files(str(OUT / "pl_song.wav"))
    time.sleep(1.5)
    pg.get_by_role("button", name="Analyze song").click()
    pg.wait_for_selector(".mp-player[data-ready]:not(.mp-empty) .mp-bar", timeout=240000)
    pg.wait_for_function("() => document.querySelector('.mp-player[data-ready]:not(.mp-empty)').dataset.stemsReady === '1'", timeout=60000)
    pg.wait_for_function("() => { const a = document.querySelector('.mp-player:not(.mp-empty) .mp-audio'); return a && a.readyState >= 1; }", timeout=30000)
    s = pg.evaluate(JS_STATE)
    check("player rendered with every bar", s["bars"] == 32, f"{s['bars']} bars")
    check("tab drawn (6 strings per bar)", s["tabRows"] == 32 * 6, f"{s['tabRows']} rows")
    check("audio served by Gradio", s["dur"] and abs(s["dur"] - 65) < 1.5, f"duration {s['dur']}")
    g = pg.evaluate("() => JSON.parse(document.querySelector('.mp-player[data-ready]:not(.mp-empty)').dataset.gains || '{}')")
    check("starts as play-along (guitar stem silent in the live mix)", g.get("guitar") == 0 and g.get("bass") == 1, str(g))
    pg.screenshot(path=str(OUT / "p1_loaded.png"), full_page=True)
    time.sleep(1.0)
    geo = pg.evaluate("""() => { const r = document.querySelector('.mp-player[data-ready]:not(.mp-empty)');
        const sh = r.querySelector('.mp-sheet'), sr = sh.getBoundingClientRect();
        const bars = [...r.querySelectorAll('.mp-bar')].map(b => b.getBoundingClientRect());
        const full = bars.filter(b => b.top >= sr.top - 1 && b.bottom <= sr.bottom + 1);
        const partial = bars.filter(b => b.bottom > sr.top + 1 && b.top < sr.bottom - 1).length - full.length;
        return { lines: new Set(full.map(b => Math.round(b.top))).size, partial,
                 fret: parseFloat(getComputedStyle(r.querySelector('.mp-row i')).fontSize) }; }""")
    check("guitar tab: two full lines visible, bigger fret numbers", geo["lines"] == 2 and geo["partial"] == 0 and geo["fret"] >= 15, str(geo))

    # play with count-in (4 beats at 120 BPM = 2 s of clicks before audio)
    pg.locator(".mp-play").click()
    time.sleep(1.0)
    s = pg.evaluate(JS_STATE)
    check("count-in holds audio", s["paused"] and s["t"] < 0.05, f"t={s['t']:.2f}")
    time.sleep(3.2)
    s = pg.evaluate(JS_STATE)
    check("playing after count-in", not s["paused"] and s["t"] > 0.5, f"t={s['t']:.2f}")
    check("highlighted bar matches audio time", s["nowBar"] == s["expectBar"],
          f"bar {s['nowBar'] + 1} vs {s['expectBar'] + 1}")
    check("now-chord matches the song", s["chordNow"] == truth[s["expectBar"]],
          f"{s['chordNow']} vs {truth[s['expectBar']]}")
    check("next chord shown", s["nextChord"].strip() != "", f"{s['nextChord']} {s['nextIn']}")

    # jump far ahead: highlight must follow
    pg.evaluate("() => { document.querySelector('.mp-player:not(.mp-empty) .mp-audio').currentTime = 37.1; }")
    time.sleep(0.4)
    s = pg.evaluate(JS_STATE)
    check("highlight follows a seek", s["nowBar"] == s["expectBar"] == 18, f"bar {s['nowBar'] + 1}")
    check("chord after seek", s["chordNow"] == truth[s["expectBar"]], f"{s['chordNow']} vs {truth[s['expectBar']]}")
    pg.screenshot(path=str(OUT / "p2_playing.png"))

    # speed 50%: time advances at half rate, pitch preserved flag on
    pg.locator(".mp-speed").fill("50")
    pg.locator(".mp-speed").dispatch_event("input")
    t0 = pg.evaluate(JS_STATE)["t"]; time.sleep(2.0); s = pg.evaluate(JS_STATE)
    adv = s["t"] - t0
    check("speed 50% halves playback", abs(s["rate"] - 0.5) < 1e-6 and 0.8 < adv < 1.2,
          f"rate {s['rate']}, advanced {adv:.2f}s in 2s, label '{s['speedOut']}'")
    pg.locator(".mp-speed").fill("100"); pg.locator(".mp-speed").dispatch_event("input")

    # section click sets loop to that section
    pg.locator(".mp-sec").nth(1).click()
    time.sleep(0.4)
    s = pg.evaluate(JS_STATE)
    check("timeline click sets loop to section", (s["from"], s["to"]) == ("9", "16"),
          f"{s['from']}-{s['to']}")
    check("timeline click jumps to section", s["expectBar"] == 8, f"bar {s['expectBar'] + 1}")

    # loop bars 2-3 without count-in: must wrap from end of bar 3 to start of bar 2
    pg.locator(".mp-countin").uncheck()
    pg.locator(".mp-from").fill("2"); pg.locator(".mp-to").fill("3")
    pg.locator(".mp-to").dispatch_event("change")
    pg.evaluate("() => { document.querySelector('.mp-player:not(.mp-empty) .mp-audio').currentTime = 5.6; }")
    seen = []
    for _ in range(14):
        time.sleep(0.25); seen.append(round(pg.evaluate(JS_STATE)["t"], 2))
    wrapped = any(a > 5.5 and b_ < 2.6 for a, b_ in zip(seen, seen[1:]))
    check("loop wraps bar 3 -> bar 2", wrapped and max(seen) < 6.2 and min(seen) >= 1.9, str(seen))

    # switch source keeps position
    t_before = pg.evaluate(JS_STATE)["t"]
    pg.locator(".mp-src").select_option("full")
    time.sleep(0.8)
    s = pg.evaluate(JS_STATE)
    g = pg.evaluate("() => JSON.parse(document.querySelector('.mp-player[data-ready]:not(.mp-empty)').dataset.gains)")
    check("switching to 'Full song' changes the mix without interrupting playback",
          all(v == 1 for v in g.values()) and not s["paused"] and abs(s["t"] - t_before) < 1.5,
          f"{t_before:.2f} -> {s['t']:.2f} {g}")

    # play / pause behaves like a normal player: pause stops exactly there, play resumes there
    ST = """() => { const r = document.querySelector('.mp-player[data-ready]:not(.mp-empty)');
        const a = r.querySelector('.mp-audio');
        return [a.currentTime, a.paused, r.classList.contains('is-counting')]; }"""
    pb = pg.locator(".mp-player:not(.mp-empty) .mp-play")
    pg.locator(".mp-loop-on").uncheck(); pg.locator(".mp-countin").check()
    pg.evaluate("() => { const a = document.querySelector('.mp-player:not(.mp-empty) .mp-audio'); a.pause(); }")
    pg.locator(".mp-bar").nth(4).click(); time.sleep(0.3)           # jump to bar 5
    pb.click(); time.sleep(0.5)
    t, paused, counting = pg.evaluate(ST)
    check("play from a bar line counts in", paused and counting, f"t={t:.2f}")
    pb.click(); time.sleep(3.0)
    t2, paused2, counting2 = pg.evaluate(ST)
    check("clicking during count-in cancels it (no surprise start)", paused2 and not counting2 and abs(t2 - t) < 0.01,
          f"t={t2:.2f} paused={paused2}")
    pb.click(); time.sleep(4.0)
    pb.click(); time.sleep(0.2)
    t3, paused3, _ = pg.evaluate(ST)
    time.sleep(1.0)
    t4, paused4, _ = pg.evaluate(ST)
    check("pause stops at that moment and stays", paused3 and paused4 and abs(t4 - t3) < 0.01 and t3 > t + 1,
          f"paused at {t3:.2f}, 1 s later {t4:.2f}")
    pb.click(); time.sleep(0.4)
    t5, paused5, counting5 = pg.evaluate(ST)
    check("play resumes from the same spot right away (no count-in mid-bar)",
          not paused5 and not counting5 and 0.2 < t5 - t3 < 0.6, f"{t3:.2f} -> {t5:.2f}")

    # keyboard: space pauses
    pg.locator("body").click(position={"x": 5, "y": 5})
    pg.keyboard.press("Space"); time.sleep(0.3)
    check("space bar pauses", pg.evaluate(JS_STATE)["paused"])

    check("no JavaScript errors", not errors, "; ".join(errors)[:300])
    pg.screenshot(path=str(OUT / "p3_full.png"), full_page=True)
    pg.set_viewport_size({"width": 400, "height": 900})
    time.sleep(0.5)
    pg.screenshot(path=str(OUT / "p4_mobile.png"), full_page=False)
    b.close()

print(f"\n{sum(results.values())}/{len(results)} checks passed")
