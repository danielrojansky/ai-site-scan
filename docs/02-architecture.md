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
  |-- GET /api/agent?url=...  -> Python function (api/agent.py), asked after the scan
  |       '-- ~25 requests to the site + DNS over HTTPS; api/_agentready.py judges them -> level + 23 checks
  |-- GET /api/webmcp?url=... -> Node function (api/webmcp.js), asked when the Agent readiness panel opens
  |       '-- headless Chrome with a WebMCP stand-in -> the tools the page registers
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
| `api/_location.py` | The local-business check as pure functions: does the page say where the business is, and does the business depend on a place? Place lists in Hebrew and English. Imported by `_checks.py` |
| `api/agent.py` | Agent readiness API (Python): fetches the files and records, handler, rate limit, cache |
| `api/_agentready.py` | The 23 agent readiness checks and the level rules as pure functions (no network) |
| `api/webmcp.js` | WebMCP API (Node): opens the page in headless Chrome and lists the tools it registers |
| `api/shot.js` | Screenshot API (Node 24), CommonJS handler with dynamic `import()` |
| `tests/test_checks.py` | Offline tests for `_checks.py` and `_location.py` (`python -m unittest discover -s tests`) |
| `tests/test_agentready.py` | Offline tests for `_agentready.py` |
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
| `actions[]` | `category`, `label`, `priority`, `gain`, `items[]` (the steps), `source` (`engine` or `extra`), ranked. The `location` action also has `lead` (what is wrong, one sentence) and `note` (why it was flagged) |
| `wins[]` | Plain sentences about what already works |
| `missed` | What the AI agent missed: `items[]` (`stage`, `impact`, `title`, `detail`, `fix` = the action category that holds the how-to, `hint` = a one-line how-to for items that may have no action), `optional[]` (unproven conventions, not counted), `stages[]` (journey step with `status` ok, warn, fail or skipped), `count`, `potential` (score if every engine gain is earned, at most 100), `usable` (false if the page could not be read) |
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
- Also: `location` (where the page says the business is, see the next section).

### Bot probes

14 bots in 3 roles: training (GPTBot, ClaudeBot, CCBot, meta-externalagent), AI search (OAI-SearchBot, Claude-SearchBot, PerplexityBot, Googlebot, Bingbot, Applebot, Amazonbot) and on request (ChatGPT-User, Claude-User, Perplexity-User). The first 8 (OpenAI, Anthropic, Perplexity) are "core". Each bot gets a verdict:
- `ok`: HTTP 200 and a page of a similar size to the browser's.
- `blocked`: HTTP 403, 429, 451 or 503 (or any error status the browser did not get), or a page under 30% of the browser's size (same rule as the engine).
- `challenge`: a bot check page (Cloudflare, Incapsula, PerimeterX and others).
- `error`: no answer. `different`: HTTP 200 but under 70% or over 150% of the browser's size.

The browser request itself is checked too: a bot check page there (for example AWS WAF, HTTP 202) means the whole scan was blocked.

### Where is the business (`ai_view.location`)

Built in `api/_location.py` while the page is parsed, before scripts are removed. An AI tool that reads the page as text cannot place a business that never says where it is, so it may skip it for local questions or put it in the wrong area. Three groups of facts:
- **`local`**: does the business depend on a place or a delivery area? Points: local business structured data 3, opening hours 2, a place to visit (branch, appointment, reservation) 1, delivery or pickup 1, phone number 1, online shop (cart words) 1, map 1. `strong` = local business data, or opening hours with 3 points, or 5 points. `likely` = 3 points. Otherwise `none`: the page is never flagged (software, media, documentation).
- **`visible`**: what the page text says. `status` is `ok` (a street address, PO box, postal code, "Address:" label with a value, `<address>` block, "located in" phrase, or a known place in the title, headings, header or footer), `area` (only a country or a delivery area such as "all over Israel"), `mention` (a place in passing, or only a map), `hidden` (nothing visible, but the structured data has an address) or `none`.
- **`schema`**: the business as the JSON-LD tells it (type, name, address, `geo` ok, empty or none, `areaServed`, phone, hours). A `Person` is ignored. `OnlineStore` is not a local type.

Severity (`location_impact`): strong and status `none` or `hidden` is **critical**; likely and `none` or `hidden` is **high**; strong and `area` or `mention` is **medium**; everything else is not flagged. The check is not in the score. It also adds the optional extra "empty map pin" (`geo_empty`, low) and a win ("The page says where the business is").

### What the AI agent missed (`build_missed`)

