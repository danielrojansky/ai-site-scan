# AI Site Scan: architecture

## Big picture

```
Browser (index.html, one file, no build step)
  |-- GET /api/scan?url=...  -> Python function (api/scan.py)
  |       |  all of these run at the same time:
  |       |-- geo-optimizer-skill async audit (robots, llms.txt, schema, meta, content, ...)
  |       |-- 1 browser request + 14 bot requests (api/_checks.py judges them)
  |       |-- robots.txt, then the sitemap
  |       '-- page analysis: AI view, headings, links, language, JavaScript risk
  |       -> ranked actions + wins + platform scores + JSON response
  |-- GET /api/shot?url=...  -> Node function (api/shot.js)
  |       '-- headless Chrome (@sparticuz/chromium + puppeteer-core) -> JPEG
  '-- localStorage: scan history (last 30), fixes ticked off
      Fix guide (.md): built in the browser from the scan JSON
```
All hosting is on Vercel. There is no database.

## Files

| File | Role |
|---|---|
| `index.html` | Whole frontend: HTML, CSS and JavaScript in one file |
| `api/scan.py` | Scan API (Python 3.12 on Vercel): handler, rate limit, cache, parallel scan, action ranking |
| `api/_checks.py` | Our checks as pure functions (no network): bot judging, page analysis, findings. The underscore keeps Vercel from making it an endpoint |
| `api/shot.js` | Screenshot API (Node 24), CommonJS handler with dynamic `import()` |
| `tests/test_checks.py` | Offline tests for `_checks.py` (`python -m unittest discover -s tests`) |
| `requirements.txt` | `geo-optimizer-skill[async]==4.28.0` (the async extra adds httpx) |
| `package.json` | `@sparticuz/chromium` 158.0.0, `puppeteer-core` 25.18.1, Node 24.x |
| `vercel.json` | Framework pinned to Other (`null`), function limits, security headers |
| `README.md` | Short repo readme |
| `docs/` | This documentation |

## Scan API: `GET /api/scan?url=<address>`

### Request handling (in order)

1. Read `url`. Empty or longer than 2048 characters: 400.
2. Add `https://` if no scheme. Host must have a dot: else 400.
3. SSRF check with the engine's `validate_public_url` (public IPs only, DNS resolved once). Unknown domain: 400 "This domain could not be found". Private or metadata address: 400 "URL not allowed".
4. Cache: same URL within 10 minutes returns the cached result with `cached: true` and `cached_age_s`. `fresh=1` skips the cache. Every Rescan in the page sends it, so a rescan after a fix never shows an old answer.
5. Rate limit: 10 scans per IP per 10 minutes, per function instance. Over the limit: 429 with `Retry-After`.
6. Run `scan(url)`. Site unreachable for both the engine and the page fetch: 502 with a clear message. Scan longer than 50 s: 502 "took too long". Any other error: 500, logged to stderr. The handler never crashes the function. One log line per scan: host, score, times.

### `scan(url)`: how it is fast

`scan_async` starts two tasks at once and waits for both (50 s limit):
1. **Engine:** `run_full_audit_async(url, load_plugins=False)`. It fetches the homepage and 7 site files in parallel. If the async engine fails for any reason, the one-by-one `run_full_audit` runs instead.
2. **`inspect_site`:** one browser request (10 MB max, one quiet retry on 429/5xx), 14 bot requests (at most 6 at a time), robots.txt and then the sitemap. The page analysis runs in a thread while the bots are still answering.

All requests use the engine's `fetch_url_async`: public addresses only, DNS pinned, every redirect checked, size capped, no retry with backoff (the old fetcher retried 429/5xx up to 3 times, which made blocked sites slow).

**Engine hook:** the engine's own CDN check requests 6 bots one by one (the slowest part). While a scan runs, `audit_cdn_ai_crawler` inside the engine module is replaced by `_cdn_check_from_probes`, which returns the result built from our parallel probes with the same rules and the same fields. A `ContextVar` carries the per-scan result, so concurrent scans do not mix. Outside a scan the original function runs. Result: identical scores, no duplicate requests.

Measured on 8 sites: scores identical, scan time 0.8 to 4.9 s (before: 3.2 to 10.2 s). Example: bluevine.com 6.3 s to 1.1 s.

### Response fields (main ones)

