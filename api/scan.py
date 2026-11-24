"""AI Site Scan API.

GET /api/scan?url=https://example.com

Runs the geo-optimizer-skill audit (MIT, github.com/auriti-labs/geo-optimizer-skill) and adds
our own checks: the page as a fetcher without JavaScript sees it, 14 bot probes, a sitemap check,
server speed and page structure.

Speed: the engine's async audit, the bot probes and the file checks all run at the same time.
The engine's slow, one-by-one CDN check is fed from our parallel probes (same rules, same result),
so the score does not change and nothing is fetched twice.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import json
import os
import re
import statistics
import sys
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urljoin, urlparse

# The Vercel file system is read-only except /tmp. The engine may write cache files.
os.environ.setdefault("HOME", "/tmp")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # lets us import _checks on any runtime

import socket  # noqa: E402

# On Vercel, a failed DNS lookup can raise a plain OSError instead of socket.gaierror.
# The engine only catches gaierror, so convert it here.
_orig_getaddrinfo = socket.getaddrinfo


def _safe_getaddrinfo(*args, **kwargs):
    try:
        return _orig_getaddrinfo(*args, **kwargs)
    except socket.gaierror:
        raise
    except OSError as e:
        raise socket.gaierror(str(e)) from e


socket.getaddrinfo = _safe_getaddrinfo

import _checks as C  # noqa: E402
import geo_optimizer.core.audit as _engine_audit  # noqa: E402
from geo_optimizer.cli.formatters import format_audit_json  # noqa: E402
from geo_optimizer.core.audit import run_full_audit, run_full_audit_async  # noqa: E402
from geo_optimizer.models.results import CdnAiCrawlerResult  # noqa: E402
from geo_optimizer.utils.http_async import fetch_url_async  # noqa: E402
from geo_optimizer.utils.validators import normalize_url_scheme, validate_public_url  # noqa: E402

APP_VERSION = "2.1.0"  # keep in step with the footer in index.html
ENGINE = "geo-optimizer-skill"
try:
    from importlib.metadata import version as _v

    ENGINE_VERSION = _v("geo-optimizer-skill")
except Exception:  # pragma: no cover
    ENGINE_VERSION = "unknown"

# ---------------------------------------------------------------------------
# Basic rate limit and cache (per function instance, best effort)
# ---------------------------------------------------------------------------
RATE_WINDOW_S = 600
RATE_MAX = int(os.environ.get("RATE_MAX", "10"))
CACHE_TTL_S = 600
SCAN_TIMEOUT_S = 50  # the function limit is 60 s
BOT_CONCURRENCY = 6  # polite: at most 6 probes at once against one site
_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)
_cache: dict[str, tuple[float, dict]] = {}


def _rate_ok(ip: str) -> tuple[bool, int]:
    now = time.time()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > RATE_WINDOW_S:
            q.popleft()
        if len(q) >= RATE_MAX:
            return False, int(RATE_WINDOW_S - (now - q[0])) + 1
        q.append(now)
        return True, 0


# ---------------------------------------------------------------------------
# Engine hook: feed its CDN check from our parallel probes
# ---------------------------------------------------------------------------
_cdn_future_var: contextvars.ContextVar = contextvars.ContextVar("cdn_future", default=None)
_orig_cdn_check = getattr(_engine_audit, "audit_cdn_ai_crawler", None)


def _cdn_check_from_probes(base_url: str):
    """Replaces the engine's sequential CDN check while a scan is running. Falls back to the original."""
    fut = _cdn_future_var.get()
    if fut is None:
        return _orig_cdn_check(base_url)
    try:
        return fut.result(timeout=25)
    except Exception:
        return CdnAiCrawlerResult()  # not checked, like the engine does when a site is unreachable


if _orig_cdn_check is not None:
    _engine_audit.audit_cdn_ai_crawler = _cdn_check_from_probes


