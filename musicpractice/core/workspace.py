"""Local workspace: one folder per song, keyed by audio hash.

Uploading the same file twice reuses the folder, so expensive results
(stems take minutes on CPU) are computed only once.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .models import Song

DEFAULT_ROOT = Path.home() / ".music-practice"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class Workspace:
    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def import_song(self, audio_path: Path | str, title: str | None = None) -> Song:
        audio_path = Path(audio_path)
        song_id = _hash_file(audio_path)
        existing = self.get(song_id)
        if existing is not None and existing.has("audio:mix"):
            return existing
        song_dir = self.root / song_id
        song_dir.mkdir(parents=True, exist_ok=True)
        dest = song_dir / f"original{audio_path.suffix.lower()}"
        shutil.copyfile(audio_path, dest)
        if existing is not None:              # audio was cleared to save space: it's back
            from .storage import restored
            return restored(existing, dest)
        return self.create_song(song_id, title or audio_path.stem, dest)

    def song_dir(self, song_id: str) -> Path:
        return self.root / song_id

    def get(self, song_id: str) -> Song | None:
        d = self.root / song_id
        return Song.load(d) if (d / "artifacts.json").exists() else None

    def create_song(self, song_id: str, title: str, audio_path: Path,
                    extra: dict | None = None) -> Song:
        """Register audio already placed inside the song's folder."""
        song = Song(id=song_id, dir=self.root / song_id, title=title,
                    artifacts={"audio:mix": str(audio_path), **(extra or {})})
        song.save()
        return song

    def list_songs(self) -> list[Song]:
        return [Song.load(d) for d in sorted(self.root.iterdir())
                if (d / "artifacts.json").exists()]
