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

import _location as LOC  # where the page says the business is (same folder)

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
        hidden = a.get("aria-hidden") == "true" or a.find_parent(attrs={"aria-hidden": "true"}) is not None
        # a link hidden from assistive tech (often a duplicate thumbnail link) is not read as empty
        if not labelled and not hidden and not a.find(["img", "svg", "picture", "video", "canvas", "input"]):
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
    ld_nodes: list[dict] = []
    jsonld_invalid = 0
    for s in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        try:
            data = json.loads(s.get_text() or "")
            _jsonld_types(data, jsonld_types)
            LOC.collect_nodes(data, ld_nodes)
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
    head_text = " ".join(x for x in (title, meta(name="description"), meta(prop="og:title"), meta(prop="og:description")) if x)
    try:
        location = LOC.location_facts(soup, md, head_text, ld_nodes)  # before scripts are removed
    except Exception:  # a new check must never break the report
        location = None
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
        "location": location,
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


def has_content_signal(robots_text: str) -> bool:
    """A Content-Signal line (contentsignals.org) tells AI how it may use the content: train, search, answer."""
    return bool(re.search(r"^\s*content-signal\s*:", robots_text or "", re.I | re.M))


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
        items.append(f"The server takes about {med / 1000:.1f} seconds to answer. Fetchers set time limits, so a slow "
                     "page is more likely to be skipped. Check hosting, caching and slow database calls.")
        prio = "high"
    elif med >= 2500:
        items.append(f"The server takes about {med / 1000:.1f} seconds to answer. Fast pages are fetched more "
                     "reliably by AI tools. Add caching or a CDN.")
        prio = "medium"
    mb = html_bytes / 1_048_576
    if html_bytes > 3_000_000:
        items.append(f"The HTML is {mb:.1f} MB. Tools that read pages for AI often limit how much they take, so a very "
                     "large page can get cut. Aim for under 1 MB: move big inline scripts and data out of the HTML.")
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


def location_impact(loc: dict | None) -> str | None:
    """How serious is it that the page does not say where the business is? None when it is fine, or does not matter.

    Only a business that depends on a place or a delivery area counts (shop, clinic, restaurant, local service).
    A software or media site is never flagged."""
    if not loc:
        return None
    level = (loc.get("local") or {}).get("level")
    status = (loc.get("visible") or {}).get("status")
    if level not in ("strong", "likely") or status == "ok":
        return None
    if status in ("none", "hidden"):
        return "critical" if level == "strong" else "high"
    return "medium" if level == "strong" else None  # names a country, an area or a place in passing, but not where it is


def _addr_line(s: dict) -> str:
    parts = [s.get("street"), s.get("city") or s.get("region")]
    line = ", ".join(p for p in parts if p)
    # an address in Hebrew or Arabic inside an English sentence would flip its brackets: isolate it
    return f"⁨{line}⁩" if re.search(r"[֐-ۿ]", line) else line


