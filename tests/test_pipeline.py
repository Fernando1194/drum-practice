"""End-to-end tests on synthetic songs with known answers.

Demucs is replaced by a stand-in that returns the true stems, so everything after
separation (analysis, transcription, tab, practice, export) runs for real.
"""
import importlib.util
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from musicpractice.agents import Conductor
from musicpractice.core import Registry, Song, Workspace
from musicpractice.plugins.harmony import HarmonyAnalyzer
from musicpractice.plugins.input import DownloadError, import_url
from musicpractice.plugins.rhythm import LibrosaBeatTracker
from musicpractice.plugins.structure import StructureAnalyzer
from musicpractice.plugins.tab import arrange
from synth import SR, make_song

HAS_BASIC_PITCH = importlib.util.find_spec("basic_pitch") is not None


class StandInSeparator:
    """Pretends to be Demucs: copies the synthetic song's true stems."""
    name = "stand-in"
    requires = ("audio:mix",)
    produces = tuple(f"stem:{s}" for s in ("vocals", "drums", "bass", "guitar", "piano", "other"))

    def __init__(self, stems: dict[str, str]):
        self.stems = stems

    def available(self):
        return True

    def run(self, song, inputs):
        out = {}
        silent = None
        for name in ("vocals", "drums", "bass", "guitar", "piano", "other"):
            dest = song.dir / "stems" / f"{name}.wav"
            dest.parent.mkdir(exist_ok=True)
            if name in self.stems:
                shutil.copy(self.stems[name], dest)
            else:
                if silent is None:
                    y, _ = sf.read(self.stems["guitar"])
                    silent = np.zeros_like(y)
                sf.write(dest, silent, SR)
            out[f"stem:{name}"] = str(dest)
        return out


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth")
    truth, stems = make_song(d / "song.wav", stems_dir=d / "stems")
    return d / "song.wav", truth, stems


@pytest.fixture(scope="module")
def analyzed(synth, tmp_path_factory):
    path, truth, stems = synth
    song = Workspace(tmp_path_factory.mktemp("ws")).import_song(path)
    reg = Registry()
    for p in (LibrosaBeatTracker(), HarmonyAnalyzer(), StructureAnalyzer()):
        reg.register(p)
    reg.ensure(song, "chord_chart")
    reg.ensure(song, "sections")
    return song, truth


# ---------------- input ----------------

def test_url_import_downloads_converts_and_caches(synth, tmp_path):
    path, _, _ = synth
    mp3 = tmp_path / "song.mp3"
    import subprocess
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-t", "5", str(mp3)],
                   check=True)
    ws = Workspace(tmp_path / "ws")
    a = import_url(ws, mp3.as_uri(), allow_file_urls=True)
    b = import_url(ws, mp3.as_uri(), allow_file_urls=True)
    assert a.id == b.id and Path(a.get("audio:mix")).suffix == ".wav"
    assert sf.info(a.get("audio:mix")).duration == pytest.approx(5, abs=0.1)


@pytest.mark.parametrize("url", ["https://open.spotify.com/track/xyz",
                                 "https://music.apple.com/us/album/1",
                                 "ftp://example.com/a.mp3", "hello"])
def test_url_rejections(tmp_path, url):
    with pytest.raises(DownloadError):
        import_url(Workspace(tmp_path), url)


# ---------------- analysis ----------------

def test_tempo_bars_key(analyzed):
    song, truth = analyzed
    assert song.get("tempo") == pytest.approx(120, abs=1)
    assert len(song.get("bars")) == len(truth)
    assert song.get("key")["name"] == "C major"


def test_chords_per_bar(analyzed):
    song, truth = analyzed
    chart = song.get("chord_chart")
    correct = sum(c == [t] for c, t in zip(chart, truth))
    assert correct / len(truth) >= 0.9


def test_sections_found_and_repeats_lettered(analyzed):
    song, _ = analyzed
    secs = song.get("sections")
    assert [(s["start_bar"], s["end_bar"]) for s in secs] == [(1, 8), (9, 16), (17, 24), (25, 32)]
    assert [s["letter"] for s in secs] == ["A", "B", "A", "B"]


