# Church Scrapes — Project Plan

_Last updated: 2026-08-21 15:00 UTC — national 13,140 (+5,556, corpus +73%), gap 9,562. See STATE OF PLAY at the top._

## 📊 STATE OF PLAY — 2026-08-21 15:00 UTC (read this first)

**Where it started:** a CSV of 69 Wisconsin churches that appeared to have no bulletins.
They had bulletins. So did roughly five and a half thousand others.

| | baseline (08-17) | now | change |
|---|---|---|---|
| **national churches with a bulletin PDF** | 7,584 | **13,140** | **+5,556 (corpus +73%)** |
| national gap (source but no PDF) | 14,997 | **9,562** | −5,435 |
| **Wisconsin** | 271 (28%) | **620 (64.0%)** | +349, 1.03M name rows |
| the 86 named WI church ids | 0 | **55** | +55 |

Focus states: MN **527 (67.0%)** · WI **620 (64.0%)** · GA **150 (61.0%)** · PA **837 (55.3%)** ·
AZ **128 (40.3%, website-limited)**. Large states: NY **1,135 (61.5%)** · IL 718 · TX 623 · CA 759.

### The seven defects, each of which looked like "this parish has no bulletin"

1. The sweep that judged them ran 2026-08-13, two days BEFORE its own Playwright/OOM fixes landed.
2. Four early-returns in discovery: it stopped on finding a PAGE rather than PDFs.
3. Both download paths fetched LPi's HTML reader wrapper and silently discarded it.
4. ParishesOnline's public JSON API (which returns the PDF directly) was never used.
5. Wrong stored URLs for merged/dead parishes, with no fallback to a known-good page.
6. **`blocked` hosts were retried the one way that provably cannot work** (see below).
7. **The known-page fallback crashed on the line that logged its own success** — a `logger.info`
   in a file that has no logger, sitting on the SUCCESS branch, so it failed exactly when it
   worked. Two wrong diagnoses preceded finding it; only calling the real `process_church()`
   directly exposed it.

### The finding that mattered most

`blocked` hosts answer a residential IP **and** a real browser **together** — 8 of 8 tested go
403 → 200. Neither signal alone passes. Since `blocked` meant "go direct, WITHOUT a proxy", 5,457
hosts were being retried in the only way guaranteed to fail. **This also unblocks the crime
project's WI Cloudflare walls**, which had the whole stack built and were waiting on exactly this.

### What is now automated (it was not before)

**`church-walled-sweep`** — `crn-da43b7n10e5c73anojc0`, daily 05:00 UTC, own container. Orders
states by LIVE gap, round-robins them across 7 days, retries every state weekly unattended,
reports to Telegram. Verified end to end: a real run took GA 149 → 153.

### Open items

- **`ref_name_dataset` is NOT created** — needs owner approval. Until then US-only dictionaries
  keep demoting Polish/Hispanic/Vietnamese/Filipino surnames off the dashboard.
- **Non-Catholic expansion**: planned and evidence-backed (OSM wins; LCMS 401s, ELCA serves no
  data). Three owner decisions first: ODbL share-alike, whether to email LCMS for API access, and
  go-ahead on OSM-first.
- **1,681 churches nationally have an unusable `website_url`** (14% of the gap) — measured, not
  fixed. AZ is half of its own gap this way.
- **`render.yaml` here is linked to no blueprint** — editing it changes nothing; services are
  created by hand via the API.
- 31 of the 86 WI targets remain zero: some genuinely publish nothing, some are years stale, the
  rest are same-host-undiscoverable.

---

### 2026-08-23 — the walled recovery had quietly stopped

Asked "are the states all done?" the honest answer was no, and the reason was a
gap in how this was set up rather than in the scraping itself.

**What had happened.** Eight per-state walled passes (AZ, CA, NY, PA, TX, KS, NE,
VT) were OOM-killed on 2026-08-21 and were never relaunched, because the thing
relaunching them was a supervisor loop living inside an interactive session. When
the session ended, the supervisor ended, and the eight states simply stopped. Only
IL finished. National coverage moved 13,116 -> 13,170 in two days: +54.

**The scheduled sweep was fine** — `church-walled-sweep` fired on both 08-22 and
08-23 as designed. But 08-22 was OOM-killed at the 2Gi ceiling six hours in, and
because `bucket_for_day()` shards on the weekday, every state that day's run had
not yet reached would not come around again for a week.

**Fix.** Schedule changed `0 5 * * *` -> `0 5,17 * * *`. Both runs on a given day
resolve to the same weekday bucket, and the per-state queue is
least-recently-checked-first with per-church claims, so the 17:00 run resumes at
the frontier the 05:00 OOM left behind instead of starting over. No code change,
and it removes the need for a supervisor to exist at all.

The eight states were relaunched by hand at the same time to recover the lost two
days.

**Reading at the time of writing (live, over VPN):**
national 24,158 churches / 22,702 with a source / 13,170 with a PDF; gap 9,532.
WI 620 (64.0%), MN 527 (67.0%), PA 840 (55.5%), GA 151 (61.4%), AZ 128 (40.3%).
Worst gaps: CA 761, TX 748, NY 706, PA 673, IL 483.


### 2026-08-23 (later) — proving it, and a watchdog so Ben is not the monitor

Ben's answer to the fix above was "make sure this is live and working and I
don't constantly have to remind you." Fair, so this is what was verified rather
than asserted, and what now does the reminding instead of him.

**Verified live, by reading Render back rather than trusting the write:**
`church-walled-sweep` is `0 5,17 * * *`, not suspended, carrying PROXY_URL and
both Telegram vars. The eight states that had been dead since 08-21 (AZ, CA, NY,
PA, TX, KS, NE, VT) were relaunched and confirmed `running`.

**A bug this turn is worth recording,** because two verification steps passed
while it was live. The watchdog below was committed, pushed, built and run — and
failed twice — before `git ls-tree origin/master` showed the file had never
reached master at all. The working branch is `fix/bulletin-lpi-zero-pdfs` and
`git push origin HEAD` pushes THERE, while every Render service builds from
`master`. `python <file-not-in-image>.py` exits 2, Render reports a bare
`nonZeroExit: 2` with no runtime logs, and it reads exactly like an argparse
bug. Neither "the commit exists" nor "the build succeeded" checks the branch.

**`church-bulletin-watchdog`** — `crn-da5m378u01pc73fobqv0`, daily 12:30 UTC.
It does not check whether services are up; every service was green throughout
the 08-21 failure. It checks whether the NUMBER MOVED, which is the one signal
a healthy process doing nothing cannot fake:

  1. churches-with-a-PDF up by at least 1% of the remaining gap per day
  2. the sweep cron ran within 26h (read from service EVENTS — cron runs are not
     jobs, and polling /jobs concludes the cron never fired when it fires daily)
  3. that run did not die

Failure sends Telegram and exits non-zero. A healthy run says nothing.

**The bar is a fraction of the gap, not a fixed number, and that mattered.** The
first cut asserted `coverage > previous`. Replayed against the actual 08-21
failure — +54 churches over 48 hours — it passed silently, which is the only
test that counted. A stall here is a crawl, not a zero. A fixed bar would have
caught it but goes shrill as the corpus saturates, and an alert that fires every
morning gets muted. 1% of the gap per day is ~95/day now and relaxes later.

Test matrix, all against live db99: healthy (+1,170/24h) quiet; the real
08-21 stall (+54/48h) alerts against a 192 bar; a hard stop (+0/25h) alerts; a
6-minute window is not judged at all — that last guard exists because the first
deployed run false-alarmed six minutes after a manual test.

Telegram delivery confirmed 200. Final job on Render `succeeded`, state
persisted to the service env var.


### 2026-08-23 (later still) — the supervisor is a cron now, not a session

Within minutes of reporting the above fixed, the replacement supervisor loop was
killed and VT died with nothing to relaunch it. Same class of failure, third
time. The loop was never the fix; it was the bug wearing a different hat.

**`church-walled-supervisor`** — `crn-da5mhdrncjis739da9u0`, hourly at :20.

It holds **no state file**, which also retires the older race: several armed
monitors ran the old bash script concurrently, and a cycle that read the tracker
before another rewrote it wrote back a SHORTER list — silently dropping NY, CA
and TX from being watched at all. Truth now comes from two places that cannot be
raced. Render is asked which passes are running (parse `--state XX` out of
running/pending job commands); db99 is asked which states still have a gap.
Nothing is remembered between runs.

A failed jobs API returns None rather than an empty set and the cycle stands
down. Treating an API blip as "nothing is running" once relaunched all thirteen
states at once, and `bulletin_pdf` has no unique index while the per-church
claim is select-then-stamp, so two passes over one state duplicate rows.

Verified against live infrastructure before and after shipping: it saw all 8
passes in flight and stood down; given free slots it picked IL and OH, the worst
states not already running; given a bad API key it stood down rather than
fanning out. The deployed run on Render succeeded and launched nothing, exactly
as it should with 8 of 8 slots full.

**The three crons, and what each is for:**

| cron | when | job |
|---|---|---|
| `church-walled-sweep` | 05:00 + 17:00 UTC | does the work; evening resumes the morning's OOM frontier |
| `church-walled-supervisor` | hourly :20 | relaunches dead per-state passes, caps at 8 concurrent |
| `church-bulletin-watchdog` | 12:30 UTC | tells Ben if the NUMBER stops moving |

None of them depends on a session being open.


### 2026-08-23 — working the residual 9,530 gap

**A wrong conclusion, corrected in the same session.** 9,387 of the 9,530 gap
churches carry a `bulletin_checked_at` inside the last seven days, and I read
that as "we already tried these, so re-running cannot help." That is wrong.
`bulletin_checked_at` is stamped as a CLAIM *before* processing — deliberately,
because stamping only on success made a church that kills its worker immortal at
the head of the queue. A stamp therefore proves a church was claimed, not that
it was finished. `sainthelenampls.org` is the proof: in the gap, stamped
recently, and discovery finds **12 PDFs on it right now**. Re-running with the
current code does help; throughput is the constraint, not a missing fix.

**What the gap actually is.** Probing a 160-church sample of the 6,285 marked
`not_found` (`diag_gap_notfound.py`, fixed seed so it re-measures like for like):

| verdict | share | ≈ churches | meaning |
|---|---|---|---|
| DEAD | 38.1% | ~2,400 | url does not resolve or 404s |
| ALIVE, says "bulletin" | 26.9% | ~1,690 | site is fine — not yet successfully processed |
| ALIVE, no keyword | 33.1% | ~2,080 | mixed; some JS-only, some genuinely have none |
| WALLED | 1.9% | ~120 | needs the browser+proxy path |

The gap spans **5,236 distinct hosts**, so no single shared-vendor fix remains
of the kind that took national coverage 7,584 -> 13,140 (the LPi JSON API).

**Defect 8: nothing ever read `church.website_url_clean`.** It holds a resolved
url; for 130 parishes `website_url` is still a bare CatholicIndex stub
(`/api/out?id=...`, no scheme or host, unfetchable by construction) while the
real site sits one column over. Also fixed: 868 gap churches where raw and clean
are on different hosts now get a second attempt, and the facebook/diocese
filters — which excluded 1,637 churches from every sweep — now let a church
through when its clean url is the parish's own domain. Those 142 were dropped
before discovery saw them, so they never appeared in a failure report either.

Measured, not assumed: **2 of 40** test churches recovered (baseline 0), and the
URL repair upgraded **147** urls. Both real, both small. Worth having, not the
answer to 9,530.

**The actual lever is throughput.** States are disjoint by construction, so
running more of them at once cannot duplicate rows (`bulletin_pdf` has no unique
index, which is why this matters). `WALLED_MAX_RUNNING` raised 8 -> 16;
sixteen state passes now in flight across the worst-gap states — CA, TX, NY, PA,
IL, OH, WI, MI, NJ, MO, MA, MN, FL, KS, CT, MD.


### 2026-08-24 — Wisconsin goes non-Catholic: ELCA is in

The corpus was Catholic-only, and not by convention — `church` had no
`denomination` column at all. Wisconsin is the most Lutheran state in the
country, so it is where the first non-Catholic adapter pays best.

**The source.** `elca.org/directory/congregations` is Webflow with no data in
its JS chunks, which is why an earlier look concluded the directory served
nothing. It is rendered by **Wized**, whose config names a public **Xano**
backend returning an entire state in one unauthenticated GET:

    GET {xano}/api:tzLVgPph/api/get_congregations?LocationState=Wisconsin

`LocationState` takes the **full state name** — passing "WI" returns `[]`, which
is almost certainly what the earlier probe hit and read as "no data".

**Verified independently before building on it** (not taken from the agent's
report): 624 WI records, **513 with the congregation's own `LocationURL`**, 624
with lat/lon, 553 with email. `website_url` drives the entire bulletin pipeline,
so a roster without websites would have been worthless; this one carries them,
plus coordinates that feed the existing ≤150m dedup and a synod that gives
denomination for free. It carries **no worship times** — `services` stays empty
rather than inventing a Sunday 10am.

**Loaded:** WI now holds **1,009 Catholic + 624 ELCA**, 513 of the ELCA rows
with a website. Catholic count identical before and after — the additive
guarantee held.

**The guard that matters.** `decide_match` pairs churches within 150m with
similar names, and downtown blocks put a Lutheran congregation that close to a
Catholic parish routinely. `bulletin_pdf` hangs off `church_id`, so one bad
merge would silently attribute a congregation's bulletins — and everyone named
in them — to the wrong church. All 24,879 existing rows were backfilled
`denomination='catholic'` (every one came from a Catholic-only source), which is
what makes the cross-denomination refusal reliable rather than a guess.