Built in `api/_checks.py` from the same data as the actions, told from the agent's side. Only measured facts are listed. Each item has a stage, an impact (critical, high, medium, low), a title in the agent's voice ("Could not tell who you are") and a plain detail.
- **Get in:** page not found, server error or bot check page (then nothing else is listed, `usable` is false), AI crawlers refused, robots.txt blocks, long crawl delay, noindex or noai, slow server, huge page.
- **Read:** almost empty page (JavaScript) or very little text, text blocked by `data-nosnippet`, images without alt text, video without captions, cookie notice first, bots get a different page.
- **Understand:** no title, summary or H1, no sections, little text, no or broken or incomplete structured data (Organization, WebSite, FAQ), no canonical, language missing or wrong, no preview tags, different brand names, no numbers, key facts too late, **could not tell where the business is** (local businesses only, critical when nothing says where), empty map pin.
- **Trust:** no author (articles only), no date, no official profile links, no contact details, no About link, plain HTTP.
- **Find more:** links that are not real links, sitemap missing, broken or not listed, nofollow.
- **Optional (listed apart, never counted):** llms.txt, news feed, AI files, Content Signals (a `Content-Signal` line in robots.txt). They are new, unproven conventions.

The stage status is `fail` if any critical or high item is in it, `warn` for medium or low, `ok` for none. Google, Bing and Apple bots are never counted as missed (many sites refuse imitations of them on purpose). `potential` is the score plus every engine gain, at most 100.
### Actions (`build_actions`)

Own checks first, then the engine's recommendations by category, then more own checks:
- `status`: page not found (404, 410), server error (5xx), soft 404 (HTTP 200 with a "not found" title).
- `access`: the site blocked the scan (critical); or core AI bots refused: critical from 4 of 8, else high. Googlebot, Bingbot and Applebot are not reported as a problem (many sites refuse imitations of them on purpose). The bot cards show them.
- `rendering` (critical or high) or `thin` (high); `structure` (no `<main>`, low).
- Engine categories: `gain` = category max minus category score. Priority: high if gain 8 or more, medium if 3 to 7, low otherwise. Critical if the text mentions noindex or noai. The engine's "blockers" category is high. Clean-up: remove `[G-XXX]` codes and warning emoji, replace CLI hints with plain text, remove long dashes, drop duplicates of our own checks (JavaScript items, the CDN block note, "no Sitemap: directive").
- Extra checks (no score, `source: "extra"`): `bot_content` (bots get a different page), `performance` (median answer time, HTML size, no compression), `language` (text script does not match `lang`), `headings` (empty H1, several H1, skipped levels), `links` (vague or empty links), `title_desc` (too long, or description too short), `crawl` (nofollow), `cookie` (a cookie banner is the first text), `sitemap` (declared but broken, or not declared), `trust` (page served over HTTP), `location` (a local business whose page text does not say where it is: critical, high or medium, see above).
- Sort: critical, high, medium, low. Inside one priority the `location` fix goes first, then the biggest gain.

## Agent readiness API: `GET /api/agent?url=<address>`

A separate function, so the main scan stays fast. The page asks for it right after the scan and shows the answer in the closed Details panel **Agent readiness**. It tests the public standards that isitagentready.com (Cloudflare) tests. We do not call that site. The rules come from its public documentation and our results were compared with its website on four real sites. The code is our own.

### Request handling

1. Same address checks as the scan (empty or over 2048 characters: 400, `https://` added, public addresses only).
2. Cache: 10 minutes per address. `fresh=1` skips it (every Rescan sends it). Rate limit: 20 per IP per 10 minutes per function instance (`AGENT_RATE_MAX`). Over the limit: 429 with `Retry-After`.
3. `gather` in `agent.py` asks in two waves, 6 requests at a time, all through the engine's `fetch_url_async` (public addresses only, DNS pinned, every redirect checked, size capped). Wave one: the 19 paths in the table, the homepage a second time with `Accept: text/markdown`, and 5 DNS lookups over HTTPS (Cloudflare first, then Google). Wave two, only when needed: the sitemap that robots.txt names, the second ARD path, the Poppy issuer file and the homepage scripts (to read WebMCP code). About 25 requests reach the site.
4. The check stops at 28 s (the function limit is 40 s). `evaluate` in `_agentready.py` turns the answers into the result. A broken answer makes one row "unable". It never crashes the function.

### The 23 checks

12 are scored for the level and the count (scored). The others are optional: they show in the panel with an "optional" tag and never lower the count. The five commerce rows are informational.