@pytest.mark.parametrize("cut_beats,bpm", [(1, 120), (3, 140), (2, 96)])
def test_bar_grid_survives_pickup_and_tempo(tmp_path, cut_beats, bpm):
    """Song starting mid-bar: chords must still line up with bar lines."""
    truth, _ = make_song(tmp_path / "s.wav", bpm=bpm)
    y, _ = sf.read(tmp_path / "s.wav")
    sf.write(tmp_path / "s.wav", y[int(cut_beats * 60 / bpm * SR):], SR)
    song = Workspace(tmp_path / "ws").import_song(tmp_path / "s.wav")
    reg = Registry()
    reg.register(LibrosaBeatTracker())
    reg.register(HarmonyAnalyzer())
    reg.ensure(song, "chord_chart")
    assert song.get("tempo") == pytest.approx(bpm, abs=1)
    chart = song.get("chord_chart")[1:]           # bar 1 is the partial pickup bar
    assert sum(c == [t] for c, t in zip(chart, truth[1:])) / len(chart) >= 0.9


# ---------------- tab ----------------

def melody(pitches, step=0.25):
    return [{"start": i * step, "end": i * step + 0.2, "pitch": p, "velocity": 0.8}
            for i, p in enumerate(pitches)]


def test_tab_keeps_fast_run_in_one_position():
    tab = arrange(melody([57, 60, 62, 64, 67, 69, 72, 74]), "guitar")
    frets = [f for ev in tab for _, f in ev["positions"] if f > 0]
    assert max(frets) - min(frets) <= 4


def test_tab_removes_overtones_and_gives_open_c():
    chord = [{"start": 0.0, "end": 1, "pitch": p, "velocity": v}
             for p, v in [(48, 0.7), (52, 0.6), (55, 0.5), (60, 0.38), (79, 0.4)]]
    tab = arrange(chord, "guitar")
    assert sorted(map(tuple, tab[0]["positions"])) == [(1, 3), (2, 2), (3, 0)]  # x-3-2-0


def test_bass_tab_in_range():
    tab = arrange(melody([28, 33, 40, 45, 26]), "bass")   # 26 is below low E: transposed
    assert all(0 <= f <= 20 and 0 <= s < 4 for ev in tab for s, f in ev["positions"])
    assert tab[-1]["transposed"]


# ---------------- full workflow ----------------

@pytest.mark.skipif(not HAS_BASIC_PITCH, reason="basic-pitch not installed")
def test_full_workflow(synth, tmp_path):
    path, truth, stems = synth
    c = Conductor(Workspace(tmp_path / "ws"))
    c.reg._plugins.insert(0, StandInSeparator(stems))

    song, log = c.process(str(path), "guitar")
    assert set(song.stems()) >= {"guitar", "bass", "drums", "vocals"}
    assert (song.dir / "isolated" / "guitar.wav").exists()
    assert (song.dir / "isolated" / "minus_guitar.wav").exists()

    # hints use stems: vocals only in B sections
    hints = [s["hint"] for s in song.get("sections")]
    assert hints[0] == "Intro" and hints[1] == "Chorus" and hints[3] == "Chorus"

    # first bar: C chord notes on the downbeat
    first = sorted(n["pitch"] for n in song.get("notes:guitar") if n["start"] < 0.1)
    assert {48, 52, 55} <= set(first)
    tab = c.transcription.tab_text(song, "guitar", 1, 2)
    assert "1 C" in tab and "2 G" in tab

    exs = c.practice.suggest(song, "guitar", 9, 16)
    assert exs[0].request.gains["guitar"] == 1.0           # listen to the part alone
    assert any(e.request.ladder for e in exs)
    busy = [e.request.start_bar for e in exs if e.title.split(". ")[1].startswith("Busy")]
    assert len(busy) == 2 and abs(busy[0] - busy[1]) >= 2   # no overlapping spots
    density = c.practice.bar_density(song, "guitar")
    assert 1.5 <= density[10] <= 2.5                         # 4 strums/bar at 120 BPM = 2/s
    out = c.practice.render(song, exs[-2].request)
    assert "minus-guitar" in out.name and sf.info(out).duration > 10

    zip_path = __import__("musicpractice.agents", fromlist=["export_bundle"]).export_bundle(
        song, "guitar", log)
    names = zipfile.ZipFile(zip_path).namelist()
    assert {"transcription.md", "guitar.mid", "guitar_notes.csv",
            "isolated/guitar.wav", "isolated/minus_guitar.wav"} <= set(names)
    assert any(n.startswith("practice/") for n in names)
    md = zipfile.ZipFile(zip_path).read("transcription.md").decode()
    assert "C major" in md and "## Guitar tab" in md


