"""Gradio UI, one page.  Run:  python -m musicpractice.app

Paste link -> pick instrument -> Analyze -> practice in the synced player.
Below the player: downloadable practice tracks, isolated audio and the export zip.
"""
from __future__ import annotations

import argparse
import os
import json
import platform
import traceback
from dataclasses import asdict
from pathlib import Path

import gradio as gr

from .agents import INSTRUMENTS, Conductor, RunLog, export_bundle
from .core import Song, Workspace
from .plugins import drum_edit
from .practice.tools import PracticeRequest
from .ui import HEAD, build_player, section_label
from .ui.theme import APP_CSS, THEME

MIX_FULL = "Full song"

EMPTY_PLAYER = """
<div class="mp-player mp-empty" data-ready="1" style="padding:28px">
  <p style="margin:0;font-size:16px">Paste a song link above and press <b>Analyze song</b>.
  The drum kit and the drum grid will follow the song here while it plays.</p>
</div>"""


def mix_choices(instrument: str, has_stems: bool) -> list[str]:
    if not has_stems:
        return [MIX_FULL]
    return [f"Play along (no {instrument})", MIX_FULL, f"Only {instrument}"]


def gains_for(choice: str, instrument: str, song: Song) -> dict[str, float]:
    stems = song.stems()
    if choice.startswith("Only") and stems:
        return {s: (1.0 if s == instrument else 0.0) for s in stems}
    if choice.startswith("Play along") and stems:
        return {instrument: 0.0}
    return {}


def choice_for(gains: dict[str, float], instrument: str, has_stems: bool) -> str:
    if not gains or not has_stems:
        return MIX_FULL
    if gains.get(instrument, 1.0) == 0.0:
        return f"Play along (no {instrument})"
    return f"Only {instrument}"


BRAND = ('<div class="mp-brand-name">Drum<i>.</i>Practice</div>'
         '<div class="mp-brand-sub">Paste a song, get a drum lesson you can play on your electronic kit.</div>')
BRAND_ALL = ('<div class="mp-brand-name">Music<i>.</i>Practice</div>'
             '<div class="mp-brand-sub">Paste a song, pick your instrument, practice along.</div>')


def notes_md(log: RunLog) -> str:
    """Drums-only page: the song title and numbers are in the player's header; only notes here."""
    return "\n".join(f"- {n}" for n in log.notes)


def summary_md(song: Song, log: RunLog) -> str:
    key = song.get("key")
    d = int(song.get("duration"))
    out = [f"### {song.title}",
           f"{song.get('tempo'):.0f} BPM, key of {key['name']} (could also be "
           f"{key['runner_up']}), {len(song.get('bars'))} bars, {d // 60}:{d % 60:02d}"]
    if log.notes:
        out += [""] + [f"- {n}" for n in log.notes]
    return "\n".join(out)


def enabled_instruments() -> list[str]:
    """Drums only for now (the other instruments' transcription is frozen while drums are the
    focus). MUSIC_PRACTICE_ALL_INSTRUMENTS=1 shows them all again."""
    if os.environ.get("MUSIC_PRACTICE_ALL_INSTRUMENTS") == "1":
        return list(INSTRUMENTS)
    return ["drums"]


