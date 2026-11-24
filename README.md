# AI Site Scan

A small web app. Enter a URL and get:

1. An **AI readiness score** (0 to 100) with a score per category.
2. A **ranked list of fixes**, with the points each category can still gain.
3. A **simulated AI view**: the page HTML before JavaScript runs, turned into text. This is what most AI crawlers and fetch tools get. It is shown next to a screenshot of the real page.
4. A **bot access test**: robots.txt rules plus the real HTTP answer the server gives to ClaudeBot, GPTBot and PerplexityBot user agents.

## How it works

- `index.html`: the whole frontend (no build step).
- `api/scan.py`: a Vercel Python function. `GET /api/scan?url=...`
  - Runs the audit with [geo-optimizer-skill](https://github.com/auriti-labs/geo-optimizer-skill) (MIT).
  - Builds the AI view with BeautifulSoup.
  - Turns the engine's recommendations into ranked actions and adds its own checks (JavaScript dependence, bot blocking at server level, `<main>` element).
  - Uses the engine's SSRF protection, so it only fetches public addresses.
  - Basic rate limit (10 scans per IP per 10 minutes, per function instance) and a 10 minute result cache.
- The screenshot comes from the free WordPress mShots service.

## Run locally

```bash
pip install -r requirements.txt
npx vercel dev
```

## Limits

- It scans one page (the URL you enter), plus site files like robots.txt, llms.txt and sitemap.
- The AI view does not run JavaScript. Google and some agents do run it, so their view can be richer.
- The rate limit is in memory. For heavy public use, add a Vercel Firewall rate limit rule or a KV store.
