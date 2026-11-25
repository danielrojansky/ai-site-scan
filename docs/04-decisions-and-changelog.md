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
| 23:37 | Compared with the isitagentready.com website on four sites: the level matched on all four (a small shop at level 1, developers.cloudflare.com at level 5, isitagentready.com at level 5, vercel.com at level 1). Differences are listed in the roadmap |
| 23:37 | 32 new tests (108 in all) |

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