| Group | Check | Where we look | Passes when |
|---|---|---|---|
| Discoverability | robots.txt | `/robots.txt` | It answers 200 with at least one rule group |
| | Sitemap | the sitemap that robots.txt names, else `/sitemap.xml` | It is a valid sitemap. The note says whether robots.txt lists it |
| | Link headers | the `Link` header of the homepage | It has a registered relation: `api-catalog`, `service-desc`, `service-doc`, `describedby`, or `alternate` without `hreflang` |
| | DNS-AID | SVCB records under `_index._agents`, `_a2a._agents` and `_mcp._agents` of the domain (DNS over HTTPS) | A valid SVCB record exists. The note says whether DNSSEC was confirmed. A failed lookup is "unable" |
| Content for agents | Markdown negotiation | the homepage with `Accept: text/markdown` | It answers `Content-Type: text/markdown` |
| | Search for Agents (optional) | `/.well-known/search?q=ai` | It answers Markdown |
| Bot access control | AI bot rules | robots.txt | It names AI crawlers (20 known names), or its `*` group sets rules for all crawlers |
| | Content Signals | robots.txt | A `Content-Signal` line with values (`ai-train`, `search`, `ai-input`) |
| | Web Bot Auth (optional) | `/.well-known/http-message-signatures-directory` | It lists public keys |
| APIs, auth, MCP and skills | API catalog | `/.well-known/api-catalog` | A valid linkset (RFC 9727) with at least one API |
| | OAuth and OIDC discovery (optional) | `/.well-known/openid-configuration`, then `/.well-known/oauth-authorization-server` | Valid metadata with an `issuer` |
| | OAuth Protected Resource (optional) | `/.well-known/oauth-protected-resource` | Valid metadata (RFC 9728) with a `resource` |
| | Auth.md (optional) | `/auth.md` | The file exists |
| | MCP Server Card | `/.well-known/mcp/server-card.json` | A card with a name, a version and an endpoint. Two real shapes are accepted |
| | A2A Agent Card (optional) | `/.well-known/agent-card.json` | A card with a name and skills |
| | Agent Skills index | `/.well-known/agent-skills/index.json` | Skills are listed, each with a type, a URL and a sha256 digest |
| | WebMCP | the homepage code, then the page run in a real browser | The page registers tools through `document.modelContext` or `navigator.modelContext`, or has forms with a `toolname` attribute |
| | ARD | `/.well-known/ai-catalog.json` (and the alternate path it names) | A catalog with entries |
| | Poppy (optional) | `/.well-known/poppy.json` | Discovery is found. The note says whether the issuer was verified or no session interfaces are declared |
| Commerce (all optional) | x402 | `/.well-known/x402`, an HTTP 402 answer, or a `_x402` DNS record | A manifest or payment terms |
| | MPP | `/openapi.json` | Operations with `x-payment-info` |
| | UCP | `/.well-known/ucp` | Valid metadata with a `protocol_version` |
| | ACP | `/.well-known/acp.json` | Valid metadata with a protocol version |

Status of a row: `pass`, `fail`, `neutral` (an optional check that is missing, so not a failure) or `unable` (blocked, timed out or the DNS lookup failed; left out of the count).

### Levels

The level is the highest step whose conditions are met, counting from 1 without gaps.

| Level | Name | Needs |
|---|---|---|
| 0 | Not Ready | |
| 1 | Basic Web Presence | two of robots.txt, a sitemap and Link headers |
| 2 | Bot-Aware | AI bot rules and Content Signals in robots.txt |
| 3 | Agent-Readable | Markdown negotiation |
| 4 | Agent-Integrated | one of MCP server card, A2A agent card, Agent Skills index or API catalog |
| 5 | Agent-Native | MCP server card, Agent Skills index and API catalog together, or Web Bot Auth together with OAuth, OIDC or Auth.md metadata |

### Response fields

| Field | Meaning |
|---|---|
| `level`, `level_name`, `next` | The level and what the next one needs (`next` is null at level 5) |
| `passed`, `scored`, `percent`, `unable` | Scored checks passed out of the scored checks that could be read |
| `groups[]` | Five groups with their counts |
| `checks[]` | 23 rows: `id`, `group`, `label`, `profile`, `scored`, `status`, `message`, `evidence[]` (what we asked and what came back), `why`, `fix`, `spec` (link to the standard). `webMcp` has `browser: true` when a real browser can still change its result |
| `reachable` | False when the site answered none of the requests |
| `is_commerce`, `shop_signals` | Whether the homepage looks like an online shop. It only changes the wording of the payment rows |
| `app_version`, `url`, `scanned_at`, `duration_ms` | Basics |

## WebMCP API: `GET /api/webmcp?url=<address>`

WebMCP lets a page offer actions to a browser agent as tools. A page registers them by running its JavaScript, so reading files cannot see them. This function opens the page in headless Chrome (same packages as the screenshot function). Real browsers do not offer the WebMCP API yet, so a small script is installed before the page runs. It provides `document.modelContext` and `navigator.modelContext`, answers like the real API, records every `registerTool` and `provideContext` call and sends each tool out of the page at once through a binding (`__webmcpReport`). Forms with a `toolname` attribute count too.

