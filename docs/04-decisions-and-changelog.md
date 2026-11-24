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
