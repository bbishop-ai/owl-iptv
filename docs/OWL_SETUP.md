# Owl IP Player setup on Onn Google TV

1. Install **Owl IP Player** from Google Play on the Onn box.
2. Add this remote M3U URL: `https://bbishop-ai.github.io/owl-iptv-aggregator/playlist.m3u`.
3. Add this XMLTV EPG URL in Owl's EPG/source setting: `https://bbishop-ai.github.io/owl-iptv-aggregator/epg.xml`.
4. Let Owl finish importing, then open a channel with guide data to verify the link.

The generated M3U also embeds both common EPG header spellings. As of the audit date, Owl's public Google Play instructions say to add an XMLTV source but do not document automatic `url-tvg` or `x-tvg-url` handling. Treat the separate EPG URL as required unless a future Owl release explicitly documents embedded discovery.

If the app caches an old list, reload or remove/re-add the playlist. The stable URLs never change; successful GitHub Pages deployments replace the complete artifact set together.

