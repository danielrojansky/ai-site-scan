# AI Site Scan

A small web app. Enter a URL and get a score and a to-do list in seconds. The report is built in layers, and only the first is open:

1. **The score card**: an AI readiness score (0 to 100), a one-line verdict, how many issues are critical, high, medium or low, and the main button for the fix guide. Under it, the AI agent's journey in five steps (get in, read, understand, trust, find more). Tap a step to see only its issues.
2. **One list of issues**, severe first. Each issue shows once: what the AI saw, why it matters and how to fix it. Tick issues off as you fix them. This is the free teaser for the full report and the fix guide.
3. **Details, closed until you open them**: how AI sees your page (the page without JavaScript next to a screenshot), the bot access test (14 crawlers and AI assistants tested at once), AI platforms, key facts and the score breakdown.

Current version: **2.3.0** (shown in the page footer).

## Documentation

Full docs are in [`docs/`](docs/): overview, architecture, operations, decisions and changelog, roadmap.

## Features

- **One scan button** in the address bar. It says **Rescan** when the address is the report on screen (always a fresh scan, never a cached one) and **Scan** for any other address. It changes as you type.
- **History** tab: the last 30 scans are saved in the browser (localStorage). Open any old report, rescan it, or delete it. Score changes and "fixed since last scan" are shown.
- **Fix guide (.md)**: a Markdown file with each fix, why it matters, ready-to-paste code filled in for the site (JSON-LD, llms.txt, robots.txt, head tags, SSR advice), how to check each fix, and a prompt for AI coding tools.
- Plain-language verdict, severity counts, share link, copy summary. The AI journey strip filters the issue list.
- **Where is the business?** A shop, clinic, restaurant or delivery business must say where it is in the page text. If the address is missing, or only in hidden structured data, the report shows a red alert under the score, a critical fix at the top and a ready-to-paste fix (an address block and LocalBusiness JSON-LD filled from the site's own data). Software and media sites are never flagged.
- Calm, playful interface: a small mascot, a count-up score ring, smooth open and close, confetti only for a great score with no critical issue. It respects "reduce motion".

## How it works

- `index.html`: the whole frontend (no build step).
- `api/scan.py`: a Vercel Python function. `GET /api/scan?url=...`
  - Runs the audit with [geo-optimizer-skill](https://github.com/auriti-labs/geo-optimizer-skill) (MIT), using its async path.
  - At the same time it probes the site as 14 bots, reads robots.txt and the sitemap, and builds the AI view. Everything runs in parallel, so a scan takes about 1 to 3 seconds (it took 3 to 10 before).
  - The engine's one-by-one CDN check is fed from our parallel probes (same rules), so the score is unchanged and nothing is fetched twice.
  - `api/_checks.py` holds our own checks (pure functions, tested offline): bot access, server speed and size, heading structure, link text, language, sitemap, soft 404 and more.
  - `api/_location.py` decides whether the page says where the business is (visible text and structured data) and whether the business depends on a place.
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
- The location check knows Israeli towns and about 230 big cities worldwide. A place that is not on the list is still found when the page prints a street address, an "Address:" label or an `<address>` block. It reads the page you scan, not the whole site.
- The bot test copies each bot's name from a cloud server. Sites that check bot IP addresses (many do for Googlebot, Bingbot and Applebot) can refuse the copy and still let the real bot in.
- The rate limit is in memory. For heavy public use, add a Vercel Firewall rule or a KV store.

## Deploy

The Vercel project `ai-site-scan` is connected to this repo. Each push to `main` deploys to https://ai-site-scan.vercel.app. Other branches get a preview URL.
Do not use "Import" in Vercel again: it creates a second project.
Bump the version in `api/scan.py` (`APP_VERSION`) and `index.html` (`APP_VERSION`) on every production push.