| Field | Meaning |
|---|---|
| `app_version` | Version of this app (also in the page footer) |
| `url`, `scanned_at` (UTC), `duration_ms`, `timings`, `cached` | Basics |
| `score`, `band` | Engine score 0 to 100 and band name |
| `error` | Engine error (for example "HTTP 403"), else null |
| `categories[]` | `key`, `label`, `score`, `max` for 9 areas |
| `penalty` | Negative points from the engine |
| `actions[]` | `category`, `label`, `priority`, `gain`, `items[]`, `source` (`engine` or `extra`), ranked |
| `wins[]` | Plain sentences about what already works |
| `missed` | What the AI agent missed: `items[]` (`stage`, `impact`, `title`, `detail`), `optional[]` (unproven conventions, not counted), `stages[]` (journey step with `status` ok, warn, fail or skipped), `count`, `potential` (score if every engine gain is earned, at most 100), `usable` (false if the page could not be read) |
| `platforms[]` | ChatGPT, Perplexity, Google AI: `score`, `strengths`, `tips` (from the engine) |
| `trust` | Trust grade and 5 layers with found and missing signals (from the engine) |
| `ai_view` | See below |
| `audit` | Full raw engine output (large) |
| `engine` | Name, version, repo |

### AI view (`ai_view`)

- The page is fetched with a normal Chrome user agent and decoded the way a browser would (header charset, meta charset, then a guess).
- Parse with BeautifulSoup + lxml. Read title, meta description, Open Graph, `lang`, canonical, JSON-LD types (and the count of invalid blocks), meta robots.
- Convert the `<body>` to Markdown-like text. Drop script, style, svg, iframe and hidden elements. Keep headings, links `[text](href)`, lists, tables, `**bold**`, code blocks, images as `[image: alt]` or `[image: no alt text]`, and form controls as `[button: ...]`. Limit: 40,000 characters.
- Count visible words (no script, style, template, noscript). Detect empty SPA roots and "enable JavaScript" messages.
- **JavaScript risk:** high if there is a "needs JavaScript" message, an empty SPA root with fewer than 200 words, or fewer than 50 words together with a script bundle and no real content. If there are fewer than 50 words but no script, or the page has an H1 or a full paragraph, the page is "thin" (little text, `thin: true`), not JavaScript-dependent. Medium below 120 words, else low.
- Also: headings summary, link and image counts, language check, response time, compression, `browser_challenge`, sitemap result, and `bot_access` (see below).

### Bot probes

14 bots in 3 roles: training (GPTBot, ClaudeBot, CCBot, meta-externalagent), AI search (OAI-SearchBot, Claude-SearchBot, PerplexityBot, Googlebot, Bingbot, Applebot, Amazonbot) and on request (ChatGPT-User, Claude-User, Perplexity-User). The first 8 (OpenAI, Anthropic, Perplexity) are "core". Each bot gets a verdict:
- `ok`: HTTP 200 and a page of a similar size to the browser's.
- `blocked`: HTTP 403, 429, 451 or 503 (or any error status the browser did not get), or a page under 30% of the browser's size (same rule as the engine).
- `challenge`: a bot check page (Cloudflare, Incapsula, PerimeterX and others).
- `error`: no answer. `different`: HTTP 200 but under 70% or over 150% of the browser's size.

The browser request itself is checked too: a bot check page there (for example AWS WAF, HTTP 202) means the whole scan was blocked.

### What the AI agent missed (`build_missed`)

Built in `api/_checks.py` from the same data as the actions, told from the agent's side. Only measured facts are listed. Each item has a stage, an impact (critical, high, medium, low), a title in the agent's voice ("Could not tell who you are") and a plain detail.
- **Get in:** page not found, server error or bot check page (then nothing else is listed, `usable` is false), AI crawlers refused, robots.txt blocks, long crawl delay, noindex or noai, slow server, huge page.
- **Read:** almost empty page (JavaScript) or very little text, text blocked by `data-nosnippet`, images without alt text, video without captions, cookie notice first, bots get a different page.
- **Understand:** no title, summary or H1, no sections, little text, no or broken or incomplete structured data (Organization, WebSite, FAQ), no canonical, language missing or wrong, no preview tags, different brand names, no numbers, key facts too late.
- **Trust:** no author (articles only), no date, no official profile links, no contact details, no About link, plain HTTP.
- **Find more:** links that are not real links, sitemap missing, broken or not listed, nofollow.
- **Optional (listed apart, never counted):** llms.txt, news feed, AI files. They are new, unproven conventions.

The stage status is `fail` if any critical or high item is in it, `warn` for medium or low, `ok` for none. Google, Bing and Apple bots are never counted as missed (many sites refuse imitations of them on purpose). `potential` is the score plus every engine gain, at most 100.
### Actions (`build_actions`)

