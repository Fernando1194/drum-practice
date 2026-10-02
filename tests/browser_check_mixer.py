"""Live mixer in headless Chromium: 6 stems play in sync, volumes/mute/solo/presets work."""
import shutil
import sys
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
WS = Path.home() / ".music-practice-browser-check-mixer"
shutil.rmtree(WS, ignore_errors=True)
_, stems = make_song(OUT / "mx_song.wav", stems_dir=OUT / "mx_stems")
ws = Workspace(WS)
c = Conductor(ws)
c.reg._plugins.insert(0, StandInSeparator(stems))
launch(build_app(c), ws, server_port=7872, prevent_thread_lock=True, quiet=True)

results = {}
def check(name, ok, detail=""):
    results[name] = ok
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

P = "document.querySelector('.mp-player[data-ready]:not(.mp-empty)')"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("http://127.0.0.1:7872/?__theme=dark")
    pg.wait_for_selector("text=Paste a song link above")
    pg.get_by_label("I play").click(); pg.get_by_role("option", name="guitar").click()
    pg.get_by_text("Or upload an audio file").click()
    pg.locator("input[type=file]").set_input_files(str(OUT / "mx_song.wav"))
    time.sleep(1.5)
    pg.get_by_role("button", name="Analyze song").click()
    pg.wait_for_selector(".mp-player[data-ready]:not(.mp-empty) .mp-strip", timeout=300000)
    pg.wait_for_function(f"() => {P}.dataset.stemsReady === '1'", timeout=60000)
    pg.wait_for_function(f"() => {P}.querySelector('.mp-audio').readyState >= 2 && {P}.__mpFollowers.every(f => f.readyState >= 2)", timeout=30000)
    check("all 6 stems load (incl. the 6th, which used to stall)", True)
    time.sleep(1.0)
    gains = lambda: pg.evaluate(f"() => JSON.parse({P}.dataset.gains)")
    names = pg.evaluate(f"() => [...{P}.querySelectorAll('.mp-strip')].map(s => s.dataset.stem)")
    check("one mixer strip per stem", names == ["vocals", "drums", "bass", "guitar", "piano", "other"], str(names))
    g = gains()
    check("starts as play-along: guitar silent, the rest at full", g["guitar"] == 0 and all(v == 1 for k, v in g.items() if k != "guitar"), str(g))

    def play_and_measure(seconds, start=1.0):
        pg.evaluate(f"""() => {{ const r = {P}; r.querySelector('.mp-countin').checked = false;
            r.dataset.driftMax = '0'; r.querySelector('.mp-audio').currentTime = {start}; }}""")
        time.sleep(0.4)
        pg.evaluate(f"() => {P}.querySelector('.mp-audio').play()")
        samples = []
        t_end = time.time() + seconds
        while time.time() < t_end:
            time.sleep(0.2)
            samples.append(pg.evaluate(f"""() => {{ const r = {P}, a = r.querySelector('.mp-audio');
                return [a.currentTime, parseFloat(r.dataset.drift || '0')]; }}"""))
        pg.evaluate(f"() => {P}.querySelector('.mp-audio').pause()")
        settled = [d for t, d in samples[5:]]          # after the first second
        return max(settled), sum(settled) / len(settled), samples[-1][0] - samples[0][0]

    worst, mean, advanced = play_and_measure(6)
    check("100% speed: stems stay within 30 ms of each other", worst < 0.03,
          f"worst {worst * 1000:.0f} ms, mean {mean * 1000:.0f} ms, played {advanced:.1f} s")
    pg.locator(".mp-player:not(.mp-empty) .mp-speed").fill("50")
    pg.locator(".mp-player:not(.mp-empty) .mp-speed").dispatch_event("input")
    worst, mean, advanced = play_and_measure(6, start=20.0)
    check("50% speed: stems stay within 30 ms of each other", worst < 0.03 and 2.0 < advanced < 3.4,
          f"worst {worst * 1000:.0f} ms, mean {mean * 1000:.0f} ms, played {advanced:.1f} s of song in ~6 s")
    pg.locator(".mp-player:not(.mp-empty) .mp-speed").fill("100")
    pg.locator(".mp-player:not(.mp-empty) .mp-speed").dispatch_event("input")

    # jumps while playing: click bars far apart, drift must recover fast
    pg.evaluate(f"() => {{ {P}.dataset.driftMax = '0'; {P}.querySelector('.mp-audio').play(); }}")
    for i in (10, 3, 25, 7):
        pg.locator(".mp-player:not(.mp-empty) .mp-bar").nth(i).click()
        time.sleep(1.2)
    drift_after_jumps = pg.evaluate(f"() => parseFloat({P}.dataset.drift)")
    pg.evaluate(f"() => {P}.querySelector('.mp-audio').pause()")
    check("after jumping around while playing, stems are back in sync", drift_after_jumps < 0.03,
          f"{drift_after_jumps * 1000:.0f} ms")

    pg.locator(".mp-player:not(.mp-empty) .mp-src").select_option("full")
    g = gains()
    check("preset 'Full song' sets every stem to 100%", all(v == 1 for v in g.values()), str(g))
    pg.locator(".mp-player:not(.mp-empty) .mp-src").select_option("solo")
    g = gains()
    check("preset 'Only guitar' leaves just the guitar", g["guitar"] == 1 and all(v == 0 for k, v in g.items() if k != "guitar"), str(g))
    pg.locator(".mp-player:not(.mp-empty) .mp-src").select_option("full")
    w = pg.evaluate("() => document.querySelector('.mp-strip[data-stem=vocals] input[type=range]').getBoundingClientRect().width")
    check("volume sliders keep a usable width next to the typed box", w >= 60, f"{w:.0f}px")
    pg.locator('.mp-strip[data-stem="vocals"] input[type=range]').fill("30")
    pg.locator('.mp-strip[data-stem="vocals"] input[type=range]').dispatch_event("input")
    g = gains()
    sel = pg.evaluate(f"() => {P}.querySelector('.mp-src').value")
    check("lowering one slider sets that stem's volume and switches the menu to 'Custom mix'",
          g["vocals"] == 0.3 and sel == "custom", f"{g} menu={sel}")
    pg.locator('.mp-strip[data-stem="drums"] .mp-m').click()
    g = gains()
    check("M mutes a stem", g["drums"] == 0 and g["bass"] == 1, str(g))
    pg.locator('.mp-strip[data-stem="bass"] .mp-s').click()
    g = gains()
    check("S solos a stem (everything else silent)", g["bass"] == 1 and all(v == 0 for k, v in g.items() if k != "bass"), str(g))
    pg.locator('.mp-strip[data-stem="bass"] .mp-s').click()
    g = gains()
    check("un-solo restores the custom mix", g["vocals"] == 0.3 and g["drums"] == 0 and g["guitar"] == 1, str(g))
    vols = pg.evaluate(f"() => {P}.querySelector('.mp-audio').volume")
    check("volumes reach the actual audio elements", abs(vols - g["vocals"]) < 0.01, f"master (vocals) element volume {vols}")
    pg.screenshot(path=str(OUT / "mx_mixer.png"), clip={"x": 0, "y": 380, "width": 1600, "height": 330})
    widths = pg.evaluate(f"() => [...{P}.querySelectorAll('.mp-strip')].map(x => Math.round(x.getBoundingClientRect().width))")
    check("mixer strips have equal widths (none stretched)", max(widths) - min(widths) <= 2, str(widths))
    pg.locator(".mp-mixer summary").click(); time.sleep(0.3)
    saved = pg.evaluate("() => JSON.parse(localStorage.getItem('music-practice-layout') || '{}').mixerOpen")
    old_key = pg.evaluate(f"() => {P}.dataset.key")
    pg.get_by_role("button", name="Analyze song").click()      # re-render the player from scratch
    pg.wait_for_function(f"() => {P} && {P}.dataset.key !== '{old_key}'", timeout=300000); time.sleep(0.5)
    check("a collapsed mixer stays collapsed in the next session (remembered)",
          saved is False and pg.evaluate(f"() => !{P}.querySelector('.mp-mixer').open"), f"saved mixerOpen={saved}")
    check("no JavaScript errors", not errors, "; ".join(errors)[:300])
    b.close()

print(f"\n{sum(results.values())}/{len(results)} checks passed")
