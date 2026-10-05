"""Keeping the song folder small.

Measured on a real workspace (56 songs analyzed during development): 25 GB, of which 25.7 GB of
WAV (the download, six separated tracks, two "isolated" mixes, each ~40 MB per 4 minutes) and
1.05 GB of Opus copies, the only audio the player actually plays. Transcription, edits and
patterns are under 1 MB per song.

Two rules:

  * compact: once a song is analyzed, every WAV is replaced by its Opus copy (128 kbit/s, what
    the player already streamed) and temporary/derived audio is deleted. ~450 MB -> ~20 MB.
    Everything the player does keeps working; code that reads audio later (practice tracks,
    export, a re-transcription after an app update) reads the Opus through audio_io.
  * limit: when the folder grows past the limit (default 5 GB), the audio of the songs opened
    longest ago is deleted. Their transcription, sensitivity and hand edits stay; opening the
    song again downloads and separates it again (minutes), and the edits come back as they were.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .models import Song

VERSION = 1
DEFAULT_LIMIT_GB = 5.0
SCRATCH = ("isolated", "practice", "export", "_demucs", "_demucs_drums")


def limit_bytes() -> int:
    """MUSIC_PRACTICE_MAX_GB overrides the default (0 = no limit)."""
    try:
        gb = float(os.environ.get("MUSIC_PRACTICE_MAX_GB", DEFAULT_LIMIT_GB))
    except ValueError:
        gb = DEFAULT_LIMIT_GB
    return int(gb * 1e9)


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def opus_copy(src: str | Path) -> Path:
    """Opus copy of an audio file (in a 'web' folder next to it), made once. Returns the
    original if it is already compressed or ffmpeg can't encode it."""
    src = Path(src)
    if src.suffix.lower() in (".webm", ".opus", ".ogg", ".m4a", ".mp3"):
        return src
    out = src.parent / "web" / (src.stem + ".webm")
    if out.exists() and (not src.exists() or out.stat().st_mtime >= src.stat().st_mtime):
        return out
    out.parent.mkdir(exist_ok=True)
    try:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-c:a", "libopus",
                        "-b:a", "128k", str(out)], check=True, capture_output=True, timeout=600)
        return out
    except (OSError, subprocess.SubprocessError):
        out.unlink(missing_ok=True)
        return src


def audio_keys(song: Song) -> list[str]:
    return [k for k in song.artifacts if k == "audio:mix" or k.startswith("stem:")]


def compact(song: Song) -> int:
    """Replace the song's WAVs with Opus copies and delete scratch audio. Returns bytes freed.
    Safe to run again (does nothing the second time)."""
    before = size_of(song.dir)
    for key in audio_keys(song):
        p = Path(song.artifacts[key])
        if p.suffix.lower() != ".wav" or not p.exists() or song.dir.resolve() not in p.resolve().parents:
            continue                                   # never touch files outside the song folder
        o = opus_copy(p)
        if o != p and o.exists() and o.stat().st_size > 0:
            song.artifacts[key] = str(o)
            p.unlink()
    for name in SCRATCH:
        shutil.rmtree(song.dir / name, ignore_errors=True)
    for stray in song.dir.glob("original.*"):          # leftovers of a download
        if str(stray) not in song.artifacts.values():
            stray.unlink(missing_ok=True)
    song.artifacts["storage:compact"] = VERSION
    song.save()
    return max(before - size_of(song.dir), 0)


def touch(song: Song) -> None:
    song.artifacts["storage:last_used"] = time.time()
    song.save()


def last_used(song: Song) -> float:
    return float(song.artifacts.get("storage:last_used") or song.manifest_path.stat().st_mtime)


def evict(song: Song) -> int:
    """Delete the song's audio, keep its transcription and edits. Returns bytes freed."""
    before = size_of(song.dir)
    for key in audio_keys(song):
        p = Path(song.artifacts.pop(key))
        if song.dir.resolve() in p.resolve().parents:
            p.unlink(missing_ok=True)
    for name in ("stems", "web", *SCRATCH):
        shutil.rmtree(song.dir / name, ignore_errors=True)
    for f in song.dir.glob("original.*"):
        f.unlink(missing_ok=True)
    # the drum track will come from a new separation: run the finer drum model again (the hits
    # already made from that model's track are kept, see TranscriptionAgent._better_drum_stem)
    if song.has("drums:hits") and song.artifacts.get("drums:source") == "stem":
        song.artifacts.setdefault("drums:hits_model", song.artifacts.get("drums:stem_model"))
    song.artifacts.pop("drums:stem_model", None)
    song.artifacts.pop("drums:stem_ft_failed", None)
    song.artifacts["storage:evicted"] = True
    song.save()
    return max(before - size_of(song.dir), 0)


def is_evicted(song: Song) -> bool:
    return bool(song.artifacts.get("storage:evicted")) or not song.has("audio:mix")


def restored(song: Song, audio_path: Path) -> Song:
    """Audio is back (downloaded or uploaded again): the song works as before."""
    song.artifacts["audio:mix"] = str(audio_path)
    song.artifacts.pop("storage:evicted", None)
    song.artifacts.pop("storage:compact", None)
    song.save()
    return song


def enforce_limit(ws, keep: set[str] = frozenset(), limit: int | None = None) -> list[str]:
    """Free space until the workspace is under the limit, oldest-opened songs first.
    Songs in `keep` (the one being opened) are never touched. Returns the titles cleared."""
    limit = limit_bytes() if limit is None else limit
    if limit <= 0:
        return []
    total = size_of(ws.root)
    if total <= limit:
        return []
    cleared = []
    for song in sorted((s for s in ws.list_songs() if s.id not in keep and not is_evicted(s)),
                       key=last_used):
        if total <= limit:
            break
        total -= evict(song)
        cleared.append(song.title)
    return cleared


def report(ws) -> dict:
    songs = ws.list_songs()
    return {"songs": len(songs), "bytes": size_of(ws.root),
            "wav_bytes": sum(f.stat().st_size for f in ws.root.rglob("*.wav")),
            "without_audio": sum(1 for s in songs if is_evicted(s))}