**Protestant vocabulary.** Lutherans and Methodists mostly do not call it a
bulletin: it is a worship folder, an order of worship, or announcements. Added
to `BULLETIN_PATHS`, `BULLETIN_PAGE_KEYWORDS` and `BULLETIN_LINK_PATTERNS`,
**appended not inserted** — `BULLETIN_PATHS[2]` and `[3:]` are referenced by
index further down the file, and there is now an assertion that `[2]` is still
`/bulletin/`. Discovery tested on three real ELCA sites: **12, 1 and 1 PDFs**.

**Sources ruled out, and why.** LCMS returns 401 and needs a key from them —
their anti-abuse token was deliberately not reverse-engineered. `engage.suran.com`
(the UCC finder's backend) names ClaudeBot with `Disallow: /`, and WELS is
`Disallow: /`; neither was fetched. PCUSA's finder sits under a disallowed path.
Between them they yielded 0 WI congregations, so the cost of leaving them is nil
against ELCA's 624. Remaining permitted headroom: **OSM/Overpass** (2,341 WI
non-Catholic Christian, 526 with websites, ODbL attribution required) and
**UMC** (`church_url` *and* worship times, needs a lat/lon grid).

**Also this session:** the residential proxy was found returning **407** while
sixteen walled passes ran against it — recovering nothing from any walled host
and exiting 0, the exact "green while doing nothing" failure. Topped up and
confirmed live (both legs 200). The watchdog now probes the proxy directly every
run, so it can never be silent again.


### 2026-08-24 — Wisconsin denomination grid, and what each is worth in NAMES

The goal is names of people connected to Christian parishes — first name, last
name, and the city of the church. Every decision below is ranked by that, not by
how many congregations a source lists.

**The yield multiplier, measured not guessed.** Wisconsin currently has 795
churches with at least one bulletin PDF, 91,325 PDFs, and 1,101,168 extracted
name rows. Deduplicated that is:

| | WI today |
|---|---|
| distinct person names | **73,735** |
| distinct with BOTH first and last name | **73,747** |
| distinct high-confidence, not flagged suspect | **36,201** |

So **~93 distinct names per church that has bulletins**, ~46 of them
high-confidence. That is the number to multiply any new congregation count by —
and note the funnel before it: of ELCA's 624 WI congregations, 513 carry a
website (82%), ~82% of those are reachable, and ~64% of reachable ones publish a
bulletin. **Roughly 43% of listed congregations end up producing names.**

#### The grid

| Source / denomination | WI spaces | with website | permitted? | est. churches w/ bulletins | est. distinct names |
|---|---|---|---|---|---|
| **Catholic** (already loaded) | 1,009 | — | yes, done | ~651 today | ~60,500 (in hand) |
| **ELCA** (loaded this session) | **624** | 513 | **yes** — public Xano API | ~270 | **~25,000** |
| **OSM net-new, has website** | 441 | 441 | yes (ODbL attribution) | ~231 | **~21,500** |
| **OSM net-new, no website** | 1,006 | 0 | yes, but needs a URL-resolution pass first | ~322 if 50% resolve | ~30,000 |
| UMC | ~132 in OSM | — | robots clean, **endpoint unverified (404)** | folded into OSM | — |
| LCMS | ~414 | — | **no** — API returns 401, needs a key from LCMS | 0 | 0 |
| WELS | ~452 | — | **no** — `Disallow: /` | 0 | 0 |
| UCC (suran backend) | — | — | **no** — names ClaudeBot, `Disallow: /` | 0 | 0 |

**OSM net-new is genuinely new.** 2,234 named Christian places of worship in WI;
787 sit within 150m of a church already in db99 (the dedup ran *after* the ELCA
load, so ELCA is accounted for); **1,447 are net new**, 441 of them carrying a
`website` tag. Breakdown of those 441: 100 unspecified, 77 lutheran, 40 baptist,
40 methodist/UMC, 14 nondenominational, 13 presbyterian, 13 catholic, 12
evangelical-lutheran, 12 evangelical, 12 orthodox, 11 LDS, 9 UCC, 8 AoG.

#### What this adds up to for Wisconsin

WI holds **1,633 churches today** (1,009 Catholic + 624 ELCA) against a
reachable ceiling of about **3,080**. On names: **73,735 distinct today**, and
roughly **150,000** if ELCA finishes, OSM's 441 with-websites are loaded, and the
1,006 without websites get a resolution pass. **Approximately double.**

#### Priority order, highest names-per-hour first

1. **Finish ELCA WI** — running now; 144 churches already have PDFs from a
   standing start. Zero new code. ~25,000 names.
2. **OSM net-new with websites (441)** — one Overpass POST, reuses the ELCA
   loader shape. ~21,500 names. ODbL attribution is the only obligation.
3. **Roll ELCA to all 50 states** — the same single GET per state; ~8,356
   congregations nationally, the single largest lever in the project.
4. **URL-resolution pass for OSM's 1,006 without websites** — needs building;
   `run_resolve_urls.py` is hard-wired to CatholicIndex and cannot be reused.
5. UMC — find the real endpoint first; largely overlaps OSM methodist rows.

Deliberately not pursued: LCMS (needs a key they must grant), WELS and the UCC
backend (explicit `Disallow: /`). Together ~866 WI congregations, which is real —
but they are unreachable, and items 1–4 above are worth more names than the
entire blocked set.


## 🔴 2026-08-17 — "THESE CHURCHES HAVE NO BULLETINS" WAS WRONG

69 Wisconsin churches carried zero extracted names and read as parishes that simply do not
publish. They do. Four probe agents checked all 69 by hand against the live web:

| | |
|---|---|
| Publish a bulletin | **66 of 69** |
| Have a verifiable bulletin page | **68 of 69** |
| Genuinely none | **1** — St. Bernard, Abbotsford (site states email/paper only) |
| Real but stale | 2 — St. John Nepomucene (2024), St. James Neshkoro (Mar 2025) |

Every PDF was confirmed by **HTTP status *and* `%PDF` magic bytes**, because the ParishesOnline
CDN serves genuine PDFs as `application/octet-stream` — trusting `Content-Type` would have
rejected the real files.

### Why they looked empty

**1. The sweep that judged them was itself broken.** All 67 were last checked **2026-08-13** —
two days *before* the Playwright thread-safety and worker-OOM fixes (`8473ceb`, `c2eebd7`) landed
on 08-15. Their zeros were an artifact of that run, then read as fact.

**2. Discovery returned on finding a PAGE, not PDFs.** Four separate early-returns, one root
cause. Each abandoned the church with 0 PDFs and skipped every remaining strategy:

- Strategy 1 returned on any truthy LPi id even when the widget produced nothing
- Strategy 2 returned on the first link merely *matching* "bulletin"/"newsletter" — frequently a
  submission form or an advertiser page
- Strategy 3 returned from an LPi homepage embed with 0 PDFs
- the `lpi_link` branch returned whatever `extract_lpi_pdfs` gave, including nothing

**3. `find_lpi_parish_id` pattern 3 returns a bare org SLUG** (`st-therese-of-lisieux-church`)
that the widget API cannot resolve, so every slug parish silently looked empty.

**4. The download path discarded the file it was handed.** LPi returns reader links shaped
`parishesonline.com/publication-page/<slug>?selectedPublication=<real pdf>`. Both download paths
fetched the *wrapper*, got `text/html`, and dropped it — so a church could discover a dozen
bulletins and still record **0 downloaded**.

### The fix that matters most

ParishesOnline/LPi is ~75% of these parishes and renders bulletins client-side, so the PDF is
never an `<a href>` in the served HTML. It also exposes a public, unauthenticated JSON API that
returns the direct file:

```
GET /organizations/slug/<slug>                        -> data.salesforce_id
GET /organizations/<sf_id>/publications?type=bulletin -> data[].fileUrl
```

`extract_lpi_pdfs_from_api()` now runs ahead of the widget and org-page scrapes and accepts either
handle form. Verified against four parishes covering both forms — 12 bulletins each, newest dated
2026-08-16.

### Better parsing cannot fix a wrong input

Discovery re-derives from `church.website_url` every run, so a parish whose stored site is expired
or superseded by a merger can never recover however good the parsing gets — and small WI parishes
merge constantly (`stlouisparishwi.com` is dead; St. Columbkille now publishes under St. Katharine
Drexel; St. Joan of Arc under `scsjcluster.org`). Two additions:

- `process_church()` falls back to a **known** bulletin page on another host when discovery from
  `website_url` comes up empty
- `seeds/wi_verified_bulletin_pages.json` — 87 hand-verified pages, seeded into `bulletin_source`

### Scale: this is ~10× bigger than the reported CSV

Wisconsin has **1,009 churches, 974 with a bulletin source, but only 271 with any PDF**. About
**700 WI churches** sit in the same failure state as the 69 that were reported. The merged fixes
apply to all of them; a full re-sweep is what converts it.

### 🔓 'blocked' was never a dead end — walled hosts answer a browser THROUGH the proxy

The single most useful finding of the day, and it inverts a standing assumption.

Maine sits at 6.4% coverage with 185 of its 205 zero-PDF churches on hosts labelled `blocked`.
Those verdicts were *correct* — every host records direct 403 **and** proxy 403, real responses on
both legs, not the 407/ProxyError signature of our own account lapsing. So a proxy alone was
never going to move them, and re-labelling would not have helped.

But the full access matrix, measured across **eight hosts in two states**:

| | requests | requests + proxy | browser | **browser + proxy** |
|---|---|---|---|---|
| hc-catholics.org | 403 | 403 | 403 | **200** (94 KB) |
| portlanddiocese.net | 403 | 403 | 403 | **200** (13 KB) |
| sjvcatholics.org | 403 | 403 | 403 | **200** (180 KB) |
| theppb.org | 403 | 403 | 403 | **200** (89 KB) |
| allsaintsmaine.com | 403 | 403 | 403 | **200** (100 KB) |
| stmaxkolbe.org | 403 | 403 | 403 | **200** (28 KB) |
| stmarysbigriver.com | 403 | 403 | 403 | **200** (71 KB) |
| holycrosswi.org | 403 | 403 | 403 | **200** (101 KB) |

**8 of 8.** The WAF scores both signals and wants both: neither a residential address nor a real
browser passes alone. Which means `blocked` — defined as "go direct, WITHOUT a proxy" — is
precisely the one combination that cannot work. **5,457 hosts carry that verdict** and gate a
large share of the 14,997-church gap. They were never permanently unreachable; they were being
retried in the only way guaranteed to fail.

This refines the standing note that WI's Cloudflare walls need "a residential proxy **or** a
CF-solver". The answer is a residential proxy **and** a real browser, together.

**Strategy 5** in `find_bulletin_page` now gives policy-blocked hosts one browser attempt through
the proxy. Gated to blocked hosts because a browser is built per call. `proxies_for()` is
deliberately unchanged — plain requests through the proxy still 403, so widening it there would
spend metered GB for the same failure.

**Verified end to end, and honestly: it is an unlock, not a cure.** Of five walled hosts run
through real discovery — two returned **100 genuine bulletins each including the current
2026-08-16 edition**, two were reachable but yielded nothing, and one returned four PDFs that
were not bulletins at all (a cemetery fee schedule, a stewardship worksheet) because the homepage
was tried first and a browser harvests every PDF on the page it lands on. Those would have been
mined for names and produced confident junk — worse than the zero they replaced. Fixed by trying
bulletin paths before the homepage and filtering the fallback.

A 200 proves a page is reachable. It does not prove bulletins can be found on it. Conflating
those two is the error that started this whole day.

### WISCONSIN COMPLETE (2026-08-19 00:46 UTC)

| | reported | final | change |
|---|---|---|---|
| churches with >=1 PDF | 271 (28%) | **603 (61.8%)** | **+332** |
| bulletin_name rows | 752,782 | **1,013,531** | **+260,749** |
| bulletin_pdf rows | 78,645 | **87,571** | +8,926 |
| the 86 named targets | 0 | **55** | +55 |

Wisconsin more than doubled its bulletin coverage and gained a quarter of a million names, from a
starting report of 69 churches that appeared to have no bulletins. They did — and so did roughly
330 others in the same state.

### After the logger fix (2026-08-18 23:55 UTC)

| | |
|---|---|
| the 86 named targets | **55** recovered, 31 still zero |
| WI churches with >=1 PDF | **603** (61.9%) |
| WI bulletin_name rows | **1,013,288** |

The fix moved 47 -> 55. Verified examples: 28511 Nativity/Big River now holds **100 bulletin_pdf
rows**, and 28491 Our Lady of the Lakes took **4 PDFs / 127 names** on the next pass — its earlier
"download failed" was only the newest listed edition (dated 2026-08-23), which LPi advertises
before uploading. The older editions download fine.

**What the remaining 31 actually are** — three different things, not one backlog:

1. **Genuinely no bulletin.** St. Bernard, Abbotsford states on its own site that the bulletin is
   email/paper only. Correctly zero, and no amount of engineering changes that.
2. **Real but stale.** St. John Nepomucene (last edition 2024), St. James Neshkoro (March 2025).
3. **Same-host, undiscoverable.** e.g. 28536 St. John Vianney, whose known page is on the SAME
   host as its website_url, so the fallback is correctly skipped — but ordinary discovery finds
   nothing there either. This is a distinct failure from the dead-domain case and needs its own
   investigation rather than being counted as generic backlog.

### Wisconsin state of play (2026-08-18 22:20 UTC)

| | session start | now |
|---|---|---|
| churches with >=1 PDF | 271 (28%) | **591 (60.6%)** |
| bulletin_name rows | 752,782 | **1,007,227** |
| the 86 named targets | 0 | **47** |

