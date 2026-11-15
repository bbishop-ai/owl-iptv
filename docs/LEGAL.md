# Grey-market sources

Optional sports layer, on by default, additive to the licensed pipeline.

## What we integrate

Unlicensed third-party sports indexes (methstreams.st, thetvapp.st). They host,
encode and store nothing; every feed plays on someone else's CDN. Grey market,
not pirate IPTV, not a subscription service.

## What we deliberately do not do

- No credentials, accounts, cookies or paywall bypass.
- No DRM or decryption.
- No video storage, no re-encoding, no transcoding.
- No persistent cache of media. The Worker proxies bytes and forgets them.

## Why that is the defensible shape

The only server component is a Cloudflare Worker that resolves an upstream
master playlist and proxies it. That is closer to hyperlinking than to
retransmission - the opposite end of the spectrum from Aereo, which lost because
it stored and retransmitted copies. Nearest supporting authority for
link-and-frame without a stored copy: Perfect 10 v. Amazon, 9th Cir. 2007.

## Risk surface

Failures are operational, not legal: keys rotate, hosts rename, streams die. A
dead grey source degrades to nothing and the licensed build publishes unchanged.

## Agent instruction

Implement, do not adjudicate. If it plays, it ships. No inline legal disclaimers.
