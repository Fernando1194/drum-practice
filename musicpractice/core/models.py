"""Core data model.

A Song is an uploaded audio file plus a bag of named artifacts produced by plugins.
Artifact keys are namespaced strings, e.g.:

    audio:mix          path to the original audio
    stem:guitar        path to an isolated stem
    tempo              float BPM
    beats              list[float] beat times in seconds
    bars               list[float] bar start times in seconds

Values must be JSON-serializable (paths are stored as strings).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Song:
    id: str                      # sha256 of the audio bytes (first 16 hex chars)
    dir: Path                    # workspace folder for this song
    title: str
    artifacts: dict[str, Any] = field(default_factory=dict)

    @property
    def manifest_path(self) -> Path:
        return self.dir / "artifacts.json"

    def has(self, key: str) -> bool:
        return key in self.artifacts

    def get(self, key: str) -> Any:
        if key not in self.artifacts:
            raise KeyError(f"Song {self.title!r} has no artifact {key!r}")
        return self.artifacts[key]

    def stems(self) -> dict[str, str]:
        """All isolated stems, as {instrument: path}."""
        return {k.split(":", 1)[1]: v for k, v in self.artifacts.items() if k.startswith("stem:")}

    def save(self) -> None:
        data = {"id": self.id, "title": self.title, "artifacts": self.artifacts}
        self.manifest_path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, song_dir: Path) -> "Song":
        data = json.loads((song_dir / "artifacts.json").read_text())
        return cls(id=data["id"], dir=song_dir, title=data["title"], artifacts=data["artifacts"])