Wisconsin has crossed **a million name rows** and 60% coverage, from 28%. The named targets sit at
47 pending the budget-fixed rerun.

### 14% of the remaining gap is BAD DATA, not hard targets (2026-08-20 03:10 UTC)

Arizona sat flat at 39.9% while every other focus state moved. It was not a scraping problem:
**101 of AZ's 197 zero-PDF churches share one fake "host"** —
`out?id=us-az-ak-chin-st-francis-of-assisi-mi` — a query-string fragment sitting in `website_url`
where a domain should be. Half of Arizona's remaining gap is unscrapeable by construction.

Measured nationally with `diag_bad_urls.py`: of **12,076** zero-PDF churches, **1,681 (14%)** have
an unusable `website_url`:

| shape | count | example |
|---|---|---|
| `/api/out?id=...` unresolved | **522** | CA 366, TX 129, NY 106, AZ 100, PA 87 |
| empty | 632 | `''` |
| placeholder | 499 | `#` |
| malformed host | 28 | `http:// http://www.sjccc.net/`, `https://stmary@stmarykeywest.com` |

**CORRECTION on the 522.** They are not one population. The Alabama sample I first probed points
at **masstimes.org**; the Arizona rows point at **catholicindex.org** and all 84 of them DO carry
the `&sig=` that `run_resolve_urls.py` was written for. Generalising from the first sample was
wrong. What is true of both: the upstream is dead — CatholicIndex is Cloudflare-blocked, and the
masstimes ids now resolve to /404 — so **neither can be re-resolved from its redirect**.

**Three hypotheses tested and discarded for the masstimes shape:**

1. Follow it on discovermass.com -> 301s to an unrelated "out-of-love" interstitial.
2. Treat the embedded id as a DiscoverMass parish slug -> 404 on every slug shape.
3. The real origin is **masstimes.org**, which resolves the destination CLIENT-side in an
   AngularJS app (a plain fetch only ever sees the shell). Resolved it with headless Chromium —
   the same tool that beat the walled hosts — and it lands on **masstimes.org/404**.

So those ids no longer exist upstream. **No redirect-following approach can recover either
shape.** And `run_resolve_urls.py` is not the answer despite the matching URL shape: it fetches
the CatholicIndex interstitial, which Cloudflare now blocks, and it writes local JSONL rather than
db99.

**What AZ actually has to pivot on:** all 100 of its broken-URL churches carry a usable
`source_url`, and 0 have a stored bulletin page. But the DiscoverMass route does not rescue them
either — parsed `st-francis-of-assisi-bagdad-az` and DiscoverMass holds the parish with an
**empty website field**. So the website is genuinely absent upstream, not merely unresolved, and
recovering these means finding the parish site fresh (search / diocese directory), not repairing
a link.

Recording the negative result deliberately: it would have been easy to build a redirect-repair
pass on hypothesis 1 or 2 and ship something that silently produced nothing.

### THE RECOVERY WAS NEVER SCHEDULED — now it is (2026-08-21)

The owner asked whether this would run weekly or need watching. It needed watching, and worse:

```
grep walled render.yaml supervise_bulletin_sweep.py run_daily_pipeline.py  ->  NOTHING
```

**The walled-host pass recovered +5,532 churches nationally and was scheduled nowhere.** Every
run over 08-18..21 was launched and babysat by hand. The moment nobody was watching, any church
needing browser+proxy would have gone quietly back to yielding zero PDFs — indistinguishable from
"this parish publishes no bulletin", which is the exact failure this whole effort existed to fix.
It would have reappeared by omission.

**Two traps found while fixing it:**

1. **`render.yaml` in this repo is connected to nothing.** No blueprint is linked, so editing it
   creates and changes NOTHING — the live services were all made by hand. Anyone reading that file
   would reasonably assume otherwise. The cron had to be created explicitly via
   `POST /v1/services` (note: `runtime: docker`, not `image`, or it 400s asking for an image).
2. **`PROXY_URL` had to be copied to the new service.** Without it the cron would run green and
   recover nothing, because browser+proxy *together* is the entire mechanism.

**`church-walled-sweep`** — `crn-da43b7n10e5c73anojc0`, daily **05:00 UTC**, own container:

- 05:00 is deliberate: two hours clear of the 03:00 bulletin cron, so they never share a box. A
  Chromium per blocked host alongside spaCy and pdfplumber does not fit 2GB — running it inline
  OOM-killed three shards.
- `run_walled_sweep.py` orders states by **live gap** read from the database, then round-robins
  them into 7 buckets (`states[i::7]`). Round-robin rather than contiguous slices so no single day
  draws all of NY/CA/TX/PA; ordered by gap so the rotation follows the work and re-orders itself as
  coverage improves. **Every state is retried weekly, unattended.**
- One worker, bounded per-run browser budget, bounded per-state timeout. Exits 0 on a per-state
  failure — the per-church watermark makes those resumable, and a red cron for one OOM'd state
  trains everyone to ignore the alarm.
- Reports to Telegram every run, because a Render one-off's stdout is not retrievable and a cron
  that quietly stopped working would look exactly like the problem it prevents.

**Verified end to end on the cron itself, not just locally:** a real run against Georgia took it
**149 -> 153 churches**. Build, secrets, db99, live-gap query, bucketing, browser+proxy, and the
database write are all proven on the scheduled service.

### ALL 50 STATES RELAUNCHED ON FIXED CODE (2026-08-20 23:20 UTC)

Acting on the consequence below: every state was relaunched with `--days-fresh 0` on the
post-logger-fix image, so no state is left carrying the crashing fallback. **30 of 50 have now
finished the re-run; national at 13,051 (+5,467), gap 14,997 -> 9,651.**

The re-run is earning its keep, and the gains are small per state but real — exactly the shape
predicted. Georgia had already "completed" once with the broken fallback and picked up 2 more on
the fixed pass (147 -> 149); MN 522 -> 524; PA 822 -> 824. Invisible before, because the states
reported themselves finished.

### NON-CATHOLIC EXPANSION: the source ranking flipped, with evidence

A planning agent tested the three candidate sources live rather than trusting the strategy doc.
**The Phase 0 gate resolved the OPPOSITE way to the "Decisions taken" section:**

| source | verified result |
|---|---|
| **OpenStreetMap / Overpass** | WI has 2,943 places of worship; **2,341 Christian non-Catholic**; 1,849 with name+coords; **527 with a website**, 86% returning HTTP 200 on sample. One HTTP POST. |
| **locator.lcms.org** | `/api/congregations` returns **401**. The SPA sends a rolling anti-abuse token derived from `Date.now()`. Re-implementing it is a judgment call for the owner, not a detail to bake in — prefer asking LCMS for API access. |
| **elca.org** | **Zero** congregation data in HTML, no API in any of its 9 JS chunks, no per-congregation sitemap URLs, `auth0-spa-js` present. Needs a browser network capture just to establish feasibility. |

So the two "primary" directories yield **no websites today**, and OSM yields ~450 usable ones.
`website_url` is what the entire bulletin pipeline runs on, so OSM leads.

**Three findings that change the build:**

1. **`CATEGORY_MAP` has THREE copies, not two** — `scrape_to_db99.py:68`, `sync_to_db99.py:89`,
   and **`src/etl/transformers.py:40`**. Update fewer than all three and Protestant services
   silently become `other`: data loss that looks like success.
2. **The cross-denomination merge risk is measured, not theoretical.** 33 WI non-Catholic OSM
   churches sit within 150 m of a Catholic one, 8 within 60 m — and `decide_match`'s 60 m branch
   matches on **distance alone, ignoring name similarity**. Ungated, ~15-20 Lutheran congregations
   would silently UPDATE a Catholic `church_id` and re-point its FK-linked bulletin history.
   Unrecoverable without a restore. Gate on denomination FAMILY (db99 says `catholic`, OSM says
   `roman_catholic`), exclude known-mismatches from the candidate list entirely, and when either
   side is unknown TIGHTEN rather than guess. Prefer a visible duplicate over a bad merge.
3. **ODbL / share-alike.** OSM data carries attribution and share-alike obligations, and mixing it
   into `church` arguably makes db99 a Derivative Database. Mitigations are cheap (stamp
   `source_provider='osm'`, put the OSM object URL in `source_url`, footer attribution) but the
   decision is the owner's to make.

Full plan: `C:\Users\cashk\.claude\plans\virtual-conjuring-treasure.md` and the agent's build
plan in-session.

### CONSEQUENCE: most completed state passes ran the BROKEN fallback

Render one-off jobs run the image they were **launched** with, not the current one. The state
passes launched at 15:30 UTC (the ten largest) and 19:45 UTC (the other 36) on 2026-08-18. The
logger fix deployed at **22:45**. So **every state pass that completed on its first attempt ran
with the crashing known-page fallback** — the same defect that pinned the 86 named WI churches at
47 instead of 55.

Only states that happened to FAIL and get relaunched after 22:45 picked up the fix. That is luck,
not design, and it means roughly twenty completed states carry avoidable under-recovery.

**Re-running them is cheap and uses the same tooling.** The rule to carry forward: after any fix
to discovery or the fallback, a state is only "done" if its pass STARTED after that deploy.
Completion time is not enough.

### Focus states, re-run with the fixed code (2026-08-19 23:00 UTC)

| state | churches | with source | with PDFs | gap |
|---|---|---|---|---|
| PA | 1,611 | 1,513 | 703 (46.5%) | **810** |
| WI | 980 | 968 | 596 (61.6%) | 372 |
| MN | 791 | 786 | 501 (63.7%) | 285 |
| AZ | 324 | 318 | 127 (39.9%) | **191** |
| GA | 251 | 246 | 141 (57.3%) | 105 |

All five relaunched with `--days-fresh 0`, which rechecks every church not producing bulletins.

**Where they stand (2026-08-20 06:00 UTC):**

| state | session start | now | status |
|---|---|---|---|
| MN | 208 (26.5%) | **522 (66.4%)** | complete — best of the five |
| WI | 271 (28%) | **623 (64.4%)** — 1,037,756 names | **complete** |
| GA | 107 (43.9%) | **147 (59.8%)** | complete |
| PA | 517 (34.3%) | **822 (54.3%)** | running |
| AZ | 83 (26.1%) | **128 (40.3%)** | **website-limited — still gains slowly, but ~100 churches have no site upstream** |

AZ is the outlier and the reason is now known: ~100 of its 191 remaining churches have no website
anywhere upstream, so no pass can move them.
Note the churches->source gap is already small in these states (PA 98, WI 12, MN 5), so real
coverage expansion here means MORE CHURCHES — the denomination work — not more scraping of the
existing roster.

### ROOT CAUSE of the stuck 39: the fallback crashed on the line logging its success

Two diagnoses were wrong before this one — a budget cap, then queue ordering — and both were
reasoned rather than measured. Calling the real `process_church()` directly (`diag_process_church.py`)
produced the answer in one run:

```
NameError: name 'logger' is not defined
  extract_bulletins_to_db99.py:458
  logger.info(f"  recovered via known bulletin page: {known_page}")
```

`extract_bulletins_to_db99.py` has **no logger** — it prints. `logger.info` was copied in from
`run_bulletin_scraper.py`, which does have one, when the known-page fallback was ported to the
production path.

**Why it stayed hidden for hours.** The failing line sits on the SUCCESS branch:

```python
if alt.get("pdf_urls"):
    logger.info(...)          # <- NameError
    result = alt
    pdf_urls = alt["pdf_urls"]
```

The fallback discovered the bulletins and then died before assigning them — **it failed precisely
when it worked**. Churches where the fallback found nothing never reached the line and looked
perfectly healthy. So the symptom read as "the fallback does not recover these churches", which is
what sent two investigations down the wrong path.

Concretely: church 28511 discovers **100 bulletins** and downloads an **846 KB** PDF on the same
box, and still read 0 in the database.

**The lesson worth keeping: every isolated component passed.** Discovery, the walled strategy, the
download, the URLs, the church list — all verified working separately. The defect existed only in
the integration, only on the success path, and only calling the real function found it. Test the
pieces to build confidence; call the actual entry point to find the truth.

### The walled browser BUDGET silently capped recovery (2026-08-18 21:40 UTC)

The 86 named WI churches were re-run with `--walled-browser` and only **2** more recovered
(45 -> 47), which contradicted direct measurement: 28511 has 100 bulletins available at
stmarysbigriver.com and 28491 has 12 at dsoll.org, both with verified pages already seeded.

Rather than theorise again — one confident-but-wrong diagnosis had already cost a round —
`diag_church_path.py` traces the REAL production path per church_id: stored URL, its policy
verdict, primary discovery, whether `_different_host()` lets the fallback fire, the fallback
result, and whether the first PDF actually downloads. It found **three different causes**:

| church | discovery | download | verdict |
|---|---|---|---|
| 28511 Nativity / Big River | 100 PDFs via `walled_browser_proxy` | **OK, 846 KB** | works end to end, yet DB says 0 |
| 28491 Our Lady of the Lakes | 12 PDFs | **FAILED** | newest listed edition is dated 2026-08-23 and not uploaded yet |
| 28533 Holy Cross | 0 | — | host reachable, bulletins genuinely not discoverable — real parser gap |
| 28440 St. Bernard | 0 | — | **correct** — this parish publishes no PDF at all |

**28511 is the tell.** It succeeds in isolation and fails in production, and the only difference is
`WALLED_BROWSER_BUDGET`. The cap was 400, but 86 churches x 2 discovery calls (primary + fallback)
x 3 candidate paths is **up to 516** — so the budget ran out partway and every church after that
got no browser attempt at all, silently. Recovery became a function of queue position.

