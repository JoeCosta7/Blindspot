"""Disk JSON cache with a TTL, plus an in-process layer so warm requests do not
re-read large files. Keys are the source name and its arguments. Failures are
never cached: `cached_fetch` only stores what `fetch` returns."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

HOUR = 3600
DAY = 24 * HOUR

_memory: dict[Path, tuple[float, Any]] = {}


def _path(cache_dir: Path, source: str, args: dict[str, Any]) -> Path:
    digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:20]
    return cache_dir / f"{source}_{digest}.json"


def cache_get(cache_dir: Path, source: str, args: dict[str, Any], ttl_s: float) -> Any | None:
    """The cached value, or None if absent, expired or unreadable."""
    path = _path(cache_dir, source, args)
    entry = _memory.get(path)
    if entry is None:
        try:
            raw = json.loads(path.read_text())
            entry = (float(raw["saved_at"]), raw["value"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        _memory[path] = entry
    saved_at, value = entry
    if time.time() - saved_at > ttl_s:
        return None
    return value


def cache_set(cache_dir: Path, source: str, args: dict[str, Any], value: Any) -> None:
    path = _path(cache_dir, source, args)
    now = time.time()
    _memory[path] = (now, value)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"saved_at": now, "source": source, "args": args, "value": value})
        )
        tmp.replace(path)
    except OSError:
        pass  # a read-only disk must not fail the request


async def cached_fetch(
    cache_dir: Path,
    source: str,
    args: dict[str, Any],
    ttl_s: float,
    fetch: Callable[[], Awaitable[Any]],
) -> tuple[Any, bool]:
    """Returns (value, came_from_cache). `fetch` must return JSON-serialisable
    data; None is treated as "nothing to cache"."""
    hit = cache_get(cache_dir, source, args, ttl_s)
    if hit is not None:
        return hit, True
    value = await fetch()
    if value is not None:
        cache_set(cache_dir, source, args, value)
    return value, False


def clear_memory() -> None:
    _memory.clear()
