# AI Site Scan: decisions and changelog

## Key decisions

| Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Scan engine | geo-optimizer-skill (Python, MIT) | open-agent-readiness (Node), geolint, LLMScout, own checks | Most complete open-source audit, scored 0 to 100, ranked recommendations, built-in SSRF protection |
| Hosting | Vercel (Python + Node functions) | Run only on the user's computer; artifact page | A browser page cannot fetch other sites (CORS), so a server is needed. Vercel was already connected |
| "AI view" | Raw HTML with no JavaScript, as text | Headless browser render | Matches what most AI crawlers and fetch tools get |
| Visitor screenshot | Own headless Chrome function | WordPress mShots (first version), paid screenshot APIs | mShots failed on the user's phone. Own function has no outside dependency |
| History | Browser localStorage, 30 scans | Server database with login | Private, no accounts, no database cost. Limit: one browser only |
| Fix guide | Templates in the browser, filled with site data | AI-written guide per scan | No cost per scan, no invented facts. TODO marks where only the owner knows the value |
| Access | Public, no login, basic rate limit | Login, Vercel Firewall rule | Simple to start. Firewall rule is the next step if traffic grows |
| Writing style | Short, plain English, no long dashes | | Owner preference |
| Scan speed (v2) | Engine async path + parallel probes, engine CDN check fed from our probes | Keep the one-by-one engine; own fast scanner instead of the engine | Same score, 4 to 6 times faster, no extra fetches, engine stays the source of the score |
| Extra checks (v2) | Own pure functions in `api/_checks.py`, shown as "extra check", not in the score | Add them to the score; fork the engine | The score stays the engine's, so it can be compared and trusted. Extra checks are testable offline |
| Bot imitations (v2) | Report only AI-vendor bots as a problem; show Google, Bing and Apple in the table | Report every refused bot | Many sites refuse imitations of verified bots on purpose, so it would raise false alarms |
| Interface (v2) | Playful motion and a mascot in plain CSS and JS | Animation library; separate asset files | Keeps one file with no build step. Motion is off for "reduce motion" |
| One scan button (v2.1) | One address-bar button: Rescan for the same site, Scan for another; Rescan skips the cache; no separate New scan or second Rescan | Keep both buttons and rename them; keep a New scan button in the top bar | Two primary buttons and three ways to rescan confused people. One control that names what it will do needs no explaining |
| What the AI missed (v2.1) | Free section right after the score, from measured data only; optional conventions kept apart | Show best-practice lists; lock the details now | It shows owners what they are missing in the agent's own words. Payment is not built; the section is the teaser for it |
| Local business location (v2.2) | A check that flags a local business whose page text never says where it is. Critical when the page clearly depends on a place (local business data, opening hours, or 5 signals), high for a shop or delivery business, medium when only a country or area is named. Not in the score. Software and media sites are never flagged | A score penalty; flag every site without an address; ask an AI model | The score stays the engine's. A flag on every site would be a false alarm, so it applies only to a business that depends on a place. Found on a real site: the address was only in hidden JSON-LD, so most AI tools never saw it |
| New interface (v2.3) | One narrow column. One list of issues (each issue once: what the AI saw, how to fix), severe first. The AI journey is a strip that filters the list. Details (AI view, bots, platforms, key facts, score breakdown) are closed panels. The screenshot loads only when its panel opens | Keep the separate "What the AI missed", "Start here" and "All fixes" sections; remove the mascot; keep the wide layout | Daniel asked for less visible text, everything still reachable, severe issues first. Showing one issue three times (missed, top fixes, all fixes) was the main source of text. The mascot stays small and calm |
| Agent readiness (v2.4) | A separate function `/api/agent` and a closed Details panel with 23 rows and a level from 0 to 5. The page asks for it after the scan. Only WebMCP uses a real browser, once, when the panel opens. Not in the score | Put the checks in `/api/scan`; add them to the score; run every check in Chrome; depend on isitagentready.com's own scanner | The scan stays at 1 to 3 seconds and the score stays the engine's. Files, headers and DNS can be read exactly and cheaply. Only WebMCP needs a running page. We apply the public standards and the rules in isitagentready.com's public documentation ourselves, and compared the results with its website on four real sites |
| Payments (v2.5) | Polar as merchant of record. One-time products with a License Key benefit. Checkout links, no own checkout page. The key unlocks the guide; one activation per site (label = host) | Stripe (Daniel would be the seller and handle tax himself); Dodo Payments; Lemon Squeezy; own database of buyers | Polar handles tax and receipts in every country, pays out to Israel, and keeps the keys. No database to run |
| Accounts (v3) | Supabase Auth and Postgres, one serverless function for all account actions | Own email codes and a KV store; Clerk or Auth0 | Free at this size, row security, Google login built in, and one function keeps the project under Vercel's function limit |
| Key delivery (v2.5) | `/api/claim`: the success URL carries the checkout ID; the server reads the checkout, opens a customer session and reads the new key; the page applies it | Buyer pastes the key from Polar's email; webhook that emails the key | No copy and paste, so "instant delivery" is true. Paste stays as a fallback |
| Merchant review pages (v2.5) | Pricing, contact, refunds and delivery, terms, privacy, built from one template with the same footer on every page | Policy generator text; one long legal page | Polar, Dodo and card networks check that prices, delivery, refund and cancellation terms, a contact route and the seller's identity are on the live site and match the account form |