def test_workflow_without_separation_warns(synth, tmp_path, monkeypatch):
    path, _, _ = synth
    c = Conductor(Workspace(tmp_path / "ws"))
    song, log = c.process(str(path), "drums", separate=True, transcribe=True)
    assert any("Demucs" in n for n in log.notes)
    assert any("drums" in n for n in log.notes)
    assert song.has("sections") and not song.stems()


# ---------------- robustness: messy, real-world-like input ----------------

@pytest.mark.parametrize("seed", range(5))
def test_tab_never_crashes_on_random_notes(seed):
    """Real transcriptions contain wide chords, wrong notes and out-of-range pitches."""
    rng = np.random.default_rng(seed)
    notes = [{"start": round(float(i * 0.2), 2), "end": float(i * 0.2 + 0.15),
              "pitch": int(rng.integers(20, 100)), "velocity": float(rng.random())}
             for i in range(200) for _ in range(int(rng.integers(1, 7)))]
    for inst in ("guitar", "bass"):
        tab = arrange(notes, inst)
        assert len(tab) == 200
        assert all(0 <= f <= 22 for ev in tab for _, f in ev["positions"])


def test_render_tab_handles_empty_grid():
    from musicpractice.plugins.tab import render_tab
    assert render_tab([], "guitar", [], [], 10.0) == ""


# ---------------- drums ----------------

import synth_drums as SD
from musicpractice.plugins.drums import GM, PIECES, transcribe_drums


@pytest.mark.parametrize("kit_name", ["A", "B"])
def test_drum_transcription_accuracy(kit_name):
    hits, beat = SD.groove(16, 110)
    y, truth = SD.render(hits, 16 * 4 * beat, kit=SD.kit_b() if kit_name == "B" else None,
                         bleed_bpm=110)
    sc = SD.score([(h["time"], h["piece"]) for h in transcribe_drums(y, beat_s=beat)], truth)
    for piece in ("kick", "snare", "hihat", "ride"):
        assert sc[piece]["f1"] >= 0.9, (piece, sc[piece])
    for piece in ("crash", "tom_high", "tom_mid", "tom_floor"):
        assert sc[piece]["f1"] >= 0.75, (piece, sc[piece])


def test_drum_grid_shows_the_groove():
    from musicpractice.plugins.drum_tab import render_drum_tab
    hits, beat = SD.groove(4, 110)
    y, _ = SD.render(hits, 4 * 4 * beat, lead_in=0.0)
    found = transcribe_drums(y, beat_s=beat)
    beats = [i * beat for i in range(16)]
    tab = render_drum_tab(found, beats, beats[::4], 16 * beat)
    rows = {line[:2]: line[3:] for line in tab.splitlines() if "|" in line}
    assert rows["BD"].startswith("o-------o-o-----|")
    assert rows["SD"].startswith("----o-------o---|")
    assert rows["HH"].startswith("x-x-x-x-x-x-x-x-|")


def test_drum_workflow_and_export(tmp_path):
    truth, stems = SD.song_with_drums(tmp_path / "song.wav", tmp_path / "stems")
    c = Conductor(Workspace(tmp_path / "ws"))
    c.reg._plugins.insert(0, StandInSeparator(stems))
    song, log = c.process(str(tmp_path / "song.wav"), "drums")
    hits = song.get("drums:hits")
    sc = SD.score([(h["time"], h["piece"]) for h in hits], truth)
    # Plumbing check. Accuracy is measured on real recordings by tests/benchmark_mdb.py:
    # synthetic cymbals don't sound like real ones, so only kick/snare are meaningful here.
    assert all(sc[p]["f1"] >= 0.85 for p in ("kick", "snare")), sc
    assert {h["piece"] for h in hits} <= set(PIECES)
    from musicpractice.plugins.drums_adtof import adtof_available
    assert adtof_available() or any("basic detector" in n for n in log.notes)
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(song.get("midi:drums"))
    assert pm.instruments[0].is_drum and {n.pitch for n in pm.instruments[0].notes} >= {36, 38, 42}
    assert c.practice.bar_density(song, "drums") is not None
    from musicpractice.ui import build_player
    html = build_player(song, "drums", [("full", "Full", song.get("audio:mix"))])
    assert "mp-kit-svg" in html and 'data-p="snare"' in html and "mp-player--drums" in html
    md = zipfile.ZipFile(__import__("musicpractice.agents", fromlist=["x"]).export_bundle(
        song, "drums", log)).read("transcription.md").decode()
    assert "Drum grid" in md and "BD|" in md


