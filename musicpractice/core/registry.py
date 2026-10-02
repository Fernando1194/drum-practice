"""Plugin contract and dependency-resolving pipeline.

Adding a feature = writing a class with `requires`, `produces` and `run`,
then registering it. The pipeline figures out the execution order and
skips anything already cached on the Song.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from .models import Song

log = logging.getLogger(__name__)


@runtime_checkable
class Plugin(Protocol):
    name: str
    requires: tuple[str, ...]
    produces: tuple[str, ...]

    def available(self) -> bool:
        """False if optional dependencies (torch, demucs...) are missing."""
        ...

    def run(self, song: Song, inputs: dict[str, Any]) -> dict[str, Any]:
        """Return a dict containing (at least) every key in `produces`."""
        ...


class Registry:
    def __init__(self) -> None:
        self._plugins: list[Plugin] = []

    def register(self, plugin: Plugin) -> Plugin:
        self._plugins.append(plugin)
        return plugin

    def plugins(self) -> list[Plugin]:
        return list(self._plugins)

    def producer_of(self, key: str) -> Plugin:
        candidates = [p for p in self._plugins if key in p.produces]
        usable = [p for p in candidates if p.available()]
        if usable:
            return usable[0]            # first registered available plugin wins
        if candidates:
            names = ", ".join(p.name for p in candidates)
            raise RuntimeError(f"Artifact {key!r} needs a plugin whose dependencies are "
                               f"not installed: {names}")
        raise LookupError(f"No plugin produces {key!r}")

    def ensure(self, song: Song, key: str, *, force: bool = False,
               _stack: tuple[str, ...] = ()) -> Any:
        """Make sure `key` exists on the song, running plugins as needed."""
        if song.has(key) and not force:
            return song.get(key)
        if key in _stack:
            raise RuntimeError(f"Dependency cycle: {' -> '.join(_stack + (key,))}")

        plugin = self.producer_of(key)
        inputs = {req: self.ensure(song, req, _stack=_stack + (key,))
                  for req in plugin.requires}

        log.info("Running %s for %s", plugin.name, song.title)
        outputs = plugin.run(song, inputs)
        missing = set(plugin.produces) - outputs.keys()
        if missing:
            raise RuntimeError(f"Plugin {plugin.name} did not produce {sorted(missing)}")

        song.artifacts.update(outputs)
        song.save()
        return song.get(key)

    def available_keys(self) -> list[str]:
        return sorted({k for p in self._plugins if p.available() for k in p.produces})