## Changelog

All times Asia/Jerusalem, 24 Nov 2026.

| Time | Change |
|---|---|
| 22:50 | Requirements agreed: Vercel, geo-optimizer-skill, repo and project named ai-site-scan, text AI view, public, English |
| 23:03 | First version live: scan API, AI view, bot access test, ranked actions, single-page frontend |
| 23:05 | JavaScript risk check tuned (was too strict); engine "blockers" set to high, not critical |
| 23:07 | Clear 502 error when the site cannot be reached |
| 23:08 | Fixed crash on unknown domains (DNS `OSError` on Vercel) |
| 23:10 | Clear report when a site blocks all scans (for example nytimes.com) |
| 23:15 | Duplicate Vercel project from "Import" deleted. `framework: null` pinned. Git auto-deploy connected and verified |
| 23:25 | Screenshot moved from mShots to own headless Chrome function (`api/shot.js`). Fixed ESM import |
| 23:40 | New scan button, History tab, friendlier report (verdict, top 3, filters, "why it matters"), Markdown fix guide export, share link, copy summary. Long dashes removed from all output |
| 23:42 | Wording for pages with no text ("No words are in the HTML") |

### 25 Nov 2026, version 2.0.0

All times Asia/Jerusalem.

| Time | Change |
|---|---|
| 00:40 | Scan engine: async audit, parallel probes, engine CDN check fed from our probes. Scores identical, 4 to 6 times faster |
| 00:40 | 14 bots instead of 3, with bot check page detection, size comparison and roles (training, search, on request) |
| 00:40 | New checks: sitemap (declared but broken), server speed and page size, headings, link text, language, title and description length, nofollow, cookie banner first, bots get a different page, soft 404 |
| 00:40 | Fixes: 404 and 5xx are no longer shown as "blocked"; AWS WAF and Cloudflare bot check pages are detected; pages with little text and no JavaScript are no longer called "needs JavaScript" (for example paulgraham.com and example.com); non-UTF-8 pages are decoded correctly; pages up to 10 MB are read (cnn.com was 6.7 MB); the "since last scan" number compared a scan with itself |
| 00:40 | New interface: animated hero with mascot, scan stage, count-up score ring, confetti, reveal on scroll, "Already working", bot cards, platform scores, tick-off fix list with progress, "fixed since last scan", trend line, 8 key facts, sticky section nav, back to top |
| 00:40 | Version shown in the footer. Offline tests added (`tests/`) |

### 25 Nov 2026, version 2.1.0

All times Asia/Jerusalem.

| Time | Change |
|---|---|
| 01:07 | New section "What the AI agent missed" right after the score: journey bar (get in, read, understand, trust, find more), a card for every blind spot, optional extras apart, and a call to action for the full report and fix guide |
| 01:07 | One smart Scan/Rescan button in the address bar. The top-bar New scan button and the second Rescan button are gone. Rescan always asks for a fresh scan (`fresh=1`), because a cached answer made a rescan look like it did nothing |
| 01:07 | Cache age shown ("cached, 3 min ago"); an "all fixes ticked" banner with Rescan now; clicking the address selects it all |
| 01:07 | Wording: slow servers and big pages are described with hedged claims, not unverified numbers. 12 new tests (60 in all) |

### 25 Nov 2026, version 2.2.0

All times Asia/Jerusalem.

| Time | Change |
|---|---|
| 22:40 | New check "Say where your business is" (`api/_location.py`). Found on a Hebrew chocolate workshop page: the address was only in hidden JSON-LD, the page text named no place, and the engine scored the page 80 with no hint. Now it is a critical fix, first in "Start here", with a card "Could not tell where you are" in the Understand stage |
| 22:40 | Red alert under the score, a new verdict ("AI can read this page, but cannot tell where the business is"), a mascot line, no confetti while the problem stands |
| 22:40 | The fix guide gets a first step with a visible address block, a one-sentence template in Hebrew or English and LocalBusiness JSON-LD filled from the site's own data |
| 22:40 | Extras: empty map pin (`geo` with no latitude and longitude), Content Signals line in robots.txt (optional, never counted). 14 new tests (74 in all) |
| 22:40 | Checked on about 60 real pages: only the page above got a critical flag. Software, media and documentation sites were never flagged. Three national brands got a medium flag (they name only the country) |

