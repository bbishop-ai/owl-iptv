"""Resolve taifood embed paths to indianservers masters via headless browser.

Reads config/thetvapp_channels.json, opens each embed in headless chromium, reads
jwplayer's resolved master URL, writes build/generated/thetvapp.m3u. Never fails the
build: an unresolvable embed is dropped; total failure writes a valid, empty playlist.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "thetvapp_channels.json"
DEFAULT_RELAY = "https://owl-pluto-relay.bradsai818.workers.dev"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"

RESOLVE_JS = """
(() => {
  try {
    const p = window.jwplayer && window.jwplayer('player');
    if (p && p.getPlaylist && p.getPlaylist().length) return p.getPlaylist()[0].file || '';
    const v = document.querySelector('video');
    if (v && (v.src || v.currentSrc)) return String(v.src || v.currentSrc);
    return '';
  } catch (e) { return ''; }
})()
"""


def relay_url(base: str, upstream: str) -> str:
    # indianservers CDN TLS-blocks non-browser fetchers (python, Cloudflare Workers),
    # so thetvapp channels ship DIRECT master URLs - no relay hop. Tokens are minted
    # at build time and re-minted at every build (~6h); a build-stale token is dead
    # until the next build, same lifecycle as a weekly team key.
    return upstream


def resolve_all(embeds: list[str], timeout_ms: int, per_page_wait_ms: int) -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    resolved: dict[str, str] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            channel="chromium",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 720})
        # The embed gates on headless signals; mask the obvious ones.
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        for embed in embeds:
            try:
                page.goto(embed, wait_until="domcontentloaded", timeout=timeout_ms)
                file = ""
                for _ in range(max(1, per_page_wait_ms // 4000)):
                    page.wait_for_timeout(4000)
                    file = (page.evaluate(RESOLVE_JS) or "").strip()
                    if file.startswith("http"):
                        break
                if file.startswith("http") and not file.startswith("blob:"):
                    resolved[embed] = file
                    print(f"  resolved {embed.rsplit('/', 1)[-1]} -> {file[:80]}")
                else:
                    print(f"  unresolved {embed.rsplit('/', 1)[-1]}")
            except Exception as exc:
                print(f"  error {embed.rsplit('/', 1)[-1]}: {str(exc)[:80]}")
        browser.close()
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--out", default="build/generated/thetvapp.m3u")
    parser.add_argument("--relay", default=os.environ.get("GREY_RELAY") or DEFAULT_RELAY)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--wait-ms", type=int, default=int(os.environ.get("THETVAPP_WAIT_MS", "24000")))
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    channels = cfg.get("channels", {})
    entries = sorted(channels.items())
    lines = ["#EXTM3U"]
    kept = 0
    if entries:
        embeds = [meta.get("embed") for _, meta in entries if meta.get("embed")]
        resolved = {}
        try:
            resolved = resolve_all(embeds, args.timeout_ms, args.wait_ms)
        except Exception as exc:  # playwright missing/browser fail: keep empty
            print(f"resolver failed entirely: {exc}")
        for key, meta in entries:
            embed = meta.get("embed", "")
            file = resolved.get(embed, "")
            if not file:
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
            display = meta.get("name", key).replace(",", " ")  # M3U: commas split name/URL
            lines.extend([f"#EXTINF:-1 {encoded},{display}", relay_url(args.relay, file)])
            kept += 1
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"thetvapp overlay: {kept}/{len(entries)} resolved -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