def test_cymbal_ring_rule_separates_crash_from_ride():
    from musicpractice.plugins.drums_adtof import CRASH_RING, cymbal_rings
    hits = [(0.5, "ride"), (1.0, "ride"), (1.5, "crash"), (3.5, "ride")]
    y, truth = SD.render(hits, 4.0, lead_in=0.0)
    rings = cymbal_rings(y, [t for t, _ in truth])
    labels = ["crash" if a >= CRASH_RING and b >= CRASH_RING else "ride" for a, b in rings]
    assert labels == [p for _, p in truth], rings


# ---------------- cache: transcriptions made from the mix are redone once stems exist ----------------

class _NoSeparator(StandInSeparator):
    def available(self):
        return False


@pytest.mark.skipif(not HAS_BASIC_PITCH, reason="basic-pitch not installed")
@pytest.mark.parametrize("instrument", ["guitar", "drums"])
def test_mix_transcription_is_redone_from_stem(tmp_path, instrument):
    if instrument == "drums":
        truth, stems = SD.song_with_drums(tmp_path / "song.wav", tmp_path / "stems")
    else:
        _, stems = make_song(tmp_path / "song.wav", stems_dir=tmp_path / "stems")
    ws = Workspace(tmp_path / "ws")

    # 1) first analysis without separation (as before Demucs was installed): from the mix
    c1 = Conductor(ws)
    c1.reg._plugins.insert(0, _NoSeparator(stems))
    song, log = c1.process(str(tmp_path / "song.wav"), instrument, separate=True)
    key = "drums:source" if instrument == "drums" else "notes:guitar:source"
    assert song.artifacts[key] == "mix"
    before = (song.get("drums:hits") if instrument == "drums" else song.get("notes:guitar"))

    # 2) separation now available: the same song must be re-transcribed from the stem
    c2 = Conductor(ws)
    c2.reg._plugins.insert(0, StandInSeparator(stems))
    song, log = c2.process(str(tmp_path / "song.wav"), instrument, separate=True)
    assert song.artifacts[key] == "stem"
    assert not any("No isolated" in n for n in log.notes)
    after = (song.get("drums:hits") if instrument == "drums" else song.get("notes:guitar"))
    assert after != before
    if instrument == "guitar":
        # the tab follows the new notes: every tab event time is a note start of the new notes
        starts = {round(n["start"], 3) for n in after}
        assert all(round(ev["time"], 3) in starts for ev in song.get("tab:guitar"))


# ---------------- fixing drums: sensitivity sliders and hand edits ----------------

def test_sensitivity_mapping_and_edit_merging():
    from musicpractice.plugins import drum_edit as DE
    for f in DE.FAMILIES:
        assert DE.threshold_for(f, 50) == DE.DEFAULT_THRESHOLDS[f]
        assert DE.threshold_for(f, 0) == DE.STRICTEST and DE.threshold_for(f, 100) == DE.CANDIDATE_MIN
        for s in (0, 13, 50, 77, 100):
            assert abs(DE.sensitivity_for(f, DE.threshold_for(f, s)) - s) < 0.2
    e = DE.merge_edits({"add": [], "remove": []}, add=[[1.0, "tom_high"]], remove=[[2.0, "snare"]])
    assert e == {"add": [[1.0, "tom_high"]], "remove": [[2.0, "snare"]]}
    # undoing in a later save cancels out instead of piling up
    e = DE.merge_edits(e, add=[[2.01, "snare"]], remove=[[0.99, "tom_high"]])
    assert e == {"add": [], "remove": []}
    hits = [{"time": 1.0, "piece": "snare", "velocity": 0.5, "score": 0.9},
            {"time": 1.5, "piece": "snare", "velocity": 0.5, "score": 0.1}]
    assert [h["time"] for h in DE.select(hits, DE.DEFAULT_THRESHOLDS)] == [1.0]
    out = DE.apply_edits(DE.select(hits, DE.DEFAULT_THRESHOLDS),
                         {"add": [[1.25, "tom_floor"], [1.25, "tom_floor"]], "remove": [[1.02, "snare"]]})
    assert [(h["time"], h["piece"]) for h in out] == [(1.25, "tom_floor")]   # no duplicates