def check_location(loc: dict | None) -> dict | None:
    """The page must say where the business is. A local business that does not is told so, loudly."""
    prio = location_impact(loc)
    if not prio:
        return None
    vis, sch, local = loc.get("visible") or {}, loc.get("schema") or {}, loc.get("local") or {}
    status = vis.get("status")
    what = ", ".join(local.get("signals") or [])
    items: list[str] = []
    if status == "hidden":
        items.append(f"The page text never says where the business is. Only the hidden structured data does ({_addr_line(sch)}). "
                     "Many AI tools read just the visible text and skip hidden code, so they never learn where you are. "
                     "An AI tool that cannot place a business may skip it for local questions, or put it in the wrong area.")
    elif status == "none":
        items.append("The page text never says where the business is. We found no address, city, map or service area in the text AI tools read, "
                     "and no address in the structured data either. An AI tool that cannot place a business may skip it for local questions, "
                     "or put it in the wrong area.")
    elif status == "area":
        items.append(f"The page names the area it serves ({', '.join(vis.get('area') or [])}), but not where the business is. "
                     "Add the city or the full address as plain text, so AI tools know where the shop or office is.")
    else:
        pl = ", ".join(vis.get("places") or [])
        items.append((f"A place is only mentioned in passing ({pl}), " if pl else "The location is only shown on a map, ")
                     + "and the page never says plainly where the business is. Print the full address in the footer or a contact block.")
    items.append("Print the full address (street, number, city, postal code) as plain text in the footer or a contact block, on every page.")
    items.append("Add one sentence that names the area you serve, for example \"We are in [city] and deliver across [region]\".")
    items.append("Put the city in the title, the H1 and the meta description of the pages that sell locally.")
    if vis.get("contact_link") and status in ("none", "hidden"):
        items.append("Do not rely on the contact page: AI tools read one page at a time, so the address must be on this page too.")
    if status == "hidden" or sch.get("local_type") or sch.get("found"):
        items.append("Keep the LocalBusiness structured data (JSON-LD) in step with the page: same address, a map pin (latitude and longitude), "
                     "opening hours and the cities or regions you serve.")
    else:
        items.append("Add LocalBusiness structured data (JSON-LD) with the same address, a map pin (latitude and longitude), opening hours "
                     "and the cities or regions you serve.")
    if sch.get("geo") == "empty":
        items.append("The map pin (geo) in your structured data has no latitude and longitude, so it points nowhere. Fill it in or remove it.")
    if sch.get("area_country_only") and status != "area":
        items.append(f"Your structured data lists only a country as the area served ({', '.join(sch.get('area_served') or [])}). Name the cities or regions you serve.")
    action = _action("location", "Say where your business is", prio, items[1:])
    action["lead"] = items[0]  # what is wrong, in one sentence; the steps follow in items
    if what:
        action["note"] = f"We treat this as a local business because the page shows: {what}."
    return action


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
    loc = ai.get("location") or {}
    if (loc.get("local") or {}).get("level") in ("strong", "likely") and (loc.get("visible") or {}).get("status") == "ok":
        place = ((loc["visible"].get("places") or [])[:1] or [""])[0]
        wins.append("The page says where the business is" + (f" ({place})" if place else "") + ".")
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


# ---------------------------------------------------------------------------
# What the AI agent missed: the same audit data, told from the agent's side.
# Only facts we measured. Optional conventions (llms.txt, feeds) are kept apart.
# ---------------------------------------------------------------------------
STAGES = (("reach", "Get in"), ("read", "Read"), ("understand", "Understand"), ("trust", "Trust"), ("discover", "Find more"))
_STAGE_ORDER = {k: i for i, (k, _) in enumerate(STAGES)}


# Which fix (the action category) holds the how-to for each thing the AI missed.
# The report joins the two, so every issue shows once: what the AI saw, and how to fix it.
FIX_FOR = {
    "no_answer": "status", "not_found": "status", "server_error": "status", "site_blocked": "access",
    "bots_refused": "access", "robots_block": "robots", "crawl_delay": "robots", "noindex": "meta", "noai": "meta",
    "slow": "performance", "huge_page": "performance",
    "js_only": "rendering", "little_text": "rendering", "thin": "thin", "nosnippet": "google_ai", "images": "other",
    "video": "other", "cookie": "cookie", "bot_page": "bot_content",
    "no_title": "meta", "no_description": "meta", "no_canonical": "meta", "no_og": "meta",
    "no_h1": "content", "no_sections": "content", "short": "content", "no_facts": "content", "buried": "content",
    "no_schema": "schema", "no_org": "schema", "no_website": "schema", "broken_schema": "schema", "schema_fields": "schema", "faq": "schema",
    "no_lang": "signals", "wrong_lang": "language", "brand": "brand_entity", "location": "location", "geo_empty": "location",
    "no_author": "google_ai", "no_date": "google_ai", "no_profiles": "brand_entity", "few_profiles": "brand_entity",
    "no_contact": "brand_entity", "no_about": "brand_entity", "no_https": "trust",
    "links": "google_ai", "sitemap": "sitemap", "no_sitemap": "robots", "nofollow": "crawl",
}


# One-line how-to for the things that may have no fix row of their own (the report shows it when no action matches).
HINTS = {
    "no_answer": "Check that the address is right and the site is online, then scan again.",
    "robots_block": "Open robots.txt and remove the Disallow rules for the AI crawlers you want to allow. If the block is on purpose, no action is needed.",
    "crawl_delay": "Lower or remove the Crawl-delay line in robots.txt.",
    "no_sitemap": "Create a sitemap.xml that lists your main pages, then add the line Sitemap: https://your-site/sitemap.xml to robots.txt.",
    "noindex": "Remove the noindex tag or header, unless you want this page hidden from search and AI.",
    "noai": "Remove the noai tag, unless you do not want AI tools to use this page.",
    "images": "Add short alt text to images that carry meaning. Decorative images can have empty alt text.",
    "video": "Add captions or a transcript next to the video.",
}