That cap was added earlier the same day as an OOM guard, and the guard itself was right — an
ungated version had already killed three sweep shards. Setting it too low for a targeted run is
the error, and it fails in the same shape as everything else here: **a limit that reads as an
absence of data.** Re-ran at `--walled-budget 3000`.

### Knowing when a state is done rather than stuck (South Dakota)

SD failed and relaunched eight times. The tempting read is "flaky, keep relaunching". The numbers
said otherwise:

| SD | 07:27 | 12:36 |
|---|---|---|
| churches with >=1 PDF | 117 | **117 (unchanged)** |
| bulletin_pdf rows | 20,497 | 20,589 (+92) |
| bulletin_name rows | 344,746 | 348,129 (+3,383) |

Five hours and eight relaunches bought **zero** additional churches — only more editions for
churches that already worked. Its remaining 82 sourced churches do not yield to this technique.
Dropped from the supervisor and its job cancelled.

The distinction that matters: *"still producing rows"* is not the same as *"still making
progress"*. Coverage is the metric; row counts will keep ticking up long after a pass has stopped
finding anything new.

### My own supervisor reproduced the bug it was watching for (2026-08-19 09:30 UTC)

The per-state supervisor developed two faults, both of which produced a **confident wrong signal**
— the same failure mode as every real bug in this effort:

1. **Race on the job file.** Several armed monitors each invoked the supervisor; a run that read
   the tracking list before another rewrote it wrote back a SHORTER list. Eight states — including
   NY, CA and TX — silently stopped being tracked, so a failure in them would never have been
   relaunched. Fixed with an atomic `mkdir` lock.
2. **Unknown treated as failure.** The status check relaunched on any non-running value, including
   the **empty string returned when the API call itself fails**. One transient Render blip
   relaunched all 13 states at once — and since `bulletin_pdf` has no unique index, two passes on
   the same church would duplicate rows. Fixed: unknown keeps tracking and looks again next cycle.

The second is the operational form of a rule already in CLAUDE.md: *a timeout is not a denial*.
Unknown is not failure.

Both were caught only by reconciling against Render instead of trusting the tracker — the tracker
reported "ALL STATE PASSES COMPLETE" while all 13 were still running. **Verify against the system
of record, never against your own bookkeeping.**

### Four states, measured 2026-08-18 09:20 UTC

The walled pass is not state-specific — IA and KS converted at the same rate ME did, without any
per-state work:

| state | before | now (12:33 UTC) | gain |
|---|---|---|---|
| WI | 271 (28%) | **534** (55.2%) | **+263** |
| ME | 13 (6.4%) | **89** (43.8%) — complete | **+76** |
| IA | 101 (20.8%) | **260** (52.3%) — complete, 706,304 names | **+159** |
| KS | 73 (20.9%) | **143** (40.7%) | **+70** |

**National: 7,584 -> 8,447 churches with bulletins (+863)**, gap 14,997 -> 14,138.

**RESULT (2026-08-20 19:20 UTC): national at 12,864 churches with bulletins, +5,280 from the
7,584 baseline — the corpus grew 70%; gap 14,997 -> 9,838.** 33 states finished, 10 running.

Where the largest states landed, each from a low base with no per-state work:

| state | before | now |
|---|---|---|
| NY | 614 (33.7%) | **1,121 (60.7%)** |
| CA | 443 (29.1%) | **758 (49.8%)** |
| PA | 517 (34.3%) | **703 (46.5%)** |
| IL | 448 (37.6%) | **717 (59.7%)** — complete |
| TX | 347 (25.4%) | **621 (45.3%)** |
| OH | 375 (37.8%) | **570 (57.2%)** |
| WI | 271 (28%) | **596 (61.6%)** |

New York alone gained **470 churches**. The large states are converting hardest, which is where
the churches actually are:

| state | before | now |
|---|---|---|
| NY | 614 (33.7%) | **968 (53.1%)** |
| CA | 443 (29.1%) | **695 (45.7%)** |
| PA | 517 (34.3%) | **699 (46.3%)** |
| IL | 448 (37.6%) | **703 (58.6%)** |
| TX | 347 (25.4%) | **530 (38.8%)** |

 Wisconsin, the state this started from, is complete at 603
(61.8%).

**Rolled out to ALL 50 STATES (2026-08-18 19:45 UTC), 48 passes tracked.** National crossed
**+1,126 churches** (7,584 -> 8,710), gap 14,997 -> 13,875, with every large state moving:
MN +97, CA +115, IL +83, MA +47, NY +45, OH +44. Render absorbed ~48 concurrent containers with
only one launch rejection.

**Rolled out to 12 states first (2026-08-18 18:00 UTC).** WI + KS still running, plus the ten largest
gaps: NY, CA, TX, PA, IL, OH, MN, MA, NJ, MI — roughly 7,800 of the national gap in one batch.
A supervisor script (`scratchpad/walled_supervise.sh`, mirroring
`supervise_bulletin_sweep.py`) relaunches whatever dies every 10 minutes, because these passes
run at the memory ceiling by design and failure is the normal case, not the exception. WI and IA have
both crossed half their sourced churches; Iowa has more than doubled.

**Operational reality:** the walled pass runs at the memory ceiling by construction — a browser per
walled host alongside spaCy and pdfplumber on 2GB — so it dies and needs relaunching. WI's first
attempt failed at 1h53m. Because each church is CLAIMED before processing and the queue is ordered
least-recently-checked-first, a relaunch resumes at the frontier rather than redoing work. Budget
for several cycles per large state, and never run it inline in a sweep.

### Maine proves the walled unlock scales to a whole state

Maine was the worst-covered state in the country: **13 of 203** sourced churches had any PDF
(6.4%), with 185 of its 205 zero-PDF churches on `blocked` hosts. Ran the walled pass against it
— `--walled-browser --workers 1`, its own container:

| | churches with ≥1 PDF | coverage | PDF rows | name rows |
|---|---|---|---|---|
| before | 13 | 6.4% | — | — |
| +2h | 52 | 25.6% | 4,841 | 82,380 |
| +3h | 69 | 34% | 6,202 | 115,119 |
| **final (4h25m)** | **89** | **43.8%** | **7,853** | **156,943** |

**+76 churches, a 6.8x increase**, and 156,943 names from a state that was effectively dark.
Maine went from worst-covered in the country to above the national average. So the finding is not confined to the eight hosts it was
discovered on — it converts a state, and Maine had been capped at 6% for as long as those labels
have existed. Iowa (20.8%) and Kansas (20.9%) launched next; whether their gaps are as
concentrated on blocked hosts is measured, not assumed.

**How to run it** (never inline in a sweep — see the OOM note above):

    python extract_bulletins_to_db99.py --state ME --walled-browser --walled-budget 400 --workers 1

`--walled-browser` is a CLI flag rather than an env var for two reasons, both learned the hard
way: Render's `startCommand` is argv, so an inline `WALLED_BROWSER=1` prefix is read as the
executable and the job dies in seconds looking exactly like an OOM; and setting it on the service
would hand browser-launching to the next scheduled cron, which is how three shards died.

### National scale of the same bug (measured 2026-08-17 21:16 UTC)

`verify_bulletins_national.py`, per state because a roll-up hides exactly the failure this
project already lived through:

| | |
|---|---|
| states | 50 |
| churches with a website | 24,158 |
| with a bulletin source | 22,581 |
| **with any PDF** | **7,584** at baseline -> **7,788** after one full sweep cycle |
| **gap — source but no PDF** | **14,997** -> **14,793** |

So the 69 churches that were reported are ~0.5% of the population in that state. Worst coverage:
ME 6.4%, IA 20.8%, KS 20.9%, OK 21.7%, AL 23.0%.

**Wisconsin, the only state with the fixes applied, is at 46.5%** against a 33.6% national
average — it went 28% → 47%. If that +19 points holds elsewhere it implies roughly +4,000
churches nationally, though that is an extrapolation from one state, not a promise.

### Two hours lost to invisible infrastructure — worth recording

- **`scripts/` is in `.dockerignore`.** Three one-off jobs failed in ~55s producing *nothing*,
  which looked exactly like an OOM or an import crash. Nothing under `scripts/` reaches the image,
  so `python scripts/foo.py` had no file to run. A bare `python -c print(1)` one-off succeeding at
  the same moment is what isolated it. **Runnable entry points belong at the repo root here.**
- **`_readback.py` to S3 no-ops on these services.** They carry Secrets Manager access for db99
  but not `s3:PutObject`, and `publish()` swallows failures by design — so a verify job reported
  *succeeded* having written nothing, which is worse than no channel at all. Fallback added: stash
  the report in a Render env var, readable through the same API that sets it.
- **db99 is unreachable from the workstation even with the VPN up** (VPC endpoint). All
  verification must therefore run on Render and report back.

### Result, measured in db99

| | session start | final | gain |
|---|---|---|---|
| churches with ≥1 PDF | 271 | **468** | **+197** |
| bulletin_name rows | 752,782 | **830,875** | **+78,093** |
| churches carrying the current (2026-08-16) edition | 43 | **181** | **+138** |
| bulletin_pdf rows | 78,645 | 80,997 | +2,352 |
| the 86 named recovery targets | 0 | **55** | **+55** |

Then the state-wide walled pass (297 WI churches on `blocked` hosts, which the earlier targeted
run never touched) carried it further — mid-run at 08:19 UTC: **481 churches with PDFs, 850,144
name rows, 192 carrying the current edition**. So WI stands at **+210 churches and +97,362 names**
for the session, still climbing.

Contributions, in order: the LPi API + wrapper unwrapping + removing the four early-returns took
it 271 → 345; the fallback exception fix and full sweep took it to 457; the walled-host browser
pass took it to 468 and lifted the named targets from 34 to 45.

Holy Cross (28532) went 0 → 12 PDFs / 561 names in the run that carried the fallback fix, and
its stored site (`sahcsjcatholics.com`) is not where its bulletins live (`holycrosswi.org`) —
consistent with the fallback, but NOT proof of it. Discovery from the stored site could have
found the LPi widget unaided, and the log line that would separate the two paths is not
retrievable from a one-off. Attributing a specific recovery to a specific code path needs the
source recorded in `bulletin_source.discovery_source`, which is a fair follow-up.

### A fifth defect, found only because production disagreed with local

The known-page fallback never ran for the churches it was built for. If
`find_bulletin_page(website_url)` **raised** instead of returning empty, the exception
propagated straight past it — and raising is exactly what a broken stored site does: a lapsed
domain, a TLS failure, or the literal string `#` (church 28511 genuinely has that in
`website_url`). So the fallback was reachable only for churches whose site worked well enough to
return an empty result, and unreachable for precisely the dead and merged parishes it existed to
rescue. Both discovery calls are now wrapped.

Local testing could never have caught this: locally those sites answer. Only the production/local
disagreement exposed it — dsoll.org and stlouiscaledoniawi.org both return **200 from Render** yet
yielded nothing, which ruled out egress and left the code.

### Proxy: what a top-up actually bought

The shared 711 endpoint was returning **407** from Render — account, not consumption. After the
owner topped up, verified from Render: 200 through the tunnel, exiting a **genuine US residential
ISP** (WideOpenWest), so the country code is right (a lowercase `-country-us` authenticates but
returns useless exits).

Value, measured rather than assumed — WI's 671 zero-PDF churches by host policy:

| policy | churches | what a proxy does |
|---|---|---|
| `direct` | **329** | nothing — already reachable; this is the real prize |
| `blocked` | 308 | nothing — fails direct *and* proxied |
| unmeasured | 23 | unknown |
| `needs_proxy` | **11** | the only ones a proxy directly unblocks |

**Re-measuring the mislabelled cohort.** 713 hosts carried a `blocked` verdict whose proxy leg
never produced a real HTTP code (407 / ProxyError / timeout) — i.e. the verdict blamed the host
for our own account lapsing. Re-probed with a healthy proxy and written: **31 flip to `direct`,
3 to `needs_proxy`, 679 confirm blocked.** (The dry run a few minutes earlier read 31/5/677 —
run-to-run variance on flaky hosts, not a bug.)

**Be honest about what that buys.** `proxies_for()` returns a proxy ONLY for `needs_proxy`;
`blocked` and `direct` both go direct, and nothing skips a blocked host. So the 31
`blocked` → `direct` corrections change **no scraping behaviour at all** — they make the table
truthful, nothing more. The functional gain is the **3** hosts that became `needs_proxy`. The
value of this exercise was mostly confirmatory: 679 of 713 suspect verdicts were right.

New flags `--recheck-proxy-failures` (the precise suspect cohort) and `--only-blocked`.

Three WI parish sites (stmaxkolbe, stmarysbigriver, holycrosswi) 403 **direct AND through a
residential IP** — genuinely blocked, consistent with the existing finding that the WI Cloudflare
walls need a solver, not an IP.

### Name classifier is outdated against name_engine — and the fix is not the obvious one

Compared the live scorer against `names_people_matcher` (the source of truth for
[[identify-score-person-names]]). Four divergences:

| | church-scrapes | `name_engine` |
|---|---|---|
| engines | 2 — SSA/Census dict + spaCy NER *veto* | **3** — Dictionary, spaCy NER, **NameDataset** |
| combination | dict score; NER failure caps it at 0.35 | weighted average of all three |
| `high` | `score >= 0.7` | `>= 0.7` **and all engines agree** |
| `medium` | **`>= 0.4`** | **`>= 0.5`** |
| `is_suspect` | only when the NER veto fails | `engines disagree AND spread > 0.3`, **any tier** |

The costly one is the **missing NameDataset engine**. SSA + Census are US-centric; Catholic
bulletins are full of Polish, Hispanic, Vietnamese and Filipino surnames, which score low on US
dictionaries and get filtered off the dashboard — indistinguishable from the scraper having
missed them. Second: `medium >= 0.4` means the 0.4–0.5 band is displayed as real people when
current logic calls it `low`.

