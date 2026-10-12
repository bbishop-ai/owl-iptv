// Pluto relay for owl-iptv: /plu/<24-hex>.m3u8 -> fresh Pluto v2 session -> 302 to stitcher master.
// Replaces jmp2.uk after Pluto's v1 shutdown (Oct 2026). Each play mints its own guest session.
const BOOT = "https://boot.pluto.tv/v4/start";

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