def test_drum_fixes_rebuild_hits_midi_and_player(tmp_path):
    from musicpractice.plugins import drum_edit as DE
    from musicpractice.ui import build_player
    truth, stems = SD.song_with_drums(tmp_path / "song.wav", tmp_path / "stems")
    c = Conductor(Workspace(tmp_path / "ws"))
    c.reg._plugins.insert(0, StandInSeparator(stems))
    song, _ = c.process(str(tmp_path / "song.wav"), "drums")
    original = [(h["time"], h["piece"]) for h in song.get("drums:hits")]
    assert song.has("drums:candidates")
    kept, _ = DE.playable(DE.select(song.get("drums:candidates"), DE.DEFAULT_THRESHOLDS),
                          DE.DEFAULT_THRESHOLDS)
    assert [(h["time"], h["piece"]) for h in kept] == original

    snare = next(h for h in song.get("drums:hits") if h["piece"] == "snare")
    add_t = round(song.get("bars")[1] + 0.001, 3)
    edits = DE.merge_edits(DE.edits_of(song), add=[[add_t, "tom_floor"]],
                           remove=[[snare["time"], "snare"]])
    hits = DE.rebuild(song, edits=edits)
    got = {(h["time"], h["piece"]) for h in hits}
    assert (snare["time"], "snare") not in got and (add_t, "tom_floor") in got
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(song.get("midi:drums"))
    assert 41 in {n.pitch for n in pm.instruments[0].notes}            # the added floor tom

    # moving a slider keeps the hand edits
    if any(h.get("score") is not None for h in song.get("drums:candidates")):
        strict = {**DE.DEFAULT_THRESHOLDS, "hihat": DE.threshold_for("hihat", 0)}
        hits2 = DE.rebuild(Song.load(song.dir), thresholds=strict)
        assert (add_t, "tom_floor") in {(h["time"], h["piece"]) for h in hits2}
        assert sum(h["piece"] == "hihat" for h in hits2) <= sum(p == "hihat" for _, p in original)

    song = Song.load(song.dir)
    html = build_player(song, "drums", [("full", "Full", song.get("audio:mix"))])
    assert 'class="mp-fix"' in html and "mp-show-fix" in html
    assert 'data-p="tom_floor"' in html and "mp-row--empty" in html   # rows for every piece
    # back to the AI's version
    DE.rebuild(song, thresholds=dict(DE.DEFAULT_THRESHOLDS), edits={"add": [], "remove": []})
    assert [(h["time"], h["piece"]) for h in Song.load(song.dir).get("drums:hits")] == original


def test_three_hands_at_once_drops_the_weakest_but_never_the_hihat():
    from musicpractice.plugins import drum_edit as DE
    thr = DE.DEFAULT_THRESHOLDS
    h = lambda t, p, s: {"time": t, "piece": p, "velocity": .5, "score": s}
    hits = [h(1.0, "hihat", 0.25), h(1.01, "snare", 0.9), h(1.02, "tom_floor", 0.6),   # 3 hands
            h(1.0, "kick", 0.9),                                                         # foot: fine
            h(2.0, "snare", 0.5), h(2.0, "crash", 0.9), h(2.0, "kick", 0.9),             # 2 hands: fine
            h(3.0, "snare", 0.3), h(3.01, "crash", 0.9), h(3.02, "ride", 0.35)]          # ride weakest
    kept, n = DE.playable(hits, thr)
    got = {(x["time"], x["piece"]) for x in kept}
    assert n == 2
    assert (1.02, "tom_floor") not in got and (1.0, "hihat") in got and (1.01, "snare") in got
    assert (3.02, "ride") not in got and {(2.0, "snare"), (2.0, "crash")} <= got
    # hi-hat + ride at the same instant + snare: one cymbal reported twice, the weaker goes
    dup = [h(5.0, "hihat", 0.25), h(5.0, "ride", 0.9), h(5.0, "snare", 0.5)]
    kept, n = DE.playable(dup, thr)
    assert n == 1 and {x["piece"] for x in kept} == {"ride", "snare"}
    # hits without a score (basic detector) are never touched
    plain = [{k: v for k, v in x.items() if k != "score"} for x in hits]
    assert DE.playable(plain, thr) == (sorted(plain, key=lambda x: x["time"]), 0)


