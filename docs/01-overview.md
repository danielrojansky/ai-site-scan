# AI Site Scan: overview

Last updated: 24 Nov 2026, 23:45 (Asia/Jerusalem)
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

1. **Score (0 to 100)** with a plain-language verdict, for example "AI tools see an almost empty page".
2. **Start here:** the 3 most important fixes.
3. **How AI sees your site:** a side-by-side view.
   - Visitor view: a real screenshot from headless Chrome (runs JavaScript).
   - AI view: the HTML before JavaScript runs, turned into text. This is what most AI crawlers and fetch tools get.
4. **All fixes:** ranked by priority (critical, high, medium, low), with "+N pts" (points the area can still gain) and a "why it matters" note. Filter by priority.
5. **Key facts:** words visible to AI, JavaScript risk, structured data, page size, language.
6. **AI bot access table:** robots.txt rule and the real server answer for ClaudeBot, GPTBot and PerplexityBot.
7. **Score by area:** 9 areas from the engine.
8. **Fix guide (.md) export:** a step-by-step Markdown guide for a developer or an AI coding tool, with ready-to-paste code filled in for the site.
9. **History tab:** last 30 scans, saved in the browser. Open, rescan, delete, and see score changes.
10. **Other:** New scan button, Rescan, Share link (opens a fresh scan), Copy summary, JSON download.

## Status (24 Nov 2026)

- Live and public. No login.
- Auto-deploy: each push to `main` deploys to production.
- Tested on: anthropic.com (57), react.dev (63), excalidraw.com (50, JavaScript-only), nytimes.com (blocks all cloud traffic), duet121.com (45, JavaScript-only), a fake domain, and a private address (refused).

## Documents in this project

| Doc | Content |
|---|---|
| `01-overview.md` | This file |
| `02-architecture.md` | How it works: components, API, logic, security |
| `03-operations.md` | Deploy, config, testing, troubleshooting |
| `04-decisions-and-changelog.md` | Why it is built this way, and what changed when |
| `05-roadmap.md` | Known limits and next ideas |
| `code/scan.py`, `code/shot.js` | Copies of the two API files. The full code, including `index.html`, is in the GitHub repo (the source of truth) |
