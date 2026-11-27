"""AI Site Scan: agent readiness API.

GET /api/agent?url=https://example.com

Checks the standards that let AI agents find and use a site: robots.txt rules for AI, Content Signals,
Link headers, Markdown negotiation, MCP and A2A cards, Agent Skills, API catalog, OAuth metadata, payment
protocols and more (see _agentready.py for the full list and the rules).

It is a separate function on purpose: the main scan stays fast, and the page asks for this one afterwards.
About 25 small requests go to the site, at most 6 at a time, all through the engine's SSRF-safe client.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, urljoin, urlparse

# The Vercel file system is read-only except /tmp. The engine may write cache files.
os.environ.setdefault("HOME", "/tmp")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import socket  # noqa: E402

# On Vercel, a failed DNS lookup can raise a plain OSError instead of socket.gaierror (the engine only catches gaierror).
_orig_getaddrinfo = socket.getaddrinfo


def _safe_getaddrinfo(*args, **kwargs):
    try:
        return _orig_getaddrinfo(*args, **kwargs)
    except socket.gaierror:
        raise
    except OSError as e:
        raise socket.gaierror(str(e)) from e


socket.getaddrinfo = _safe_getaddrinfo

import _agentready as A  # noqa: E402
from _posthog import capture_unhandled_exception  # noqa: E402
from _posthog_logs import posthog_logs  # noqa: E402
from geo_optimizer.utils.http_async import fetch_url_async  # noqa: E402
from geo_optimizer.utils.validators import normalize_url_scheme, validate_public_url  # noqa: E402

APP_VERSION = "2.4.0"  # keep in step with the footer in index.html
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
RATE_WINDOW_S = 600
RATE_MAX = int(os.environ.get("AGENT_RATE_MAX", "20"))
CACHE_TTL_S = 600
CHECK_TIMEOUT_S = 28  # the function limit is 40 s
CONCURRENCY = 6  # polite: at most 6 requests at once against one site
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


async def _get(url: str, headers: dict, sem: asyncio.Semaphore, *, timeout: int = 6, max_size: int = 600_000) -> dict:
    async with sem:
        r, err = await fetch_url_async(url, timeout=timeout, max_size=max_size, headers=headers)
    if r is None:
        return {"status": None, "error": err, "content": b"", "headers": {}, "url": url, "req": url}
    return {"status": r.status_code, "error": None, "content": r.content or b"",
            "headers": {k.lower(): v for k, v in r.headers.items()}, "url": str(r.url), "req": url}


_DOH = ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve")
_RTYPE = {"SVCB": 64, "HTTPS": 65, "TXT": 16}


async def _doh(name: str, rtype: str, sem: asyncio.Semaphore) -> dict:
    """One DNS lookup over HTTPS (the answer carries the DNSSEC flag). NXDOMAIN is an empty answer, not an error."""
    last = "no answer"
    for base in _DOH:
        r = await _get(f"{base}?name={quote(name)}&type={_RTYPE[rtype]}", {"Accept": "application/dns-json"}, sem, timeout=5, max_size=200_000)
        if r["status"] == 200:
            try:
                j = json.loads(r["content"])
            except Exception:
                last = "bad answer"
                continue
            answers = [str(a.get("data", "")).strip('"') for a in j.get("Answer") or [] if a.get("type") in (16, 64, 65)]
            return {"name": name, "type": rtype, "answers": answers, "ad": bool(j.get("AD")), "error": None}
        last = f"HTTP {r['status']}" if r["status"] else str(r["error"] or "no answer")[:60]
    return {"name": name, "type": rtype, "answers": [], "ad": False, "error": last}


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def gather(page_url: str) -> dict:
    """Fetch every file the checks read. Whatever has not arrived when the time is up counts as 'could not check'."""
    p = urlparse(page_url)
    origin = f"{p.scheme}://{p.netloc}"
    host = (p.hostname or "").removeprefix("www.")
    sem = asyncio.Semaphore(CONCURRENCY)
    P: dict = {"dns": {}}

    async def fetch(name: str, url: str, extra: dict | None = None, max_size: int = 600_000):
        P[name] = await _get(url, {**BROWSER_HEADERS, **(extra or {})}, sem, max_size=max_size, timeout=10 if max_size > 2_000_000 else 6)

    async def lookup(key: str, label: str, rtype: str):
        P["dns"][key] = await _doh(f"{label}.{host}", rtype, sem)

    sizes = {"sitemap": 4_000_000, "home": 1_500_000, "robots": 1_000_000, "openapi": 10_000_000}  # others are small files
    first = [fetch(n, origin + path, extra, sizes.get(n, 600_000)) for n, (path, extra) in A.PROBES.items()]
    first.append(fetch("page_md", page_url, {"Accept": "text/markdown, text/html;q=0.8"}, 1_500_000))
    if not _is_ip(host):
        first += [lookup(k, lbl, t) for k, (lbl, t) in A.DNS_PROBES.items()]

    async def run_all(coros: list):
        tasks = [asyncio.ensure_future(c) for c in coros]
        done, pending = await asyncio.wait(tasks, timeout=max(1.0, deadline - time.time()))
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc:
                print(f"agent probe failed: {type(exc).__name__}", file=sys.stderr)

    deadline = time.time() + CHECK_TIMEOUT_S
    await run_all(first)

    # second wave: files that only make sense once the first ones are read
    second = []
    robots_ok = A._state(P.get("robots"))[0] == "ok"
    robots = A.parse_robots(A._text(P["robots"])) if robots_ok else {"sitemaps": [], "agentmap": []}
    if robots["sitemaps"]:
        u = urljoin(origin + "/", robots["sitemaps"][0])
        if u.rstrip("/") != (origin + "/sitemap.xml"):
            second.append(fetch("sitemap_declared", u, {"Accept": "application/xml, text/xml, */*;q=0.5"}, 4_000_000))
    html = A._text(P["home"]) if A._state(P.get("home"))[0] == "ok" else ""
    if A._json(P.get("ard"))[0] != "ok":
        cands = list(robots["agentmap"])
        m = re.search(r'<link[^>]+rel=["\']ai-catalog["\'][^>]*href=["\']([^"\']+)["\']', html, re.I) or re.search(r'<link[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\']ai-catalog["\']', html, re.I)
        if m:
            cands.append(m.group(1))
        for a in (P["dns"].get("dns_catalog") or {}).get("answers", []):
            mm = re.search(r"url=(\S+)", a)
            if mm:
                cands.append(mm.group(1))
        if cands:
            second.append(fetch("ard_alt", urljoin(origin + "/", cands[0]), JSON_ACCEPT))
    out, _, poppy = A._json(P.get("poppy"))
    if out == "ok" and isinstance(poppy, dict) and A._declares_interfaces(poppy):
        iss = (poppy.get("auth") or {}).get("issuer") if isinstance(poppy.get("auth"), dict) else None
        url = A.poppy_issuer_url(iss) if isinstance(iss, str) else None
        if url:
            second.append(fetch("poppy_issuer", url, JSON_ACCEPT))
    if not (re.search(r"modelContext", html) or re.search(r"<form\b[^>]*\btoolname\s*=", html, re.I)):
        P["scripts"] = []
        srcs = []
        for s in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I):
            u = urljoin(origin + "/", s)
            if urlparse(u).netloc == p.netloc and u not in srcs:
                srcs.append(u)

        async def scripts():
            res = await asyncio.gather(*[_get(u, {**BROWSER_HEADERS, "Accept": "*/*"}, sem, max_size=400_000) for u in srcs[:5]])
            P["scripts"] = [r for r in res if r["status"] == 200]

        if srcs:
            second.append(scripts())
    if second:
        await run_all(second)
    return P


JSON_ACCEPT = A.JSON_ACCEPT


def check(page_url: str) -> dict:
    t0 = time.time()
    P = asyncio.run(gather(page_url))
    host = (urlparse(page_url).hostname or "").removeprefix("www.")
    out = A.evaluate(P, host)
    out.update(app_version=APP_VERSION, url=page_url, scanned_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
               duration_ms=int((time.time() - t0) * 1000))
    return out


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

            capture_unhandled_exception(e)
            traceback.print_exc(file=sys.stderr)
            try:
                return self._send(500, {"error": f"Check failed: {type(e).__name__}"})
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
        fresh = (qs.get("fresh") or [""])[0] in ("1", "true")
        with _lock:
            hit = _cache.get(key)
        if hit and not fresh and time.time() - hit[0] < CACHE_TTL_S:
            data = dict(hit[1])
            data["cached"] = True
            return self._send(200, data)
        ip = (self.headers.get("x-forwarded-for") or self.client_address[0] or "?").split(",")[0].strip()
        allowed, retry = _rate_ok(ip)
        if not allowed:
            return self._send(429, {"error": f"Too many checks. Try again in {retry} seconds."}, {"Retry-After": str(retry)})
        try:
            data = check(url)
        except Exception as e:
            return self._send(500, {"error": f"Check failed: {type(e).__name__}: {str(e)[:200]}"})
        print(f"agent host={p.hostname} level={data['level']} passed={data['passed']}/{data['scored']} unable={data['unable']} ms={data['duration_ms']}", flush=True)
        posthog_logs.info(
            "agent_readiness_check_completed",
            extra={
                "level": data["level"],
                "passed_count": data["passed"],
                "scored_count": data["scored"],
                "unable_count": data["unable"],
                "duration_ms": data["duration_ms"],
                "fresh_check": fresh,
            },
        )
        with _lock:
            _cache[key] = (time.time(), data)
            if len(_cache) > 200:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:50]:
                    _cache.pop(k, None)
        return self._send(200, data)