def test_repeated_patterns_are_found_in_the_drum_grid():
    from musicpractice.plugins.drum_tab import drum_bar_grids
    from musicpractice.plugins.drums import PIECES
    from musicpractice.plugins.patterns import bar_patterns, runs
    hits, beat = SD.groove(32, 120)          # hi-hat groove, ride groove from bar 9, fill every 4th
    H = [{"time": t, "piece": p} for t, p in hits]
    beats = [i * beat for i in range(32 * 4)]
    rows, grids = drum_bar_grids(H, beats, beats[::4], 32 * 4 * beat, rows=list(PIECES))
    res = bar_patterns(rows, grids)
    got = [(p["n"], runs(p["bars"]), p["fill"]) for p in res["patterns"]]
    assert got == [(6, "1-3, 5-7", False), (8, "4, 8, 12, 16, 20, 24, 28, 32", True),
                   (18, "9-11, 13-15, 17-19, 21-23, 25-27, 29-31", False)]
    # 32 bars, 2 things to learn: (hi-hat groove x3 + fill) x2, then (ride groove x3 + fill) x6
    assert [(st["pattern"], runs(st["bars"]), st["phrase"], st["reps"], st["times"])
            for st in res["steps"]] == [(0, "1-8", 4, 3, 2), (2, "9-32", 4, 3, 6)]
    # one missed hit doesn't split a groove; a crash accent on beat 1 doesn't either
    noisy = [h for h in H if not (abs(h["time"] - 17 * 4 * beat - beat) < 1e-6 and h["piece"] == "snare")]
    rows, grids = drum_bar_grids(noisy, beats, beats[::4], 32 * 4 * beat, rows=list(PIECES))
    assert len(bar_patterns(rows, grids)["patterns"]) == 3


def test_bar_grid_reaches_the_end_of_a_fading_song(tmp_path):
    """The beat tracker drops the beats of a fade-out; the grid must still reach the end,
    or the last bars have nowhere to draw their notes."""
    from musicpractice.core.models import Song
    from musicpractice.plugins.rhythm import LibrosaBeatTracker
    make_song(tmp_path / "s.wav", stems_dir=tmp_path / "st")
    y, sr = sf.read(tmp_path / "s.wav")
    L = int(12 * sr)
    g = np.ones(len(y)); g[-L:] = np.logspace(0, -2.2, L)
    sf.write(tmp_path / "fade.wav", y * g[:, None], sr)
    song = Song(id="x", dir=tmp_path, title="fade")
    out = LibrosaBeatTracker().run(song, {"audio:mix": str(tmp_path / "fade.wav")})
    beat = 60 / out["tempo"]
    from musicpractice.plugins.rhythm import audible_end
    end = audible_end(y.mean(axis=1), sr)                          # 63.9 s; the file has 1 s of silence after
    assert out["beats"][-1] > end - 1.5 * beat                    # was ~10 s short
    assert out["bars"][-1] > end - 5 * beat
    gaps = np.diff(out["beats"])
    assert gaps.max() < 1.3 * np.median(gaps)                     # evenly continued


