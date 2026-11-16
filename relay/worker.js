// Pluto relay for owl-iptv: /plu/<24-hex>.m3u8 -> fresh Pluto v2 session -> 302 to stitcher master.
// Replaces jmp2.uk after Pluto's v1 shutdown (Oct 2026). Each play mints its own guest session.
const BOOT = "https://boot.pluto.tv/v4/start";

// Grey-market proxy: /g?r=<referer>&u=<encoded upstream>.
// Re-fetches the upstream master on every play, so the short-lived signatures in
// its child playlists are always fresh, and supplies the Referer the hosts want.
const GREY_HOSTS = new Set([
  "dynamic.bakgethwa.com", "topstreamer.site", "trendy48.site",
  "o300801.mp.lura.live", "o300801-mp-lura-live.akamaized.net",
  "rally-tv-live.akamaized.net",
]);
const GREY_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36";

function greyTarget(req) {
  const url = new URL(req.url);
  const raw = url.searchParams.get("u");
  if (!raw) return null;
  let target;
  try { target = new URL(raw); } catch { return null; }
  if (target.protocol !== "https:" || !GREY_HOSTS.has(target.hostname)) return null;
  return { target, referer: url.searchParams.get("r") || "https://topstreamer.site/" };
}

async function greyProxy(req) {
  const hit = greyTarget(req);
  if (!hit) return new Response("bad grey target\n", { status: 400 });
  const upstream = await fetch(hit.target.toString(), {
    headers: { "User-Agent": GREY_UA, Referer: hit.referer, Accept: "*/*" },
    redirect: "follow",
  });
  if (!upstream.ok) return new Response(`upstream ${upstream.status}\n`, { status: 502 });
  const type = upstream.headers.get("content-type") || "";
  const isPlaylist = /mpegurl/i.test(type) || /\.m3u8?($|\?)/i.test(hit.target.pathname + hit.target.search);
  if (!isPlaylist) {
    return new Response(upstream.body, {
      headers: { "content-type": type || "application/octet-stream", "cache-control": "no-store", "access-control-allow-origin": "*" },
    });
  }
  const origin = new URL(req.url).origin;
  const wrap = (raw) => `${origin}/g?r=${encodeURIComponent(hit.referer)}&u=${encodeURIComponent(new URL(raw, hit.target).toString())}`;
  const rewritten = (await upstream.text()).split("\n").map((line) => {
    const trimmed = line.trim();
    if (!trimmed) return line;
    if (trimmed.startsWith("#")) return line.replace(/URI="([^"]+)"/g, (_m, uri) => `URI="${wrap(uri)}"`);
    return wrap(trimmed);
  }).join("\n");
  return new Response(rewritten, {
    headers: { "content-type": "application/vnd.apple.mpegurl", "cache-control": "no-store", "access-control-allow-origin": "*" },
  });
}

async function session(channelId) {
  const q = new URLSearchParams({
    appName: "web", appVersion: "9.4.0", deviceVersion: "122.0.0", deviceModel: "web",
    deviceMake: "chrome", deviceType: "web", clientID: crypto.randomUUID(),
    clientModelNumber: "1.0.0", serverSideAds: "false", channelID: channelId,
  });
  const r = await fetch(`${BOOT}?${q}`, { headers: { "User-Agent": "Mozilla/5.0" } });
  if (!r.ok) throw new Error(`boot ${r.status}`);
  const b = await r.json();
  if (!b.sessionToken || !b.servers?.stitcher) throw new Error("boot: no token");
  return b;
}

export default {
  async fetch(req) {
    const url = new URL(req.url);
    if (url.pathname === "/" || url.pathname === "/health") {
      return new Response("owl pluto relay ok\n", { headers: { "content-type": "text/plain" } });
    }
    if (url.pathname === "/g") return greyProxy(req);
    const m = url.pathname.match(/^\/plu\/([0-9a-f]{24})(?:\.m3u8)?$/);
    if (!m) return new Response("not found\n", { status: 404 });
    try {
      const b = await session(m[1]);
      const target = `${b.servers.stitcher}/v2/stitch/hls/channel/${m[1]}/master.m3u8?${b.stitcherParams}&jwt=${b.sessionToken}&masterJWTPassthrough=true`;
      return new Response(null, { status: 302, headers: { location: target, "cache-control": "no-store" } });
    } catch (e) {
      return new Response(`relay error: ${e.message}\n`, { status: 502, headers: { "cache-control": "no-store" } });
    }
  },
};
