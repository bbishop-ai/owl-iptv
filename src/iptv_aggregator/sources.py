from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import aiohttp

from .parse import parse_playlist


class SourceFetcher:
    def __init__(self, cache_dir: Path, timeout: int = 25, concurrency: int = 8, root_dir: Path | None = None):
        self.cache_dir = cache_dir
        self.root_dir = (root_dir or cache_dir.parents[1]).resolve()
        self.timeout = timeout
        self.semaphore = asyncio.Semaphore(concurrency)
        cache_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_all(self, sources: list[dict[str, Any]]):
        enabled = [s for s in sources if s.get("enabled", True) and s.get("kind") == "playlist"]
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        headers = {"User-Agent": "owl-iptv/1.0"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            return await asyncio.gather(*(self._fetch(session, source) for source in enabled))

    async def _fetch(self, session: aiohttp.ClientSession, source: dict[str, Any]):
        if source.get("path"):
            try:
                path = (self.root_dir / source["path"]).resolve()
                path.relative_to(self.root_dir)
                raw = path.read_bytes()
                if len(raw) > int(source.get("max_bytes", 25_000_000)):
                    raise ValueError("source exceeds max_bytes")
                channels = parse_playlist(raw.decode("utf-8", errors="replace"), source["id"])
                if not channels:
                    raise ValueError("no channels parsed")
                return source, channels, "bundled"
            except Exception as exc:
                return source, [], f"failed: {exc}"

        key = hashlib.sha256(source["url"].encode()).hexdigest()
        body_path = self.cache_dir / f"{key}.body"
        meta_path = self.cache_dir / f"{key}.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        conditional = {}
        if meta.get("etag"):
            conditional["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            conditional["If-Modified-Since"] = meta["last_modified"]
        try:
            async with self.semaphore, session.get(source["url"], headers=conditional, allow_redirects=True) as response:
                if response.status == 304 and body_path.exists():
                    text = body_path.read_text(encoding="utf-8", errors="replace")
                    return source, parse_playlist(text, source["id"]), "cached"
                response.raise_for_status()
                raw = await response.read()
                if len(raw) > int(source.get("max_bytes", 25_000_000)):
                    raise ValueError("source exceeds max_bytes")
                text = raw.decode("utf-8", errors="replace")
                channels = parse_playlist(text, source["id"])
                if not channels:
                    raise ValueError("no channels parsed")
                body_path.write_bytes(raw)
                meta_path.write_text(json.dumps({
                    "etag": response.headers.get("ETag", ""),
                    "last_modified": response.headers.get("Last-Modified", ""),
                }), encoding="utf-8")
                return source, channels, "fresh"
        except Exception as exc:
            if body_path.exists():
                text = body_path.read_text(encoding="utf-8", errors="replace")
                cached = parse_playlist(text, source["id"])
                if cached:
                    return source, cached, f"stale-cache: {exc}"
            return source, [], f"failed: {exc}"
