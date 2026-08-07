# Proxy policy — church scrapes

**Default: NO PROXY.** Every host this project touches is measured below. A proxy is
opt-in per source, only where evidence shows it is required.

## How it is ENFORCED (not just documented)

Per-host labels live in **`scrape_host_policy`** and are applied per request by
`src/utils/host_policy.py`. **Setting `PROXY_URL` alone no longer tunnels anything** — a host must
be labelled `needs_proxy` to be proxied. The scraper session is deliberately *not* proxied
session-wide, because that applies to every host a thread touches.

| policy | meaning | what the scraper does |
|---|---|---|
| `direct` | fetches fine from our own egress | **no proxy** (the default) |
| `needs_proxy` | fails direct, and the proxy **demonstrably fixes it** | use the proxy |
| `blocked` | fails direct **and** through the proxy | **no proxy** — it would burn metered GB for the same 403 |

Unknown hosts fail open to `direct`. Three states, not two: `blocked` is the one that saves money,
and collapsing it into `needs_proxy` is exactly the waste this table prevents.

**Re-label with `label_host_proxy_policy.py`, and run it as a Render one-off job** — it probes
direct, probes the proxy *only when direct failed*, and records both statuses with the egress IP.

### Full survey — all 15,024 hosts, measured from Render 2026-08-07
| policy | hosts | share |
|---|---|---|
| `direct` | 9,195 | 61.2% |
| `blocked` | 5,597 | 37.3% |
| **`needs_proxy`** | **232** | **1.5%** |

**Only 1.5% of hosts justify a proxy.** That is the number that makes per-host enforcement worth
having: proxying everything would spend metered GB on 98.5% of traffic that does not need it, and
proxying nothing would lose 232 hosts.

**What the proxy actually fixed** (`needs_proxy`, by direct failure mode):

| direct | → proxy | hosts |
|---|---|---|
| 403 | 200 | 114 |
| **429** | 200 | **51** |
| SSLError | 200 | 32 |
| 307 / ReadTimeout / 404 | 200 | 27 |

Those **51 hosts returning 429 (Too Many Requests) direct, and 200 through a fresh IP, are
evidence we are being rate-limited from Render** — not merely refused for being a datacenter. It
supports the "we may have earned this with our own volume" theory. Keep per-host rates modest.

### `blocked` is three different things — read it carefully
Of the 5,597:
- **~4,354 genuinely blocked** — 403 direct *and* 403 through the proxy.
- **~850 simply dead or broken** — `ConnectionError`, `404`, DNS/TLS failures. No proxy fixes a site
  that is not there; these are a data-quality problem in `church.website_url`, not an egress one.
- **711 inconclusive** — the verdict is `blocked` because the **proxy leg itself errored**
  (`ProxyError`), which proves the proxy failed, not that the host refused it. Worth re-probing when
  the proxy is healthy; `classify()` currently collapses these into `blocked`.

All three correctly result in **no proxy**, so enforcement is right either way — but do not read
"5,597 blocked" as "5,597 hosts are refusing us".

> ⚠️ **The verdict is probabilistic, because the proxy rotates.** `27823.sites.ecatholic.com` scored
> `needs_proxy` while `17009.sites.ecatholic.com` scored `blocked` — same vendor platform, so the
> difference is most likely which residential exit IP was drawn on that attempt, not a property of
> the host. Treat a single `blocked` verdict on a host whose siblings pass as "worth re-probing",
> not as settled. Do not build automatic proxy-retry on `blocked` without watching the GB bill.

