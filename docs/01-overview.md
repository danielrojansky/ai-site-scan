# AI Site Scan: overview

Last updated: 25 Nov 2026, 23:35 (Asia/Jerusalem). App version 2.4.0
Owner: Daniel Rojansky

## What it is

AI Site Scan is a free web app. You enter a website address. The app tells you how well AI tools (ChatGPT, Claude, Perplexity, Google AI Overviews) can reach, read and understand that site, and what to fix.

## Links

| Item | Value |
|---|---|
| Live app | https://ai-site-scan.vercel.app |
| GitHub repo | https://github.com/danielrojansky/ai-site-scan (branch `main`) |
| Vercel project | `ai-site-scan` (id `prj_mmAM1HwKQduftTI2wD2Rd9AjdcZt`) |
| Vercel team | `danielrojansky-8273s-projects` (id `team_xh9PcZyIa6lW6nLv8G0YrACi`) |
| Scan engine | geo-optimizer-skill 4.28.0, MIT, https://github.com/auriti-labs/geo-optimizer-skill |

## What the user gets

1. **Score (0 to 100)** with a plain-language verdict, for example "AI tools see an almost empty page". The ring counts up, a mascot reacts, and a "potential score" bar shows what the top fixes could add.
2. **What the AI agent missed:** right after the score. The page read as an AI agent reads it, as a 5-step journey (get in, read, understand, trust, find more) with every blind spot as a card (what it could not find, why it matters, how big). It is the free teaser for the full report and fix guide. **Start here:** the 3 most important fixes. **Already working:** a list of what is fine, so the report is not only bad news.
3. **How AI sees your site:** a side-by-side view.
   - Visitor view: a real screenshot from headless Chrome (runs JavaScript).
   - AI view: the HTML before JavaScript runs, turned into text. This is what most AI crawlers and fetch tools get.
4. **All fixes:** ranked by priority (critical, high, medium, low), with "+N pts" (points the area can still gain) and a "why it matters" note. Filter by priority. Tick fixes off and see progress. Fixes from our own checks carry an "extra check" tag and do not change the score.
5. **Key facts:** 8 tiles: words visible to AI, JavaScript risk, structured data, page size, server speed, sitemap, headings, images.
6. **Can the AI bots get in?** 14 crawlers and AI assistants (OpenAI, Anthropic, Perplexity, Google, Microsoft, Apple, Common Crawl, Amazon, Meta), each with its robots.txt rule and the real server answer.
7. **How each AI platform sees you:** ChatGPT, Perplexity and Google AI scores with strengths and tips.
8. **Score by area:** 9 areas from the engine, plus trust signals.
9. **Fix guide (.md) export:** a step-by-step Markdown guide for a developer or an AI coding tool, with ready-to-paste code filled in for the site.
10. **History tab:** last 30 scans, saved in the browser. Open, rescan, delete, and see score changes.
11. **Agent readiness (v2.4):** a closed Details panel with 23 checks of the standards AI agents look for (the ones isitagentready.com tests), a level from 0 to 5, and for each check what we asked, why it matters and how to fix it. It does not change the score.
12. **Other:** one Scan/Rescan button in the address bar (see below), Share link (opens a scan), Copy summary, JSON download.

## Status (25 Nov 2026)

- Live and public. No login.
- Auto-deploy: each push to `main` deploys to production. Other branches get a preview URL.
- Version 2.4.0: Agent readiness panel. All the checks of isitagentready.com, as a level from 0 to 5 and 23 rows, loaded after the scan from a new function.
- Version 2.3.0: new interface. One narrow column, one list of issues (severe first), everything else collapsed.
- Version 2.2.0: the "Say where your business is" check for local businesses (critical when the page text never says where the business is), a red alert under the score, and an optional Content Signals extra.
- Version 2.1.0: "What the AI agent missed" section, one smart Scan/Rescan button (Rescan always bypasses the cache). Version 2.0.0 brought the playful interface, the parallel scan engine (about 4 to 6 times faster), the 14-bot test and the extra checks.
- Tested on: anthropic.com (57), react.dev (63), excalidraw.com (50, JavaScript-only), stripe.com (86), duet121.com (45, JavaScript-only), paulgraham.com (35, little text, no JavaScript), wikipedia.org (69), nytimes.com (blocks all cloud traffic), cnn.com (refuses AI bots with HTTP 451), a fake domain, and a private address (refused). Scores match the old one-by-one engine on every site compared.

## Documents in this project

| Doc | Content |
|---|---|
| `01-overview.md` | This file |
| `02-architecture.md` | How it works: components, API, logic, security |
| `03-operations.md` | Deploy, config, testing, troubleshooting |
| `04-decisions-and-changelog.md` | Why it is built this way, and what changed when |
| `05-roadmap.md` | Known limits and next ideas |
| `code/scan.py`, `code/shot.js` | Copies of the two API files. The full code, including `index.html`, `api/_checks.py` and `tests/`, is in the GitHub repo (the source of truth) |
