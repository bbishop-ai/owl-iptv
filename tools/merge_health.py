#!/usr/bin/env python3
"""Merge health check results from all shards and update validation cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Merge health check results")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    cache_path = Path(args.cache)
    output_path = Path(args.output)

    # Load existing cache
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    # Merge all shard results
    total_checked = 0
    for shard_file in sorted(input_dir.glob("*.json")):
        with shard_file.open() as f:
            data = json.load(f)
        for url, result in data.get("results", {}).items():
            cache[url] = result
            total_checked += 1

    # Save updated cache
    output_path.write_text(json.dumps(cache, indent=2))

    # Generate summary
    alive = sum(1 for v in cache.values() if v.get("ok"))
    dead = sum(1 for v in cache.values() if not v.get("ok"))
    avg_reliability = sum(v.get("reliability", 0) for v in cache.values()) / max(len(cache), 1)

    summary = {
        "total_cached": len(cache),
        "last_merge": "shards_merged",
        "alive": alive,
        "dead": dead,
        "avg_reliability": round(avg_reliability, 1),
        "checked_this_run": total_checked,
    }
    Path(args.input_dir).parent.joinpath("health-summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()