> ## ⚠️ 2026-08-07 CORRECTION — measure from the machine that RUNS the scraper
>
> The table below was originally measured from a laptop on a residential connection. That was the
> wrong vantage point, and it gave a wrong answer for part of the estate.
>
> **~1,441 parish sites return 403 from Render but 200 from a residential IP.** These are the
> deep-archive parishes (3,000–3,900 stored PDFs each) that the 2026-08 recovery sweep hit hardest.
>
> | Same 7 sites, fetched from… | Result |
> |---|---|
> | Residential laptop | **7/7 → 200** |
> | **Render datacenter egress** (`74.220.49.50`) | **7/7 → 403** |
> | **711 residential proxy** (`-country-US`) | **7/7 → 403** |
>
> **The 711 proxy does NOT fix it** (`proxy_ok: 0` of 18). So this is still a "no proxy" project —
> not because nothing is blocked, but because the proxy we have does not help. Its exits are
> presumably on the same reputation lists as the datacenter ranges; only a clean residential IP
> passes.
>
> Whether our own volume triggered these blocks is **not established**: the same hosts hold
> thousands of PDFs we previously scraped successfully, so they were reachable before. Treat
> "we may have earned this block" as a live possibility and keep per-host rates modest.
>
> Impact is limited: those parishes are already deeply archived, and the other ~20,000 churches
> scrape fine from Render. Reproduce with
> `python -u diagnose_discovery_egress.py --limit 20 --compare-proxy` (writes to `scrape_log`,
> because Render does not serve cron runtime logs).

Using a proxy where it is not needed is not neutral — it costs money, adds latency, and has
actively *broken* scrapes here (see "Proxy made things worse").

_Measured 2026-08-06, direct from a residential IP, plain `requests` + a Chrome UA._

## Verdict table

| Source / host | Role | Direct result | Proxy needed? |
|---|---|---|---|
| **Parish websites** (~22.5k domains) | bulletin page discovery | **38/40 → HTTP 200**, 1×500, 1×conn error, **0 blocks** | **NO** |
| `discovermass.com` | mass-times source (sitemap + parish pages) | 200 | **NO** |
| `bulletins.discovermass.com` | bulletin PDF host | 200, valid `%PDF` | **NO** |
| `parishesonline.com` (LPi) | bulletin widget/archive | 200 | **NO** |
| `irp.cdn-website.com` | bulletin PDF host | 200, valid `%PDF` | **NO** |
| `files.ecatholic.com` | bulletin PDF host | **200, valid `%PDF`** on real file URLs | **NO** |
| `4.files.edl.io` | bulletin PDF host | 200, valid `%PDF` | **NO** |
| `catholicindex.org` — homepage | (retired source) | 200 | n/a |
| `catholicindex.org` — content | (retired source) | **403 `cf-mitigated: challenge`** | **A proxy does NOT fix this** |

### Read the root path carefully
`files.ecatholic.com/` returns **403** at the bare root — that is directory-listing denial, not
blocking. Real file URLs under it return 200 with a valid PDF. **Always test an actual URL the
scraper would request** before labelling a host as blocked; a root-path probe would have put a
proxy on a host that never needed one.

## Proxy made things worse here
- **DiscoverMass through the 711 proxy: silent 0-write.** A Render job exited 0 having written
  nothing because every proxied fetch failed; a local re-run collapsed 86 parishes → 2. `PROXY_URL`
  was **removed** from `church-mass-times-cron`. Direct fetching is the reliable path.
- **CatholicIndex is a Cloudflare *managed challenge*, not an IP block.** Rotating residential IPs
  does not solve a JS challenge — requests, curl_cffi, Playwright-headless,
  undetected-chromedriver-headed and the 711 proxy all fail. The source was retired in favour of
  DiscoverMass instead of buying a scraping browser.
- Per [[feedback_no_curlcffi_through_proxy]]: TLS impersonation *through* an HTTP proxy is a
  100% Cloudflare block. If a proxy is ever needed here, use plain `requests` through it.

## How it is wired
`PROXY_URL` is read in `extract_bulletins_to_db99.py`, `run_bulletin_scraper.py` (session +
Playwright), `run_resolve_urls.py`. It is **unset everywhere in production**, so all paths go
direct. Setting it turns the proxy on for *every* request in that process — there is no per-host
selection today. So do not set it service-wide to fix one host; that is the failure mode described
in [[feedback_proxy_global_breaks_spiders]].

## Before adding a proxy to any source
1. Fetch a **real** URL the scraper uses (not the domain root) directly, and record status +
   `cf-mitigated` + whether the body is what you expect.
2. If blocked, classify it: **IP/rate block** (a proxy can help) vs **JS/managed challenge**
   (a proxy will not).
3. Only then enable it for that source, and record the evidence in this table.
