# AI Site Scan: overview

Last updated: 28 Nov 2026, 14:00 (Asia/Jerusalem). App version 3.0.1
Owner: Daniel Rojansky

## What it is

AI Site Scan is a web app. You enter a website address. The app tells you how well AI tools (ChatGPT, Claude, Perplexity, Google AI Overviews) can reach, read and understand that site, and what to fix. The scan is free. The full report (every fix with ready-to-paste code) is paid: $19 once for one site, or Pro at $29 a month for 10 reports.

## Links

| Item | Value |
|---|---|
| Live app | https://getaisitescan.com (the old https://ai-site-scan.vercel.app still works) |
| GitHub repo | https://github.com/danielrojansky/ai-site-scan (branch `main`) |
| Vercel project | `ai-site-scan` (id `prj_mmAM1HwKQduftTI2wD2Rd9AjdcZt`) |
| Vercel team | `danielrojansky-8273s-projects` (id `team_xh9PcZyIa6lW6nLv8G0YrACi`) |
| Scan engine | geo-optimizer-skill 4.28.0, MIT, https://github.com/auriti-labs/geo-optimizer-skill (not named on the public site since 28 Nov 2026) |
| Accounts and data | Supabase project `ai-site-scan`, ref `rhbhkeuvftqwjjoyzseh`, us-east-1 |
| Payments | Polar, organization "Daniel Consulting" (merchant of record) |
| Email | Resend (sending, domain getaisitescan.com) and ImprovMX (support@ to Gmail) |
| Analytics | PostHog, US region |

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
13. **Full report (paid, v3):** the first 3 fixes are free. Buyers log in (email code or Google), pay on Polar, and every fix opens. The report is emailed with the fix guide attached and saved in "My reports". A Full report includes 1 rescan of the site within 30 days ("Scan again" in My reports uses it in one click). Pro includes rescans of its sites while the month is paid.

## Status (28 Nov 2026)

- Live at getaisitescan.com and selling: Full report $19 and Pro $29 a month on Polar. Version 3.0.1.
- Tested end to end on the live site on 28 Nov 2026: email code login, Google login, a $0 Full report order with a 100% discount code (webhook, credit, unlock, report email with the guide attached, My reports, the included rescan). The live Pro subscription was not bought, because Polar asks for a card even at $0.
- Auto-deploy: each push to `main` deploys to production. Other branches get a preview URL. Previews have no account settings, so their paywall is off.
- Version history and test results: `04-decisions-and-changelog.md`.

## Documents in this project

| Doc | Content |
|---|---|
| `01-overview.md` | This file |
| `02-architecture.md` | How it works: components, API, logic, security |
| `03-operations.md` | Deploy, config, testing, troubleshooting |
| `04-decisions-and-changelog.md` | Why it is built this way, and what changed when |
| `05-roadmap.md` | Known limits and next steps |
