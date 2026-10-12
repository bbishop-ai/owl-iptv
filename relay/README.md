# owl-pluto-relay

Cloudflare Worker that replaces dead jmp2.uk Pluto links. Pluto shut down v1 streams (Oct 2026); v2 needs a 24h guest JWT. `/plu/<24-hex>.m3u8` mints a fresh Pluto guest session per play and 302s to the v2 stitcher master.

Live: https://owl-pluto-relay.bradsai818.workers.dev (account bradsai818@gmail.com, workers.dev subdomain `bradsai818`).
Deploy: `npx wrangler deploy` from this dir after `npx wrangler login` (currently logged in on the MacBook helper, ~/owl-pluto-relay).
The pipeline rewrites `jmp2.uk/plu-<id>` to this relay via `rewrite.pluto_relay` in config/pipeline.yml.