Own checks first, then the engine's recommendations by category, then more own checks:
- `status`: page not found (404, 410), server error (5xx), soft 404 (HTTP 200 with a "not found" title).
- `access`: the site blocked the scan (critical); or core AI bots refused: critical from 4 of 8, else high. Googlebot, Bingbot and Applebot are not reported as a problem (many sites refuse imitations of them on purpose). The bot cards show them.
- `rendering` (critical or high) or `thin` (high); `structure` (no `<main>`, low).
- Engine categories: `gain` = category max minus category score. Priority: high if gain 8 or more, medium if 3 to 7, low otherwise. Critical if the text mentions noindex or noai. The engine's "blockers" category is high. Clean-up: remove `[G-XXX]` codes and warning emoji, replace CLI hints with plain text, remove long dashes, drop duplicates of our own checks (JavaScript items, the CDN block note, "no Sitemap: directive").
- Extra checks (no score, `source: "extra"`): `bot_content` (bots get a different page), `performance` (median answer time, HTML size, no compression), `language` (text script does not match `lang`), `headings` (empty H1, several H1, skipped levels), `links` (vague or empty links), `title_desc` (too long, or description too short), `crawl` (nofollow), `cookie` (a cookie banner is the first text), `sitemap` (declared but broken, or not declared), `trust` (page served over HTTP).
- Sort: critical, high, medium, low, then by gain.

## Screenshot API: `GET /api/shot?url=<address>`

- Checks the host resolves only to public IPs (own `isPrivateIp` for IPv4 and IPv6). Else 400.
- Starts headless Chrome from `@sparticuz/chromium` (or `CHROME_PATH` for local tests).
- Request interception: blocks private addresses (also on redirects and subresources), media, websockets and event streams.
- Waits for `networkidle2` (20 s max), plus 0.8 s, then takes a 1280 x 1600 JPEG (quality 70).
- Response header `Cache-Control: public, max-age=300, s-maxage=600`, so the Vercel edge caches each image for 10 minutes.
- Error: 502 JSON with the error message.

## Frontend (`index.html`)

- Views: **Scan** and **History** (tabs in the sticky top bar). The brand name returns to the home screen. Clicking the active **Scan** tab scrolls to the address bar and selects the address.
- **One scan button.** The address bar button starts every scan. If the address is the same site as the report on screen it says **Rescan** (with a refresh icon, and a fresh scan is requested); for any other address it says **Scan**. It changes while you type. A one-line hint under the bar explains the two cases. There is no separate New scan button and no second Rescan button. After all fixes are ticked, a banner offers **Rescan now** and the bar button pulses.
- Scan flow: form, example chips, scan stage (page mock with a laser line, mascot, progress bar, funny status lines, checklist with cosmetic timing that finishes fast when the answer arrives), then the report.
- Report sections: summary (count-up score ring, band badge, verdict, potential-score bar, mascot, trend sparkline, "fixed since last scan", 4 stat tiles, buttons), sticky section nav with scroll spy, **What the AI agent missed**, Start here (top 3), Already working, How AI sees your site (Compare, Visitor view, AI view with typed lines), Can the AI bots get in? (14 bot cards), How each AI platform sees you, All fixes (filters, accordion, tick-off list with progress), fix guide export card, Key facts (8 tiles), Score by area and trust signals.
- Motion: reveal on scroll, count-ups, confetti for scores of 80 and up, spotlight on hover, mascot eyes follow the pointer. All of it is switched off for visitors who ask for reduced motion.
- Ticked fixes are stored in `localStorage` key `aiSiteScan.done.v1` per site and reset on a fresh scan.
- `?url=` in the address runs a scan on load. Share link uses this.
- History: `localStorage` key `aiSiteScan.history.v1`, max 30 entries. Each entry stores the scan JSON without the large raw `audit` (only the robots lists are kept). If storage is full, the oldest entries are dropped.
- Times are shown in 24-hour format (`en-GB`).

### Fix guide generator (`buildFixGuide`)

Runs in the browser. It builds a Markdown file `ai-fix-guide-<host>.md` with:
1. Score and verdict.
2. How to use, plus a ready prompt for an AI coding tool (rules: no invented facts, replace TODO, show diffs).
3. Summary table of all fixes.
4. Steps, only for problems that were found:
   - Let AI crawlers reach the site (CDN and WAF steps, curl check).
   - Put content in the HTML (Next.js, Nuxt or generic advice, based on the detected framework).
   - JSON-LD Organization + WebSite, filled with name, URL, description and language. Adds `sameAs` and `contactPoint` only when the scan asks for them.
   - `/llms.txt` template with the site's own internal links (from the AI view).
   - Head tags: canonical, viewport, Open Graph, description, RSS, modified time, author, `lang`.
   - robots.txt and sitemap with AI bots, plus the "search yes, training no" option and any sitemap findings.
   - Content structure (including heading findings), smaller improvements, optional AI discovery files, and other findings with their "why".
5. Full findings list and "after you deploy" steps.

## Security

- SSRF protection in both functions (DNS resolved once, private ranges blocked, redirects checked).
- No user data is stored on the server. History lives in the browser.
- Security headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy`.
- All values shown in the page are HTML-escaped. Links are created only for `http(s)` URLs.
- Public with no login. Abuse control is the basic in-memory rate limit (see the roadmap).
