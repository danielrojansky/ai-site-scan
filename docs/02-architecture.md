# AI Site Scan: architecture

## Big picture

```
Browser (index.html, one file, no build step)
  |-- GET /api/scan?url=...  -> Python function (api/scan.py)
  |       |-- geo-optimizer-skill: full audit (robots, llms.txt, schema, meta, content, ...)
  |       |-- own "AI view": raw HTML -> text, no JavaScript
  |       |-- own checks: bot access by user agent, JavaScript risk, <main> element
  |       '-- ranked actions + JSON response
  |-- GET /api/shot?url=...  -> Node function (api/shot.js)
  |       '-- headless Chrome (@sparticuz/chromium + puppeteer-core) -> JPEG
  '-- localStorage: scan history (last 30)
      Fix guide (.md): built in the browser from the scan JSON
```

All hosting is on Vercel. There is no database.

## Files

| File | Role |
|---|---|
| `index.html` | Whole frontend: HTML, CSS and JavaScript in one file |
| `api/scan.py` | Scan API (Python 3.12 on Vercel), class `handler(BaseHTTPRequestHandler)` |
| `api/shot.js` | Screenshot API (Node 24), CommonJS handler with dynamic `import()` |
| `requirements.txt` | `geo-optimizer-skill==4.28.0` |
| `package.json` | `@sparticuz/chromium` 158.0.0, `puppeteer-core` 25.18.1, Node 24.x |
| `vercel.json` | Framework pinned to Other (`null`), function limits, security headers |
| `README.md` | Short repo readme |
| `docs/` | This documentation |

## Scan API: `GET /api/scan?url=<address>`

### Request handling (in order)

1. Read `url`. Empty or longer than 2048 characters: 400.
2. Add `https://` if no scheme. Host must have a dot: else 400.
3. SSRF check with the engine's `validate_public_url` (public IPs only, DNS resolved once). Unknown domain: 400 "This domain could not be found". Private or metadata address: 400 "URL not allowed".
4. Cache: same URL within 10 minutes returns the cached result with `cached: true`.
5. Rate limit: 10 scans per IP per 10 minutes, per function instance. Over the limit: 429 with `Retry-After`.
6. Run `scan(url)`. Site unreachable for both the engine and the AI view: 502 with a clear message. Any other error: 500, logged to stderr. The handler never crashes the function.

### `scan(url)` steps

1. `run_full_audit(url, load_plugins=False)` from the engine, formatted with `format_audit_json`.
2. `build_ai_view(url)`: see below.
3. If the engine says `js_rendering.js_dependent`, the JavaScript risk is set to high. The detected framework is copied into `ai_view.framework`.
4. `build_actions(audit, ai)`: see below.

### Response fields (main ones)

| Field | Meaning |
|---|---|
| `url`, `scanned_at` (UTC), `duration_ms`, `cached` | Basics |
| `score`, `band` | Engine score 0 to 100 and band name |
| `error` | Engine error (for example "HTTP 403"), else null |
| `categories[]` | `key`, `label`, `score`, `max` for 9 areas |
| `penalty` | Negative points from the engine |
| `actions[]` | `category`, `label`, `priority`, `gain`, `items[]` (ranked) |
| `ai_view` | See below |
| `audit` | Full raw engine output (large) |
| `engine` | Name, version, repo |

### AI view (`build_ai_view`)

- Fetch the page with a normal Chrome user agent through the engine's SSRF-safe `fetch_url` (5 MB max).
- Parse with BeautifulSoup + lxml. Read title, meta description, Open Graph, `lang`, canonical, JSON-LD types.
- Convert the `<body>` to Markdown-like text. Drop script, style, svg, iframe and hidden elements. Keep headings, links `[text](href)`, lists, tables, `**bold**`, code blocks, images as `[image: alt]` or `[image: no alt text]`, and form controls as `[button: ...]`. Limit: 40,000 characters.
- Count visible words (no script, style, template, noscript).
- Detect empty SPA roots (`#root`, `#app`, `#__next`, `#__nuxt` and others) and "enable JavaScript" messages.
- JavaScript risk: high if fewer than 50 words, a "needs JavaScript" message, or an empty SPA root with fewer than 200 words. Medium if fewer than 120 words. Else low.
- Bot access test: three extra requests with the ClaudeBot, GPTBot and PerplexityBot user agents. Records the status code and size.

### Actions (`build_actions`)

Own checks first:
- **Site blocked the scan** (critical): the normal browser request got 400 or higher.
- **Server access for AI bots** (critical): the browser request was fine, but a bot user agent got 400 or higher.
- **Content needs JavaScript** (critical) or **Little text without JavaScript** (high).
- **Mark the main content** (low): no `<main>` element.

Then the engine's recommendations, grouped by category:
- `gain` = category max minus category score.
- Priority: high if gain 8 or more, medium if 3 to 7, low otherwise. Critical if the text mentions noindex or noai. The engine's "blockers" category is high.
- Clean-up: remove `[G-XXX]` codes, replace CLI hints with plain text, remove long dashes, drop duplicates of our own checks.
- Sort: critical, high, medium, low, then by gain.

## Screenshot API: `GET /api/shot?url=<address>`

- Checks the host resolves only to public IPs (own `isPrivateIp` for IPv4 and IPv6). Else 400.
- Starts headless Chrome from `@sparticuz/chromium` (or `CHROME_PATH` for local tests).
- Request interception: blocks private addresses (also on redirects and subresources), media, websockets and event streams.
- Waits for `networkidle2` (20 s max), plus 0.8 s, then takes a 1280 x 1600 JPEG (quality 70).
- Response header `Cache-Control: public, max-age=300, s-maxage=600`, so the Vercel edge caches each image for 10 minutes.
- Error: 502 JSON with the error message.

## Frontend (`index.html`)

- Views: **Scan** and **History** (tabs in the sticky top bar), plus a **＋ New scan** button.
- Scan flow: form, example chips, progress checklist (cosmetic timing), then the report.
- Report sections: summary (score ring, verdict, buttons), Start here (top 3), How AI sees your site (Compare, Visitor view, AI view), All fixes (filters, collapsible cards with "why it matters"), fix guide export card, Key facts, Score by area.
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
   - robots.txt with AI bots and sitemap, plus the "search yes, training no" option.
   - Content structure, smaller improvements, optional AI discovery files.
5. Full findings list and "after you deploy" steps.

## Security

- SSRF protection in both functions (DNS resolved once, private ranges blocked, redirects checked).
- No user data is stored on the server. History lives in the browser.
- Security headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy`.
- All values shown in the page are HTML-escaped. Links are created only for `http(s)` URLs.
- Public with no login. Abuse control is the basic in-memory rate limit (see the roadmap).
