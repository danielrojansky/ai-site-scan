"""Page and bot-probe checks for AI Site Scan.

Pure functions, no network. scan.py fetches the pages and calls these.
The leading underscore keeps Vercel from treating this file as an endpoint.
"""

from __future__ import annotations

import gzip
import json
import re
import statistics
import zlib
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from bs4.dammit import UnicodeDammit
from geo_optimizer.core.audit_cdn import classify_block_pattern
from geo_optimizer.models.results import CdnAiCrawlerResult

# ---------------------------------------------------------------------------
# Bots we imitate
# ---------------------------------------------------------------------------
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _compat(name: str, ver: str, url: str) -> str:
    return f"Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; {name}/{ver}; +{url})"


# role: training = collects pages for model training, search = builds AI search answers,
# user = fetches a page live when a person asks an assistant about it.
# core: the bots whose block is a critical problem for AI visibility.
BOTS: list[dict] = [
    dict(name="GPTBot", vendor="OpenAI", role="training", core=True,
         ua=_compat("GPTBot", "1.2", "https://openai.com/gptbot")),
    dict(name="OAI-SearchBot", vendor="OpenAI", role="search", core=True,
         ua=_compat("OAI-SearchBot", "1.0", "https://openai.com/searchbot")),
    dict(name="ChatGPT-User", vendor="OpenAI", role="user", core=True,
         ua=_compat("ChatGPT-User", "1.0", "https://openai.com/bot")),
    dict(name="ClaudeBot", vendor="Anthropic", role="training", core=True,
         ua="Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)"),
    dict(name="Claude-SearchBot", vendor="Anthropic", role="search", core=True,
         ua=_compat("Claude-SearchBot", "1.0", "https://www.anthropic.com/claude-searchbot")),
    dict(name="Claude-User", vendor="Anthropic", role="user", core=True,
         ua="Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)"),
    dict(name="PerplexityBot", vendor="Perplexity", role="search", core=True,
         ua=_compat("PerplexityBot", "1.0", "https://perplexity.ai/perplexitybot")),
    dict(name="Perplexity-User", vendor="Perplexity", role="user", core=True,
         ua=_compat("Perplexity-User", "1.0", "https://perplexity.ai/perplexity-user")),
    dict(name="Googlebot", vendor="Google", role="search", core=False,
         ua="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    dict(name="Bingbot", vendor="Microsoft", role="search", core=False,
         ua="Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"),
    dict(name="Applebot", vendor="Apple", role="search", core=False,
         ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/13.1.1 Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)"),
    dict(name="CCBot", vendor="Common Crawl", role="training", core=False,
         ua="CCBot/2.0 (https://commoncrawl.org/faq/)"),
    dict(name="Amazonbot", vendor="Amazon", role="search", core=False,
         ua="Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Amazonbot/0.1; "
            "+https://developer.amazon.com/support/amazonbot) Chrome/119.0.6045.214 Safari/537.36"),
    dict(name="meta-externalagent", vendor="Meta", role="training", core=False,
         ua="meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)"),
]
BOTS_BY_NAME = {b["name"]: b for b in BOTS}

# The six bots geo-optimizer-skill's own CDN check requests, in its order. We feed
# its result from our parallel probes so the engine score and report stay identical.
ENGINE_CDN_BOTS = ("GPTBot", "OAI-SearchBot", "PerplexityBot", "Claude-SearchBot", "Googlebot", "Applebot")

_BLOCK_STATUSES = {403, 429, 451, 503}
# Same rule as the engine: specific block/challenge pages count at any size; generic words
# ("captcha", "blocked") count only when the bot did not receive the page.
_STRONG = (
    "cf-browser-verification", "challenge-platform", "just a moment", "checking your browser", "ray id",
    "bot detection", "attention required! | cloudflare", "cf-chl-", "incapsula incident",
    "pardon our interruption", "px-captcha", "captcha-delivery.com", "errors.edgesuite.net",
)
_WEAK = (
    "access denied", "captcha", "blocked", "forbidden", "verify you are human", "are you a robot",
    "unusual traffic from your",
)
_CDN_HEADERS = {
    "cf-ray": "cloudflare", "cf-cache-status": "cloudflare", "x-amz-cf-id": "aws-cloudfront",
    "x-amz-request-id": "aws", "x-akamai-transformed": "akamai", "x-cdn": "", "x-served-by": "",
    "x-vercel-id": "vercel", "server": "",
}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------
def decode_body(content: bytes, content_type: str = "") -> str:
    """Decode a page the way a browser would: header charset, then meta charset, then guess."""
    m = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    known = [m.group(1)] if m else []
    try:
        return UnicodeDammit(content, known_definite_encodings=known, is_html=True).unicode_markup or ""
    except Exception:
        return content.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Bot probes