`name_engine/` is now **vendored** into this repo (names_people_matcher is private, so a
pip-install would need a Docker build secret). **This copy must be kept in sync with the source
repo.**

**A silent-degradation hazard, worth knowing about generally.** The engines *disable themselves*
on import failure rather than raising, so a broken spaCy quietly reduces the "consensus" to two
engines. That does not merely weaken scores — it inverts them. Measured on Python 3.14 where
spaCy cannot load: `Parish Council` → medium/0.67, `Fr. John Smith` → low/0.35. Rescoring 776K
rows in that state would corrupt the table while reporting success. `measure_rescore_impact.py`
therefore **aborts unless all three engines are active**.

**Memory is the binding constraint, and it rules out the obvious integration.** Measured on the
2GB cron plan:

| loaded | RSS |
|---|---|
| baseline | 11 MB |
| + Dictionary (SSA+Census) | 92 MB |
| **+ NameDataset** | **1,948 MB** |
| + spaCy `en_core_web_sm` | 1,948 MB |

NameDataset alone costs **~1.86 GB**. With `en_core_web_lg` (709 MB) the total is ~2.6 GB —
impossible here. Even the `sm` configuration leaves ~100 MB of headroom before any batch work.
So the consensus **cannot** be wired inline: extraction already carries spaCy lg plus pdfplumber
across 2 workers, which is why `--workers` was cut from 4 to 2 after 27 OOM kills in 56 jobs.

**Decision: export the name lists to db99 instead of loading them per process.** Holding 1.86 GB
of Python objects to answer membership questions is the wrong shape, and this project already
solved it twice — `ref_ssa_names` and `ref_census_surnames` are db99 tables, not in-process
dicts. A `ref_name_dataset` table plus indexed lookups gives the international coverage inline,
at near-zero memory, on the existing plan, keeping `lg`.

**Upstream bug to fix in names_people_matcher:** `_compute_consensus`'s docstring says
`spread > 0.4`; the code uses `0.3`. The skill doc matches the code, so the docstring is the odd
one out.

### Status — what is proven and what is not

| | |
|---|---|
| ✅ Proven locally | 0 → 12 PDFs for a slug parish; wrapper URL downloads as a real 7.2 MB `%PDF`; 21 → 43 of 69 churches yield PDFs |
| ✅ Proven in prod | 87 corrected pages seeded into `bulletin_source`; readback channel working |
| ✅ Proven in prod | **Recovery in db99: +74 churches with a first-ever PDF, +23,295 names, +54 churches on the current edition.** 34 of the 86 named targets recovered outright. |
| ⏳ In flight | Full WI sweep on the fixed code (4 shards) over the ~626 churches still at zero, then the remaining states. |
| ❌ Not recoverable today | The Cloudflare-walled WI hosts. They 403 direct AND through a verified US residential exit, so more proxy GB will not move them — they need a solver. |

### Next

1. ✅ Targeted `--church-ids` run over the 87 (new flag — recovering a few corrected churches must
   not cost a two-hour state sweep). Ran twice: 32/86 recovered, then 34/86 after the fallback fix.
2. ✅ Verified per-church rather than by state aggregate. This mattered: the first report used a
   hand-copied slice of 18 ids and read "0 recovered" in the very run where 30 churches gained
   their first PDF. The target list is now read from the seed file so the report cannot disagree
   with what was worked on.
3. ⏳ Full WI sweep on the fixed code, 4 disjoint shards (`MOD(church_id, 4)`), over the ~626
   churches still at zero — 329 of them on `direct` hosts needing no proxy at all.
4. Remaining states, via `supervise_bulletin_sweep.py` (6 shards, relaunches OOM-killed ones).

### Follow-ups worth doing

- **Record the discovery path per church.** `bulletin_source.discovery_source` should say whether a
  recovery came from `website_url` or the known-page fallback. Right now attribution is guesswork:
  Holy Cross recovered in the run carrying the fallback fix and its stored site is not where its
  bulletins live, but that is circumstantial, and the log line that would settle it is not
  retrievable from a one-off.
- **A unique index on `bulletin_pdf(bulletin_source_id, pdf_url_hash)`.** Already listed as an
  accepted limitation below, and it is what forces every concurrent run to be sequenced by hand —
  it is why the sweep had to be cancelled before each targeted run today.
- **The Cloudflare-walled WI hosts** need a solver. More proxy GB will not move them; that is now
  measured, not assumed.

**Operating rule reaffirmed:** everything is proven by a **forced run on Render**, verified against
db99 in the same session. Never by waiting for the cron, and never off a green deploy.

## 🆕 2026-08-18 — BEYOND CATHOLIC: DENOMINATION SCHEMA + MULTI-SOURCE WISCONSIN

Wisconsin holds ~987 Catholic churches here. It is also the most Lutheran state in the country:
ELCA ~694, WELS ~452, LCMS ~414 (199 South District + 215 North). **~1,560 Lutheran congregations
alone**, before Methodist, Baptist, UCC, Presbyterian, Episcopal and non-denominational. Adding
other denominations plausibly triples WI, and every new church feeds the same product — service
times, bulletin PDFs, extracted names.

### Schema: additive only, nothing renamed

The schema is Catholic *in its bones*, not just its comments: no `denomination` column and no
`source`/`provider` column at all (provenance is currently inferred from a
`service.source_service_id` prefix, `dm-`). `lk_service_category` is
`mass/confession/adoration/devotions/education/community/other`, so a Protestant "Sunday Worship"
falls through `CATEGORY_MAP.get(name, "other")` into the catch-all and **loses its meaning while
looking like a success**. `church.has_perpetual_adoration`, `mass_count`, `confession_count`,
`adoration_count`, `lk_diocese` and `lk_note_tag` (`vigil`, `exposition`, `holy_day`) are all
Catholic-specific.

Planned, all additive so the Catholic path behaves identically:

- `church.denomination VARCHAR(60) NULL` + index
- `church.source_provider VARCHAR(40) NULL` — retires the fragile `dm-` prefix as the only
  provenance signal
- **new `lk_service_category` rows only**: `worship`, `sunday_school`, `bible_study`, `prayer`,
  `youth`. Existing Catholic rows and the `*_count` columns are untouched — `data_loader.py`
  reads them.
- extend `CATEGORY_MAP` in **both** `sync_to_db99.py:89` **and** `scrape_to_db99.py:68` — they are
  duplicated, and changing one leaves the two paths disagreeing.
- **backfill `denomination='catholic'` on all existing rows.** Every current row came from a
  Catholic-only source, so this makes the column trustworthy immediately and lets the dedup
  matcher gate on it. Without it NULL would mean both "unknown" and "Catholic legacy". ~23k rows
  on a shared DB, so it goes through the Telegram approval bridge.

### Sources: many, not one

Deliberately multi-source — no single directory covers the field, and each has a different failure
mode. **Permissions checked, not assumed:**

| Source | robots | Gives | Verdict |
|---|---|---|---|
| `locator.lcms.org` | `Allow: /` | LCMS, both WI districts (~414) | primary |
| `elca.org/directory/congregations` | permitted | ELCA (~694) | primary |
| **OpenStreetMap / Overpass** | open data | `amenity=place_of_worship` + `denomination` + `website` tags | primary — free, structured, no ToS friction, and it carries the website field the bulletin pipeline needs |
| `umc.org`, `ucc.org`, `pcusa.org`, `episcopalchurch.org`, `ag.org`, `efca.org`, `crcna.org` | to check | their own congregations | per-denomination adapters |
| `faithstreet.com` | `Allow: /` + sitemap index | name/address/geo/phone, 1,138+ WI in sitemap1 alone | roster / gap-filler only |
| `yearbook.wels.net` | **`Disallow: /`** | — | **EXCLUDED. Do not scrape.** Reach WELS congregations through permitted rosters or not at all. |

**FaithStreet is a roster, not a full source** — measured: its church pages carry JSON-LD
`@type: Church` with name, address, `geo` and phone (which plugs straight into the existing ≤150m
matcher), but its `url` field is the FaithStreet page itself, **not the congregation's website**,
and service times are React-rendered. Since `website_url` drives the entire bulletin pipeline,
FaithStreet alone yields no bulletins.

### Bulletin vocabulary is half-ready

`BULLETIN_PATHS`/`BULLETIN_PAGE_KEYWORDS` already cover "bulletin" and "newsletter", both common in
mainline Protestant use. They will **miss** the Lutheran/Methodist "worship folder", "order of
worship", "order of service", "weekly update" — worth adding, especially in Wisconsin. Vendor
handling is Catholic-only (LPi/ParishesOnline, eCatholic); the Protestant equivalents are
Subsplash, Sharefaith, Ekklesia 360, Planning Center/Church Center, Tithe.ly, Breeze. Everything
else generalises unchanged: `unwrap_pdf_url()`, the walled-host browser+proxy strategy, the
column-aware PDF extraction and the name engine.

### Adding a source is copy-the-pattern, not implement-an-interface

There is no ABC or registry. Each source needs the DiscoverMass trio: `src/scrapers/<source>.py`
(enumerate + parse returning the shared `{"church": ..., "services": ...}` dict),
`<source>_to_db99.py` (match-or-insert), `run_<source>_all.py` (state orchestrator). Reuse
`decide_match()` (geo ≤150m + normalized name/city, with its **AMBIGUOUS verdict that must never
auto-merge** — that rule protects FK-linked bulletin history) and `_synth_service_id()` for
idempotency, with a new prefix per source.

**Cross-denomination dedup risk:** a Protestant church can sit within 150m of a Catholic one.
The matcher must gate on `denomination` so a Lutheran congregation can never silently merge onto
a Catholic `church_id`.

## 🔀 2026-08-08..10 — PER-HOST PROXY POLICY, ENFORCED IN CODE

`PROXY_URL` no longer tunnels anything on its own. Every host is labelled in **`scrape_host_policy`**
and the proxy is chosen **per request** by `src/utils/host_policy.py`.

| policy | meaning | scraper does |
|---|---|---|
| `direct` | fine from our egress | no proxy |
| `needs_proxy` | fails direct, proxy **demonstrably fixes it** | use proxy |
| `blocked` | fails direct **and** via proxy | **no proxy** — same 403, but paid for |

Three states, not two: `blocked` is what stops us paying metered GB to receive identical 403s.
Unknown hosts fail open to `direct`. Survey of all **15,055 hosts from Render**:
**direct 9,326 (61.9%) · blocked 5,491 (36.5%) · needs_proxy 238 (1.6%)**.
Only 1.6% justify a proxy — that ratio is the whole argument for per-host enforcement.

### ⚠️ The bug that made it a no-op for two days
`host_policy.load()` was **never called**, so the cache stayed empty, `proxies_for()` returned None
for every URL, and the scraper went **direct for all 15,055 hosts** — including the 238 that only
answer through the proxy. It fails open, so nothing errored; those hosts just silently never yielded.
Symptom: 342 `needs_proxy` churches stuck at 23% coverage through a full re-sweep with a healthy
proxy. **The earlier unit test passed because it handed `proxies_for()` a policy dict directly — it
tested the function, not the wiring.** Fixed; the regression test now asserts the real path (before
`load()` a needs_proxy host routes direct = the bug; after, it routes via proxy).
Proof it works in production: **244 PDFs pulled from needs_proxy hosts**, which 403 direct.

### Proxy findings that overturned an earlier conclusion
- The original "nothing needs a proxy" verdict was measured **from a laptop** — the wrong vantage
  point. ~4,400 hosts 403 from Render while returning 200 residentially.
- **Measure from the machine that runs the scraper.** Now a global rule.
- **A datacenter block does not imply a proxy fixes it:** 711's exits got 403 on 7/7 of the same
  hosts. `blocked` exists because of this.
- **711 died mid-survey (`407`)** and silently relabelled **690 hosts** as "blocked" when only our
  own account had failed. Root cause was **plan expiry** (Regular tiers are 30-day; Enterprise is
  "Lifetime valid"), not consumption — our own probing used ~12 MB.

## 🛡️ 2026-08-10 — PRECONDITION CHECKS (`verify_scrape_preconditions.py`)

Every silent failure here shared one shape: green exit code, rows landing, result still wrong.
So these assert **preconditions**, each earned from a real incident — proxy reachable,
`needs_proxy` cohort yielding, host policy fresh, rotation advancing, label sanity.
Runs **first** in `verify_all.py` so a meaningless run is flagged before its output is judged healthy.

**A check that cries wolf gets ignored** — which is how the original bug survived five months. The
first version reported a 163-day-old frontier; all 63 "stale" churches were ones the sweep
deliberately excludes (diocese directory pages, Facebook links) and **zero were real**. The check now
mirrors the sweep's own exclusion filter and reads 12 days.

## ✅ 2026-08-10 — PRE-FLIGHT OF THE REAL CRON ENTRYPOINT
Everything during the recovery ran `extract_bulletins_to_db99.py` directly; the cron's actual command
(`run_daily_pipeline.py --bulletins-only`, which also does backfill → rescore → refresh-stats →
health → redeploy → verify) had **never run on the new code**. Pre-flighted with `--limit 25`:

```
2026-08-10  bulletins=OK,   backfill=OK, rescore=OK, refresh_stats=OK, health=OK, redeploy=OK, verify=OK
2026-08-04  bulletins=FAIL, backfill=OK, rescore=OK, refresh_stats=OK, health=OK, redeploy=OK
2026-07-28  bulletins=FAIL, backfill=OK, rescore=OK, refresh_stats=OK, health=OK, redeploy=OK
```

