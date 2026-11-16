"""Mint a taifood token, probe its TTL at 0/10/25/35 min (in-session vs fresh browser)."""
import asyncio
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
DELAYS = [0, 600, 1500, 2100]  # 0, 10, 25, 35 min

FRESH_PROBE = """
import asyncio
async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, channel="chromium", args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        pg = await b.new_context(user_agent={ua!r}).new_page()
        try:
            resp = await pg.goto({master!r}, timeout=20000)
            print(resp.status if resp else "?")
        except Exception as exc:
            print("ERR:" + str(exc)[:40])
        finally:
            await b.close()
asyncio.run(main())
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
        pg.goto("https://taifood-blog.asia/embed/nfl-network", wait_until="domcontentloaded", timeout=30000)
        master = ""
        for _ in range(6):
            pg.wait_for_timeout(4000)
            master = pg.evaluate(
                "(() => { try { const p = window.jwplayer('player'); return (p && p.getPlaylist && p.getPlaylist().length) ? p.getPlaylist()[0].file : ''; } catch(e) { return ''; } })()"
            ) or ""
            if master:
                break
        t0 = time.time()
        print(f"minted at t0: {master[:80]}", flush=True)
        for delay in DELAYS:
            wait = delay - (time.time() - t0)
            if wait > 0:
                time.sleep(wait)
            in_session = pg.evaluate(
                "(url) => fetch(url, {mode:'cors'}).then(r => String(r.status)).catch(e => 'ERR:' + String(e).slice(0,40))",
                master,
            )
            print(f"TTL t+{delay // 60}min: in-session={in_session} fresh-browser={fresh_check(master)}", flush=True)
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
