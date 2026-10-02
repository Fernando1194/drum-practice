import numpy as np
import pytest
import soundfile as sf

from musicpractice.core import Registry, Workspace
from musicpractice.plugins.rhythm import LibrosaBeatTracker
from musicpractice.practice import PracticeRequest, render_practice
from musicpractice.practice import tools

SR = 44100


def make_song_audio(path, bpm=120, bars=16, sr=SR):
    """Kick-like pulse on every beat, accented downbeat, over a quiet drone."""
    beat = 60 / bpm
    n = int(sr * beat * 4 * bars) + sr
    y = 0.05 * np.sin(2 * np.pi * 110 * np.arange(n) / sr)
    t = np.arange(int(0.08 * sr)) / sr
    for i in range(4 * bars):
        amp = 0.9 if i % 4 == 0 else 0.6
        hit = amp * np.sin(2 * np.pi * 80 * t) * np.exp(-t * 40)
        s = int(i * beat * sr)
        y[s: s + len(hit)] += hit
    sf.write(path, np.stack([y, y]).T.astype(np.float32), sr)
    return path


@pytest.fixture
def song(tmp_path):
    ws = Workspace(tmp_path / "ws")
    return ws.import_song(make_song_audio(tmp_path / "test_song.wav"))


# ---------- registry ----------

class Fake:
    def __init__(self, name, requires, produces, ok=True):
        self.name, self.requires, self.produces, self.ok = name, requires, produces, ok
        self.calls = 0

    def available(self):
        return self.ok

    def run(self, song, inputs):
        self.calls += 1
        return {k: f"{self.name}({','.join(map(str, inputs.values()))})" for k in self.produces}


def test_registry_resolves_dependencies_and_caches(song):
    reg = Registry()
    a = reg.register(Fake("A", ("audio:mix",), ("x",)))
    b = reg.register(Fake("B", ("x",), ("y",)))
    assert reg.ensure(song, "y").startswith("B(A(")
    reg.ensure(song, "y")
    assert a.calls == 1 and b.calls == 1


def test_registry_cycle_and_missing(song):
    reg = Registry()
    reg.register(Fake("A", ("q",), ("p",)))
    reg.register(Fake("B", ("p",), ("q",)))
    with pytest.raises(RuntimeError, match="cycle"):
        reg.ensure(song, "p")
    with pytest.raises(LookupError):
        reg.ensure(song, "nothing")


def test_unavailable_plugin_gives_clear_error(song):
    reg = Registry()
    reg.register(Fake("demucs", ("audio:mix",), ("stem:guitar",), ok=False))
    with pytest.raises(RuntimeError, match="not installed"):
        reg.ensure(song, "stem:guitar")


def test_workspace_reuses_same_file(tmp_path):
    ws = Workspace(tmp_path / "ws")
    f = make_song_audio(tmp_path / "a.wav", bars=2)
    s1 = ws.import_song(f)
    s1.artifacts["tempo"] = 99
    s1.save()
    assert ws.import_song(f).get("tempo") == 99


# ---------- rhythm ----------

def test_beat_tracker_finds_tempo_and_bars(song):
    reg = Registry()
    reg.register(LibrosaBeatTracker())
    reg.ensure(song, "bars")
    assert abs(song.get("tempo") - 120) < 3
    assert 15 <= len(song.get("bars")) <= 17


# ---------- practice ----------

def add_fake_stems(song):
    d = song.dir / "stems"
    d.mkdir()
    n = int(SR * song.get("duration"))
    t = np.arange(n) / SR
    for name, f in [("guitar", 330), ("bass", 82), ("drums", 0)]:
        y = 0.3 * np.sin(2 * np.pi * f * t) if f else np.zeros(n)
        p = d / f"{name}.wav"
        sf.write(p, np.stack([y, y]).T.astype(np.float32), SR)
        song.artifacts[f"stem:{name}"] = str(p)


def test_render_loop_length(song):
    reg = Registry()
    reg.register(LibrosaBeatTracker())
    reg.ensure(song, "bars")
    s, e = tools.bar_range_to_seconds(song, 2, 3)
    req = PracticeRequest(start_bar=2, end_bar=3, speed=1.0, loops=3, gap_s=1.0,
                          use_count_in=False)
    out, _ = sf.read(render_practice(song, req))
    expected = 3 * (e - s) + 2 * 1.0 + 2.0
    assert abs(len(out) / SR - expected) < 0.05


def test_speed_ladder_and_mixer(song):
    reg = Registry()
    reg.register(LibrosaBeatTracker())
    reg.ensure(song, "bars")
    add_fake_stems(song)
    req = PracticeRequest(start_bar=1, end_bar=2, gains={"guitar": 0, "bass": 1, "drums": 1},
                          loops=1, gap_s=0, ladder=True, start_speed=0.5, end_speed=1.0, step=0.25)
    assert req.speeds() == [0.5, 0.75, 1.0]
    path = render_practice(song, req)
    out, _ = sf.read(path)
    s, e = tools.bar_range_to_seconds(song, 1, 2)
    seg = e - s
    tempo = song.get("tempo")
    count_ins = sum(4 * 60 / (tempo * sp) for sp in req.speeds())
    expected = seg / 0.5 + seg / 0.75 + seg / 1.0 + count_ins
    assert abs(len(out) / SR - expected) < 0.2


def test_mixer_mutes_stem(song):
    reg = Registry()
    reg.register(LibrosaBeatTracker())
    reg.ensure(song, "bars")
    add_fake_stems(song)
    req = PracticeRequest(start_bar=1, end_bar=2, gains={"guitar": 0, "bass": 1, "drums": 1},
                          loops=1, gap_s=0, use_count_in=False)
    out, _ = sf.read(render_practice(song, req))
    mono = out[:, 0] if out.ndim > 1 else out
    spec = np.abs(np.fft.rfft(mono))
    freqs = np.fft.rfftfreq(len(mono), 1 / SR)
    g = spec[(freqs > 320) & (freqs < 340)].max()
    b = spec[(freqs > 75) & (freqs < 90)].max()
    assert g < b * 0.01


def test_invalid_bar_range(song):
    reg = Registry()
    reg.register(LibrosaBeatTracker())
    reg.ensure(song, "bars")
    with pytest.raises(ValueError):
        tools.bar_range_to_seconds(song, 5, 2)