# ---------------------------------------------------------------------------
def probe_summary(resp: dict) -> dict:
    """Shrink a fetched response to the numbers the checks need (so bodies can be freed)."""
    content = resp.get("content") or b""
    text = content.decode("utf-8", "replace")
    return {
        "status": resp.get("status"), "error": resp.get("error"), "ms": resp.get("ms", 0),
        "bytes": len(content), "chars": len(text), "head": text[:6000].lower(),
    }


def judge_bot(probe: dict, base: dict, bot: dict) -> dict:
    """Turn one probe into the row shown in the report.

    "blocked" and "challenge" follow the engine's rules. "different" is ours: the bot got HTTP 200
    but a page of a very different size than the normal browser did.
    """
    status = probe.get("status")
    chars = probe.get("chars", 0)
    base_ok = base.get("status") == 200 and base.get("chars", 0) > 0
    ratio = round(chars / base["chars"], 2) if status == 200 and base_ok else None
    e_blocked = status is None or status in _BLOCK_STATUSES or (ratio is not None and chars > 0 and ratio < 0.3)
    head = probe.get("head", "")
    strong = any(s in head for s in _STRONG)
    weak = any(w in head for w in _WEAK)
    delivers = ratio is not None and 0.7 <= ratio <= 1.5
    e_challenge = strong or (weak and not delivers)
    refused = status is not None and status >= 400 and (base.get("status") or 0) < 400
    if status is None:
        verdict = "error"
    elif e_challenge:
        verdict = "challenge"
    elif e_blocked or refused:
        verdict = "blocked"
    elif ratio is not None and (ratio < 0.7 or ratio > 1.5):
        verdict = "different"
    else:
        verdict = "ok"
    return {
        "status": status, "error": probe.get("error"), "bytes": probe.get("bytes", 0), "ms": probe.get("ms", 0),
        "ratio": ratio, "verdict": verdict, "vendor": bot["vendor"], "role": bot["role"], "core": bot["core"],
        "_e_blocked": e_blocked, "_e_challenge": e_challenge, "_chars": chars,
    }


def build_cdn_result(base: dict, base_headers: dict, rows: dict[str, dict]) -> CdnAiCrawlerResult:
    """Build the engine's CdnAiCrawlerResult from our parallel probes (same rules, same fields)."""
    r = CdnAiCrawlerResult()
    if base.get("status") is None:  # not reachable even as a browser: the engine skips the check
        return r
    r.browser_status = base["status"]
    r.browser_content_length = base["chars"]
    headers = {k.lower(): v for k, v in (base_headers or {}).items()}
    for key, cdn in _CDN_HEADERS.items():
        if key in headers:
            r.cdn_headers[key] = headers[key]
            if cdn and not r.cdn_detected:
                r.cdn_detected = cdn
    server = headers.get("server", "").lower()
    if "cloudflare" in server:
        r.cdn_detected = "cloudflare"
    elif "akamai" in server:
        r.cdn_detected = "akamai"
    for name in ENGINE_CDN_BOTS:
        row = rows.get(name)
        if row is None:
            continue
        r.bot_results.append({
            "bot": name, "status": row["status"] or 0, "content_length": row.get("_chars", 0),
            "blocked": row["_e_blocked"], "challenge_detected": row["_e_challenge"],
        })
    r.checked = True
    r.any_blocked = any(b["blocked"] or b["challenge_detected"] for b in r.bot_results)
    r.block_pattern = classify_block_pattern(r.bot_results)
    return r


_PAGE_CHALLENGE = (
    "just a moment", "attention required! | cloudflare", "checking your browser", "cf-browser-verification",
    "pardon our interruption", "incapsula incident", "px-captcha", "captcha-delivery.com", "awswafintegration", "gokuprops",
)


def is_challenge_page(head: str, size: int) -> bool:
    """True when the page we got (as a normal browser) is a bot check page, not the site."""
    return size < 40_000 and any(t in head for t in _PAGE_CHALLENGE)


def public_rows(rows: dict[str, dict]) -> dict[str, dict]:
    """Rows for the JSON response: drop the private engine flags."""
    return {n: {k: v for k, v in r.items() if not k.startswith("_")} for n, r in rows.items()}