def _it(stage: str, id_: str, impact: str, title: str, detail: str, optional: bool = False) -> dict:
    out = {"id": id_, "stage": stage, "impact": impact, "title": title, "detail": detail, "optional": optional,
           "fix": None if optional else FIX_FOR.get(id_)}
    if id_ in HINTS and not optional:
        out["hint"] = HINTS[id_]
    return out


def _few(names, n: int = 4) -> str:
    names = list(names)
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


def _details(audit: dict, key: str) -> dict:
    return (((audit or {}).get("checks") or {}).get(key) or {}).get("details") or {}


def _google_check(audit: dict, cid: str) -> dict | None:
    for c in ((audit or {}).get("google_ai") or {}).get("checks") or []:
        if c.get("id") == cid:
            return c
    return None


def _pack(items: list[dict], usable: bool, score, potential) -> dict:
    core = [i for i in items if not i["optional"]]
    stages = []
    for key, label in STAGES:
        its = [i for i in core if i["stage"] == key]
        if not usable and key != "reach":
            st = "skipped"
        elif any(i["impact"] in ("critical", "high") for i in its):
            st = "fail"
        elif its:
            st = "warn"
        else:
            st = "ok"
        stages.append({"key": key, "label": label, "status": st, "count": len(its)})
    core.sort(key=lambda i: (_STAGE_ORDER[i["stage"]], _RANK[i["impact"]]))
    return {"usable": usable, "count": len(core), "items": core, "optional": [i for i in items if i["optional"]],
            "stages": stages, "score": score, "potential": potential}


def _page_problem(ai: dict) -> dict | None:
    """The page itself could not be read: then nothing else was missed on purpose, the agent never got in."""
    status = ai.get("browser_status") or 0
    if not ai.get("ok") and not status:
        return _it("reach", "no_answer", "critical", "Could not open the page",
                   f"The site did not answer ({ai.get('error') or 'no response'}), so the AI agent read nothing.")
    if status in (404, 410):
        return _it("reach", "not_found", "critical", "The page does not exist",
                   f"The server says HTTP {status}. Check the address, then scan a page that exists.")
    if status >= 500:
        return _it("reach", "server_error", "critical", "The server returned an error",
                   f"HTTP {status}. The site may be down or overloaded, so the AI agent read nothing.")
    if ai.get("browser_challenge"):
        return _it("reach", "site_blocked", "critical", "Could not open the page",
                   f"The site showed a bot check page (HTTP {status}) instead of its content, so the AI agent read nothing.")
    if status >= 400:
        return _it("reach", "site_blocked", "critical", "Could not open the page",
                   f"The site answered HTTP {status} instead of its content, so the AI agent read nothing.")
    return None


