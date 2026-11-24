# AI Site Scan

A small web app. Enter a URL and get:

1. An **AI readiness score** (0 to 100) with a score per category, and a mascot that reacts to it.
2. A **ranked list of fixes**, with the points each category can still gain. Tick fixes off as you do them.
3. A **simulated AI view**: the page HTML before JavaScript runs, turned into text. This is what most AI crawlers and fetch tools get. It is shown next to a screenshot of the real page.
4. A **bot access test**: robots.txt rules plus the real HTTP answer the server gives to 14 crawlers and AI assistants (GPTBot, ClaudeBot, PerplexityBot, Googlebot and more), all tested at once.
5. **What already works**, a per-platform view (ChatGPT, Perplexity, Google AI) and trust signals.

Current version: **2.0.0** (shown in the page footer).

## Documentation

Full docs are in [`docs/`](docs/): overview, architecture, operations, decisions and changelog, roadmap.

## Features

- **New scan** button in the top bar, always one tap away.
- **History** tab: the last 30 scans are saved in the browser (localStorage). Open any old report, rescan it, or delete it. Score changes and "fixed since last scan" are shown.
- **Fix guide (.md)**: a Markdown file with each fix, why it matters, ready-to-paste code filled in for the site (JSON-LD, llms.txt, robots.txt, head tags, SSR advice), how to check each fix, and a prompt for AI coding tools.
- Plain-language verdict, top 3 fixes, filters by priority, share link, copy summary.
- Animated, playful interface: scan stage, count-up score ring, confetti for good scores, reveal on scroll. It respects "reduce motion".

## How it works

- `index.html`: the whole frontend (no build step).
- `api/scan.py`: a Vercel Python function. `GET /api/scan?url=...`
  - Runs the audit with [geo-optimizer-skill](https://github.com/auriti-labs/geo-optimizer-skill) (MIT), using its async path.
  - At the same time it probes the site as 14 bots, reads robots.txt and the sitemap, and builds the AI view. Everything runs in parallel, so a scan takes about 1 to 3 seconds (it took 3 to 10 before).
  - The engine's one-by-one CDN check is fed from our parallel probes (same rules), so the score is unchanged and nothing is fetched twice.
  - `api/_checks.py` holds our own checks (pure functions, tested offline): bot access, server speed and size, heading structure, link text, language, sitemap, soft 404 and more.
  - Uses the engine's SSRF protection (public addresses only, DNS pinning, every redirect checked).
  - Basic rate limit (10 scans per IP per 10 minutes, per function instance) and a 10 minute result cache.
- `api/shot.js`: a Node function that opens the page in headless Chrome (`@sparticuz/chromium` + `puppeteer-core`) and returns a JPEG. It blocks private addresses and caches each image at the Vercel edge for 10 minutes.

## Run locally

```bash
pip install -r requirements.txt
npx vercel dev
```

## Test

```bash
python -m unittest discover -s tests
```

## Limits

- It scans one page (the URL you enter), plus site files like robots.txt, llms.txt and sitemap.
- The AI view does not run JavaScript. Google and some agents do run it, so their view can be richer.
- The bot test copies each bot's name from a cloud server. Sites that check bot IP addresses (many do for Googlebot, Bingbot and Applebot) can refuse the copy and still let the real bot in.
- The rate limit is in memory. For heavy public use, add a Vercel Firewall rule or a KV store.

## Deploy

The Vercel project `ai-site-scan` is connected to this repo. Each push to `main` deploys to https://ai-site-scan.vercel.app. Other branches get a preview URL.
Do not use "Import" in Vercel again: it creates a second project.
Bump the version in `api/scan.py` (`APP_VERSION`) and `index.html` (`APP_VERSION`) on every production push.