# ---------------------------------------------------------------------------
# Fetching (all through the engine's SSRF-safe client: DNS pinning, redirect checks, size cap)
# ---------------------------------------------------------------------------
async def _get(url: str, headers: dict, *, timeout: int, max_size: int, sem: asyncio.Semaphore,
               retry: bool = False) -> dict:
    r = err = None
    ms = 0
    for attempt in range(2 if retry else 1):
        t0 = time.perf_counter()
        async with sem:
            r, err = await fetch_url_async(url, timeout=timeout, max_size=max_size, headers=headers)
        ms = int((time.perf_counter() - t0) * 1000)
        if r is not None and r.status_code in (429, 500, 502, 503, 504) and attempt == 0 and retry:
            await asyncio.sleep(0.7)  # one quiet retry for the page itself: transient errors are common
            continue
        break
    if r is None:
        return {"status": None, "error": err, "ms": ms, "content": b"", "headers": {}, "url": url, "hops": 0}
    return {"status": r.status_code, "error": None, "ms": ms, "content": r.content or b"",
            "headers": {k.lower(): v for k, v in r.headers.items()}, "url": str(r.url),
            "hops": getattr(r, "redirect_hops", 0)}


async def probe_files(origin: str) -> dict:
    """robots.txt (for the Sitemap line), then the sitemap itself."""
    sem = asyncio.Semaphore(1)
    robots = await _get(origin + "/robots.txt", C.BROWSER_HEADERS, timeout=8, max_size=1_000_000, sem=sem)
    declared: list[str] = []
    if robots["status"] == 200:
        declared = [urljoin(origin + "/", u) for u in C.sitemap_lines(C.decode_body(robots["content"]))]
    target = declared[0] if declared else origin + "/sitemap.xml"
    sm = await _get(target, C.BROWSER_HEADERS, timeout=8, max_size=8_000_000, sem=sem)
    parsed = C.parse_sitemap(target, sm["status"], sm["content"], sm["error"])
    parsed["declared"] = bool(declared)
    return {"declared": declared, "sitemap": parsed}


async def inspect_site(url: str, cdn_future: concurrent.futures.Future) -> dict:
    """The page as a browser sees it, 14 bot probes, robots.txt and the sitemap, all at once."""
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    sem = asyncio.Semaphore(BOT_CONCURRENCY)
    tasks: list[asyncio.Task] = []

    def spawn(coro):
        t = asyncio.create_task(coro)
        tasks.append(t)
        return t

    try:
        files_task = spawn(probe_files(origin))
        base_task = spawn(_get(url, C.BROWSER_HEADERS, timeout=10, max_size=10_000_000, sem=sem, retry=True))
        bot_tasks = {b["name"]: spawn(_get(url, {"User-Agent": b["ua"]}, timeout=8, max_size=5_000_000, sem=sem))
                     for b in C.BOTS}

        base = await base_task
        status = base["status"]
        page = None
        page_error = None
        page_task = None
        if status is not None:
            html = C.decode_body(base["content"], base["headers"].get("content-type", ""))
            page_task = spawn(asyncio.to_thread(C.analyze_html, html, base["url"]))

        async def settle(t):  # one bad probe must not lose the whole report
            try:
                return await t
            except Exception as e:
                return {"status": None, "error": type(e).__name__, "ms": 0, "content": b"", "headers": {}, "url": url, "hops": 0}

        summaries = {n: C.probe_summary(await settle(t)) for n, t in bot_tasks.items()}
        base_sum = C.probe_summary(base)
        rows = {b["name"]: C.judge_bot(summaries[b["name"]], base_sum, b) for b in C.BOTS}
        if not cdn_future.done():
            cdn_future.set_result(C.build_cdn_result(base_sum, base["headers"], rows))

        if page_task is not None:
            try:
                page = await page_task
            except Exception as e:  # keep the rest of the report
                page_error = f"AI view failed: {type(e).__name__}"
        files = await files_task
        ms = [base["ms"]] + [s["ms"] for s in summaries.values() if s["status"] and s["status"] < 500]
        challenge = status is not None and C.is_challenge_page(base_sum["head"], base_sum["bytes"])
        return {"base": base, "base_sum": base_sum, "page": page, "page_error": page_error, "rows": rows,
                "files": files, "ms": ms, "challenge": challenge}
    finally:
        if not cdn_future.done():
            cdn_future.set_result(CdnAiCrawlerResult())
        for t in tasks:
            if not t.done():
                t.cancel()


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------
CATEGORY_LABELS = {
    "robots": "AI crawler access (robots.txt)",
    "llms": "llms.txt file",
    "schema": "Structured data (JSON-LD)",
    "meta": "Title, description and meta tags",
    "content": "Content structure",
    "signals": "Language, feeds and freshness",
    "ai_discovery": "AI discovery files",
    "brand_entity": "Brand and entity signals",
    "google_ai": "Google AI readiness",
    "blockers": "Possible blockers",
    "other": "Other improvements",
}


