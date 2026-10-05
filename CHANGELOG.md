# Changelog

Every version: what changed and why, with the measurement behind it when there is one.
Design notes and all measurements in detail (Portuguese): [docs/PROJETO.md](docs/PROJETO.md).
To publish a version as a GitHub release: `scripts/release.sh v0.28.0` (see the script).

## v0.28.0 (2026-10-05): Disk space

**Disk space: 25 GB -> 1.7 GB** on a real workspace of 56 songs.

- After analysis, every WAV is replaced by its Opus copy (what the player already played) and scratch audio is deleted: ~450 MB -> ~30 MB per song.
- Disk limit (default 5 GB, `--max-gb`): past it, the audio of the songs opened longest ago is removed. Transcription, sensitivity and hand edits stay; opening the song again downloads and separates it again without re-transcribing.
- `drum-practice --clean` shrinks songs analyzed by older versions. Gradio's temp folder is cleared hourly.

**Measured:** re-transcribing from Opus instead of WAV changes nothing that matters (ADTOF on MDB Drums, F1 within 0.004 per piece; toms within 0.013).

## v0.27.0 (2026-10-03): Pulses on the playhead

**Grid marks pulse when the playhead crosses them.** They used to pulse at the hit's audio time; a hit off the 16th grid (8% of hits are more than 40 ms off, even on hand-annotated beats) pulsed before or after the line reached its mark, so the line seemed to pass notes that didn't react. The kit still flashes on the audio time.

**Lighter animations.** Pulses use Web Animations instead of class toggling with a forced page layout on every hit.

## v0.26.1 (2026-10-03): Visible folded panels

**Folded panels are easy to find.** The mixer remembers being folded, so a folded mixer looked gone. Its title is now a header bar with a yellow chevron and show/hide; the upload and accordion titles are brighter with accent-colored arrows.

## v0.26.0 (2026-10-03): Beat figures

**Beat figures.** Every beat of the bar gets a letter, so repeated beats are visible at a glance: plain rock reads "A B C B" (A = kick + hi-hat, B = snare + hi-hat, C = kick on the beat and the "&"). A chip per figure shows a drawing of its four 16ths and how often it is played; clicking it lights every beat that plays it.

**Measured** (MDB Drums rock/pop/funk, grouping from the transcription vs from the hand annotations, adjusted Rand index): 0.84 on the drum stem, 0.72 on the full mix. Cut 0.3 chosen over 0.4 because 0.4 merges "kick on the beat" with "kick on the beat and the &", a difference the drummer has to play.

## v0.25.0 (2026-10-02): One look for the whole page

**One look for the whole page.** The form around the player now uses the player's own dark theme with one yellow accent; the song title, BPM, bars and length sit at the top of the player.

**Fewer fields.** In drums-only mode the instrument picker, checkboxes, duplicate audio players and explanatory text are gone: paste a link, press Analyze.

## v0.24.0 (2026-10-02): First public version

First public version: paste a song link (or upload a file) and get a drum lesson to play on an electronic kit.

**What it does**
- Separates the drums (Demucs htdemucs_6s, drums re-separated with the htdemucs_ft drum model) and transcribes every hit with ADTOF.
- Moves the bar grid onto the drummer (timing, beat 1, half-speed detection), so the same groove is drawn the same way.
- Finds grooves, fills and practice steps; synced player with kit diagram, drum grid, Steps view, mixer and a MIDI highway for e-kits.
- Everything runs locally.

**Measured** (MDB Drums, 23 real recordings, F1): kick 0.93, snare 0.77, hi-hat 0.86, cymbals 0.86, toms 0.46. Bar lines on the right beat 88% of the time (65% with the beat tracker alone).

Versions v1 to v23 were developed before this repository existed; their history, decisions and measurements are in `docs/PROJETO.md`.

## Before this repository (v1 to v23)

Developed before the code was on GitHub; v0.24.0 contains all of it.

- v1-v3: base app: link in, separation, analysis, transcription, tab, practice tracks, export; runs on WSL.
- v4: one-page player synced with the music.
- v5-v7: drums: kit diagram, same colors in the grid, ADTOF transcription.
- v8: bigger two-line grid; one player at a time; whole-piece flash animations.
- v9: resizable kit/grid layout, live mixer per instrument.
- v10: Guitar Hero style highway for MIDI e-kits.
- v11: transcription redone from the isolated track once it exists.
- v12: "Fix transcription" panel: sensitivity per piece, editing in the grid.
- v13: playhead synced with the marks; typeable slider values.
- v14: continuous staff per line.
- v15: MIDI monitor in the highway.
- v16: "three hands" rule (no unplayable hits).
- v17: smooth follow scroll.
- v18: repeated patterns (grooves, fills, one-offs) with colors and loop per pattern.
- v19: tolerant pattern matching (weights + clustering, ARI 0.47 -> 0.73) and the Steps view.
- v20: grip to choose how many lines ahead to see.
- v21: bar grid reaches the end of the song (fade-outs and quiet endings had no transcription).
- v22: bar grid follows the drummer (timing, beat 1, half speed); repeatable Demucs.
- v23: a kick heard twice (as a weak snare/hi-hat echo) becomes one hit.