### 25 Nov 2026, version 2.3.0

All times Asia/Jerusalem.

| Time | Change |
|---|---|
| 22:59 | The whole interface is rebuilt: one narrow column, a score card with the verdict, severity pills and the main button, then the AI journey strip, then **Fix first** (critical and high), **More to improve**, **What works**, and five closed **Details** panels |
| 22:59 | The three lists (What the AI missed, Start here, All fixes) became one list. API: each missed item now has `fix` (the action category) and some have `hint`; the `location` action has `lead` and `note`; its steps are shorter |
| 22:59 | Less text: shorter verdicts, one-line summaries on rows, steps shown three at a time, the home screen is a headline, the address box and examples. The screenshot loads only when its panel is opened (it took 5 to 20 seconds on every scan before) |
| 22:59 | Reports saved by older versions still open (the page maps missed items to fixes itself). 2 new tests (76 in all) |

### 25 Nov 2026, version 2.4.0

All times Asia/Jerusalem.

| Time | Change |
|---|---|
| 23:37 | New **Agent readiness** panel in Details: 23 checks of the standards AI agents look for, the same set that isitagentready.com tests (robots.txt, sitemap, Link headers, DNS-AID, Markdown negotiation, Search for Agents, AI bot rules, Content Signals, Web Bot Auth, API catalog, OAuth and OIDC, OAuth Protected Resource, Auth.md, MCP server card, A2A agent card, Agent Skills, WebMCP, ARD, Poppy, x402, MPP, UCP, ACP). A level from 0 (Not Ready) to 5 (Agent-Native), what the next level needs, and for every check what we asked, why it matters, how to fix it and a link to the standard. It does not change the score |
| 23:37 | Two new functions: `api/agent.py` (files, headers and DNS over HTTPS, about 25 requests to the site) and `api/webmcp.js` (headless Chrome with a WebMCP stand-in). The page asks for the first after the scan and for the second when the panel opens. The result is saved in the history |
| 23:37 | The fix guide gets an optional step for Content Signals, a Link header and Markdown for agents |
| 23:37 | The WebMCP function bounds every wait and sends each tool out of the page the moment it registers. Found on the first preview: vercel.com froze our small Chrome and the function hit the 40 s platform limit. Now a heavy page answers in time, or shows as "unable", never as a platform timeout |
| 23:37 | Compared with the isitagentready.com website on four sites: the level matched on all four (a small shop at level 1, developers.cloudflare.com at level 5, isitagentready.com at level 5, vercel.com at level 1). Differences are listed in the roadmap |
| 23:37 | 32 new tests (108 in all) |

### 27 Nov 2026, version 2.5.0

All times Asia/Jerusalem.

| Time | Change |
|---|---|
| 21:00 | Own domain getaisitescan.com (www redirects to it). robots.txt with Content Signals, sitemap.xml, llms.txt, llms-full.txt, feed.xml, /.well-known/ai.txt, /ai/summary.json (with the WebMCP tool), /ai/faq.json, /ai/service.json, logo, favicon, Open Graph image |
| 21:00 | Home page: brand line in the H1, JSON-LD graph (Organization, WebSite, WebPage, WebApplication with the 3 offers, FAQPage), a visible "What AI Site Scan checks" section with pricing and 7 questions, WebMCP attributes on the scan form. Own score on a local copy: 62 before, 92 after |
| 21:00 | New pages: /pricing, /contact, /about, /refund (refunds, cancellation and delivery), /terms, /privacy. Same header and footer on every page. Business details: Daniel Rojansky, sole trader in Israel, trading as Daniel Consulting. Polar named as merchant of record |
| 21:00 | Paywall: the first 3 fixes stay free, the rest and the fix guide need a license key. $19 for one site, $49 for 10 sites. It stays off until the Polar settings exist in Vercel |
| 21:00 | New functions: `/api/config` (public settings), `/api/unlock` (license key activation per site), `/api/claim` (key delivery after checkout), `/api/email` (report and llms.txt by email, optional reminder), `/api/polar-webhook` (sale event and email), `/api/cron-selfcheck` (daily scan of our own site). Every Polar call pins `Polar-Version: 2026-10` |
| 21:00 | Analytics and ads: PostHog without cookies until the visitor accepts, Google Ads tag only after consent. Both stay off until their settings exist |
| 21:00 | Landing pages /llms-txt and /ai-robots-txt (the same app with their own headline) |
| 21:00 | 13 new handler tests with a fake Polar (claim, unlock, config, webhook signature) and a browser test of the paywall, the return from checkout and every page |