def _clean_rec(text: str, base_url: str) -> str:
    text = re.sub(r"^[⚠️\s]+", "", text)
    text = re.sub(r"^\[G-[A-Z-]+\]\s*", "", text)
    text = re.sub(r":\s*geo llms --base-url \S+\.?", ". You can generate one with a free llms.txt generator.", text)
    # No long dashes in output: "X — Y" becomes "X: Y" (links) or "X. Y" (sentences).
    text = re.sub(r"\s+—\s+(?=https?://)", ": ", text)
    text = re.sub(r"\s+—\s+", ". ", text).replace("—", ", ")
    text = text.replace("/about or /chi-siamo link", "/about link").replace(" or /chi-siamo", "")
    return _clean_ws(text)


def _clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def build_actions(audit: dict, ai: dict, site: dict | None) -> list[dict]:
    breakdown = audit.get("score_breakdown") or {}
    maxes = audit.get("score_max") or {}
    by_cat = audit.get("recommendations_by_category") or {}
    actions: list[dict] = []

    if ai.get("ok"):
        status = ai.get("browser_status") or 0
        wc = ai.get("word_count", 0)
        words = f"{wc} word{'s' if wc != 1 else ''}"
        for a in (C.check_status(status, ai.get("title"), wc), C.check_blocked(status, bool(ai.get("browser_challenge")))):
            if a:
                actions.append(a)
        unusable = status >= 400 or bool(ai.get("browser_challenge"))
        if not unusable:
            actions += C.check_bots((site or {}).get("rows") or {}, status)
            if ai.get("js_risk") == "high":
                actions.append({
                    "category": "rendering", "label": "Content needs JavaScript", "priority": "critical", "gain": None,
                    "source": "extra",
                    "items": [("No words are" if wc == 0 else f"Only {words} {'is' if wc == 1 else 'are'}") + " in the HTML before JavaScript runs. Most AI fetchers do not run JavaScript, so they see an almost empty page. Use server-side rendering or static generation (for example Next.js SSR/SSG, Astro, or prerendering)."],
                })
            elif ai.get("js_risk") == "medium":
                actions.append({
                    "category": "rendering", "label": "Little text without JavaScript", "priority": "high", "gain": None,
                    "source": "extra",
                    "items": [f"The HTML has {words} before JavaScript runs. Make sure your key content (what you do, prices, FAQ) is in the server HTML."],
                })
            thin = C.check_thin(bool(ai.get("thin")), wc, ai.get("iframes", 0))
            if thin:
                actions.append(thin)
            if not ai.get("has_main") and not ai.get("thin"):
                actions.append({
                    "category": "structure", "label": "Mark the main content", "priority": "low", "gain": None,
                    "source": "extra",
                    "items": ["Wrap the main page content in a <main> element. This helps AI tools separate it from menus and footers."],
                })

    have = {a["category"] for a in actions}
    rows = (site or {}).get("rows") or {}
    sitemap_action = None
    extra: list[dict | None] = []
    if ai.get("ok") and (ai.get("browser_status") or 0) < 400 and not ai.get("browser_challenge"):
        files = (site or {}).get("files") or {}
        sitemap_action = C.check_sitemap(files.get("sitemap"), files.get("declared") or [])
        extra = [
            C.check_https(ai.get("final_url") or ""),
            C.check_bot_content(rows),
            C.check_performance((site or {}).get("ms") or [], ai.get("html_bytes", 0), ai.get("content_encoding")),
            C.check_language(ai.get("lang_note")),
            C.check_headings(ai["headings"]) if ai.get("headings") else None,
            C.check_links(ai["links"]) if ai.get("links") else None,
            C.check_title_desc(ai.get("title"), ai.get("description")),
            C.check_crawl(ai.get("robots_tokens") or [], ai.get("x_robots_tag")),
            C.check_cookie_banner(ai.get("markdown", "")),
            sitemap_action,
        ]

    for cat, recs in by_cat.items():
        if not recs:
            continue
        mx = maxes.get(cat)
        sc = breakdown.get(cat)
        gain = (mx - sc) if isinstance(mx, int) and isinstance(sc, int) else None
        items = [_clean_rec(r, audit.get("url", "")) for r in recs]
        if have & {"access", "status"}:
            items = [i for i in items if not re.match(r"Site returned HTTP \d+", i)]
        if "access" in have or "access_other" in have:
            items = [i for i in items if "blocks AI crawlers" not in i]  # we report it in more detail
        if have & {"rendering", "thin"}:
            items = [i for i in items if "javascript" not in i.lower()]
        if sitemap_action:  # ours is more specific than the engine's "no Sitemap: directive" note
            items = [i for i in items if not re.match(r"(robots\.txt has no sitemap|no robots\.txt, no declared sitemap)", i, re.I)]
        if not items:
            continue
        prio = "high" if (gain or 0) >= 8 else "medium" if (gain or 0) >= 3 else "low"
        if cat == "blockers":
            prio = "high"
        if any(r.lower().lstrip("⚠️ ").startswith(("critical", "[critical")) or "noindex" in r.lower() or "noai" in r.lower() for r in recs):
            prio = "critical"
        actions.append({
            "category": cat, "label": CATEGORY_LABELS.get(cat, cat), "priority": prio, "gain": gain,
            "score": sc, "max": mx, "items": items, "source": "engine",
        })

    actions += [a for a in extra if a]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda a: (order[a["priority"]], -(a.get("gain") or 0)))
    return actions


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------
class ScanError(Exception):
    pass


