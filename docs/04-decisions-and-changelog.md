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