### 28 Nov 2026, version 3.0.0

| Change |
|---|
| Accounts: log in with an email code or Google (Supabase). The free check still needs no login |
| New plans: Full report $19 once (1 site, 1 rescan within 30 days) and Pro $29 a month (10 reports). License keys and the 10-site pack are gone |
| Reports are emailed (guide attached) and saved in "My reports", where they open again later |
| New `/api/account` function (me, checkout, unlock, save, sync, portal, report). `/api/claim` and `/api/unlock` removed |
| Webhook gives and removes credits. Pricing, terms, refund and privacy pages describe the subscription, renewal and cancellation |
| Tests: 13 account and webhook tests with a fake Supabase and Polar, credit rules tested in the database, browser test of scan, login, pay, unlock, workspace |

### 28 Nov 2026, after launch

| Change |
|---|
| Scoring-engine credit removed from public pages, llms files, FAQ data and scan output |
| Webhook: sale email and PostHog revenue sent once per order (`webhook_events`), even when Polar repeats a delivery |
| $0 orders (100% discount) are marked "Test order (free)" and send no revenue event; the buyer still gets the report |
| Headers: `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `Permissions-Policy`, and a report-only Content-Security-Policy |
| Caching: `/vendor/` for 1 year (versioned file names), logo, OG image and favicon for 1 day |
| Product IDs come from `POLAR_PRODUCT_SINGLE` and `POLAR_PRODUCT_PRO`. The old `POLAR_CHECKOUT_*` and `POLAR_PORTAL_URL` settings are deleted |
| Report email attaches the guide as `text/markdown`. The score counts up only the first time a result is shown |
| Google login live (Google Auth Platform app "AI Site Scan", in production) |
| Tests moved into the repo: `tests/test_accounts.js` (16), `tests/ui_flow.py` with `tests/devserver.py` |

### 28 Nov 2026, version 3.0.1

| Change |
|---|
| "Scan again" in My reports uses the included rescan in one click |
| The lead email ("Email me this report") links to the full fix guide again. It read the deleted `POLAR_CHECKOUT_SINGLE` setting |
| Daily self-check also emails when the paywall, Pro or email is off, or the database does not answer |
| Report-only Content-Security-Policy allows PostHog's assets host (it was the only violation seen) |
| Tests: 18 account tests (lead email, self-check), browser test covers the one-click rescan |

### 28 Nov 2026, version 3.0.2

| Change |
|---|
| The checkout sends the visitor's PostHog ID (as `reference_id`) and first-visit campaign tags (`utm_*`) to Polar. The webhook's purchase event then joins the visitor's funnel, and the sale email names the source. Campaign tags are kept across pages only after the visitor accepts cookies |
| Tests: 19 account tests |

## Test results at launch

| Site | Score | Notes |
|---|---|---|
| anthropic.com | 57 | Readable, no JSON-LD, no llms.txt |
| react.dev | 63 | Readable (Next.js SSR), has llms.txt |
| excalidraw.com | 50 | JavaScript-only, critical |
| duet121.com | 45 | JavaScript-only (0 words in HTML), critical |
| nytimes.com | 0 | Blocks all cloud traffic (HTTP 403) |
| nonexistent domain | n/a | 400 "domain could not be found" |
| 169.254.169.254 | n/a | 400, SSRF blocked |

## Test results version 2.0.0 (old engine time to new engine time, same score)

| Site | Score | Time | Notes |
|---|---|---|---|
| bluevine.com | 77 | 6.3 s to 1.1 s | |
| anthropic.com | 57 | 3.2 s to 0.8 s | |
| react.dev | 63 | 4.6 s to 1.2 s | Extra: two H1, heading levels skip |
| excalidraw.com | 50 | 3.9 s to 0.9 s | JavaScript-only, critical |
| stripe.com | 86 | 8.6 s to 2.0 s | 14 of 14 bots, 8 things working |
| paulgraham.com | 35 | 10.2 s to 2.0 s | Little text (not JavaScript), declared sitemap returns 404 |
| duet121.com | 45 | 7.8 s to 1.6 s | JavaScript-only |
| wikipedia.org | 69 | 9.4 s to 4.9 s | 5 bots refused (ClaudeBot and the IP-checked ones) |
| nytimes.com | 0 | 1.2 s to 0.9 s | Blocks all cloud traffic |
| cnn.com | 57 | n/a | HTML is 6.7 MB, all 8 AI bots refused (HTTP 451) |
| amazon.com | 36 | n/a | AWS WAF bot check page detected |
| mozilla.org | 47 | n/a | Bots get 42% of the browser's page (no language header) |