**Rule: exercise the cron's real entrypoint, not the script it wraps.** The wrapper's other six steps
are where the untested risk lives.

## ⚠️ 2026-08-06 — RENDER `startCommand` IS NOT A SHELL (cost hours, read this first)

Every shard the supervisor launched died within seconds. It looked exactly like the OOM being
chased at the time, and led to a long detour tuning memory that changed nothing.

**Render does not run `startCommand` through a shell.** So this:

```
MAX_PDFS_PER_CHURCH=150 NER_MODEL=en_core_web_sm python -u extract_bulletins_to_db99.py ...
```

is parsed with `MAX_PDFS_PER_CHURCH=150` as the **executable name**, and the job fails instantly.
The same mistake had already broken a `sh -c "python a.py; b=$?; ..."` verify command.

**Rules:**
- `startCommand` must be **bare argv**: `python -u script.py --flag`. No env-var prefixes, no
  `sh -c`, no `;`, `&&`, `$?`, `$(...)`, or redirection.
- Pass configuration as **service env vars** (one-off jobs inherit them), not command prefixes.
- Need several steps? Write a Python wrapper (`verify_all.py`) instead of shell chaining.

**How it was proved:** three concurrent one-off jobs with the bare command `sleep 300` all ran and
succeeded, while `sh -c "...sleep 420..."` jobs failed in ~80s. That isolated the cause to command
parsing — not memory, not concurrency, not the scraper. The one shard that survived all night was
the only one launched before the prefix was introduced.

**Corrected diagnosis:** the earlier "shards are OOMing on the 2GB plan" conclusion was **wrong**.
Memory was never demonstrated to be the limit. `MAX_PDF_SIZE_MB` had been dropped to 12 on that bad
theory and is **restored to 25** — real bulletins run 6–8MB and a 12MB cap silently skips them.
(An oversized PDF creates no row, so it is deferred to a later pass, not lost.)

## 🔁 2026-08-06 — SWEEP IS SELF-HEALING (`church-sweep-supervisor`)

Shards still die intermittently. `supervise_bulletin_sweep.py` on cron
**`crn-d9pugau417fc73f6lj10`, `*/20 * * * *`** relaunches any shard that is not running, and pings
Telegram only when it actually intervened. Restarting is free: `church.bulletin_checked_at` is
stamped per church, so a relaunched shard resumes at the frontier instead of redoing work.

**Verified live:** two shards died between 03:01 and 03:15; the 03:20 tick relaunched both with no
human involved.

Two gotchas worth keeping:
- Treat a **`pending`** job as alive. Counting it as dead launches a duplicate for the same shard
  every tick and piles up jobs that compete for resources.
- **`/v1/services/{id}/jobs` lists only one-off jobs, not scheduled cron runs.** An empty list does
  NOT mean the cron never fired — read `serviceDetails.lastSuccessfulRunAt` instead. (Note this
  field is unreliable on some crons, where it stays `None` despite runs; cross-check by looking for
  side effects the run would have produced.)

## 🌐 2026-08-06 — PROXY POLICY (⚠️ SUPERSEDED — see the 2026-08-07 Render-403 entry below)
**This entry was measured from a laptop, which was the wrong vantage point.** The conclusion
("`PROXY_URL` stays unset") still holds, but not for the reason given here: ~1,441 parish sites DO
403 from Render, and the 711 proxy does not fix them. Read the 2026-08-07 entry before acting on
anything in this section.

Full table in `docs/PROXY_POLICY.md`; global rule added to `global-config/CLAUDE.md`.
Measured direct from a residential IP: parish websites **38/40 → 200, zero blocks**;
`discovermass.com`, `bulletins.discovermass.com`, `parishesonline.com`, `irp.cdn-website.com`,
`files.ecatholic.com`, `4.files.edl.io` all 200 with valid PDFs. **`PROXY_URL` stays unset.**
- **Probe a real URL, not the domain root.** `files.ecatholic.com/` 403s at the root (directory
  listing denied) while real bulletin PDFs under it return 200 — a root probe would have put a
  proxy on a host that never needed one.
- `catholicindex.org` is a Cloudflare **managed challenge** (`cf-mitigated: challenge`), which a
  proxy cannot fix; already retired in favour of DiscoverMass.

## ✅ 2026-08-06 — MASS TIMES CONFIRMED HEALTHY
`verify_mass_times_run.py`: **17,689 churches re-scraped over 7 days across all 50 states**,
462,243 services on file, 28.9% stale >30d. Checks distinct states touched, because a stuck
rotation there would look exactly like the bulletin bug, plus a global-staleness floor to catch the
slow death that killed CatholicIndex silently for months.

`verify_all.py` runs both verifications in one process (a Python wrapper, not shell chaining) and
exits with the worst child code. Cron **`church-bulletin-verify` `crn-d9pscd67bikc7380h680`** now
runs **Tue 15:00 UTC — 12 hours after the bulletin cron's 03:00 start.**

## ⚠️ 2026-08-06 — RECOVERED NAMES WERE ALL `low` UNTIL RESCORED

The sweep inserted **646,157 names at 100% `low` confidence — zero high, zero medium.** Not a bug in
scoring: `extract_bulletins_to_db99.py` inserts a provisional score, and **`rescore_names_sql.py` is
what actually assigns confidence** from the SSA/Census reference tables in db99.
`run_daily_pipeline.py` runs it after its own extraction; an ad-hoc sharded sweep does not.

Running it turned that into **415,856 high / 136,879 medium / 97,711 low**.

**Rule: any run of `extract_bulletins_to_db99.py` outside `run_daily_pipeline.py` MUST be followed by
`rescore_names_sql.py --new-only`.** Names that look worthless in the dashboard are worse than
missing names, because nobody goes looking for them. The supervisor now does this automatically when
the queue drains (rescore → refresh-stats → coverage report → Telegram).

## 🧹 2026-08-06 — MERGED-NAME CLEANUP: 194,363 artifacts downgraded (Step 8b)

Column-aware extraction runs one bulletin row into the next, producing `Lynette Eilermann Hannah`
and `Vince Eimer Vince` **at HIGH confidence**.

**`split_merged_name()` does NOT fix this — two traps:**
1. It reads `data/reference/ssa_first_names.csv` / `census_surnames.csv`, which are **not in the repo
   or the Docker image**. It is a **silent no-op in production**. Wiring it into the extractor ships
   dead code (attempted and reverted).
2. Its heuristic ("last word is a common SSA first name and NOT a Census surname") is wrong for these
   cases anyway: **Hannah, Anthony and Jeffery are all genuine Census surnames** (ranks 1779, 618,
   3638), so it skips them — and loosening it would destroy real names like `Sarah Jane Hannah`.

**Step 8b in `rescore_names_sql.py` uses evidence instead:** downgrade a 3-word name only when its
first two words **already exist as their own name in the SAME PDF**. That proves the third word bled
in from the neighbouring row, and nothing is lost — the correct 2-word name is already stored at its
own confidence. Needs no reference files and cannot fire on a genuine three-part name unless the
two-part version is also present in the same document.

Backlog cleanup across all 23.5M rows: **194,363 downgraded**. Clean high-confidence names now
16,160,189 of 23,490,583 (68.8%).

**Deliberately conservative — some artifacts remain.** It only fires when the 2-word base was also
extracted, so `Michaela Maze Linda` and `Laurence Husak John` survive at high confidence. Catching
those needs a heuristic that would also destroy `Jose Benito Chavez` and `Juan Carlos Montes`, which
are correctly preserved today. Do not "improve" this without a way to tell the two apart.

## ✅ 2026-08-06 — DASHBOARD `/health` FIXED (commit 43514ab)
`dashboard/Dockerfile` copied only `dashboard/app/`, so `src.utils.health_checks` was absent and the
endpoint 500'd **in production while working locally** — which is exactly why it went unnoticed.
Copies only the three files the import needs (not all of `src/`, which drags spacy/playwright into a
dashboard image that does not need them). Also fixed the `sys.path` insert, which was correct in the
repo (`dashboard/app` → root) but resolved to `/` in the container where `src/` sits at `/app/src`;
it now probes both and adds whichever actually contains `src/`. Verified 200, `db=connected`.

## ✅ 2026-08-06 23:00 UTC — RECOVERY SWEEP COMPLETE (both tiers drained)

| | |
|---|---|
| Productive queue | **0** (was 8,057) |
| Discovery queue | **0** (of ~13,383) |
| Churches swept | **21,770** |
| Names recovered | **1,119,435** (591,180 high / 190,377 medium) |
| `bulletin_pdf` | 2,808,920 · `bulletin_name` 23,854,993 · clean high 16,280,490 |

Ran to completion unattended: supervisor drained the productive tier → rescore →
refresh-stats → coverage report → chained into discovery → drained that too. Verified rather than
assumed (names are NOT 100% `low`, stats moved 1,273,271 → 1,425,689, shards exit `[queue-drained]`).

**Discovery more than doubled the source base:** `bulletin_source` 9,499 → 19,399 churches; **968
sources produced their first-ever PDF.**

### Gap recovery: parishes came back, editions did not
| month | parishes | editions |
|---|---|---|
| 2026-01 (pre-gap) | 2,071 | 108,473 |
| 2026-03 | 1,257 | 6,498 |
| 2026-05 | 1,441 | 6,369 |
| 2026-07 | 1,673 | 6,629 |

Worst gap month went from ~300 parishes (17% of best) to **1,257 (61%)**. But gap months hold ~6,000
editions against ~110,000 pre-gap, and **that is the real ceiling, not a measurement artifact**:
parishes publish only the last 8–12 weeks, so March–June editions rolled off the public web before
we reached them. We recovered every parish still hosting an archive; the rest are gone. 11 weeks
remain below 50% of the best week.

### ⚠️ Self-inflicted metadata regression, found and fixed (commit 2af10b5)
`process_church` UPSERTed `discovery_source` + `bulletin_page_url` unconditionally, so any transient
failure overwrote a real bulletin page with the bare homepage and marked it `not_found`. **1,441
sources read `not_found` while holding thousands of PDFs** — the constrained tail runs (8MB / 40-PDF
caps, 2 workers) timed out on deep-archive churches and downgraded them.

**Scope: metadata only.** Discovery always starts from `church.website_url`
(`extract_bulletins_to_db99.py:333`), never from the stored `bulletin_page_url`, so scraping
capability was never affected and no PDF or name was lost. What degraded is the recorded page URL
and the `discovery_source` label (dashboard display and reporting).

Failure now only touches `discovered_at`; the previous discovery survives. The affected rows were
re-queued (`bulletin_checked_at = NULL`) and repair themselves as each church is successfully
crawled — a slow drip, because these are the same slow deep-archive sites whose discovery times out.

### ⚠️ Head-of-line blocking (commit 9a29235) — the reason the tail stalled
`bulletin_checked_at` was stamped only AFTER `process_church` returned, so a church that killed its
worker was never stamped, stayed at the head of an oldest-first queue, and was retried by every
relaunched shard forever. The blockers were parishes holding **3,000–3,900 PDFs**, last stamped in
May. **Claim before processing**, always — one deferred church beats a permanently blocked queue.

### Post-sweep settings restored
`MAX_PDF_SIZE_MB=25`, `MAX_PDFS_PER_CHURCH=150`, `SWEEP_WORKERS=5`. The tail ran at 8/40/2 to survive;
leaving those in place would have made the weekly run permanently shallower.

## 🚫 2026-08-07 — ~1,441 PARISH SITES 403 FROM RENDER, AND NO PROXY FIXES IT

Chasing why discovery kept failing on the deep-archive parishes produced a three-way measurement on
the same 7 sites:

| Fetched from | Result |
|---|---|
| Residential laptop | **7/7 → 200** |
| **Render datacenter** (`74.220.49.50`) | **7/7 → 403** |
| **711 residential proxy** (`-country-US`) | **7/7 → 403** |

So the `not_found` flags were frequently *accurate from Render's vantage point*. Two lessons, both
now global rules:
1. **Measure from the machine that runs the scraper.** The original "nothing here needs a proxy"
   verdict was measured from a laptop and certified sources as fine that were 403ing in production.
2. **A datacenter block does not imply a proxy fixes it.** 711's exits sit on the same reputation
   lists (`proxy_ok: 0` of 18). This stays a no-proxy project — not because nothing is blocked, but
   because the proxy we have does not help. `PROXY_URL` was set only for the probe and removed
   immediately.

**Unresolved: we may have earned these blocks.** The same hosts hold thousands of PDFs we scraped
successfully before, so they were reachable at some point. Keep per-host rates modest.
Reproduce: `python -u diagnose_discovery_egress.py --limit 20 --compare-proxy` (writes to
`scrape_log`, since Render does not serve cron runtime logs).

## ✅ 2026-08-07 — DEEPER ARCHIVE PASS (item 7) COMPLETE
Targeted rather than a full re-sweep: only **129 sources** had been truncated by a cap (102 at 40,
27 at 100) and all 129 were still reachable. Re-queued them, raised `MAX_PDFS_PER_CHURCH` to 400,
ran, then **restored the cap to 150**.

Yield: **4,088 editions → 83,500 names** (57,497 high / 16,071 medium / 9,932 low) from 129 churches
— by far the richest per-church return of the whole recovery, which is what a removed truncation
looks like. Both queues drained; `bulletin_state_stats` refreshed to 1,494,920.

### 🏁 Recovery grand total (2026-08-05 20:24 → 2026-08-07 04:00 UTC)
- **1,216,587 names** from **58,305 editions**
- Table totals: `bulletin_name` 23,952,145 · `bulletin_pdf` 2,813,437
- `bulletin_source` 9,499 → 19,399 churches; 968 sources produced a first-ever PDF