async def _run_engine(url: str):
    try:
        return await run_full_audit_async(url, load_plugins=False)
    except Exception as e:  # for example httpx missing: use the one-by-one engine instead
        print(f"async engine failed ({type(e).__name__}), using the sync engine", file=sys.stderr)
        return await asyncio.to_thread(run_full_audit, url, load_plugins=False)


async def scan_async(url: str) -> dict:
    t0 = time.time()
    cdn_future: concurrent.futures.Future = concurrent.futures.Future()
    token = _cdn_future_var.set(cdn_future)
    try:
        engine_task = asyncio.create_task(_run_engine(url))  # the task copies the context: it sees cdn_future
    finally:
        _cdn_future_var.reset(token)
    site_task = asyncio.create_task(inspect_site(url, cdn_future))
    try:
        result, site = await asyncio.wait_for(asyncio.gather(engine_task, site_task, return_exceptions=True),
                                              SCAN_TIMEOUT_S)
    except asyncio.TimeoutError:
        for t in (engine_task, site_task):
            t.cancel()
        raise ScanError("The scan took too long. The site may be slow or blocking scanners. Please try again.")
    t_engine = time.time()
    if isinstance(result, BaseException):
        raise result
    audit = json.loads(format_audit_json(result))
    ai, site_data = _make_ai_view(site)
    if audit.get("error") and not ai.get("ok"):
        raise ScanError(f"Could not reach the site: {audit['error']}")
    jsr = audit.get("js_rendering") or {}
    if ai.get("ok") and jsr.get("checked"):
        ai["framework"] = jsr.get("framework_detected") or None
        has_js = ai.get("external_script_count") or ai.get("spa_roots")
        if jsr.get("js_dependent") and ai.get("js_risk") != "high" and not ai.get("thin") and has_js:
            ai["js_risk"] = "high"
    rows = (site_data or {}).get("rows") or {}
    files = (site_data or {}).get("files") or {}
    sitemap = files.get("sitemap")
    blocked_scan = (ai.get("browser_status") or 0) >= 400 or bool(ai.get("browser_challenge"))
    actions = build_actions(audit, ai, site_data)
    return {
        "app_version": APP_VERSION,
        "url": url,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "engine": {"name": ENGINE, "version": ENGINE_VERSION,
                   "repo": "https://github.com/auriti-labs/geo-optimizer-skill"},
        "score": audit.get("score"),
        "band": audit.get("band"),
        "error": audit.get("error"),
        "categories": [
            {"key": k, "label": CATEGORY_LABELS.get(k, k), "score": (audit.get("score_breakdown") or {}).get(k),
             "max": v}
            for k, v in (audit.get("score_max") or {}).items()
        ],
        "penalty": (audit.get("score_breakdown") or {}).get("negative_penalty"),
        "actions": actions,
        "missed": C.build_missed(ai, audit, rows, files, audit.get("score"), actions),
        "wins": [] if blocked_scan or not ai.get("ok") else C.build_wins(ai, audit, rows, sitemap, (site_data or {}).get("ms") or []),
        "platforms": [] if blocked_scan else C.platforms_summary(audit.get("platform_citation")),
        "trust": None if blocked_scan else C.trust_summary(audit.get("trust_stack")),
        "ai_view": ai,
        "audit": audit,
        "duration_ms": int((time.time() - t0) * 1000),
        "timings": {"engine_ms": audit.get("audit_duration_ms"), "wait_ms": int((t_engine - t0) * 1000)},
    }