def build_missed(ai: dict, audit: dict, rows: dict, files: dict, score, actions: list[dict]) -> dict:
    """List what an AI agent could not find, read, understand or trust on this page."""
    ai, audit, rows, files = ai or {}, audit or {}, rows or {}, files or {}
    gain = sum(a.get("gain") or 0 for a in actions or [] if a.get("source") == "engine")
    potential = min(100, (score or 0) + gain) if score is not None else None
    problem = _page_problem(ai)
    if problem:
        return _pack([problem], False, score, potential)

    items: list[dict] = []
    add = items.append
    meta, schema, content = _details(audit, "meta_tags"), _details(audit, "schema_jsonld"), _details(audit, "content")
    signals, brand, robots = _details(audit, "signals"), _details(audit, "brand_entity"), _details(audit, "robots_txt")
    llms, discovery = _details(audit, "llms_txt"), _details(audit, "ai_discovery")
    recs = audit.get("recommendations") or []
    words = ai.get("word_count") or 0

    # ---- Get in -----------------------------------------------------------
    core_bad = [n for n, r in rows.items() if r.get("core") and r.get("verdict") in ("blocked", "challenge", "error")]
    if core_bad:
        total = sum(1 for r in rows.values() if r.get("core"))
        first = rows[core_bad[0]]
        why = "a bot check page" if first.get("verdict") == "challenge" else "no answer" if first.get("verdict") == "error" else f"HTTP {first.get('status')}"
        one = len(core_bad) == 1
        add(_it("reach", "bots_refused", "critical" if len(core_bad) >= 4 else "high",
                "An AI crawler was turned away" if one else "Some AI crawlers were turned away",
                f"{len(core_bad)} of {total} AI crawlers {'was' if one else 'were'} refused ({_few(core_bad)}). "
                f"{'It' if one else 'For example ' + core_bad[0]} got {why} instead of your page."))
    if robots.get("bots_blocked"):
        add(_it("reach", "robots_block", "high", "robots.txt told AI to stay out",
                f"Your robots.txt blocks {_few(robots['bots_blocked'], 5)}. They will not read your pages."))
    if (robots.get("crawl_delay") or 0) > 10:
        add(_it("reach", "crawl_delay", "medium", "Was told to wait a long time",
                f"robots.txt asks crawlers to wait {int(robots['crawl_delay'])} seconds between requests, which slows them down a lot."))
    if meta.get("x_robots_noindex"):
        add(_it("reach", "noindex", "critical", "The page said \"do not use me\"",
                "An X-Robots-Tag: noindex header tells crawlers to skip this page entirely."))
    if meta.get("has_noai"):
        add(_it("reach", "noai", "high", "The page asked AI not to use it",
                "A \"noai\" tag asks AI tools not to use your content."))
    med_ms = ai.get("response_ms") or 0
    if med_ms >= 2500:
        add(_it("reach", "slow", "high" if med_ms >= 6000 else "medium", "Waited a long time for an answer",
                f"Your server took about {med_ms / 1000:.1f} seconds to answer. Fetchers set time limits, so a slow page is more likely to be skipped."))
    if (ai.get("html_bytes") or 0) > 1_500_000:
        add(_it("reach", "huge_page", "medium" if ai["html_bytes"] > 3_000_000 else "low", "The page was too big to read in full",
                f"The HTML is {ai['html_bytes'] / 1_048_576:.1f} MB. Tools that read pages for AI often limit how much they take, so it can get cut."))

    # ---- Read -------------------------------------------------------------
    if ai.get("js_risk") == "high":
        add(_it("read", "js_only", "critical", "Saw an almost empty page",
                ("No words" if not words else f"Only {words} word{'s' if words != 1 else ''}") + " reached the AI. Your content is built by JavaScript, and most AI crawlers do not run it (Google's does)."))
    elif ai.get("thin"):
        add(_it("read", "thin", "high", "Found almost no text",
                f"Only {words} word{'s' if words != 1 else ''}, and no JavaScript is involved. There is very little to read."))
    elif ai.get("js_risk") == "medium":
        add(_it("read", "little_text", "high", "Saw very little text",
                f"Only {words} words are in the HTML before JavaScript runs. Key content may be loaded later by scripts."))
    snip = _google_check(audit, "G-SNIPPET")
    if snip and snip.get("status") in ("warn", "fail"):
        m = re.search(r"(\d+)%", str(snip.get("evidence") or ""))
        add(_it("read", "nosnippet", "medium", "Was blocked from part of the text",
                (f"{m.group(1)}% of your page text" if m else "Part of your page text") + " is marked data-nosnippet, so AI summaries cannot use it."))
    mm = audit.get("multimodal") or {}
    if (mm.get("total_images") or 0) >= 3 and (mm.get("alt_coverage") or 0) < 0.8:
        blind = mm["total_images"] - (mm.get("images_with_alt") or 0)
        add(_it("read", "images", "medium" if mm["alt_coverage"] < 0.5 else "low", f"Could not see {blind} of {mm['total_images']} images",
                "They have no alt text, so the AI cannot tell what they show. Decorative images can stay empty."))
    if mm.get("has_video") and not (mm.get("has_video_captions") or mm.get("has_transcript")):
        add(_it("read", "video", "low", "Could not follow your video", "There are no captions or transcript, so the AI reads nothing from the video."))
    cookie = check_cookie_banner(ai.get("markdown", ""))
    if cookie:
        add(_it("read", "cookie", "low", "Read a cookie notice first", "The first text the AI got was your cookie banner, not your content."))
    diff = check_bot_content(rows)
    if diff:
        add(_it("read", "bot_page", diff["priority"], "Got a different page than visitors", diff["items"][0]))

    # ---- Understand -------------------------------------------------------
    if meta and not meta.get("has_title"):
        add(_it("understand", "no_title", "critical", "Found no page title", "The page has no title tag. It is the first thing AI uses to name the page."))
    if meta and not meta.get("has_description"):
        add(_it("understand", "no_description", "high", "Had no summary to go on", "There is no meta description, so the AI had to guess what the page is about."))
    if content and not content.get("has_h1"):
        add(_it("understand", "no_h1", "high", "Could not find the main topic", "The page has no H1 heading that says what it is about."))
    elif content and not content.get("has_heading_hierarchy"):
        add(_it("understand", "no_sections", "medium", "Could not split the page into answers", "There are no H2 or H3 subheadings, so there are no clear sections to quote."))
    if ai.get("js_risk") == "low" and not ai.get("thin") and 0 < words < 300:
        add(_it("understand", "short", "medium", "Found little to quote", f"Only {words} words. AI needs more substance to cite you."))
    found_types = schema.get("found_types") or []
    if schema and not found_types and not ai.get("jsonld_types"):
        add(_it("understand", "no_schema", "high", "Found no structured facts", "There is no JSON-LD data on the page, so the AI had to guess your name, logo and what you offer."))
    elif found_types:
        if any("Organization JSON-LD" in r for r in recs):
            add(_it("understand", "no_org", "high", "Could not tell who you are", "The page has no Organization data. It helps AI tell you apart from other companies with a similar name."))
        if schema.get("has_website") is False:
            add(_it("understand", "no_website", "low", "Found no WebSite data", "The page does not describe your site (name and address) in structured data."))
    if ai.get("jsonld_invalid"):
        n = ai["jsonld_invalid"]
        add(_it("understand", "broken_schema", "high", "Ignored broken structured data", f"{n} structured data block{'s' if n != 1 else ''} could not be read (invalid JSON), so the AI skipped {'them' if n != 1 else 'it'}."))
    gaps = [f"The {m.group(1)} data is missing {m.group(2).strip()}." for m in
            (re.match(r"Incomplete (\w+) schema: add required fields ([^(]+)", r) for r in recs) if m]
    if gaps:
        add(_it("understand", "schema_fields", "medium", "Structured data is incomplete", " ".join(gaps[:2])))
    if schema.get("has_visible_faq") and not schema.get("has_faq"):
        add(_it("understand", "faq", "low", "Did not know your Q&A is Q&A", "The page shows questions and answers but has no FAQ markup."))
    if meta and not meta.get("has_canonical"):
        add(_it("understand", "no_canonical", "medium", "Could not tell which URL is the real one", "There is no canonical link, so copies of the page can look like separate pages."))
    if signals and not signals.get("has_lang"):
        add(_it("understand", "no_lang", "medium", "Did not know your language", "The page does not say which language it is written in."))
    elif ai.get("lang_note"):
        add(_it("understand", "wrong_lang", "medium", "Was told the wrong language", ai["lang_note"].split(". AI tools")[0] + "."))
    loc = ai.get("location") or {}
    loc_impact = location_impact(loc)
    if loc_impact:
        st, sch = (loc.get("visible") or {}).get("status"), loc.get("schema") or {}
        if st == "hidden":
            add(_it("understand", "location", loc_impact, "Could not tell where you are",
                    f"Your address ({_addr_line(sch)}) is only in hidden code. Many AI tools read just the visible page text, "
                    "so they never learn where you are and may skip you for local questions."))
        elif st == "none":
            add(_it("understand", "location", loc_impact, "Could not tell where you are",
                    "The page text names no address, city or service area, so the AI cannot place the business. "
                    "It may skip you for local questions or put you in the wrong area."))
        elif st == "area":
            add(_it("understand", "location", loc_impact, "Found the area you serve, not where you are",
                    "The page names where you deliver, but not where the business is. The AI cannot tell which city or address is yours."))
        else:
            add(_it("understand", "location", loc_impact, "Found only a passing mention of a place",
                    "A place is mentioned in passing, or only shown on a map, but the page never says plainly where the business is."))
    if (loc.get("local") or {}).get("level") in ("strong", "likely") and (loc.get("schema") or {}).get("geo") == "empty":
        add(_it("understand", "geo_empty", "low", "The map pin in your data is empty",
                "Your structured data has a map pin (geo) with no latitude and longitude, so it points nowhere."))
    if meta and not (meta.get("has_og_title") and meta.get("has_og_description")):
        add(_it("understand", "no_og", "low", "Found no preview card", "The page has no Open Graph tags (title, description, image) for AI and link previews."))
    if brand and brand.get("brand_name_consistent") is False and len(brand.get("names_found") or []) >= 2:
        add(_it("understand", "brand", "medium", "Saw different names for you", "Your title, headings and data use different brand names, so AI is unsure which is yours."))
    if content and content.get("has_numbers") is False and words >= 120:
        add(_it("understand", "no_facts", "low", "Found no numbers to quote", "There are no concrete figures such as prices, dates or statistics. AI quotes facts."))
    if content and content.get("has_front_loading") is False and words >= 300:
        add(_it("understand", "buried", "low", "Key facts come too late", "The key information is not near the top of the page."))

    # ---- Trust ------------------------------------------------------------
    types = {str(t).lower() for t in (ai.get("jsonld_types") or found_types)}
    article = bool(types & {"article", "blogposting", "newsarticle", "techarticle", "report", "scholarlyarticle"}) or meta.get("og_type") == "article"
    by = _google_check(audit, "G-BYLINE")
    if article and by and by.get("status") in ("warn", "fail"):  # a company homepage needs no byline
        add(_it("trust", "no_author", "medium", "Could not tell who wrote it", "There is no author name on the page."))
    dt = _google_check(audit, "G-DATES")
    if dt and dt.get("status") in ("warn", "fail"):
        only_code = "structured data" in str(dt.get("evidence") or "").lower()
        add(_it("trust", "no_date", "medium" if article else "low", "Could not tell how fresh it is",
                "The date is only in the code, not shown on the page." if only_code else "There is no publish or update date on the page."))
    if brand:
        k = brand.get("kg_pillar_count") or 0
        if k == 0:
            add(_it("trust", "no_profiles", "medium", "Could not confirm who you are", "Your structured data does not link to your official profiles (LinkedIn, Wikipedia, Wikidata, Crunchbase)."))
        elif k < 3:
            add(_it("trust", "few_profiles", "low", "Found few official profiles", f"Only {k} of 4 official profiles {'is' if k == 1 else 'are'} linked (LinkedIn, Wikipedia, Wikidata, Crunchbase)."))
        if brand.get("has_contact_info") is False:
            add(_it("trust", "no_contact", "medium", "Found no contact details", "There is no address, phone or contact point in your structured data."))
        if brand.get("has_about_link") is False:
            add(_it("trust", "no_about", "low", "Found no About page", "There is no visible link to an About page."))
    if (ai.get("final_url") or "").startswith("http://"):
        add(_it("trust", "no_https", "high", "The page is not secure", "It is served over plain HTTP, which browsers and AI tools trust less."))

    # ---- Find more --------------------------------------------------------
    lk = _google_check(audit, "G-LINKS")
    if lk and lk.get("status") == "fail":
        add(_it("discover", "links", "high", "Could not follow your links", "Your links are not real links (they run through JavaScript), so the AI could not reach other pages."))
    sm, declared = files.get("sitemap"), files.get("declared") or []
    smap = check_sitemap(sm, declared)
    if smap:
        title = {"Sitemap does not work": "The sitemap link is broken", "Sitemap is not listed in robots.txt": "Did not know about your sitemap",
                 "Sitemap is empty": "The sitemap is empty"}.get(smap["label"], smap["label"])
        add(_it("discover", "sitemap", smap["priority"], title, smap["items"][0]))
    elif not declared and not robots.get("sitemaps") and (sm is None or (not sm.get("ok") and sm.get("status") not in (None, 401, 403, 429))):
        add(_it("discover", "no_sitemap", "medium", "Found no sitemap", "There is no sitemap, so crawlers must guess which pages exist."))
    crawl = check_crawl(ai.get("robots_tokens") or [], ai.get("x_robots_tag"))
    if crawl:
        add(_it("discover", "nofollow", "medium", "Was told not to follow links", crawl["items"][0]))

    # ---- Optional conventions: shown apart, never counted ---------------------
    if llms and llms.get("found") is False:
        add(_it("discover", "llms", "low", "llms.txt", "No llms.txt guide to your site. It is a new, unproven convention.", True))
    if signals and signals.get("has_rss") is False:
        add(_it("discover", "feed", "low", "News feed", "No RSS or Atom feed to announce new content.", True))
    if discovery and not discovery.get("has_well_known_ai") and not discovery.get("has_summary"):
        add(_it("discover", "ai_files", "low", "AI files", "No /.well-known/ai.txt or /ai/summary.json. These are new, unofficial conventions.", True))
    if files.get("content_signal") is False:
        add(_it("discover", "content_signal", "low", "Content Signals", "robots.txt has no Content-Signal line that says how AI may use your content (train, search, answer). It is a new convention from Cloudflare.", True))
    return _pack(items, True, score, potential)