def test_grid_follows_the_drummer():
    """Tracker grid 40 ms late, at half speed and with beat 1 on the wrong beat: the drums fix
    all three (MDB-measured problems, see drum_grid.py)."""
    from musicpractice.plugins import drum_grid as DG
    hits, beat = SD.groove(16, 120)                 # kick 1 & 3, snare 2 & 4, 8th-note hats
    H = [{"time": t + 0.5, "piece": p} for t, p in hits]
    true_beats = [0.5 + i * beat for i in range(16 * 4)]
    # 1) late + wrong bar line (beat 2 drawn as beat 1)
    tracked = [b + 0.04 for b in true_beats]
    r = DG.refine(tracked, tracked[1::4], H)
    assert np.median(np.abs(np.array(r["beats"]) - true_beats)) < 0.01
    assert abs(r["bars"][1] - (0.5 + 4 * beat)) < 0.01 and not r["doubled"]
    # 2) half speed: every other beat, the snare lands on each of them
    r = DG.refine(tracked[::2], tracked[::8], H)
    assert r["doubled"] and abs(r["tempo"] - 120) < 2
    assert all(abs(b - (0.5 + k * 4 * beat)) < 0.01 for k, b in enumerate(r["bars"][1:6], start=1))
    # 3) a straight grid is left where it is
    r = DG.refine(true_beats, true_beats[::4], H)
    assert np.abs(np.array(r["beats"]) - true_beats).max() < 0.005 and r["bars"][0] == 0.5


def test_kick_heard_twice_is_dropped_but_a_real_snare_on_a_kick_stays(tmp_path):
    """Gasoline (Audioslave) intro: one kick sound, some of them also reported as a weak snare
    or hi-hat. Same sound -> one hit. A real snare played with a kick keeps its snare."""
    from musicpractice.plugins import drum_echo, drum_edit as DE
    beat = 0.6
    hits = [(i * beat, "kick") for i in range(16)] + [(10 * beat, "snare"), (12 * beat, "snare")]
    y, truth = SD.render(hits, 16 * beat + 1, lead_in=0.0)
    path = tmp_path / "d.wav"; sf.write(path, y, SD.SR)
    cands = [{"time": i * beat, "piece": "kick", "velocity": .8, "score": .7} for i in range(16)]
    weak = lambda t, p: {"time": t, "piece": p, "velocity": .3, "score": DE.DEFAULT_THRESHOLDS[DE.FAMILY_OF[p]] * 1.3}
    cands += [weak(3 * beat, "snare"), weak(5 * beat, "snare"), weak(7 * beat, "hihat"),   # echoes
              weak(10 * beat, "snare"), weak(12 * beat, "snare")]                          # real
    n = drum_echo.mark(cands, str(path), DE.DEFAULT_THRESHOLDS)
    echo = {(round(c["time"], 2), c["piece"]) for c in cands if c.get("echo")}
    assert echo == {(round(3 * beat, 2), "snare"), (round(5 * beat, 2), "snare"), (round(7 * beat, 2), "hihat")}, echo
    assert n == 3


def test_finer_drum_stem_is_swapped_in_once_and_drums_redone(tmp_path, monkeypatch):
    """htdemucs_ft drums replace the htdemucs_6s drum stem (measured better on MDB), then the
    drums are transcribed again from it; if it can't run, the old stem stays."""
    import musicpractice.agents as AG
    truth, stems = SD.song_with_drums(tmp_path / "song.wav", tmp_path / "stems")
    c = Conductor(Workspace(tmp_path / "ws"))
    sep = StandInSeparator(stems)
    sep.__class__ = type("FakeDemucs", (StandInSeparator, AG.DemucsSeparator), {})
    c.reg._plugins.insert(0, sep)
    calls = []

    def fake_ft(song, device=None):
        calls.append(song.id)
        dest = song.dir / "stems" / "drums.wav"
        shutil.copy(stems["drums"], dest)
        return dest
    monkeypatch.setattr(AG, "separate_drums_ft", fake_ft)
    song, _ = c.process(str(tmp_path / "song.wav"), "drums")
    assert calls and song.artifacts["drums:stem_model"] == "htdemucs_ft" and song.has("drums:hits")
    song, _ = c.process(str(tmp_path / "song.wav"), "drums")           # cached: not run again
    assert len(calls) == 1

    monkeypatch.setattr(AG, "separate_drums_ft", lambda song, device=None: None)
    song.artifacts.pop("drums:stem_model"); song.save()
    song, log = c.process(str(tmp_path / "song.wav"), "drums")
    assert song.artifacts.get("drums:stem_ft_failed") and any("htdemucs_ft" in n for n in log.notes)
