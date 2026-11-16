"""Generate the optional grey-market sports overlay playlist.

Probes each configured channel master and emits an M3U the normal pipeline
ingests as a local source. Never fails the build: a dead key is dropped, and a
total failure writes a valid, empty playlist.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "grey_channels.json"
DEFAULT_RELAY = "https://owl-pluto-relay.bradsai818.workers.dev"
UPSTREAM = "https://dynamic.bakgethwa.com/hls/{key}.m3u8"
REFERER = "https://topstreamer.site/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"


def probe(url: str, timeout: int, referer: str | None = None) -> bool:
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            head = response.read(2048).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"  drop {url}: {exc}")
        return False
    if not head.lstrip().startswith("#EXTM3U"):
        print(f"  drop {url}: not a playlist")
        return False
    return True


def relay_url(base: str, upstream: str) -> str:
    return f"{base.rstrip('/')}/g?r={quote(REFERER, safe='')}&u={quote(upstream, safe='')}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="build/generated/grey.m3u")
    parser.add_argument("--relay", default=os.environ.get("GREY_RELAY") or DEFAULT_RELAY)
    parser.add_argument("--timeout", type=int, default=8)
    args = parser.parse_args()

    channels = json.loads(CONFIG.read_text(encoding="utf-8")).get("channels", {})
    lines = ["#EXTM3U"]
    kept = 0
    for key, meta in sorted(channels.items()):
        direct = meta.get("url")  # stable public master: bypass the upstream template
        upstream = direct or UPSTREAM.format(key=key)
        if not probe(upstream, args.timeout, None if direct else REFERER):
            continue
        attrs = {
            "tvg-id": meta.get("tvg_id", ""),
            "tvg-name": meta.get("name", key),
            "tvg-logo": meta.get("logo", ""),
            "group-title": meta.get("group", "Sports"),
            "tvg-language": "en",
            "tvg-country": "US",
        }
        encoded = " ".join(f'{name}="{value}"' for name, value in attrs.items() if value)
        lines.extend([f"#EXTINF:-1 {encoded},{meta.get('name', key)}", relay_url(args.relay, upstream)])
        kept += 1
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"grey overlay: {kept}/{len(channels)} keys live -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
