"""AI Site Scan API.

GET /api/scan?url=https://example.com

Runs the geo-optimizer-skill audit (MIT, github.com/auriti-labs/geo-optimizer-skill)
and adds an "AI view": the page as a fetcher that does not run JavaScript sees it.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# The Vercel file system is read-only except /tmp. The engine may write cache files.
os.environ.setdefault("HOME", "/tmp")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")

from bs4 import BeautifulSoup, Comment, NavigableString, Tag  # noqa: E402

from geo_optimizer.cli.formatters import format_audit_json  # noqa: E402
from geo_optimizer.core.audit import run_full_audit  # noqa: E402
from geo_optimizer.utils.http import fetch_url  # noqa: E402
from geo_optimizer.utils.validators import normalize_url_scheme, validate_public_url  # noqa: E402

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
# AI view
# ---------------------------------------------------------------------------
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
BOT_UAS = {
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
}
DROP_TAGS = {"script", "style", "template", "svg", "canvas", "iframe", "object", "embed", "link", "meta", "head"}
BLOCK_TAGS = {"p", "div", "section", "article", "main", "header", "footer", "aside", "nav", "form", "figure",
              "blockquote", "address", "details", "summary", "dl", "dd", "dt", "fieldset"}
MAX_MD_CHARS = 40000
SPA_ROOT_IDS = {"root", "app", "__next", "__nuxt", "svelte", "q-app", "react-root"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _to_markdown(node: Tag, base: str) -> str:
    out: list[str] = []

    def walk(el, ctx: dict):
        if isinstance(el, Comment):
            return
        if isinstance(el, NavigableString):
            t = re.sub(r"\s+", " ", str(el))
            if t.strip():
                out.append(t)
            return
        if not isinstance(el, Tag):
            return
        name = el.name.lower()
        if name in DROP_TAGS:
            return
        if el.get("hidden") is not None or el.get("aria-hidden") == "true":
            return
        style = (el.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            return
        if re.fullmatch(r"h[1-6]", name):
            txt = _clean(el.get_text(" "))
            if txt:
                out.append(f"\n\n{'#' * int(name[1])} {txt}\n\n")
            return
        if name == "br":
            out.append("\n")
            return
        if name == "hr":
            out.append("\n\n---\n\n")
            return
        if name == "img":
            alt = _clean(el.get("alt") or "")
            out.append(f"[image: {alt}]" if alt else "[image: no alt text]")
            return
        if name == "a":
            txt = _clean(el.get_text(" "))
            href = el.get("href") or ""
            if txt and href and not href.startswith(("javascript:", "#")):
                out.append(f"[{txt}]({href})")
            elif txt:
                out.append(txt)
            return
        if name in ("ul", "ol"):
            out.append("\n")
            for i, li in enumerate(el.find_all("li", recursive=False), 1):
                bullet = f"{i}." if name == "ol" else "-"
                before = len(out)
                for c in li.children:
                    walk(c, ctx)
                item = _clean("".join(out[before:]))
                del out[before:]
                if item:
                    out.append(f"\n{bullet} {item}")
            out.append("\n\n")
            return
        if name == "table":
            rows = []
            for tr in el.find_all("tr"):
                cells = [_clean(c.get_text(" ")).replace("|", "/") for c in tr.find_all(["th", "td"])]
                if any(cells):
                    rows.append(cells)
            if rows:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                out.append("\n\n| " + " | ".join(rows[0]) + " |\n|" + "---|" * w + "\n")
                for r in rows[1:]:
                    out.append("| " + " | ".join(r) + " |\n")
                out.append("\n")
            return
        if name in ("strong", "b"):
            txt = _clean(el.get_text(" "))
            if txt:
                out.append(f"**{txt}**")
            return
        if name == "pre":
            out.append("\n\n```\n" + el.get_text().strip() + "\n```\n\n")
            return
        if name in ("button", "input", "select", "textarea"):
            label = _clean(el.get_text(" ") or el.get("value") or el.get("placeholder") or el.get("aria-label") or "")
            if label:
                out.append(f"[{name}: {label}]")
            return
        block = name in BLOCK_TAGS or name == "li"
        if block:
            out.append("\n\n")
        for c in el.children:
            walk(c, ctx)
        if block:
            out.append("\n\n")

    walk(node, {})
    md = "".join(out)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n[ \t]+", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def _status_with_ua(url: str, ua: str) -> dict:
    r, err = fetch_url(url, timeout=8, max_size=3_000_000, headers={"User-Agent": ua})
    if r is None:
        return {"status": None, "error": err, "bytes": 0}
    return {"status": r.status_code, "error": None, "bytes": len(r.content or b"")}


def build_ai_view(url: str) -> dict:
    r, err = fetch_url(url, timeout=10, max_size=5_000_000, headers={"User-Agent": BROWSER_UA})
    if r is None:
        return {"ok": False, "error": err}
    html = r.text or ""
    soup = BeautifulSoup(html, "lxml")

    scripts = soup.find_all("script")
    ext_scripts = [s for s in scripts if s.get("src")]
    inline_js_bytes = sum(len(s.string or "") for s in scripts if not s.get("src") and (s.get("type") or "").lower() not in ("application/ld+json", "application/json"))
    jsonld_types: list[str] = []
    for s in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        try:
            data = json.loads(s.string or "")
            items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
            for it in items:
                t = it.get("@type") if isinstance(it, dict) else None
                if isinstance(t, list):
                    jsonld_types += [str(x) for x in t]
                elif t:
                    jsonld_types.append(str(t))
        except Exception:
            jsonld_types.append("(invalid JSON-LD)")

    def meta(name=None, prop=None):
        el = soup.find("meta", attrs={"name": name}) if name else soup.find("meta", attrs={"property": prop})
        return _clean(el.get("content")) if el and el.get("content") else None

    title = _clean(soup.title.get_text()) if soup.title else None
    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None
    canonical = soup.find("link", rel=lambda v: v and "canonical" in v)

    body = soup.body or soup
    main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    md = _to_markdown(body, url)
    body_text = _clean(body.get_text(" ")) if body else ""
    # text without script/style
    for t in body.find_all(["script", "style", "template", "noscript"]):
        t.decompose()
    visible_text = _clean(body.get_text(" "))
    words = len(visible_text.split())
    main_words = len(_clean(main.get_text(" ")).split()) if main else None

    spa_roots = []
    for el in soup.find_all(id=True):
        if el.get("id", "").lower() in SPA_ROOT_IDS:
            spa_roots.append({"id": el.get("id"), "words": len(_clean(el.get_text(" ")).split())})
    has_noscript_warning = bool(re.search(r"enable javascript|requires javascript|javascript is (disabled|required)", html, re.I))

    if words < 50 or has_noscript_warning or (spa_roots and all(x["words"] < 20 for x in spa_roots) and words < 200):
        js_risk = "high"
    elif words < 120:
        js_risk = "medium"
    else:
        js_risk = "low"

    truncated = len(md) > MAX_MD_CHARS
    bot_access = {name: _status_with_ua(url, ua) for name, ua in BOT_UAS.items()}

    return {
        "ok": True,
        "final_url": r.url,
        "status": r.status_code,
        "html_bytes": len(r.content or b""),
        "title": title,
        "description": meta(name="description"),
        "og_title": meta(prop="og:title"),
        "og_description": meta(prop="og:description"),
        "lang": lang,
        "canonical": canonical.get("href") if canonical else None,
        "jsonld_types": sorted(set(jsonld_types)),
        "word_count": words,
        "main_word_count": main_words,
        "has_main": main is not None,
        "script_count": len(scripts),
        "external_script_count": len(ext_scripts),
        "inline_js_bytes": inline_js_bytes,
        "spa_roots": spa_roots,
        "js_warning_text": has_noscript_warning,
        "js_risk": js_risk,
        "markdown": md[:MAX_MD_CHARS],
        "markdown_truncated": truncated,
        "markdown_chars": len(md),
        "bot_access": bot_access,
        "browser_status": r.status_code,
    }


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
    text = re.sub(r"^\[G-[A-Z-]+\]\s*", "", text)
    text = re.sub(r":\s*geo llms --base-url \S+\.?", ". You can generate one with a free llms.txt generator.", text)
    return _clean(text.replace("/chi-siamo", ""))


def build_actions(audit: dict, ai: dict) -> list[dict]:
    breakdown = audit.get("score_breakdown") or {}
    maxes = audit.get("score_max") or {}
    by_cat = audit.get("recommendations_by_category") or {}
    actions: list[dict] = []

    # Our own checks from the AI view come first when they matter most.
    if ai.get("ok"):
        blocked = [n for n, v in (ai.get("bot_access") or {}).items() if v.get("status") and v["status"] >= 400]
        if blocked:
            actions.append({
                "category": "access", "label": "Server access for AI bots", "priority": "critical", "gain": None,
                "items": [f"Your server returns an error to {', '.join(blocked)} (for example HTTP {ai['bot_access'][blocked[0]]['status']}), even if robots.txt allows them. Check your firewall, CDN bot protection (for example Cloudflare 'Block AI bots') or WAF rules."],
            })
        if ai.get("js_risk") == "high":
            actions.append({
                "category": "rendering", "label": "Content needs JavaScript", "priority": "critical", "gain": None,
                "items": [f"Only {ai.get('word_count', 0)} words are in the HTML before JavaScript runs. Most AI fetchers do not run JavaScript, so they see an almost empty page. Use server-side rendering or static generation (for example Next.js SSR/SSG, Astro, or prerendering)."],
            })
        elif ai.get("js_risk") == "medium":
            actions.append({
                "category": "rendering", "label": "Little text without JavaScript", "priority": "high", "gain": None,
                "items": [f"The HTML has {ai.get('word_count', 0)} words before JavaScript runs. Make sure your key content (what you do, prices, FAQ) is in the server HTML."],
            })
        if not ai.get("has_main"):
            actions.append({
                "category": "structure", "label": "Mark the main content", "priority": "low", "gain": None,
                "items": ["Wrap the main page content in a <main> element. This helps AI tools separate it from menus and footers."],
            })

    for cat, recs in by_cat.items():
        if not recs:
            continue
        mx = maxes.get(cat)
        sc = breakdown.get(cat)
        gain = (mx - sc) if isinstance(mx, int) and isinstance(sc, int) else None
        items = [_clean_rec(r, audit.get("url", "")) for r in recs]
        prio = "high" if (gain or 0) >= 8 else "medium" if (gain or 0) >= 3 else "low"
        if cat == "blockers":
            prio = "high"
        if any(r.lower().startswith(("critical", "[critical")) or "noindex" in r.lower() or "noai" in r.lower() for r in recs):
            prio = "critical"
        if cat == "blockers" and any(a["category"] == "rendering" for a in actions):
            items = [i for i in items if "javascript" not in i.lower()]
            if not items:
                continue
        actions.append({
            "category": cat, "label": CATEGORY_LABELS.get(cat, cat), "priority": prio, "gain": gain,
            "score": sc, "max": mx, "items": items,
        })

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda a: (order[a["priority"]], -(a.get("gain") or 0)))
    return actions


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
class ScanError(Exception):
    pass


def scan(url: str) -> dict:
    t0 = time.time()
    result = run_full_audit(url, load_plugins=False)
    audit = json.loads(format_audit_json(result))
    try:
        ai = build_ai_view(url)
    except Exception as e:  # keep the report even if the AI view fails
        ai = {"ok": False, "error": f"AI view failed: {type(e).__name__}"}
    if audit.get("error") and not ai.get("ok"):
        raise ScanError(f"Could not reach the site: {audit['error']}")
    jsr = audit.get("js_rendering") or {}
    if ai.get("ok") and jsr.get("checked"):
        ai["framework"] = jsr.get("framework_detected") or None
        if jsr.get("js_dependent") and ai.get("js_risk") != "high":
            ai["js_risk"] = "high"
    return {
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
        "actions": build_actions(audit, ai),
        "ai_view": ai,
        "audit": audit,
        "duration_ms": int((time.time() - t0) * 1000),
    }


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
            import traceback, sys
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
        print(f"scan request host={p.hostname}", flush=True)
        ok, err = validate_public_url(url)
        print(f"validate host={p.hostname} ok={ok} err={err}", flush=True)
        if not ok:
            return self._send(400, {"error": f"URL not allowed: {err}"})

        key = url.rstrip("/").lower()
        with _lock:
            hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            data = dict(hit[1])
            data["cached"] = True
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
        with _lock:
            _cache[key] = (time.time(), data)
            if len(_cache) > 200:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:50]:
                    _cache.pop(k, None)
        return self._send(200, data)