## 📊 Recovery sweep — earlier status at 2026-08-06 06:09 UTC
- **756,312 names** from **35,265 editions** across **5,096 churches**
- **3,380** productive churches remaining (of 8,057); shards 1 and 4 have `succeeded` outright
- Supervisor relaunches dead shards every 20 min and will self-finish (rescore + stats + coverage
  report to Telegram) when the queue drains

### Next (blocked on the sweep finishing — both compete for the same shards)
1. **Discovery tier** — ~13,383 churches that have never yielded a bulletin page (~1% hit rate):
   `--discovery-only`.
2. **Deeper archive pass** — `MAX_PDFS_PER_CHURCH` is env-set to 150 on the cron; a later pass at 400
   reaches further back for churches with long archives.

_(Original 2026-08-05 entry below.)_


## 🔴 2026-08-05 — BULLETIN CRON MADE NO FORWARD PROGRESS FOR ~5.5 MONTHS

**The weekly bulletin scrape ran, inserted names, and reported "completed" every Tuesday — while
covering only the first five states alphabetically.** It looked healthy from every angle anyone was
checking, which is the real lesson here.

### What was wrong
- `extract_bulletins_to_db99.py` selected all 22,519 churches `ORDER BY state_code, slug` and was
  SIGKILLed by a 2h step timeout around **California**, so every run restarted at **Alaska**.
  States ever reached: `AK, AL, AR, AZ, CA`. **CO→WY were never reached.**
- `scrape_log` read `bulletins=FAIL` every Tuesday from 2026-07-14 on, but `bulletins` is in
  `run_daily_pipeline.py`'s `non_critical` set, so the pipeline still logged `completed`.
- `--days-fresh` could not save it: it keyed on `bulletin_source.discovered_at`, which is NULL for
  churches that have no bulletin page, so each run re-crawled the same ~13k dead ends first.
- **Coverage proof** (`report_edition_coverage.py`, by publish week): parishes-with-an-edition fell
  off a cliff at **2026-02-23** (1,459 → 433) and decayed to ~70 by August. The residual ~430 is
  exactly the AK/AL/AR/AZ/CA set — independent corroboration of the root cause. The break is
  **2026-02-23**, months earlier than `scrape_log` retention suggested.

### The fix (commits 80a1aa0, 7b8a609, 5da29e2, 98f4d0f)
- **`church.bulletin_checked_at`** (+ index), stamped on EVERY attempt hit-or-miss, so a church with
  no bulletin page still rotates to the back. `--days-fresh` now keys off it, in SQL.
- **`--max-runtime-minutes`** — exit cleanly and KEEP the watermark instead of losing the batch to a
  SIGKILL. Bulletin window raised 2h → 10h (`BULLETIN_RUNTIME_MINUTES`).
- **Priority ordering — load-bearing:** `ORDER BY EXISTS(bulletin_source) DESC, bulletin_checked_at ASC`.
  Churches that already have a bulletin page go FIRST; they publish weekly and are the only ones that
  yield names. Seeding the watermark from `bulletin_source.discovered_at` leaves NULL for churches that
  never *yielded* a page, which is NOT "never tried" — ordering those first burned the whole window
  (measured: 1 new source per 76 churches, **0 names in 30 min**). After reordering: 39 churches →
  207 PDFs → **4,923 names in 12 min**.
- **`MAX_PDFS_PER_CHURCH` 100 → 400** — a church hit the old ceiling exactly, truncating its archive.

### Throughput: 2.7 → 43 churches/min (16x)
- **Per-HOST rate limiting.** The blocker was a single global `_last_request_time` in
  `run_bulletin_scraper.py`: every request waited `REQUEST_DELAY` behind every other, capping the
  process at 1/`REQUEST_DELAY` req/s regardless of workers. Every parish is a different server, so
  they were only ever waiting on each other. Verified: same host still serialises (1.0s), 12 distinct
  hosts no longer wait (0.00s).
- **Threads** (`--workers`) with per-thread DB connections and `requests.Session`; `_ner_lock` around
  spaCy (`nlp.pipe` mutates shared model state), `_browser_lock` around Playwright's sync API, and
  `prewarm_shared_state()` so workers don't race the lazy loads.
- **Shards** (`--shards N --shard I`, `MOD(church_id, N)`). Threads only overlap network waits; PDF
  extraction is CPU-bound and the cron is a **1-vCPU standard plan**, so one container plateaued at
  ~7/min. Shards give real parallelism. **Verify disjoint+complete before launching** (6 shards summed
  to 8,057 = union = unsharded count).
- **Shards OOM at high worker counts** on the 2GB plan — a shard died in ~5 min with no runtime logs
  (the Render logs API does not serve cron stdout). Relaunch at `--workers 5-6`; the per-church
  watermark means a dead shard resumes where it stopped.

### Edition dating — you cannot fix what you cannot measure
`pdf_date` was NULL on all **1,076,400** rows the direct-to-db99 extractor ever wrote, so there was no
way to ask which editions we hold. Now parsed from the URL (`pdf_date_from_url`), **287,625 recovered**.
Parsing needs care — patterns run against the FILENAME only, most-specific first:
- matching the whole URL let a UUID tail glue onto the filename (`.../74fc…f3ab07/07-06-25-x.pdf`
  → 2006-07-07 instead of 2025-07-06);
- the query string is dropped, or cache-busters (`?t=1768507880000`) parse as dates;
- the filename is split off BEFORE percent-decoding, so `6%2F15%2F2025.pdf` stays a filename;
- `YYYY-MM-DD` is matched before the 2-digit-year rule, which read `_2026_01_26-02-01` as Jan 26 **2002**;
- a 6-digit `YYMMDD` is trusted only when it agrees with the `/YYYY/MM/` upload path.
Every candidate must pass a real-calendar + 1995..now check, so Feb 30 / month 13 / future → NULL.
**Undated stays NULL — the download timestamp is not the publish date.**
`--refix-invalid` repaired **2,065** legacy rows the old parser got wrong (editions dated 2089, 0000-00-00):
1,453 recovered a real date, 612 cleared to NULL. **Impossible dates remaining: 0.**

### Weekly verification (commit 98cc76a) — the actual safeguard
`verify_bulletin_run.py` asserts on **data**, not exit codes: PDFs, names, **UNIQUE names**, and distinct
parishes against floors, plus an explicit "<10 states touched = rotation stuck again" alarm. Unique-vs-total
is the load-bearing metric — a sweep re-reading the same bulletins inserts nothing new, so total names can
look fine while nothing moved. Reports to **Telegram pass or fail** (silence == a dead cron). Wired as the
pipeline's last step, runs unconditionally, and is deliberately NOT `non_critical`.
Also a standalone cron **`church-bulletin-verify` `crn-d9pscd67bikc7380h680`, Tue 16:00 UTC**, so a
*dead* bulletin cron still alerts.

### Known limitation (accepted)
`bulletin_pdf` has no unique index on `(bulletin_source_id, pdf_url)`, so check-then-insert is advisory and
two concurrent PROCESSES can duplicate a row. Within one sweep each church is submitted once, so shards are
safe. Duplicates are tolerated by decision; a unique index on a URL hash would make it unconditional.

### Open
1. Backfill sweep to completion (8,057 productive churches) + re-run `report_edition_coverage.py` to
   confirm the Feb→Aug hole refilled.
2. Discovery tier (~13,383 never-yielded churches, ~1% hit rate) after the productive tier.
3. `/health` 500s on the dashboard — `dashboard/Dockerfile` copies only `dashboard/app/`, so
   `src/utils/health_checks` is absent. `healthCheckPath` is empty so nothing depends on it.
4. Name quality: `ministry_contextual`/`low` rows contain sliding-window permutations of one name run
   ("Weber Eric Stuhlsatz", "Eric Stuhlsatz Eddie"). High-confidence rows are clean.

## 🔒 2026-08-05 — DASHBOARD BEHIND A LOGIN (commit a95d317)
Every view requires a session; only `/login` and `/health` are public (`/debug/*` and
`/bulletin/*/api/*` are gated). Credentials in **`DASHBOARD_USERS`** on `srv-d6li8dtm5p6s73chuh7g`
(`user:pass,user:pass`), never committed. Username case-insensitive, password exact via
`compare_digest`. **Unset var fails closed (503), not open.** `SECRET_KEY` was already a stable random
value — regenerating it would drop every session. Regression suite: `tests/test_dashboard_auth.py` (20 checks).

## ⚠️ 2026-06-18 — MASS-TIMES SOURCE PIVOT (CatholicIndex dead → DiscoverMass)

**CatholicIndex.org is unscrapeable.** It went behind a hard Cloudflare **Managed Challenge**
(`cf-mitigated: challenge`) on all content pages (`/churches/*`, `/mass-times/*`) ~2026-04. The
daily/weekly scrape has been a **silent no-op since ~2026-04-22** (exit 0, 0 rows): `scrape_log`
shows `scrape=FAIL` every run since 2026-04-02, and 100% of churches were >30d stale. Nothing
in-house beats the challenge (requests, curl_cffi, Playwright-headless, undetected-chromedriver-
headed, residential proxy all 403). Only a paid scraping-browser (BrightData) would — not pursued.

**New source = DiscoverMass.com** (open, plain `requests` 200, ~20,283 parishes via WP sitemap):
- `src/scrapers/discover_mass.py` — `enumerate_parishes(state)` (sitemap, slug-suffix state filter)
  + `parse_parish()` (mirrors `catholic_index.scrape_church_detail` output).
- `discovermass_to_db99.py --state XX [--commit]` — match-or-insert into db99. Matches existing
  churches by **geo ≤150m + normalized name/city** (preserves `church_id` + bulletin links),
  inserts net-new, flags ambiguous (skipped). Per-church schedule replace ONLY when DM has ≥1
  service (0-service parishes keep old schedule); per-state stale-service cleanup; explicit commit.
- `run_discovermass_all.py` — loops states (**Cherry Road states first**), idempotent/self-healing.
- **Weekly pipeline Step 1 now calls `run_discovermass_all.py`** (was the dead CatholicIndex
  `run_statewide.py --detail-only`). Bulletins/rescore steps unchanged.
- **Fetch config: DIRECT, NO PROXY, `SCRAPE_DELAY_SECONDS=6` on `church-mass-times-cron`.**
  The 711 residential proxy proved UNRELIABLE for DiscoverMass — a Render one-off job through the
  proxy exited 0 but wrote **0 rows** (every proxied fetch failed; a local re-run also collapsed
  86→2). Direct fetching at ~6s is reliable (verified). `PROXY_URL` was REMOVED from the cron.
  http.py retries cover transient 503s. Slower (~10-15h national) but self-heals across weekly runs.

**VERIFIED end-to-end (2026-06-18):**
- **AR pilot (local commit path):** 86 parishes refreshed + 6 net-new; stale CatholicIndex services
  retired where DM provided a schedule; 1 church (0-service) kept its old schedule by design.
  Merged 2 pre-existing AR duplicate church rows (`scripts/merge_ar_duplicate_churches.py`), all
  bulletin names preserved.
- **ME (Render one-off, DIRECT prod path):** 144 churches refreshed, 131 with active DM services
  (vs 0 through the proxy) — confirms the direct path writes cleanly from Render's datacenter IP.

**Cherry Road geography** is synced from Limpar (source of truth) into db99 `cr_market_shape`
via `scripts/refresh_cherry_road_shapes.py` (96 projects / 845 shapes; 599 cities). Coverage diff:
`scripts/cr_coverage_report.py`. Both built "the right way" — re-run to pick up Limpar changes.

**CR ROLLOUT COMPLETE (2026-06-18): all 21/21 Cherry Road states, 9,444 churches refreshed**
(were frozen since April) via `run_discovermass_all.py --cr-only`. Counts incl. NY 1,438 · TX 1,161 ·
IL 974 · OH 792 · MI 728 · MN 599 · MO 429 · IA 419 · IN 394 · KS 323 · NE 269 · CO 234 · NM 206 ·
OK 162 · GA 160 · AL 150 · ME 144 · ID 91 · AR 86 · MA 625 · UT 60.

**NATIONAL (non-CR) NEW-CHURCH RUN IN PROGRESS (2026-06-22):** manual Render one-off
`run_discovermass_all.py --states <29 non-CR states>` (`job-d8sm98i8qa3s73bfvfg0`) — inserting
net-new churches + mass times + events for states never scraped via DiscoverMass. Landing live
(AK 58, AZ 203 … total climbing 9,444 → 9,700+). Run is Render-side, independent of any CLI session.

**⚠️ CRON DESIGN FLAW (action needed):** the scheduled cron runs `run_weekly_pipeline.py` → Step 1
`run_discovermass_all.py` (ALL states, CR-first), which **re-scrapes every church every run** at 6s
each (~30h national). A daily/weekly fire can't finish — it restarts CR-first and dies. Evidence: db99
shows DiscoverMass writes only on 2026-06-18 (manual) + 06-19 (partial cron) — **nothing 06-20/21/22,
still only 21 states**. So the national sweep is NOT self-completing via cron; it needs manual runs
today. **Fix (proposed, not yet done):** shard states by day-of-week (each fire does ~7 states →
full national weekly, finishes within limits) + a stale-only refresh pass instead of full re-scrape.

Some CR micro-towns have no parish of their own (served by neighbor towns) — a parish-existence
limit, not a coverage bug. Live count: `DB_HOST=10.10.0.8 python scripts/cr_coverage_report.py`.

