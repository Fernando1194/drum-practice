# Drum Practice

Turn any song into a drum lesson you can play on your electronic kit.

Paste a link (a studio track, a live video, a song nobody ever wrote a drum chart for), and the
app separates the drums, transcribes every hit, finds the grooves the song is built from, and
gives you a synced player with a kit diagram, a drum grid, practice steps and a Guitar Hero
style highway you play on your e-kit over MIDI. Everything runs on your own computer.

> Resumo em português no fim da página.

```
Song link -> separate the drums -> transcribe the hits -> grid on the drummer's time
          -> patterns and steps -> practice: grid, steps, highway on your MIDI kit
```

Open source (MIT), personal project, work in progress. The transcription is good, not perfect:
the numbers below say how good, and the app lets you fix what it gets wrong.

## What you get

- **Kit seen from above**, each piece in its own color; it flashes when hit, and a ring grows
  during the beat before its next hit.
- **Drum grid** (x = cymbal, o = drum, one column per 16th note) in the same colors, scrolling
  with the music. The playhead crosses each mark exactly when it sounds; the page glides to the
  next line during the last beat of a line. Drag the grip under the grid to see 1 to 8 lines ahead.
- **Patterns**: the grooves and fills the song is made of ("Groove 1 x18, Fill 1 x8"), a colored
  band with one square per bar, and a click to show only the bars of one pattern.
- **Beat figures**: one level down, every beat gets a letter: plain rock reads "A B C B"
  (A = kick + hi-hat, B = snare + hi-hat, C = kick on the beat and the "&"). A chip per figure
  shows a tiny drawing of its four 16ths and how often it's played; click it to light up every
  beat that plays it. Most songs come down to a handful of figures.
- **Steps**: the song as a handful of steps instead of 120 bars. A step is a groove played a
  few times plus the fill that closes the phrase; the view shows that groove once, and every
  time it comes round again the playhead sweeps back and a counter goes up ("2 of 3", "pass 1 of 6").
- **Highway** for an electronic kit over MIDI (Chrome/Edge, Web MIDI): notes fall to a hit
  line, each hit judged in real time (Perfect 30 ms, Good 60 ms, OK 100 ms), accuracy, combo,
  rushing/dragging, a score per loop pass, latency calibration, "learn" to map any pad, and a
  MIDI monitor that shows what each pad of your kit sends.
- **Practice controls**: speed 40% to 125% without changing pitch, loop any bars or a section,
  count-in, click track, a mixer with a volume/mute/solo per instrument (play along with the
  drums muted), every slider also typeable, layout remembered.
- **Fix transcription**: sensitivity per piece (kick, snare, hi-hat, toms, cymbals) and
  add/remove hits by clicking the grid. Your edits survive re-analysis.
- **Export**: drum MIDI (General MIDI, channel 10), a text drum grid, and the separated audio.

Keys: Space play/pause, arrows jump a bar, L toggles the loop.

## How accurate is it

Measured on [MDB Drums](https://github.com/CarlSouthall/MDBDrums) (23 real recordings,
~8,000 hits annotated by hand), F1 (1.0 = every hit found and nothing invented):

| Path | Kick | Snare | Hi-hat | Cymbals | Toms |
|---|---|---|---|---|---|
| Clean drum recording (best possible case) | 0.96 | 0.80 | 0.86 | 0.87 | 0.53 |
| **Full song, drums separated by the app (what you get)** | **0.93** | **0.77** | **0.86** | **0.86** | **0.46** |
| Full song, no separation | 0.85 | 0.70 | 0.85 | 0.83 | 0.47 |

- Kick, hi-hat and cymbals are reliable; snare is good; **ghost notes and the hi-hat pedal are
  the main misses** (about half of each), and toms are the weak spot.
- Crash vs ride is decided by how long the cymbal rings (94% right).
- Open vs closed hi-hat is not distinguished.
- The bar grid follows the drummer: on 14 rock/pop/funk recordings, bar lines land on the right
  beat 88% of the time (65% with the beat tracker alone).

What was tried and left out because it didn't help on this data (noise gate, averaging two
separations, mixing stem and full-mix predictions, timbre relabeling) is documented, with
numbers, in [docs/PROJETO.md](docs/PROJETO.md).

## Install

Python 3.10 or 3.11. Linux, macOS, or Windows through WSL (Ubuntu).

```bash
git clone https://github.com/Fernando1194/drum-practice.git
cd drum-practice
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"            # app, link download, analysis
pip install -e ".[separation]"     # drum separation (PyTorch + Demucs, ~2 GB)
pip install --no-deps "adtof-pytorch @ git+https://github.com/xavriley/ADTOF-pytorch.git"
```

- **ffmpeg** must be on your PATH: `sudo apt install ffmpeg` (Ubuntu/WSL), `brew install ffmpeg` (macOS).
- **ADTOF** is the drum transcription model. Without it the app falls back to a basic detector
  that confuses pieces on real recordings. `--no-deps` keeps it from changing your PyTorch.
- **GPU**: with an NVIDIA card, install the CUDA build of PyTorch first (pytorch.org) and run
  with `--device cuda`. On CPU, separating a 4-minute song takes a few minutes, once per song.
- The Demucs separation models (~53 MB and ~80 MB) are downloaded the first time.

## Run

```bash
drum-practice                    # opens http://127.0.0.1:7860
drum-practice --device cuda
drum-practice --clean            # shrink songs analyzed by older versions, then exit
drum-practice --max-gb 10        # disk limit for songs (default 5 GB)
```

On WSL, open http://localhost:7860 in your Windows browser. Web MIDI runs in Chrome/Edge on
Windows, so the kit plugs into Windows as usual; nothing to set up inside Ubuntu.