def build_app(conductor: Conductor) -> gr.Blocks:
    sep_ok = conductor.separation.available
    choices = enabled_instruments()
    drums_only = choices == ["drums"]
    # drums always transcribe (ADTOF, or the basic detector); the rest needs Basic Pitch
    tr_ok = drums_only or conductor.transcription.available

    def player_sources(song: Song, instrument: str):
        stems = song.stems()
        if instrument in stems:
            iso, minus = conductor.separation.isolate(song, instrument)
            return [("along", f"Play along (no {instrument})", str(minus)),
                    ("full", "Full song", song.get("audio:mix")),
                    ("solo", f"Only {instrument}", str(iso))], iso, minus
        return [("full", "Full song", song.get("audio:mix"))], None, None

    # ---------------- handlers ----------------

    def analyze(url, upload, instrument, separate, transcribe, progress=gr.Progress()):
        source = (url or "").strip() or (upload or "")
        if not source:
            raise gr.Error("Paste a song link or upload a file.")
        try:
            song, log = conductor.process(source, instrument, separate=separate,
                                          transcribe=transcribe,
                                          progress=lambda f, m: progress(f, desc=m))
            sources, iso, minus = player_sources(song, instrument)
            player = build_player(song, instrument, sources, stems=song.stems() or None)
        except Exception as e:  # show the reason in the UI, full trace in the terminal
            traceback.print_exc()
            raise gr.Error(str(e))

        labels = [section_label(s) for s in song.get("sections")]
        n_bars = len(song.get("bars"))
        state = {"dir": str(song.dir), "instrument": instrument, "log": log.notes}
        ex_choices, ex_state = exercises_for(song, instrument, 1, min(8, n_bars))
        return (
            state, notes_md(log) if enabled_instruments() == ["drums"] else summary_md(song, log), player,
            gr.update(choices=labels, value=None),
            gr.update(value=1, maximum=n_bars), gr.update(value=min(8, n_bars), maximum=n_bars),
            gr.update(choices=mix_choices(instrument, bool(song.stems())),
                      value=mix_choices(instrument, bool(song.stems()))[0]),
            gr.update(choices=ex_choices, value=None), ex_state,
            str(iso) if iso else None, str(minus) if minus else None,
            gr.Accordion(open=False), gr.Accordion(visible=True), gr.Accordion(visible=True),
        )

    def exercises_for(song: Song, instrument: str, a: int, b: int):
        exs = conductor.practice.suggest(song, instrument, a, b)
        return [e.title for e in exs], {e.title: {"why": e.why, "req": asdict(e.request)}
                                       for e in exs}

    def pick_section(state, label):
        if not state or not label:
            return gr.update(), gr.update(), gr.update(), {}
        song = Song.load(Path(state["dir"]))
        s = next(x for x in song.get("sections") if section_label(x) == label)
        choices, ex_state = exercises_for(song, state["instrument"], s["start_bar"], s["end_bar"])
        return s["start_bar"], s["end_bar"], gr.update(choices=choices, value=None), ex_state

    def load_exercise(state, title, ex_state):
        if not state or not title or title not in ex_state:
            return [gr.update()] * 11
        song = Song.load(Path(state["dir"]))
        r = ex_state[title]["req"]
        return (f"*{ex_state[title]['why']}*", r["start_bar"], r["end_bar"],
                choice_for(r["gains"], state["instrument"], bool(song.stems())),
                r["speed"], r["loops"], r["gap_s"], r["ladder"], r["start_speed"],
                r["end_speed"], r["step"])

    def render(state, start_bar, end_bar, offset, mix_choice, speed, loops, gap, count_in,
               ladder, s0, s1, step):
        if not state:
            raise gr.Error("Analyze a song first.")
        song = Song.load(Path(state["dir"]))
        req = PracticeRequest(int(start_bar), int(end_bar), bar_offset=int(offset),
                              gains=gains_for(mix_choice, state["instrument"], song),
                              speed=speed, loops=int(loops), gap_s=gap, use_count_in=count_in,
                              ladder=ladder, start_speed=s0, end_speed=s1, step=step)
        try:
            return str(conductor.practice.render(song, req))
        except ValueError as e:
            raise gr.Error(str(e))

    def fix_drums(state, cmd_json):
        """The player's 'Fix transcription' panel: new sensitivity and/or hand edits."""
        if not state or state.get("instrument") != "drums" or not cmd_json:
            return gr.update(), gr.update()
        try:
            cmd = json.loads(cmd_json)
            song = Song.load(Path(state["dir"]))
            if cmd.get("reset"):
                drum_edit.rebuild(song, thresholds=dict(drum_edit.DEFAULT_THRESHOLDS),
                                  edits={"add": [], "remove": []})
            else:
                thr = cmd.get("thresholds")
                if thr:
                    thr = {f: min(max(float(thr.get(f, d)), drum_edit.CANDIDATE_MIN),
                                  drum_edit.STRICTEST)
                           for f, d in drum_edit.DEFAULT_THRESHOLDS.items()}
                pieces = set(drum_edit.FAMILY_OF)
                clean = lambda xs: [[float(t), p] for t, p in xs if p in pieces]
                edits = drum_edit.merge_edits(drum_edit.edits_of(song), clean(cmd.get("add", [])),
                                              clean(cmd.get("remove", [])))
                drum_edit.rebuild(song, thresholds=thr or None, edits=edits)
            sources, _, _ = player_sources(song, "drums")
            player = build_player(song, "drums", sources, stems=song.stems() or None)
        except Exception as e:
            traceback.print_exc()
            raise gr.Error(f"Couldn't save the drum changes: {e}")
        return player, ""

    def export(state):
        if not state:
            raise gr.Error("Analyze a song first.")
        song = Song.load(Path(state["dir"]))
        return str(export_bundle(song, state["instrument"], RunLog(state.get("log", []))))

    # ---------------- layout ----------------

    with gr.Blocks(title="Drum Practice" if drums_only else "Music Practice", fill_width=True) as app:
        state = gr.State(None)
        ex_state = gr.State({})

        gr.HTML(BRAND if drums_only else BRAND_ALL, elem_id="mp-brand")
        with gr.Group(elem_id="mp-input"):
            with gr.Row(equal_height=True, elem_classes=["mp-input-row"]):
                url = gr.Textbox(show_label=False, container=False, scale=6, max_lines=1,
                                 elem_id="mp-url", label="Song link",
                                 placeholder="Paste a song link: YouTube, SoundCloud, Bandcamp... "
                                             "(not Spotify or Apple Music)")
                instrument = gr.Dropdown(choices, value="drums" if "drums" in choices else choices[0],
                                         label="I play", scale=1, visible=not drums_only)
                go = gr.Button("Analyze song", variant="primary", scale=1, min_width=150,
                               elem_id="mp-go")
            # drums-only: always separate and transcribe; the boxes only show when they matter
            with gr.Row(visible=not (drums_only and sep_ok)):
                separate = gr.Checkbox(value=sep_ok, interactive=sep_ok, container=False,
                                       label="Separate instruments" + ("" if sep_ok else
                                             " (Demucs not installed: the drums are transcribed "
                                             "from the full song, less accurately)"))
                transcribe = gr.Checkbox(value=tr_ok, interactive=tr_ok, container=False,
                                         visible=not drums_only,
                                         label="Transcribe notes and tab" + ("" if tr_ok else
                                               " (Basic Pitch not installed)"))
            with gr.Accordion("Or upload an audio file", open=False, elem_classes=["mp-upload"]) as upload_box:
                upload = gr.File(show_label=False, type="filepath",
                                 file_types=[".mp3", ".wav", ".flac", ".m4a", ".ogg"])

        summary = gr.Markdown(elem_id="mp-notes")
        player = gr.HTML(EMPTY_PLAYER, elem_id="mp-wrap", padding=False)

        # practice track and export only appear once there is a song
        with gr.Accordion("Make a practice track to download", open=False, elem_classes=["mp-card"],
                          visible=False) as practice_box:
            gr.Markdown("Renders a file with the bars you pick, slowed down and repeated, "
                        "with count-in clicks. Uses higher-quality slow-down than the player.")
            with gr.Row():
                section = gr.Dropdown(label="Section", choices=[], scale=2)
                start_bar = gr.Number(value=1, precision=0, label="From bar", minimum=1)
                end_bar = gr.Number(value=8, precision=0, label="To bar", minimum=1)
                offset = gr.Number(value=0, precision=0, label="Grid offset",
                                   info="Shift if bar lines feel off", visible=not drums_only)
            with gr.Row(visible=not drums_only):
                exercise = gr.Dropdown(label="Suggested exercise (fills the settings)",
                                       choices=[], scale=2)
                exercise_why = gr.Markdown()
            mix_choice = gr.Radio([MIX_FULL], value=MIX_FULL, label="What you hear")
            with gr.Row():
                speed = gr.Slider(0.3, 1.25, value=0.75, step=0.05, label="Speed")
                loops = gr.Slider(1, 20, value=4, step=1, label="Repetitions")
                gap = gr.Slider(0, 4, value=1.0, step=0.5, label="Pause between reps (s)")
                count_in = gr.Checkbox(value=True, label="Count-in clicks")
            with gr.Row():
                ladder = gr.Checkbox(value=False, label="Speed trainer (replaces Speed)")
                s0 = gr.Slider(0.3, 1.0, value=0.6, step=0.05, label="From")
                s1 = gr.Slider(0.5, 1.25, value=1.0, step=0.05, label="To")
                step = gr.Slider(0.05, 0.25, value=0.1, step=0.05, label="Step")
            build = gr.Button("Make practice track", variant="primary")
            practice_audio = gr.Audio(label="Practice track", interactive=False)

        with gr.Accordion("Export" if drums_only else "Downloads", open=False, elem_classes=["mp-card"],
                          visible=False) as export_box:
            with gr.Row(visible=not drums_only):
                iso_audio = gr.Audio(label="Your instrument alone", interactive=False)
                minus_audio = gr.Audio(label="Song without your instrument", interactive=False)
            gr.Markdown("A zip with the drum MIDI (opens as drums in MuseScore or any DAW), the drum "
                        "grid as text, the separated tracks and every practice track you made."
                        if drums_only else
                        "Export: a zip with the transcription (Markdown), MIDI, notes CSV, "
                        "the isolated tracks and every practice track you made for this song.")
            export_btn = gr.Button("Export song")
            export_file = gr.File(label="Export")

        go.click(analyze, [url, upload, instrument, separate, transcribe],
                 [state, summary, player, section, start_bar, end_bar, mix_choice,
                  exercise, ex_state, iso_audio, minus_audio,
                  upload_box, practice_box, export_box])
        url.submit(analyze, [url, upload, instrument, separate, transcribe],
                   [state, summary, player, section, start_bar, end_bar, mix_choice,
                    exercise, ex_state, iso_audio, minus_audio,
                  upload_box, practice_box, export_box])
        section.change(pick_section, [state, section], [start_bar, end_bar, exercise, ex_state])
        exercise.change(load_exercise, [state, exercise, ex_state],
                        [exercise_why, start_bar, end_bar, mix_choice, speed, loops, gap,
                         ladder, s0, s1, step])
        build.click(render, [state, start_bar, end_bar, offset, mix_choice, speed, loops, gap,
                             count_in, ladder, s0, s1, step], practice_audio)
        export_btn.click(export, [state], export_file)
        # channel for the player's drum fixes: the page's script fills the box and clicks
        cmd = gr.Textbox(elem_id="mp-cmd", elem_classes=["mp-hidden"], container=False)
        cmd_go = gr.Button("apply", elem_id="mp-cmd-go", elem_classes=["mp-hidden"])
        cmd_go.click(fix_drums, [state, cmd], [player, cmd])
    return app


def launch(app: gr.Blocks, workspace: Workspace, **kwargs):
    """The player's script and styles go in <head>; the song folder must be servable."""
    return app.launch(head=HEAD, allowed_paths=[str(workspace.root)], theme=THEME, css=APP_CSS,
                      **kwargs)


def main() -> None:
    ap = argparse.ArgumentParser(description="Personal music practice tool")
    ap.add_argument("--workspace", default=None, help="Folder for songs and results")
    ap.add_argument("--device", default=None, help="Demucs device: cuda, mps or cpu")
    ap.add_argument("--port", type=int, default=7860)
    args = ap.parse_args()
    ws = Workspace(args.workspace) if args.workspace else Workspace()
    in_wsl = "microsoft" in platform.release().lower()  # no browser inside WSL
    if in_wsl:
        print(f"\n  Open http://localhost:{args.port} in your Windows browser\n")
    launch(build_app(Conductor(ws, device=args.device)), ws, server_port=args.port,
           inbrowser=not in_wsl)


if __name__ == "__main__":
    main()