## Overview
Catholic church mass times, bulletins, and extracted names dashboard.
- **Repo**: benashkar/catholic-mass-times-scraper
- **Database**: `church_scrapes` on db99 (MySQL, us-east-1 Virginia)
- **Dashboard**: Render web service (Virginia, Docker), reads from db99 via AWS Secrets Manager
- **Pipeline**: Two Render cron jobs — daily mass times + weekly bulletins
- **Name Engine**: `benashkar/names_people_matcher` (`C:\Users\cashk\OneDrive\names_people_matcher`)

## Current Status (2026-04-03)

### COMPLETED
1. **Dashboard live** — 50 states, medium+high confidence names, shareable page URLs
2. **SQL rescore** — ref_ssa_names + ref_census_surnames on db99, junk blocklist, lowercase cleanup
3. **Column detection** — Integrated into `run_bulletin_scraper.py` via `src/utils/pdf_columns.py`
4. **Pattern 6 fix** — Changed from 200-char proximity to line-based matching (keyword line + next 5 lines)
5. **Direct-to-db99 mass times** — `scrape_to_db99.py` reads church list from db99, scrapes CatholicIndex, UPSERTs directly
6. **Direct-to-db99 bulletins** — `extract_bulletins_to_db99.py` discovers bulletin PDFs, downloads to memory (BytesIO), extracts text with column-aware pdfplumber, runs 6 regex patterns + NER veto + couple detection, inserts names directly to db99. Fully stateless.
7. **Daily pipeline** — `run_daily_pipeline.py` orchestrates: mass times scrape → bulletin extraction → rescore → health check → redeploy
8. **Multi-model name quality engine** (Phase 1-2 complete):
   - **nameparser** library replaces hand-rolled `parse_name_parts()` — handles "Fr. John M. Smith Jr." correctly
   - **probablepeople** detects couple names ("John & Mary Smith") → splits into two records
   - **NER veto gate** (spaCy en_core_web_lg) — names NER doesn't recognize as PERSON get downgraded to low/suspect
   - **SSA gender data** — male_count, female_count, male_ratio columns for husband+wife detection
   - **Expanded SQL blocklist** — ~100+ additional junk terms
9. **NER rescore of all existing data** (Phase 4 complete):
   - 2,339,934 names checked across 50 states
   - 1,556,276 false positives eliminated (66.5%)
   - ~783,658 quality names remaining on dashboard
   - Ran as 49 individual state jobs on Render (each 10s-30min depending on state size)
10. **Health checks** — 9 automated checks (table counts, confidence distribution, junk rate, known junk, lowercase names, scrape recency, empty names, backfill coverage, recent pipeline runs)
11. **PrivateLink fixed** — VPC endpoint works from Render
12. **Debug endpoint** — `/debug/logs` on dashboard for cron job visibility (Render API lacks job log access)
13. **Fix column mismatch** — `extract_bulletins_to_db99.py` and `rescore_with_ner.py` had wrong column names vs actual db99 schema (written against PG schema, but db99 tables created by `sync_to_db99.py` have different names). Fixed: `source_type`→`discovery_source`, `source_url`→`bulletin_page_url`, `url`→`pdf_url`, `extracted_at`→`text_extracted`, `extracted_context_category`→`category`, `last_scraped_at`→`discovered_at`. Added `title`+`middle_name` to INSERTs. Idempotent `ensure_schema()` auto-adds `role` column on first Render run.
14. **Dashboard: dynamic stats + CSV export + shareable filter URLs** — Stats cards (Total Names, Unique Names) now update live when any filter changes. Confidence filter moved to server-side SQL for correct paginated results. CSV download button exports filtered view. All filters (confidence, city, church, category, search) sync to URL query params for shareable links (e.g. `/bulletin/ohio/?confidence=high&city=Columbus`).
15. **Code quality cleanup** — Fixed SQL injection in rescore watermark (f-string → parameterized query). Consolidated 190 individual blocklist UPDATEs into single query. Consolidated 70 junk first/last word UPDATEs into 2 IN() queries. All cleanup steps scoped to watermark in `--new-only` mode.
16. **Last Updated column on bulletin index** — Shows `MAX(bulletin_pdf.downloaded_at)` per state. Green if within 7 days, red if stale — monitors whether the weekly bulletin pipeline is running.
17. **CSV export limit raised to 500K** — Was 50K, silently truncating large states (IL 253K, WI 64K).
18. **Schema page** (`/schema`) — ERD diagram (Mermaid.js), connection info (Render + local), Python quick start, live table row counts, key views. Full-screen ERD at `/schema/erd.html`. Standalone `docs/erd.html` committed to repo per global rules.
19. **Weekly cron deduplication** — Added `--bulletins-only` flag to `run_daily_pipeline.py`. Weekly Tuesday cron now skips mass times (already handled by daily cron), focuses on bulletin PDF collection + NER + rescore.
20. **Self-healing scraper pattern (3 layers)** — Implemented 2026-04-03:
    - **Layer 1 — Inline fallback parsers**: `src/parsers/fallback_parsers.py` with 4 functions (`parse_first_last_from_person_name`, `parse_category_from_context`, `parse_role_from_context`, `parse_title_from_person_name`). Called in `extract_bulletins_to_db99.py` after primary `parse_name_parts()` when fields are empty. Shared constants extracted to `src/parsers/bulletin_constants.py`.
    - **Layer 2 — Auto-backfill**: `backfill_empty_fields.py` re-parses existing DB records with empty critical fields. Runs as Step 2b in `run_daily_pipeline.py` (non-critical, <60s target, 50K row limit).
    - **Layer 3 — Diagnostic agent**: `/health` endpoint upgraded to comprehensive JSON with 9 structured checks. Claude Code scheduled trigger (`trig_013BWLSXWDbY4tFCXo9HiwDB`) runs at 5 AM UTC daily (except Tuesday). Fetches `/health`, diagnoses issues, opens fix PRs for blocklist additions, sends Telegram summary via Biscotcho bot.
    - **Telegram integration**: `src/utils/telegram.py`, env vars set on both Render cron services.
    - **38 tests** in `tests/test_fallback_parsers.py`.

### CURRENT DATA
- 2.6M bulletin_name rows total
- ~784K medium+high confidence (dashboard-visible) — down from ~1.2M after NER cleanup
- 23,046 churches, 280K services
- ref_ssa_names (100K with gender data), ref_census_surnames (162K) on db99

## Render Services

| Service | ID | Type | Plan | Command |
|---------|-----|------|------|---------|
| catholic-church-dashboard | `srv-d6li8dtm5p6s73chuh7g` | web | starter | gunicorn |
| church-mass-times-cron¹ | `crn-d6s8st3uibrs73e7b740` | cron (`0 3 * * 0,1,3,4,5,6`) | **standard** (2GB) | image CMD `python run_weekly_pipeline.py` (no startCommand override; use `--skip-bulletins` for mass-times only). autoDeploy=yes |
| church-weekly-bulletins | `crn-d6s8t02a214c73bt62s0` | cron (Tue 3AM) | standard | `python run_weekly_pipeline.py` |

¹ Render service display name is `church-mass-times-cron` (the original "church-daily-scrape" label is stale). Confirmed 2026-06-14.

## Pipeline Architecture

### Daily (stateless → db99)
`python run_daily_pipeline.py`
1. Scrape mass times from CatholicIndex → UPSERT to db99
2. Extract bulletin names → discover PDFs → download to memory → extract text → NER veto → UPSERT to db99 (with Layer 1 inline fallback parsers)
2b. Backfill empty fields from context (Layer 2 self-healing)
3. Rescore names via SQL (`--new-only`)
3b. Refresh bulletin_state_stats
4. Health check
5. Trigger dashboard redeploy
+2h: Layer 3 diagnostic agent checks `/health`, sends Telegram summary

### Weekly (Tue 3AM)
`python run_weekly_pipeline.py`
1. Scrape mass times (all 50 states)
2. Bulletin extraction (all states via `extract_bulletins_to_db99.py`)
3. Full SQL rescore
4. Health check + redeploy

### Name Quality Pipeline (per extracted name)
```
1. Extract via 6 regex patterns (staff, honorific, section-header, ministry, intention, prayer)
2. nameparser: parse into first/middle/last/title/suffix
3. probablepeople: detect couple → split if Household type
4. Dictionary score: SSA first + Census last (fast lookup)
5. NER veto: spaCy en_core_web_lg — downgrade if not PERSON entity
6. Consensus: dictionary + NER agreement → high/medium/low
7. SQL blocklist cleanup (runs after sync)
```

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scrape_to_db99.py` | Mass times: CatholicIndex → db99 (stateless) |
| `extract_bulletins_to_db99.py` | Bulletins: discover → download → extract → NER → db99 (stateless) |
| `run_daily_pipeline.py` | Orchestrates daily: scrape + bulletins + rescore + health + redeploy |
| `run_weekly_pipeline.py` | Orchestrates weekly: all states, full rescore |
| `rescore_names_sql.py` | SQL-based rescore + blocklist + stats refresh |
| `backfill_empty_fields.py` | Layer 2: re-parse empty fields from person_name/context |
| `rescore_with_ner.py` | One-time NER rescore of existing names (ran 2026-03-19) |
| `run_bulletin_scraper.py` | Original file-based bulletin pipeline (local use) |
| `run_job.py` | Wrapper that captures stdout/stderr → logs to db99 scrape_log |
| `scripts/prepare_name_reference.py` | Regenerate SSA (with gender) + Census reference data |

## RECENTLY COMPLETED (2026-03-21)

### Couple detection pass (Task 1)
- Column mismatch fixed, `role` column auto-added by `ensure_schema()`
- Triggered one-off job on weekly cron: `python rescore_with_ner.py --couples-only`
- Job ID: `job-d6vj5e7diees73d45cn0` — running on Render

### Downgrade daily cron (Task 2)
- Daily cron downgraded from standard ($25/mo) to starter ($7/mo)
- Dashboard upgraded to standard (2GB) — was crashing on starter (OOM)

### Re-extract states (Task 3)
- Already handled by weekly pipeline — `extract_bulletins_to_db99.py` runs every Tuesday
- Existing PDFs tracked by `text_extracted` boolean, won't be reprocessed
- New PDFs get column detection + Pattern 6 + NER veto automatically

### Optimize rescore (Task 5)
- Added `--new-only` flag to `rescore_names_sql.py` — uses `bulletin_name_id` watermark
- Daily pipeline switched from `--cleanup-only` to `--new-only`
- Full rescore (~11 min on 2.6M rows) only on weekly; daily rescores just new names (seconds)

## RECENTLY COMPLETED (2026-06-13 / 06-14)

### Cloudflare 403 fix — catholicindex.org (commit `1189922`)
- **Root cause:** CatholicIndex.org sits behind Cloudflare, which returns **HTTP 403** to self-identifying bot User-Agents. Our honest `CatholicMassTimesScraper/1.0` UA was hard-403'd, silently breaking the mass-times scrape. Verified: honest UA → 403, Chrome UA → 200 (156 KB) **from the same IP** → a UA block, not an IP block.
- **Fix:** switched `config/settings.py` `USER_AGENT` default to a realistic Chrome UA (used by `src/utils/http.py`). The bulletin paths already used a browser UA.
- **Verified end-to-end on Render's datacenter IP:** one-off probe job succeeded (fetched >50 KB), then a real `run_weekly_pipeline.py --skip-bulletins` job ran ~6 min and **succeeded** through the db99 sync. So the datacenter IP is **not** IP-blocked once the UA is fixed.

### Env-driven proxy support — wired but DORMANT (commit `1189922`)
- Added optional rotating-residential-proxy support across **all** request paths: `src/utils/http.py`, `extract_bulletins_to_db99.py` (PDF downloads), `run_bulletin_scraper.py` (requests session **+ Playwright launch**), `run_resolve_urls.py`. Canonical `PROXY_URL`/`PROXIES` defined in `config/settings.py`.
- **When `PROXY_URL` is unset (the default) everything goes direct** — this is a no-op until enabled on a Render cron. `PROXY_URL` is intentionally **NOT set** on the church crons because we confirmed we don't need it (UA fix alone works from Render).
- To enable later: set `PROXY_URL` to the working 711 endpoint (the case-sensitive `-country-US` URL — see shared-proxy notes). Zero code change required.

### Cron Docker build fix (commit `4ba2b8a`)
- The cron image build was failing at `python -m spacy download en_core_web_lg` with `ModuleNotFoundError: No module named 'click'` — `spacy>=3.7` is unpinned and the resolved typer/spacy stack stopped pulling `click` transitively (last green build had been 2026-05-12). Added explicit `click>=8.1` to `requirements.txt`. Build is green again.

## NEXT TASKS (priority order)

### 0. (watch) Re-pin spacy stack to prevent future build drift
- The `click` fix is surgical; `spacy>=3.7` is still unpinned and could drift again. Consider pinning the spacy/typer/click stack to a known-good set.

### 1. Mass times data normalization
- Events/locations returning junk — some events don't take place at the church
- Need to identify address per event or weed out off-site events
- See plan below

### 2. Monitor couple detection results
- Check job `job-d6vj5e7diees73d45cn0` completion
- Verify split couples appear correctly on dashboard

### 3. Add role column data to dashboard
- `role` column now exists on db99 (added by `ensure_schema`)
- Dashboard table has role column (index 1) but currently shows empty string
- Update `data_loader.py` to SELECT role from view, update view if needed

## DB Local Access
- **Always use** `DB_HOST=10.10.0.8` env var for local connections
- VPC endpoint from Secrets Manager doesn't work locally (only from Render)
- Example: `DB_HOST=10.10.0.8 python tests/test_pipeline_health.py`

## Debug
- `/debug/logs` on dashboard: shows recent scrape_log entries
- `/debug/logs?type=ner_rescore&limit=50`: filter by type
- `run_job.py` wrapper: captures stdout/stderr from any script → logs to db99
