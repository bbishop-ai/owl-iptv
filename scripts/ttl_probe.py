"""Mint a taifood token, probe its TTL at 0/10/25/35/60/90/120 min.

Both probes run inside ONE process with the sync Playwright API:
- in-session: page.evaluate(fetch(...)) from the minting page's context
- fresh-browser: a brand-new browser/context navigating the master URL

Prints one line per checkpoint. The minting browser stays open the whole time so
the in-session line isolates "session died" from "URL dead".
"""
import subprocess
import sys
import time

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
DELAYS = [0, 600, 1500, 2100, 3600, 5400, 7200]  # 0, 10, 25, 35, 60, 90, 120 min
EMBED = "https://taifood-blog.asia/embed/nfl-network"

FRESH_PROBE = """
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, channel="chromium", args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    try:
        ctx = b.new_context(user_agent={ua!r})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{{get:()=>undefined}});")
        pg = ctx.new_page()
        try:
            resp = pg.goto({master!r}, timeout=20000, wait_until="domcontentloaded")
            print(resp.status if resp else "?")
        except Exception as exc:
            print("ERR:" + str(exc)[:40])
    finally:
        b.close()
"""


def fresh_check(master: str) -> str:
    out = subprocess.run([sys.executable, "-c", FRESH_PROBE.format(ua=UA, master=master)],
                         capture_output=True, text=True, timeout=120)
    return (out.stdout or ("ERR:" + out.stderr[-60:])).strip()


def main() -> int:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, channel="chromium",
                               args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(user_agent=UA)
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        pg = ctx.new_page()
        pg.goto(EMBED, wait_until="domcontentloaded", timeout=30000)
        master = ""
        for _ in range(8):
            pg.wait_for_timeout(4000)
            master = pg.evaluate(
                "(() => { try { const p = window.jwplayer('player'); return (p && p.getPlaylist && p.getPlaylist().length) ? p.getPlaylist()[0].file : ''; } catch(e) { return ''; } })()"
            ) or ""
            if master:
                break
        if not master:
            print("ERROR: no master resolved", flush=True)
            return 1
        t0 = time.time()
        print(f"minted at t0: {master}", flush=True)
        for delay in DELAYS:
            wait = delay - (time.time() - t0)
            if wait > 0:
                time.sleep(wait)
            try:
                in_session = pg.evaluate(
                    "(url) => fetch(url, {mode:'cors'}).then(r => String(r.status)).catch(e => 'ERR:' + String(e).slice(0,40))",
                    master,
                )
            except PWError as exc:
                in_session = "PWERR:" + str(exc)[:40]
            fresh = fresh_check(master)
            print(f"TTL t+{delay // 60}min: in-session={in_session} fresh-browser={fresh}", flush=True)
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