- Same protection as the screenshot function: public addresses only, private redirects and subresources blocked, no media or websockets.
- Every wait is bounded, so the function answers in JSON before the 40 s limit: the first answer from the page 12 s, then the network-idle wait 6 s, a 1.5 s pause, reading the forms 4 s. A watchdog at 30 s answers with what it has and stops Chrome.
- Tools are sent out the moment they are registered, so a heavy page that freezes its main thread afterwards (vercel.com does in our small Chrome on Vercel) still reports them. If the page never lets us read it and no tool came out, the answer has `partial: true` and the page shows the row as "unable".
- Answer: `toolCount`, `tools[]` (`name`, `title`, `description`, `hasSchema`, at most 50), `detectionMethod` (`imperative_api`, `declarative_html`, `both` or null), `via` and, when the page could not be read, `partial`. A complete answer has `Cache-Control: public, max-age=300, s-maxage=600`. A partial one is not cached.
- The page calls it once, when the Agent readiness panel opens and only if the row says `browser: true` (the static reading of the homepage did not find tools). The row shows "Checking in a real browser..." and then the browser result, and the counts are updated.

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
- Scan flow: form, example chips, then a short scan screen (page mock with a laser line, mascot, one funny status line, progress bar), then the report.
- **Layout (v2.3):** one narrow centered column (760 px) on every screen size. Information comes in three layers, and only the first is open:
  1. **Score card:** ring, verdict in one line, severity pills (critical, high, medium, low), the main button **Get the fix guide**, a menu (share link, copy summary, JSON), and the AI agent's journey (five steps, each a filter for the list below).
  2. **One list of issues.** Each issue shows once. **Fix first** holds critical and high issues; **More to improve** (closed unless nothing is severe) holds medium and low plus the optional extras; **What works** is closed. A row shows severity, title, points and one line. Opening it shows what the AI saw, why it matters and the steps (three at first, then "Show N more steps"). The worst critical issue opens by itself. A tick box marks an issue done.
  3. **Details**, six closed panels with a one-line teaser each: How AI sees your page (screenshot loads only when opened), AI bots, Agent readiness, AI platforms, Key facts, Score breakdown.
- **Joining the lists:** an issue is an action (the how-to, points, priority). The report adds the agent-voice items of `missed` whose `fix` equals the action category as "What the AI saw", and raises the severity to the worst of the two. A missed item with no action becomes its own row and uses its `hint`. Reports saved before 2.3.0 have no `fix`, so the page maps them with the same table (`FIX_FOR`).
- **Agent readiness panel (v2.4):** after the scan the page calls `/api/agent` in the background (with `fresh=1` after a Rescan). The header teaser reads "Level N of 5 · X of Y checks". Opened, it shows the level, a five-step bar, what the next level needs and the rows in five groups. A row opens to show what we asked, why it matters, how to fix it and a link to the standard. When the WebMCP row has `browser: true`, opening the panel calls `/api/webmcp` once and updates that row and the counts. The result is saved with the scan in the history, so an old report opens with no new request. If the check fails the panel says so and offers Try again.
- Motion: score count-up, smooth open and close, confetti only for a score of 80 or more with no critical issue, mascot mood and eyes. All of it is switched off for visitors who ask for reduced motion.
- **Location:** a critical `location` issue changes the verdict ("AI can't tell where the business is") and removes the confetti. It is also the first row, and it opens by itself.
- Ticked fixes are stored in `localStorage` key `aiSiteScan.done.v1` per site and reset on a fresh scan.
- `?url=` in the address runs a scan on load. Share link uses this.
- History: `localStorage` key `aiSiteScan.history.v1`, max 30 entries. Each entry stores the scan JSON without the large raw `audit` (only the robots lists are kept). The agent readiness result (about 13 KB) is added to the entry when it arrives. If storage is full, the oldest entries are dropped.
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
   - Say where your business is (comes first when found): a visible address block, a one-sentence template in the page language (Hebrew or English), title and heading advice, LocalBusiness JSON-LD filled from the site's own structured data, and a Google Business Profile note.
   - `/llms.txt` template with the site's own internal links (from the AI view).
   - Head tags: canonical, viewport, Open Graph, description, RSS, modified time, author, `lang`.
   - robots.txt and sitemap with AI bots, plus the "search yes, training no" option and any sitemap findings.
   - Content structure (including heading findings), smaller improvements, optional agent readiness basics (Content Signals, a Link header and Markdown for agents, only when the Agent readiness result is loaded and the check failed), optional AI discovery files, and other findings with their "why".
5. Full findings list and "after you deploy" steps.

## Security

- SSRF protection in every function (DNS resolved once, private ranges blocked, redirects checked). The Python functions use the engine's client, the Node functions have their own check.
- No user data is stored on the server. History lives in the browser.
- Security headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy`.
- All values shown in the page are HTML-escaped. Links are created only for `http(s)` URLs.
- Public with no login. Abuse control is the basic in-memory rate limit (see the roadmap).
