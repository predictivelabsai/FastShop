# Custom domains

Merchants connect their own domains in **Settings → Domains**. The page:

1. takes the domain (for an apex domain such as `northstar.example`, it adds `www` too);
2. shows the exact records to add, and names the DNS provider from its nameservers;
3. checks DNS over DNS-over-HTTPS on **Check now** and when a domain is added;
4. marks the domain *Connected* once the TXT token is visible and the domain points at
   the platform;
5. lets the merchant make any connected domain primary. Other connected domains,
   including the previous primary such as the store's platform subdomain, answer with a 301 to the
   primary domain, keeping the path and query. The store's own webhook paths are not
   redirected, because Stripe and ShipHero do not follow redirects.

`Site.hostname` stays the primary domain, so routing, canonical URLs and checkout return
URLs work as before. Rows live in `site_domains` (migration `20261125_0035_site_domains`).

## Server settings

| Variable | Purpose |
| --- | --- |
| `FASTSHOP_INGRESS_IPV4` | The public IPv4 address merchants point apex domains at (A record). Without it or a CNAME target, the page says support connects domains. |
| `FASTSHOP_DOMAIN_CNAME_TARGET` | Hostname that subdomains such as `www` CNAME to, for example `stores.fastsme.com`. It must resolve to the ingress address. Without it, subdomains also get an A record. |
| `FASTSHOP_DNS_OVER_HTTPS_URL` | JSON DNS-over-HTTPS resolver. Default `https://cloudflare-dns.com/dns-query`. |
| `FASTSHOP_AUTOMATIC_TLS` | Set to `1` only once the edge proxy issues certificates on demand (below). It changes what the page promises about HTTPS. |

## Automatic HTTPS

Traefik, Coolify's default proxy, cannot issue certificates for hostnames it was not told
about in advance. Use Caddy's on-demand TLS in front of the app, and let it ask FastShop
before each certificate:

```caddyfile
{
	on_demand_tls {
		ask http://fastshop:5025/_domains/tls-allowed
	}
}

https:// {
	tls {
		on_demand
	}
	reverse_proxy fastshop:5025
}
```

Caddy calls `/_domains/tls-allowed?domain=<host>`. FastShop answers 200 for primary
domains and any domain a merchant has added, and 404 for everything else, so unknown
hostnames pointed at the server cannot use up certificate rate limits.

Two ways to place Caddy on the Coolify server; check which fits your Coolify version:

- switch the server's proxy to Caddy in Coolify and add the global `on_demand_tls` block
  to its configuration, or
- run Caddy as the edge on ports 80 and 443, sending merchant domains to the app with the
  block above and platform hostnames (`*.fastsme.com`) on to Traefik.

After Caddy is live and serving a test domain over HTTPS, set
`FASTSHOP_AUTOMATIC_TLS=1`. Until then, operators add each connected domain to the
application's domains in Coolify by hand.

## Not yet automatic

- Re-registering the Stripe webhook when the primary domain changes. Webhooks registered
  on an older domain keep arriving, because those paths are never redirected.
- Registering the domain with Stripe for Apple Pay and Google Pay.
- Background re-checks and an email when a domain connects: these need the scheduler,
  which is off while it also runs subscription renewals (Phase 4).
