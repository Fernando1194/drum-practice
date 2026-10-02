"""The five agents from the design, as plain Python coordinators.

Paste URL -> InputAgent -> AnalysisAgent -> SeparationAgent -> TranscriptionAgent -> PracticeAgent

None of them call an LLM: every step here has one right procedure, so a language model
would add cost and randomness without better results. The natural place for an LLM later
is PracticeAgent (turning the analysis into coaching advice), and its interface already
takes the structured analysis as input.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .core import Registry, Song, Workspace
from .plugins import default_registry
from .plugins.input import import_url
from .plugins.separation import STEMS_6
from .plugins.drums import transcribe_from_mix as transcribe_drums_from_mix
from .plugins.drums_adtof import adtof_available
from .plugins.rhythm import LibrosaBeatTracker
from .plugins.separation import DRUM_MODEL_NAME, DemucsSeparator, separate_drums_ft
from .plugins import drum_echo, drum_edit, drum_grid
from .plugins.drums_adtof import result_for_path as adtof_from_path
from .plugins.drum_tab import render_drum_tab
from .plugins.tab import TUNINGS, render_tab
from .plugins.transcription import PITCHED, transcribe_file
from .practice import tools as P

INSTRUMENTS = STEMS_6  # vocals, drums, bass, guitar, piano, other
Progress = Callable[[float, str], None]


def _noop(_: float, __: str) -> None:
    pass


@dataclass
class RunLog:
    notes: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        self.notes.append(msg)


# ---------------------------------------------------------------- Input

class InputAgent:
    def __init__(self, workspace: Workspace):
        self.ws = workspace

    def run(self, source: str | Path) -> Song:
        s = str(source).strip()
        if s.startswith(("http://", "https://")):
            return import_url(self.ws, s)
        if Path(s).is_file():
            return self.ws.import_song(Path(s))
        raise ValueError("Paste a link (https://...) or choose an audio file.")


# ---------------------------------------------------------------- Analysis

class AnalysisAgent:
    """Tempo, beats, bars, key, chords and sections."""

    def __init__(self, registry: Registry):
        self.reg = registry

    def run(self, song: Song, progress: Progress = _noop) -> None:
        progress(0.0, "Finding tempo and bars")
        # songs analyzed before the bar grid reached the end of the song: redo the grid and
        # everything drawn on it (chords per bar, sections)
        old_grid = song.has("bars") and song.artifacts.get("beats:version") != LibrosaBeatTracker.VERSION
        self.reg.ensure(song, "bars", force=old_grid)
        progress(0.4, "Detecting key and chords")
        self.reg.ensure(song, "chord_chart", force=old_grid)
        progress(0.7, "Finding song sections")
        stale = old_grid or (song.has("sections") and not song.get("sections_stem_hints")
                             and bool(song.stems()))
        self.reg.ensure(song, "sections", force=stale)  # redo once stems add better hints


# ---------------------------------------------------------------- Separation

class SeparationAgent:
    def __init__(self, registry: Registry):
        self.reg = registry

    @property
    def available(self) -> bool:
        return "stem:guitar" in self.reg.available_keys()

    def run(self, song: Song) -> bool:
        if song.stems():
            return True
        if not self.available:
            return False
        self.reg.ensure(song, "stem:guitar")  # Demucs produces all six stems in one pass
        return True

    def isolate(self, song: Song, instrument: str) -> tuple[Path, Path]:
        """Write <instrument>.wav (alone) and minus_<instrument>.wav (everything else)."""
        stems = song.stems()
        if instrument not in stems:
            raise ValueError(f"No {instrument} stem. Run separation first.")
        out = song.dir / "isolated"
        iso = out / f"{instrument}.wav"
        minus = out / f"minus_{instrument}.wav"
        if not iso.exists():
            P.save(P.load(stems[instrument]), iso)
        if not minus.exists():
            P.save(P.mix(stems, {instrument: 0.0}), minus)
        return iso, minus


# ---------------------------------------------------------------- Transcription

class TranscriptionAgent:
    def __init__(self, registry: Registry):
        self.reg = registry

    @staticmethod
    def supports(instrument: str) -> bool:
        return instrument in PITCHED or instrument == "drums"

    @property
    def available(self) -> bool:
        return "notes:guitar" in {k for p in self.reg.plugins() if p.available() for k in p.produces}

    def run(self, song: Song, instrument: str, log: RunLog) -> None:
        if instrument == "drums":
            self._drums(song, log)
            return
        if not self.supports(instrument):
            log.warn(f"No note transcription for {instrument}. Chords, tempo and sections "
                     "still apply, and you can loop the isolated track.")
            return
        if not self.available:
            log.warn("Basic Pitch is not installed, so notes/tab were skipped "
                     "(pip install basic-pitch).")
            return
        src_key = f"notes:{instrument}:source"
        if song.has(f"stem:{instrument}"):
            # a transcription cached from the full mix (song analyzed before separation was
            # available) is redone from the isolated track: that's where accuracy comes from
            redo = song.has(f"notes:{instrument}") and song.artifacts.get(src_key) != "stem"
            self.reg.ensure(song, f"notes:{instrument}", force=redo)
            song.artifacts[src_key] = "stem"
            song.save()
            if instrument in TUNINGS:
                self.reg.ensure(song, f"tab:{instrument}", force=redo)
            return
        if not song.has(f"notes:{instrument}"):
            midi = song.dir / "transcription" / f"{instrument}_from_mix.mid"
            notes = transcribe_file(song.get("audio:mix"), instrument, midi)
            song.artifacts.update({f"notes:{instrument}": notes, f"midi:{instrument}": str(midi),
                                   src_key: "mix"})
            song.save()
        log.warn(f"No isolated {instrument} track, so notes were transcribed from the full "
                 "mix. Expect notes from other instruments mixed in.")
        if instrument in TUNINGS:
            self.reg.ensure(song, f"tab:{instrument}", force=True)

    def _better_drum_stem(self, song: Song, log: RunLog) -> bool:
        """Swap in the htdemucs_ft drum stem once (see separation.py). True if it changed."""
        if (not song.has("stem:drums") or song.artifacts.get("drums:stem_model") == DRUM_MODEL_NAME
                or song.artifacts.get("drums:stem_ft_failed")):
            return False
        sep = next((p for p in self.reg._plugins if isinstance(p, DemucsSeparator)), None)
        if sep is None or not sep.available():
            return False
        path = separate_drums_ft(song, getattr(sep, "device", None))
        if path is None:
            song.artifacts["drums:stem_ft_failed"] = True
            song.save()
            log.warn("The finer drum separation (htdemucs_ft) couldn't run; using the standard one.")
            return False
        song.artifacts.update({"stem:drums": str(path), "drums:stem_model": DRUM_MODEL_NAME})
        song.save()
        return True

    def _drums(self, song: Song, log: RunLog) -> None:
        use_adtof = adtof_available()
        new_stem = self._better_drum_stem(song, log)
        # redo cached drums made with the basic detector (before ADTOF was installed), or
        # made from the full mix when an isolated drum track now exists
        stale = (use_adtof and song.has("drums:hits")
                 and (song.artifacts.get("drums:detector") != "adtof"
                      or not song.has("drums:candidates")))   # older version: no sliders
        from_mix = song.has("drums:hits") and song.artifacts.get("drums:source") != "stem"
        if song.has("stem:drums"):
            self.reg.ensure(song, "drums:hits", force=stale or from_mix or new_stem)
            if use_adtof:
                song.artifacts["drums:detector"] = "adtof"
            song.artifacts["drums:source"] = "stem"
            song.save()
        elif stale or not song.has("drums:hits"):
            log.warn("No isolated drum track, so drums were transcribed from the full mix. "
                     "Expect more misses; turn on 'Separate instruments' for better results.")
            song.artifacts.update(adtof_from_path(song, song.get("audio:mix"), float(song.get("tempo")),
                                                  suffix="_from_mix")
                                  if use_adtof else transcribe_drums_from_mix(song))
            song.artifacts["drums:source"] = "mix"
            song.save()
        self._echoes(song, use_adtof)
        self._grid_from_drums(song, log)
        if not use_adtof:     # (with ADTOF: accuracy is in the README, not repeated every song)
            log.warn("Drums were transcribed with the basic detector, which confuses pieces on real "
                     "recordings. Install ADTOF for much better results (see README, 'Drums').")

    @staticmethod
    def _echoes(song: Song, use_adtof: bool) -> None:
        """Songs transcribed before the kick-echo check (drum_echo.py): mark and rebuild once
        (no model run)."""
        if (not use_adtof or song.artifacts.get("drums:echo") == drum_echo.VERSION
                or not song.has("drums:candidates")):
            return
        audio = song.get("stem:drums") if song.artifacts.get("drums:source") == "stem" \
            else song.get("audio:mix")
        drum_echo.mark(song.get("drums:candidates"), audio, drum_edit.DEFAULT_THRESHOLDS)
        song.artifacts["drums:echo"] = drum_echo.VERSION
        drum_edit.rebuild(song)

    def _grid_from_drums(self, song: Song, log: RunLog) -> None:
        """Move the beat/bar grid onto the drummer (see plugins/drum_grid.py), once per song;
        chords and sections are redone on the new grid."""
        if song.artifacts.get("grid:drums") == drum_grid.VERSION or not song.has("drums:hits"):
            return
        r = drum_grid.refine(song.get("beats"), song.get("bars"), song.get("drums:hits"))
        if not r["changed"]:
            return
        song.artifacts.setdefault("beats:tracked", song.get("beats"))
        song.artifacts.update({"beats": r["beats"], "bars": r["bars"], "tempo": r["tempo"],
                               "grid:drums": drum_grid.VERSION})
        song.save()
        self.reg.ensure(song, "chord_chart", force=True)
        self.reg.ensure(song, "sections", force=True)
        if r["doubled"]:
            log.warn(f"The beat tracker had this song at half speed; the drums show it's "
                     f"{r['tempo']:.0f} BPM, so the grid was doubled.")

    @staticmethod
    def tab_text(song: Song, instrument: str, start_bar: int = 1,
                 end_bar: int | None = None) -> str:
        if instrument == "drums":
            if not song.has("drums:hits"):
                return ""
            return render_drum_tab(song.get("drums:hits"), song.get("beats"), song.get("bars"),
                                   song.get("duration"), start_bar, end_bar)
        if not song.has(f"tab:{instrument}"):
            return ""
        return render_tab(song.get(f"tab:{instrument}"), instrument, song.get("beats"),
                          song.get("bars"), song.get("duration"), song.get("chord_chart"),
                          start_bar, end_bar)


# ---------------------------------------------------------------- Practice

@dataclass
class Exercise:
    title: str
    why: str
    request: P.PracticeRequest


class PracticeAgent:
    """Turns the analysis into concrete loops and speed plans."""

    @staticmethod
    def bar_density(song: Song, instrument: str) -> np.ndarray | None:
        """Attacks per second, per bar, at full speed. A strummed chord counts once."""
        from .plugins.tab import group_events
        key = f"notes:{instrument}"
        if not song.has(key):
            return None
        edges = list(song.get("bars")) + [song.get("duration")]
        onsets = np.array([e["time"] for e in group_events(song.get(key))])
        counts, _ = np.histogram(onsets, bins=edges)
        return counts / np.maximum(np.diff(edges), 1e-3)

    @staticmethod
    def start_speed(notes_per_s: float) -> float:
        if notes_per_s <= 3:
            return 0.85
        if notes_per_s <= 6:
            return 0.7
        if notes_per_s <= 9:
            return 0.6
        return 0.5

    def suggest(self, song: Song, instrument: str, start_bar: int, end_bar: int) -> list[Exercise]:
        has_stem = song.has(f"stem:{instrument}")
        iso = {instrument: 1.0, **{s: 0.0 for s in song.stems() if s != instrument}}
        minus = {instrument: 0.0} if has_stem else {}
        tempo = song.get("tempo")
        exercises: list[Exercise] = []

        if has_stem:
            exercises.append(Exercise(
                "1. Listen to the part alone",
                f"Hear only the {instrument} at 80% before playing a note.",
                P.PracticeRequest(start_bar, end_bar, gains=iso, speed=0.8, loops=2)))

        density = self.bar_density(song, instrument)
        if density is not None and end_bar - start_bar >= 2:
            window = density[start_bar - 1:end_bar]
            pairs = sorted(((window[i] + window[i + 1], i) for i in range(len(window) - 1)),
                           reverse=True)
            picked: list[int] = []
            for _, i in pairs:             # two busiest 2-bar spots that don't overlap
                if all(abs(i - j) >= 2 for j in picked):
                    picked.append(i)
                if len(picked) == 2:
                    break
            for i in sorted(picked):
                a = start_bar + i
                d = float(max(window[i], window[i + 1]))
                s0 = self.start_speed(d)
                exercises.append(Exercise(
                    f"{len(exercises) + 1}. Busy spot: bars {a}-{a + 1}",
                    f"{d:.1f} attacks per second here. Climb from {int(s0 * 100)}% "
                    f"({tempo * s0:.0f} BPM) to full speed.",
                    P.PracticeRequest(a, a + 1, gains=minus, loops=3, gap_s=1.0, ladder=True,
                                      start_speed=s0, end_speed=1.0, step=0.1)))

        whole = float(density[start_bar - 1:end_bar].mean()) if density is not None else 4.0
        s0 = self.start_speed(whole)
        exercises.append(Exercise(
            f"{len(exercises) + 1}. Whole section, speed trainer",
            f"Bars {start_bar}-{end_bar}" + (" with your part removed" if minus else "")
            + f", {int(s0 * 100)}% to 100%.",
            P.PracticeRequest(start_bar, end_bar, gains=minus, loops=2, ladder=True,
                              start_speed=s0, end_speed=1.0, step=0.1)))
        exercises.append(Exercise(
            f"{len(exercises) + 1}. Play along with the record",
            "Full mix at full speed, 3 times.",
            P.PracticeRequest(start_bar, end_bar, gains={}, speed=1.0, loops=3)))
        return exercises

    @staticmethod
    def render(song: Song, req: P.PracticeRequest) -> Path:
        return P.render_practice(song, req)


# ---------------------------------------------------------------- Export

def transcription_markdown(song: Song, instrument: str, log: RunLog | None = None) -> str:
    key = song.get("key") if song.has("key") else None
    lines = [f"# {song.title}", ""]
    if song.has("source_url"):
        lines += [f"Source: {song.get('source_url')}", ""]
    lines += [f"- **Tempo:** {song.get('tempo')} BPM",
              f"- **Key:** {key['name']} (runner-up: {key['runner_up']}, "
              f"confidence {key['confidence']:.2f})" if key else "- **Key:** n/a",
              f"- **Bars:** {len(song.get('bars'))} (4/4 assumed)",
              f"- **Instrument:** {instrument}", ""]

    if song.has("sections"):
        lines += ["## Sections", "", "| Bars | Time | Part | Hint |", "|---|---|---|---|"]
        for s in song.get("sections"):
            m1, s1 = divmod(int(s["start"]), 60)
            lines.append(f"| {s['start_bar']}-{s['end_bar']} | {m1}:{s1:02d} | "
                         f"{s['letter']} | {s['hint'] or ''} |")
        lines += ["", "Hints are guesses from which instruments are playing.", ""]

    if song.has("chord_chart"):
        chart = song.get("chord_chart")
        lines += ["## Chords (one cell per bar)", "", "```"]
        for i in range(0, len(chart), 4):
            cells = " | ".join(" ".join(c).ljust(7) for c in chart[i:i + 4])
            lines.append(f"{i + 1:>3}  | {cells} |")
        lines += ["```", ""]

    tab = TranscriptionAgent.tab_text(song, instrument)
    if tab:
        title = "Drum grid (x = cymbal, o = drum, one column per 16th note)" \
            if instrument == "drums" else f"{instrument.capitalize()} tab"
        lines += [f"## {title}", "",
                  "Auto-generated draft. Check it by ear, fix what sounds wrong.", "",
                  "```", tab, "```", ""]
    elif song.has(f"notes:{instrument}"):
        lines += [f"## {instrument.capitalize()} notes", "",
                  f"{len(song.get(f'notes:{instrument}'))} notes, see the MIDI file.", ""]
    if log and log.notes:
        lines += ["## Notes from processing", ""] + [f"- {n}" for n in log.notes]
    return "\n".join(lines)


def export_bundle(song: Song, instrument: str, log: RunLog | None = None) -> Path:
    """Zip with transcription.md, MIDI, notes.csv, isolated audio and practice tracks."""
    import zipfile

    out_dir = song.dir / "export"
    out_dir.mkdir(exist_ok=True)
    md = out_dir / "transcription.md"
    md.write_text(transcription_markdown(song, instrument, log))

    files: list[tuple[Path, str]] = [(md, "transcription.md")]
    if song.has(f"notes:{instrument}"):
        csv_path = out_dir / f"{instrument}_notes.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["start_s", "end_s", "midi", "note", "velocity"])
            for n in song.get(f"notes:{instrument}"):
                w.writerow([n["start"], n["end"], n["pitch"], n["name"], n["velocity"]])
        files.append((csv_path, csv_path.name))
    if song.has(f"midi:{instrument}"):
        files.append((Path(song.get(f"midi:{instrument}")), f"{instrument}.mid"))
    for folder in ("isolated", "practice"):
        for p in sorted((song.dir / folder).glob("*.wav")):
            files.append((p, f"{folder}/{p.name}"))

    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in song.title).strip()[:60]
    zip_path = out_dir / f"{safe or 'song'} - {instrument}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for src, name in files:
            z.write(src, name)
    return zip_path


# ---------------------------------------------------------------- Conductor

class Conductor:
    """Runs the whole workflow: paste URL -> ... -> ready to practice."""

    def __init__(self, workspace: Workspace | None = None, device: str | None = None):
        self.ws = workspace or Workspace()
        self.reg = default_registry(device=device)
        self.input = InputAgent(self.ws)
        self.analysis = AnalysisAgent(self.reg)
        self.separation = SeparationAgent(self.reg)
        self.transcription = TranscriptionAgent(self.reg)
        self.practice = PracticeAgent()

    def process(self, source: str, instrument: str, separate: bool = True,
                transcribe: bool = True, progress: Progress = _noop) -> tuple[Song, RunLog]:
        if instrument not in INSTRUMENTS:
            raise ValueError(f"Instrument must be one of {INSTRUMENTS}")
        log = RunLog()
        progress(0.02, "Getting audio")
        song = self.input.run(source)

        if separate:
            if self.separation.available or song.stems():
                progress(0.1, "Separating instruments (minutes on CPU, cached after)")
                self.separation.run(song)
                self.separation.isolate(song, instrument)
            else:
                log.warn("Demucs is not installed, so instruments were not separated "
                         "(pip install -e \".[separation]\").")

        self.analysis.run(song, lambda f, m: progress(0.5 + 0.25 * f, m))
        if transcribe:
            progress(0.8, f"Transcribing {instrument}")
            self.transcription.run(song, instrument, log)
        progress(1.0, "Done")
        return song, log
