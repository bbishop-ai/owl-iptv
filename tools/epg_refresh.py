#!/usr/bin/env python3
"""EPG refresh tool - fetches and merges EPG sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import aiohttp

from iptv_aggregator.config import load_config, load_sources
from iptv_aggregator.epg import fetch_epg, xmltv_bytes
from iptv_aggregator.models import Channel
from iptv_aggregator.normalize import normalize_channel
from iptv_aggregator.parse import parse_playlist
from iptv_aggregator.sources import SourceFetcher


async def run_epg_refresh(config_path: str, cache_dir: str, output_path: str) -> dict[str, Any]:
    cfg = load_config(config_path)
    root = Path(cfg["_root"])
    sources = load_sources(root / cfg["sources_file"])
    work = root / "build"

    # Fetch channels to get tvg_ids for EPG filtering
    fetcher = SourceFetcher(
        work / "source-cache",
        cfg["fetch"]["timeout_seconds"],
        cfg["fetch"]["concurrency"],
        root,
    )
    fetched = await fetcher.fetch_all(sources)
    channels = [channel for _, items, _ in fetched for channel in items]
    channels = [normalize_channel(channel) for channel in channels]

    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    epg_data, statuses = await fetch_epg(
        sources,
        timeout_seconds=cfg["epg"]["timeout_seconds"],
        programme_ids={c.tvg_id for c in channels if c.tvg_id},
        programme_names={c.tvg_name for c in channels if c.tvg_name},
        attempts=cfg["epg"].get("attempts", 3),
        stale_cache_dir=cache_path,
    )

    # Write EPG XML
    xml_content = xmltv_bytes(channels, epg_data)
    Path(output_path).write_bytes(xml_content)

    # Save cache status
    cache_file = cache_path / "epg-status.json"
    cache_file.write_text(json.dumps(statuses, indent=2))

    return {
        "channels": len(epg_data.channels),
        "programmes": sum(len(v) for v in epg_data.programmes.values()),
        "sources_fetched": len([s for s in statuses.values() if s == "ok"]),
        "sources_failed": len([s for s in statuses.values() if s != "ok"]),
        "statuses": statuses,
    }


def main():
    parser = argparse.ArgumentParser(description="EPG refresh")
    parser.add_argument("--config", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    import asyncio
    result = asyncio.run(run_epg_refresh(args.config, args.cache_dir, args.output))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()