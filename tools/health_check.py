#!/usr/bin/env python3
"""Health check tool for IPTV streams - runs as parallel shards."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from iptv_aggregator.config import load_config, load_sources
from iptv_aggregator.models import Channel, Validation
from iptv_aggregator.sources import SourceFetcher
from iptv_aggregator.validator import Validator


async def run_health_check(
    config_path: str,
    shard_index: int,
    shard_count: int,
    cache_path: str,
    output_path: str,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    root = Path(cfg["_root"])
    sources = load_sources(root / cfg["sources_file"])
    work = root / "build"
    stage = root / "stage"
    if stage.exists():
        import shutil
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    fetcher = SourceFetcher(
        work / "source-cache",
        cfg["fetch"]["timeout_seconds"],
        cfg["fetch"]["concurrency"],
        root,
    )
    fetched = await fetcher.fetch_all(sources)
    channels = [channel for _, items, _ in fetched for channel in items]

    # Shard the channels
    shard_channels = [c for i, c in enumerate(channels) if i % shard_count == shard_index]

    # Load existing validation cache
    cache_file = Path(cache_path)
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}

    validator = Validator(
        cache_path=work / "validation-cache.json",
        timeout=cfg["validation"]["timeout_seconds"],
        concurrency=cfg["validation"]["concurrency"],
        ttl_hours=cfg["validation"]["ttl_hours"],
        deep_limit=cfg["validation"]["deep_probe_limit"],
    )
    validator.cache = cache

    # Validate shard
    results = await validator.validate(shard_channels)

    # Convert to serializable format
    serializable = {}
    for url, val in results.items():
        serializable[url] = {
            "ok": val.ok,
            "latency_ms": val.latency_ms,
            "width": val.width,
            "height": val.height,
            "fps": val.fps,
            "frozen": val.frozen,
            "error": val.error,
            "cached": val.cached,
            "checked_at": datetime.now(UTC).isoformat(),
            "check_count": cache.get(url, {}).get("check_count", 0) + 1,
            "success_count": cache.get(url, {}).get("success_count", 0) + (1 if val.ok else 0),
            "reliability": calculate_reliability(cache.get(url, {}), val),
        }

    output = {
        "shard_index": shard_index,
        "shard_count": shard_count,
        "checked": len(serializable),
        "timestamp": datetime.now(UTC).isoformat(),
        "results": serializable,
    }

    Path(output_path).write_text(json.dumps(output, indent=2))
    return output


def calculate_reliability(old: dict, new: Validation) -> float:
    """Calculate 7-day rolling reliability score (0-100)."""
    if not old:
        return 100.0 if new.ok else 0.0

    # Weight: 60% uptime, 25% resolution, 15% FPS
    uptime_weight = 0.60
    res_weight = 0.25
    fps_weight = 0.15

    # Uptime component
    checks = old.get("check_count", 1)
    successes = old.get("success_count", 1 if new.ok else 0)
    if new.ok:
        successes += 1
    uptime = (successes / (checks + 1)) * 100

    # Resolution component (normalized to 1080p = 100)
    res_score = min((new.height or 0) / 1080 * 100, 100)

    # FPS component (60fps = 100)
    fps_score = min((new.fps or 0) / 60 * 100, 100)

    return round(uptime_weight * uptime + res_weight * res_score + fps_weight * fps_score, 1)


def main():
    parser = argparse.ArgumentParser(description="IPTV health check shard")
    parser.add_argument("--config", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = asyncio.run(run_health_check(
        args.config,
        args.shard_index,
        args.shard_count,
        args.cache,
        args.output,
    ))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()