Songs and results are kept in `~/.music-practice/<song id>/` (change with `--workspace`), so a
song is only processed once.

### Disk space

Separation makes a lot of audio (about 450 MB of WAV per song). Once a song is analyzed the app
keeps only compressed copies (Opus, what the player plays) and the transcription: about 20 MB
per song. Re-transcribing from the Opus copy gives the same result (MDB Drums: F1 within 0.004
per piece, toms within 0.013).

- **Limit**: past 5 GB, the audio of the songs you opened longest ago is removed. Their
  transcription and your edits stay; opening one again downloads and separates it again.
  Change it with `--max-gb 10` (0 = no limit).
- **Songs analyzed before v28**: run `drum-practice --clean` once to shrink them (one 56-song
  folder: 25 GB of WAV).

### Links

YouTube, SoundCloud, Bandcamp, Vimeo and most sites with a playable audio/video file work (via
yt-dlp). Spotify, Apple Music, Deezer, Tidal and Amazon Music don't (DRM). You can always upload
an audio file instead. Downloading from YouTube may be against its terms of service; use it for
personal practice with music you have the right to use.

## Where things run

Everything runs locally; no audio leaves your computer.

| Step | Model / library | Runs on |
|---|---|---|
| Download | yt-dlp | your PC (internet: the song) |
| Separation | Demucs htdemucs_6s, plus the drum model of htdemucs_ft | your PC, GPU if available |
| Drum hits | ADTOF (PyTorch) | your PC, CPU |
| Tempo, bar grid, patterns, steps | librosa, numpy, scipy (no AI) | your PC |
| Interface | Gradio + your browser | your PC (localhost) |

The "agents" in the code are plain Python, not calls to a language model.
Gradio sends anonymous usage statistics by default; set `GRADIO_ANALYTICS_ENABLED=False` to stop it.

## Other instruments

The code also transcribes guitar, bass, piano and vocals (notes, tablature, chords), but that
part is frozen while the drums are the focus and is hidden in the app. To show it:
`MUSIC_PRACTICE_ALL_INSTRUMENTS=1 drum-practice`.

## Code

```
musicpractice/
  agents.py          the agents and the Conductor that runs the workflow, export
  app.py             Gradio UI (one page)
  core/              Song, Workspace (cache per song), plugin Registry
  plugins/
    input.py         link -> WAV (yt-dlp)
    separation.py    Demucs (all stems, then the drums with htdemucs_ft's drum model)
    rhythm.py        tempo, beats, bar lines
    drums_adtof.py   drum hits with ADTOF (candidates with a score each)
    drums.py         fallback detector (NMF + rules), drum MIDI
    drum_edit.py     sensitivity, "three hands" rule, hand edits
    drum_echo.py     a kick heard twice (as a weak snare/hi-hat) becomes one hit
    drum_grid.py     bar grid moved onto the drummer (timing, beat 1, half speed)
    drum_tab.py      drum grid (x/o per 16th note)
    patterns.py      grooves, fills, practice steps and one-beat figures
  ui/                player (HTML/CSS/JS): kit, grid, steps, highway, mixer, editor
tests/               pytest on synthetic songs, browser checks (Playwright), MDB benchmark
docs/PROJETO.md      design notes, decisions and every measurement (Portuguese)
```

```bash
pytest                                   # ~3 min, synthetic songs with known answers
python tests/browser_check_drums.py out  # the player in headless Chromium
python tests/benchmark_mdb.py drum_only  # real accuracy (needs MDB Drums, ~3 GB)
```

New features are plugins that declare what they need and what they produce; the Registry runs
dependencies first and caches results per song.

## Roadmap

1. Read each pad's MIDI note on a real kit (the monitor is in place), then add lanes and rows for
   open/pedal hi-hat, ride bell and rim, and export with the kit's own note map.
2. Copy a corrected bar to every repeat of the same pattern.
3. A x2 / /2 tempo button for songs detected at half or double speed.
4. A downbeat model (`beat_this`) for songs with tempo changes.
5. Import an existing drum MIDI, to compare with the transcription or to use it instead.

## Licenses

This project's code: [MIT](LICENSE). Its dependencies keep their own licenses, notably:

- **ADTOF** model weights: CC BY-NC-SA 4.0 (non-commercial). It is installed separately, not
  included here; if you use it, the drum transcription is for non-commercial use.
- Demucs: MIT. Basic Pitch: Apache 2.0. Gradio: Apache 2.0. librosa: ISC.
- MDB Drums (used only by the benchmark, not included): CC BY-NC-SA 4.0.

---

## Resumo em português

**Drum Practice** transforma qualquer música (inclusive ao vivo, sem partitura oficial) numa aula
de bateria para tocar na bateria eletrônica. Cole o link: o app separa a bateria, transcreve cada
batida, acha os grooves e viradas, e abre um player com o kit visto de cima, a grade de bateria,
as **figuras de um tempo** (cada tempo do compasso vira uma letra, para ver as batidas que se repetem), o modo **Steps** (a música em poucos passos que se repetem) e o **Highway** (estilo Guitar Hero)
tocado no seu kit via MIDI. Tudo roda no seu computador.

Precisão medida em 23 gravações reais anotadas à mão: bumbo 0,93, caixa 0,77, chimbal 0,86,
pratos 0,86, tons 0,46. Ghost notes e chimbal de pé são o que mais falta; dá para corrigir na grade.

Instalação e uso: seções *Install* e *Run* acima. Notas de projeto, decisões e todas as medições
em português: [docs/PROJETO.md](docs/PROJETO.md).