# ---------------------------------------------------------------------------
# Page analysis
# ---------------------------------------------------------------------------
DROP_TAGS = {"script", "style", "template", "svg", "canvas", "iframe", "object", "embed", "link", "meta", "head"}
BLOCK_TAGS = {"p", "div", "section", "article", "main", "header", "footer", "aside", "nav", "form", "figure",
              "blockquote", "address", "details", "summary", "dl", "dd", "dt", "fieldset"}
MAX_MD_CHARS = 40000
SPA_ROOT_IDS = {"root", "app", "__next", "__nuxt", "svelte", "q-app", "react-root"}
GENERIC_LINKS = {"click here", "here", "read more", "learn more", "more", "more info", "link", "this page",
                 "details", "click", "tap here", "see more", "view more", "continue", "go", "read on", "find out more"}


def to_markdown(node: Tag) -> str:
    """The page as plain Markdown: roughly what a fetcher without JavaScript can read."""
    out: list[str] = []

    def walk(el):
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
                    walk(c)
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
            walk(c)
        if block:
            out.append("\n\n")

    walk(node)
    md = "".join(out)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n[ \t]+", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def _hidden(el: Tag) -> bool:
    for node in (el, *el.parents):
        if not isinstance(node, Tag):
            continue
        if node.get("hidden") is not None or node.get("aria-hidden") == "true":
            return True
        style = (node.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            return True
    return False


def _jsonld_types(node, out: list[str]) -> None:
    if isinstance(node, list):
        for x in node:
            _jsonld_types(x, out)
    elif isinstance(node, dict):
        t = node.get("@type")
        if isinstance(t, list):
            out.extend(str(x) for x in t)
        elif t:
            out.append(str(t))
        if "@graph" in node:
            _jsonld_types(node["@graph"], out)


def heading_facts(body: Tag) -> dict:
    heads = []
    for h in body.find_all(re.compile(r"^h[1-6]$")):
        if _hidden(h):
            continue
        heads.append((int(h.name[1]), _clean(h.get_text(" "))))
    h1 = [t for lvl, t in heads if lvl == 1]
    skips, prev = [], None
    for lvl, _ in heads:
        if prev is not None and lvl > prev + 1:
            skips.append(f"h{prev} to h{lvl}")
        prev = lvl
    return {
        "total": len(heads), "h1_count": len(h1), "h1_samples": [t for t in h1 if t][:3],
        "h1_empty": sum(1 for t in h1 if not t), "empty": sum(1 for _, t in heads if not t),
        "skips": skips[:5], "skip_count": len(skips),
    }


def link_facts(body: Tag, host: str) -> dict:
    total = internal = empty = 0
    generic: list[str] = []
    for a in body.find_all("a", href=True):
        href = a.get("href") or ""
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        total += 1
        h = (urlparse(href).hostname or "").lower()
        if not h or h == host or h == "www." + host or host == "www." + h:
            internal += 1
        text = _clean(a.get_text(" "))
        labelled = text or a.get("aria-label") or a.get("aria-labelledby") or a.get("title")
        if not labelled and not a.find(["img", "svg", "picture", "video", "canvas", "input"]):
            empty += 1
        norm = re.sub(r"[^\w\s]", "", text.lower()).strip()
        if norm in GENERIC_LINKS and not a.get("aria-label"):
            generic.append(text)
    return {"total": total, "internal": internal, "empty": empty, "generic": len(generic),
            "generic_samples": sorted(set(generic))[:4]}


def image_facts(soup: BeautifulSoup) -> dict:
    total = missing = 0
    for img in soup.find_all("img"):
        if str(img.get("width") or "").strip() in ("0", "1") or str(img.get("height") or "").strip() in ("0", "1"):
            continue
        total += 1
        if img.get("alt") is None:
            missing += 1
    return {"total": total, "missing_alt": missing}


_SCRIPTS = {
    "hebrew": r"[֐-׿]", "arabic": r"[؀-ۿݐ-ݿ]", "cyrillic": r"[Ѐ-ӿ]",
    "cjk": r"[一-鿿぀-ヿ가-힯]", "greek": r"[Ͱ-Ͽ]", "thai": r"[฀-๿]",
    "devanagari": r"[ऀ-ॿ]", "latin": r"[A-Za-zÀ-ɏ]",
}
_LANG_SCRIPT = {"he": "hebrew", "iw": "hebrew", "ar": "arabic", "fa": "arabic", "ur": "arabic", "ru": "cyrillic",
                "uk": "cyrillic", "bg": "cyrillic", "sr": "cyrillic", "be": "cyrillic", "mk": "cyrillic",
                "el": "greek", "th": "thai", "hi": "devanagari", "mr": "devanagari", "ne": "devanagari",
                "zh": "cjk", "ja": "cjk", "ko": "cjk"}
_LATIN_LANGS = {"en", "fr", "de", "es", "it", "pt", "nl", "sv", "da", "no", "nb", "nn", "fi", "pl", "cs", "sk",
                "sl", "hr", "ro", "hu", "tr", "id", "ms", "vi", "ca", "et", "lv", "lt", "is", "ga", "cy", "eu",
                "gl", "af", "sw", "tl", "fil"}
_SCRIPT_NAME = {"hebrew": "Hebrew", "arabic": "Arabic", "cyrillic": "Cyrillic", "cjk": "Chinese, Japanese or Korean",
                "greek": "Greek", "thai": "Thai", "devanagari": "Hindi or Marathi", "latin": "Latin (for example English)"}


def dominant_script(text: str) -> tuple[str | None, int]:
    sample = text[:6000]
    counts = {name: len(re.findall(rx, sample)) for name, rx in _SCRIPTS.items()}
    total = sum(counts.values())
    if total < 80:
        return None, total
    name, n = max(counts.items(), key=lambda kv: kv[1])
    return (name if n / total >= 0.6 else None), total


def language_mismatch(lang: str | None, text: str) -> str | None:
    """Return a sentence when the page text is clearly in another script than the lang attribute says."""
    if not lang:
        return None
    primary = lang.lower().replace("_", "-").split("-")[0]
    dom, _ = dominant_script(text)
    if not dom:
        return None
    expected = _LANG_SCRIPT.get(primary) or ("latin" if primary in _LATIN_LANGS else None)
    if expected and expected != dom:
        return (f'The page text is mostly {_SCRIPT_NAME[dom]}, but the page says lang="{lang}". '
                "AI tools and translators use the lang attribute to pick the language. Set it to match the text.")
    return None


def js_assessment(words: int, ext_scripts: int, spa_roots: list[dict], js_warning: bool, iframes: int,
                  has_structure: bool = False) -> dict:
    """Is the missing text caused by JavaScript, or is the page just thin?

    has_structure: the page has a real H1, two paragraphs or one full paragraph. A short page like that is
    complete (a small static page), not an empty shell waiting for JavaScript.
    """
    spa_empty = bool(spa_roots) and all(x["words"] < 20 for x in spa_roots)
    if js_warning or (spa_empty and words < 200):
        return {"risk": "high", "thin": False}
    if words < 50:
        if (ext_scripts or spa_roots) and not (words >= 15 and has_structure):
            return {"risk": "high", "thin": False}
        return {"risk": "low", "thin": True, "iframes": iframes}  # the text is in the HTML, there is just little of it
    if words < 120:
        return {"risk": "medium", "thin": False}
    return {"risk": "low", "thin": False}


def analyze_html(html: str, final_url: str) -> dict:
    """Parse the page once and return everything the report needs about it."""
    soup = BeautifulSoup(html, "lxml")
    host = (urlparse(final_url).hostname or "").lower().removeprefix("www.")

    scripts = soup.find_all("script")
    ext_scripts = [s for s in scripts if s.get("src")]
    inline_js_bytes = sum(len(s.string or "") for s in scripts
                          if not s.get("src") and (s.get("type") or "").lower() not in ("application/ld+json", "application/json"))
    jsonld_types: list[str] = []
    jsonld_invalid = 0
    for s in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        try:
            _jsonld_types(json.loads(s.get_text() or ""), jsonld_types)
        except Exception:
            jsonld_invalid += 1

    def meta(name=None, prop=None):
        el = soup.find("meta", attrs={"name": name}) if name else soup.find("meta", attrs={"property": prop})
        return _clean(el.get("content")) if el and el.get("content") else None

    robots_tokens: set[str] = set()
    for m in soup.find_all("meta", attrs={"name": re.compile(r"^(robots|googlebot|bingbot)$", re.I)}):
        robots_tokens |= {t.strip().lower() for t in (m.get("content") or "").split(",") if t.strip()}

    title = _clean(soup.title.get_text()) if soup.title else None
    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None
    canonical = soup.find("link", rel=lambda v: v and "canonical" in v)

    body = soup.body or soup
    main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    md = to_markdown(body)  # keeps <noscript> text: a fetcher without JavaScript does read it
    for t in body.find_all(["script", "style", "template", "noscript"]):
        t.decompose()
    visible_text = _clean(body.get_text(" "))
    words = len(visible_text.split())
    main_words = len(_clean(main.get_text(" ")).split()) if main else None

    spa_roots = [{"id": el.get("id"), "words": len(_clean(el.get_text(" ")).split())}
                 for el in soup.find_all(id=True) if el.get("id", "").lower() in SPA_ROOT_IDS]
    js_warning = bool(re.search(r"enable javascript|requires javascript|javascript is (disabled|required)", html, re.I))
    iframes = len(soup.find_all("iframe"))
    heads = heading_facts(body)
    para_words = [len(p.get_text().split()) for p in body.find_all("p")]
    has_structure = bool(heads["h1_samples"]) or sum(1 for n in para_words if n >= 5) >= 2 or any(n >= 12 for n in para_words)
    js = js_assessment(words, len(ext_scripts), spa_roots, js_warning, iframes, has_structure=has_structure)

    return {
        "title": title,
        "description": meta(name="description"),
        "og_title": meta(prop="og:title"),
        "og_description": meta(prop="og:description"),
        "lang": lang,
        "canonical": canonical.get("href") if canonical else None,
        "jsonld_types": sorted(set(jsonld_types)),
        "jsonld_invalid": jsonld_invalid,
        "robots_tokens": sorted(robots_tokens),
        "word_count": words,
        "main_word_count": main_words,
        "has_main": main is not None,
        "script_count": len(scripts),
        "external_script_count": len(ext_scripts),
        "inline_js_bytes": inline_js_bytes,
        "spa_roots": spa_roots,
        "js_warning_text": js_warning,
        "js_risk": js["risk"],
        "thin": js["thin"],
        "iframes": iframes,
        "headings": heads,
        "links": link_facts(body, host),
        "images": image_facts(soup),
        "lang_note": language_mismatch(lang, visible_text),
        "markdown": md[:MAX_MD_CHARS],
        "markdown_truncated": len(md) > MAX_MD_CHARS,
        "markdown_chars": len(md),
    }


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------
def parse_sitemap(url: str, status: int | None, content: bytes, error: str | None = None) -> dict:
    out = {"url": url, "status": status, "ok": False, "kind": None, "urls": 0, "children": 0, "error": None}
    if status is None:
        out["error"] = error or "no answer"
        return out
    if status != 200:
        out["error"] = f"HTTP {status}"
        return out
    data = content
    if data[:2] == b"\x1f\x8b":  # .xml.gz: unzip with a size cap
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            data = d.decompress(data, 8_000_000)
        except Exception:
            out["error"] = "could not unzip"
            return out
    text = data.decode("utf-8", "replace")
    head = text[:3000].lower()
    if "<sitemapindex" in head:
        out.update(ok=True, kind="index", children=len(re.findall(r"<loc>", text)))
    elif "<urlset" in head:
        out.update(ok=True, kind="urlset", urls=len(re.findall(r"<loc>", text)))
    elif "<html" in head or "<!doctype html" in head:
        out["error"] = "it is a web page, not a sitemap"
    elif re.match(r"\s*https?://", text):
        out.update(ok=True, kind="text", urls=len(re.findall(r"^\s*https?://", text, re.M)))
    else:
        out["error"] = "not a sitemap file"
    return out


def sitemap_lines(robots_text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^\s*sitemap:\s*(\S+)", robots_text or "", re.I | re.M)][:3]


# ---------------------------------------------------------------------------
# Findings. Each returns one action dict, or None when all is fine.
# ---------------------------------------------------------------------------
def _action(category: str, label: str, priority: str, items: list[str]) -> dict:
    return {"category": category, "label": label, "priority": priority, "gain": None, "items": items, "source": "extra"}


_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _worst(*prios: str) -> str:
    return min(prios, key=lambda p: _RANK[p])


def check_status(status: int | None, title: str | None, words: int) -> dict | None:
    """Tell a blocked scan from a missing page, a server error and a soft 404."""
    s = status or 0
    if s in (404, 410):
        return _action("status", "Page not found", "critical", [
            f"The server says this page does not exist (HTTP {s}). Check the address for typing mistakes, "
            "or scan a page that exists, for example your homepage."])
    if s >= 500:
        return _action("status", "The site returned a server error", "critical", [
            f"The server answered HTTP {s}. The site may be down or overloaded. Try again in a few minutes. "
            "If it keeps happening, AI crawlers get the same error and cannot read the site."])
    if s == 200 and title and words < 300 and re.search(
            r"\b(404|page not found|not found|doesn'?t exist|does not exist|no longer available)\b", title, re.I):
        return _action("status", "This looks like an error page", "high", [
            f'The page answers HTTP 200 but its title says "{title[:80]}". A missing page that answers 200 '
            "(a soft 404) confuses search engines and AI tools. Check the address, and make missing pages return HTTP 404."])
    return None


def check_blocked(status: int | None, challenge: bool = False) -> dict | None:
    s = status or 0
    if challenge and s < 400:
        return _action("access", "The site blocked the scan", "critical", [
            f"The site answered with a bot check page (HTTP {s}) instead of its content. Bot protection (for example "
            "Cloudflare, AWS WAF or Akamai) challenges traffic from cloud servers. AI crawlers also run from cloud "
            "servers, so they likely get the same check. If you own this site, allow known AI crawlers in your bot "
            "protection settings."])
    if s in (404, 410) or s >= 500 or s < 400:
        return None
    return _action("access", "The site blocked the scan", "critical", [
        f"The server answered HTTP {s} to every request, even one that looks like a normal browser. Bot protection "
        "(for example Cloudflare, Akamai or a WAF) often blocks traffic from cloud servers. AI crawlers also run "
        "from cloud servers, so they likely get the same block. If you own this site, allow known AI crawlers in "
        "your bot protection settings."])


def check_bots(rows: dict[str, dict], base_status: int | None) -> list[dict]:
    """Access problem for the AI bots. Google, Bing and Apple are left out on purpose: many sites check
    those bots by IP address and refuse an imitation, which is normal and good protection."""
    if not rows or (base_status or 0) >= 400:
        return []
    bad = {n: r for n, r in rows.items() if BOTS_BY_NAME[n]["core"] and r["verdict"] in ("blocked", "challenge", "error")}
    if not bad:
        return []

    def why(r: dict) -> str:
        return {"challenge": "a bot check page", "error": "no answer"}.get(r["verdict"], f"HTTP {r['status']}")

    total = sum(1 for b in BOTS if b["core"])
    ex = next(iter(bad))
    prio = "critical" if len(bad) >= 4 else "high"
    return [_action("access", "Server access for AI bots", prio, [
        f"Your server refuses {len(bad)} of {total} AI bots ({', '.join(bad)}). For example {ex} gets {why(bad[ex])}, "
        "while a normal browser gets the page. robots.txt may allow them, but your firewall, CDN bot protection "
        "(for example Cloudflare 'Block AI bots') or WAF rules block them."])]


def check_bot_content(rows: dict[str, dict]) -> dict | None:
    diff = {n: r for n, r in rows.items() if r["verdict"] == "different"}
    if not diff:
        return None
    small = {n: r for n, r in diff.items() if r["ratio"] < 0.7}
    big = {n: r for n, r in diff.items() if r["ratio"] > 1.5}

    def names(d: dict) -> str:
        ks = list(d)
        if len(ks) == len(rows):
            return "All bots"
        return ", ".join(ks[:4]) + (f" and {len(ks) - 4} more" if len(ks) > 4 else "")

    items = []
    prio = "low"
    if small:
        worst = min(r["ratio"] for r in small.values())
        items.append(f"{names(small)} got a much smaller page than a normal browser (down to {round(worst * 100)}% "
                     "of it). The server may show bots a cut-down or partial page, for example because bots send no "
                     "language header. Check that bots see your key text.")
        prio = "medium" if worst < 0.6 else "low"
    if big:
        items.append(f"{names(big)} got a much larger page than a normal browser. This can be on purpose (for "
                     "example a pre-rendered version for bots). Check that bots and visitors see the same facts.")
    return _action("bot_content", "Bots get a different page", prio, items)


def check_performance(page_ms: list[int], html_bytes: int, encoding: str | None) -> dict | None:
    items, prio = [], "low"
    ms = [m for m in page_ms if m]
    med = statistics.median(ms) if ms else 0
    if med >= 6000:
        items.append(f"The server takes about {med / 1000:.1f} seconds to answer. Many AI tools stop waiting after a "
                     "few seconds, so a slow page is often skipped. Check hosting, caching and slow database calls.")
        prio = "high"
    elif med >= 2500:
        items.append(f"The server takes about {med / 1000:.1f} seconds to answer. Fast pages are fetched more "
                     "reliably by AI tools. Add caching or a CDN.")
        prio = "medium"
    mb = html_bytes / 1_048_576
    if html_bytes > 3_000_000:
        items.append(f"The HTML is {mb:.1f} MB. AI tools read a limited amount of text, and very large pages get cut. "
                     "Aim for under 1 MB: move big inline scripts and data out of the HTML.")
        prio = _worst(prio, "high" if html_bytes > 8_000_000 else "medium")
    elif html_bytes > 1_500_000:
        items.append(f"The HTML is {mb:.1f} MB. Large pages are slow to fetch and may be cut by AI tools. "
                     "Aim for under 1 MB.")
    if html_bytes > 100_000 and not (encoding or "").strip():
        items.append("The server sends the HTML without compression (gzip or brotli). Turning it on makes the page "
                     "several times smaller to download.")
    return _action("performance", "Server speed and page size", prio, items) if items else None


def check_headings(h: dict) -> dict | None:
    items, prio = [], "low"
    if h["h1_empty"]:
        items.append("The main heading (H1) has no text. It may contain only an image or an icon. AI tools see an "
                     "empty heading, so put the page topic in real text.")
        prio = "medium"
    if h["h1_count"] > 1:
        s = "; ".join(f'"{t[:50]}"' for t in h["h1_samples"])
        items.append(f"The page has {h['h1_count']} H1 headings ({s}). Use one H1 for the page topic and H2 for sections.")
    if h["skip_count"]:
        items.append(f"Heading levels jump ({', '.join(dict.fromkeys(h['skips']))}). Go down one level at a time so "
                     "the outline reads like a table of contents.")
    other_empty = h["empty"] - h["h1_empty"]
    if other_empty > 0:
        items.append(f"{other_empty} heading{'s are' if other_empty != 1 else ' is'} empty. "
                     f"Remove {'them' if other_empty != 1 else 'it'} or add text.")
    return _action("headings", "Heading structure", prio, items) if items else None


def check_links(lk: dict) -> dict | None:
    items = []
    if lk["generic"] >= 4:
        s = ", ".join(f'"{t}"' for t in lk["generic_samples"])
        items.append(f"{lk['generic']} links use vague text such as {s}. AI tools read the link text without its "
                     "surroundings. Say where the link goes, for example \"Read the pricing guide\".")
    if lk["empty"] >= 3:
        items.append(f"{lk['empty']} links are empty: no text, no label and no image. AI tools and screen readers "
                     "cannot tell where they lead. Add visible text or an aria-label.")
    return _action("links", "Link text", "low", items) if items else None


def check_language(note: str | None) -> dict | None:
    return _action("language", "Page language", "medium", [note]) if note else None


def check_title_desc(title: str | None, desc: str | None) -> dict | None:
    items = []
    if title and len(title) > 70:
        items.append(f"The title is {len(title)} characters. Search results and AI citations cut it at about 60 to 70. "
                     "Put the important words first.")
    if desc and len(desc) > 180:
        items.append(f"The meta description is {len(desc)} characters. Keep it near 150 to 160 so it is not cut.")
    elif desc and len(desc) < 50:
        items.append(f"The meta description is only {len(desc)} characters. Write one or two full sentences (about 150 characters).")
    return _action("title_desc", "Title and description length", "low", items) if items else None


def check_crawl(tokens: list[str], x_robots: str | None) -> dict | None:
    t = set(tokens) | {p.strip().lower() for p in re.split(r"[,\s]+", x_robots or "") if p.strip()}
    if "nofollow" in t or "none" in t:
        return _action("crawl", "Links are set to nofollow", "medium", [
            "The page tells crawlers not to follow its links (nofollow). AI crawlers and search engines will not "
            "discover the pages it links to. Remove it unless you want this."])
    return None


def check_cookie_banner(md: str) -> dict | None:
    head = (md or "")[:700].lower()
    if "cookie" in head and re.search(r"accept|consent|agree|preferences|settings", head):
        return _action("cookie", "A cookie banner comes first", "low", [
            "The first text AI tools read is a cookie notice. AI tools may quote it instead of your content. "
            "Move the banner code to the end of the page, or load it with JavaScript."])
    return None


def check_thin(thin: bool, words: int, iframes: int) -> dict | None:
    if not thin:
        return None
    extra = " The content may be inside a frame (iframe), which AI tools do not read." if iframes else ""
    return _action("thin", "Almost no text on the page", "high", [
        f"The page has only {words} word{'s' if words != 1 else ''} of text. It does not use JavaScript to build the "
        "page, so there is simply very little to read. It may be mostly images, or a menu with no description. "
        f"Add text that says who you are, what you offer and for whom.{extra}"])


def check_sitemap(sm: dict | None, declared: list[str]) -> dict | None:
    if not sm:
        return None
    if declared and not sm["ok"]:
        if sm["status"] in (None, 401, 403, 429):  # we may just be refused: not proof that it is broken
            return None
        return _action("sitemap", "Sitemap does not work", "medium", [
            f"robots.txt points to a sitemap that cannot be used: {sm['url']} ({sm['error']}). Search engines and AI "
            "crawlers use the sitemap to find your pages. Fix the file or remove the line."])
    if sm["ok"] and not declared:
        return _action("sitemap", "Sitemap is not listed in robots.txt", "low", [
            f"A sitemap exists at {sm['url']}, but robots.txt does not mention it. Add this line to robots.txt: "
            f"Sitemap: {sm['url']}"])
    if sm["ok"] and sm["urls"] == 0 and sm["children"] == 0:
        return _action("sitemap", "Sitemap is empty", "medium", [
            f"The sitemap at {sm['url']} lists no pages. Make it list the pages you want found."])
    return None


def check_https(final_url: str) -> dict | None:
    if final_url.startswith("http://"):
        return _action("trust", "The page is not served over HTTPS", "high", [
            "The page is served over plain HTTP. Use HTTPS: browsers and AI tools treat HTTP pages as less trustworthy, "
            "and many crawlers refuse to follow them."])
    return None


def trust_summary(ts: dict | None) -> dict | None:
    if not ts or not ts.get("checked"):
        return None
    layers = []
    for key in ("technical", "identity", "social", "academic", "consistency"):
        L = ts.get(key) or {}
        layers.append({"key": key, "label": L.get("label") or key.title(), "score": L.get("score", 0),
                       "max": L.get("max_score", 5), "found": L.get("signals_found", []),
                       "missing": L.get("signals_missing", [])})
    return {"grade": ts.get("grade"), "score": ts.get("composite_score"), "level": ts.get("trust_level"), "layers": layers}


def platforms_summary(pc: dict | None) -> list[dict]:
    names = {"chatgpt": "ChatGPT", "perplexity": "Perplexity", "google_ai": "Google AI Overviews", "claude": "Claude",
             "gemini": "Gemini", "copilot": "Copilot"}
    return [{"id": p["platform"], "label": names.get(p["platform"], p["platform"].replace("_", " ").title()),
             "score": p["score"], "strengths": p.get("strengths", []), "tips": p.get("recommendations", [])}
            for p in (pc or {}).get("platforms", [])]


def build_wins(ai: dict, audit: dict, rows: dict[str, dict], sitemap: dict | None, ms: list[int]) -> list[str]:
    """Plain-language list of what already works. Good news first keeps people reading."""
    wins: list[str] = []
    checks = (audit or {}).get("checks") or {}
    if rows and all(r["verdict"] == "ok" for r in rows.values() if r["core"]):
        n = sum(1 for r in rows.values() if r["verdict"] == "ok")
        wins.append(f"AI crawlers can fetch the page ({n} of {len(rows)} bots tested got it).")
    if ai.get("js_risk") == "low" and (ai.get("word_count") or 0) >= 120:
        wins.append(f"The content is in the HTML: {ai['word_count']:,} words are readable without JavaScript.")
    rb = (checks.get("robots_txt") or {}).get("details") or {}
    if rb.get("found") and rb.get("citation_bots_ok") and not rb.get("bots_blocked"):
        wins.append("robots.txt lets AI crawlers in.")
    if ai.get("jsonld_types"):
        wins.append("Structured data found: " + ", ".join(ai["jsonld_types"][:3]) + ".")
    if (checks.get("llms_txt") or {}).get("details", {}).get("found"):
        wins.append("llms.txt is in place.")
    if sitemap and sitemap.get("ok"):
        n = sitemap["urls"] or sitemap["children"]
        wins.append(f"The sitemap works ({n:,} {'sitemaps' if sitemap['kind'] == 'index' else 'URLs'}).")
    med = statistics.median([m for m in ms if m]) if any(ms) else 0
    if med and med <= 1200:
        wins.append(f"The server is fast (about {med / 1000:.1f} s).")
    if (ai.get("final_url") or "").startswith("https://"):
        wins.append("The page is served over HTTPS.")
    h = ai.get("headings") or {}
    if h.get("h1_count") == 1 and not h.get("skip_count") and not h.get("empty"):
        wins.append("One clear H1 and a clean heading order.")
    if ai.get("lang") and not ai.get("lang_note"):
        wins.append(f"The page language is set ({ai['lang']}).")
    return wins[:8]