def _make_ai_view(site) -> tuple[dict, dict | None]:
    """Combine the page, the headers and the bot rows into the ai_view the report shows."""
    if isinstance(site, BaseException):
        return {"ok": False, "error": f"AI view failed: {type(site).__name__}"}, None
    base, page = site["base"], site["page"]
    if base["status"] is None:
        return {"ok": False, "error": base["error"], "bot_access": C.public_rows(site["rows"])}, site
    if page is None:
        return {"ok": False, "error": site["page_error"] or "AI view failed", "browser_status": base["status"],
                "bot_access": C.public_rows(site["rows"])}, site
    ai = dict(page)
    ai.update({
        "ok": True,
        "final_url": base["url"],
        "status": base["status"],
        "browser_status": base["status"],
        "html_bytes": len(base["content"]),
        "content_encoding": base["headers"].get("content-encoding"),
        "x_robots_tag": base["headers"].get("x-robots-tag"),
        "response_ms": int(statistics.median([m for m in site["ms"] if m])) if any(site["ms"]) else None,
        "bot_access": C.public_rows(site["rows"]),
        "bots_probed": len(site["rows"]),
        "browser_challenge": bool(site.get("challenge")),
        "sitemap": (site.get("files") or {}).get("sitemap"),
    })
    return ai, site


def scan(url: str) -> dict:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(scan_async(url))
    # A loop already runs in this thread (some runtimes do that): use a fresh thread for ours.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, scan_async(url)).result()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict, extra_headers: dict | None = None):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            return self._get()
        except BaseException as e:  # never crash the function
            import traceback

            traceback.print_exc(file=sys.stderr)
            try:
                return self._send(500, {"error": f"Scan failed: {type(e).__name__}"})
            except Exception:
                return None

    def _get(self):
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("url") or [""])[0].strip()
        if not raw or len(raw) > 2048:
            return self._send(400, {"error": "Enter a URL, for example https://example.com"})
        url = normalize_url_scheme(raw)
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        p = urlparse(url)
        if not p.hostname or "." not in p.hostname:
            return self._send(400, {"error": "This does not look like a valid public URL."})
        ok, err = validate_public_url(url)
        if not ok:
            if err and "DNS" in err:
                return self._send(400, {"error": "This domain could not be found. Check the spelling."})
            return self._send(400, {"error": f"URL not allowed: {err}"})

        key = url.rstrip("/").lower()
        fresh = (qs.get("fresh") or [""])[0] in ("1", "true")  # a rescan must not return an old answer
        with _lock:
            hit = _cache.get(key)
        if hit and not fresh and time.time() - hit[0] < CACHE_TTL_S:
            data = dict(hit[1])
            data["cached"] = True
            data["cached_age_s"] = int(time.time() - hit[0])
            return self._send(200, data)

        ip = (self.headers.get("x-forwarded-for") or self.client_address[0] or "?").split(",")[0].strip()
        allowed, retry = _rate_ok(ip)
        if not allowed:
            return self._send(429, {"error": f"Too many scans. Try again in {retry} seconds."},
                              {"Retry-After": str(retry)})
        try:
            data = scan(url)
        except ScanError as e:
            return self._send(502, {"error": str(e)[:300]})
        except Exception as e:
            return self._send(500, {"error": f"Scan failed: {type(e).__name__}: {str(e)[:200]}"})
        print(f"scan host={p.hostname} score={data.get('score')} total_ms={data['duration_ms']} "
              f"engine_ms={data['timings']['engine_ms']} actions={len(data['actions'])}", flush=True)
        with _lock:
            _cache[key] = (time.time(), data)
            if len(_cache) > 200:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:50]:
                    _cache.pop(k, None)
        return self._send(200, data)
