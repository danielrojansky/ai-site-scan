// Agent usability: can an AI agent that drives a real browser use this page?
// Two kinds of agent are covered. Agents that read the page use its accessibility tree (roles and names of
// buttons, links and fields). Agents that look at the screen use screenshots, so what covers the page matters.
// snapshot() runs inside the page (in headless Chrome). evaluate() is pure: it turns the snapshot into checks.
// Nothing here changes the 0 to 100 score.

/* eslint-disable no-undef */
// Runs in the page. Keep it self-contained: Puppeteer sends its source text to the browser.
function snapshot() {
  const MAXN = 6000, W = innerWidth, H = innerHeight;
  const clean = (t) => String(t == null ? "" : t).replace(/\s+/g, " ").trim();
  const hiddenAnc = (e) => !!(e.closest && e.closest('[aria-hidden="true"],[hidden],[inert]'));
  const visible = (e) => {
    if (hiddenAnc(e)) return false;
    const r = e.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(e);
    return s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };
  const byIds = (ids) => clean(ids.split(/\s+/).map((i) => { const n = document.getElementById(i); return n ? n.textContent : ""; }).join(" "));
  const labelText = (e) => (e.labels && e.labels.length ? clean([...e.labels].map((l) => l.textContent).join(" ")) : "");
  function name(e) {
    const lb = e.getAttribute("aria-labelledby");
    if (lb) { const n = byIds(lb); if (n) return n; }
    const al = clean(e.getAttribute("aria-label")); if (al) return al;
    const lt = labelText(e); if (lt) return lt;
    const tag = e.tagName;
    if (tag === "INPUT") {
      const t = (e.type || "").toLowerCase();
      if (t === "submit" || t === "reset" || t === "button") return clean(e.value) || (t === "submit" ? "Submit" : t === "reset" ? "Reset" : "");
      if (t === "image") return clean(e.alt);
    } else if (tag !== "SELECT" && tag !== "TEXTAREA") {
      // innerText is empty for content that the browser has not laid out yet (content-visibility: auto),
      // but the accessibility tree still has it, so fall back to the text in the element.
      const txt = clean(e.innerText) || clean(e.textContent); if (txt) return txt;
      const img = e.querySelector && e.querySelector("img[alt]"); if (img && clean(img.alt)) return clean(img.alt);
      const st = e.querySelector && e.querySelector("svg title"); if (st && clean(st.textContent)) return clean(st.textContent);
    }
    return clean(e.getAttribute("title"));
  }
  const all = (sel) => [...document.querySelectorAll(sel)].slice(0, MAXN).filter(visible);
  const outer = (e) => e.outerHTML.replace(/\s+/g, " ").slice(0, 160);

  const buttons = all('button,[role="button"],input[type="button"],input[type="submit"],input[type="reset"],input[type="image"],summary');
  const links = all('a[href],[role="link"]');
  const fields = all('input:not([type="hidden"]):not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="image"]),select,textarea,[role="textbox"],[role="combobox"],[contenteditable="true"]');

  const unnamedButtons = buttons.filter((e) => !name(e));
  const unnamedLinks = links.filter((e) => !name(e));
  const counts = new Map(), shown = new Map();
  buttons.forEach((e) => { const n = name(e), k = n.toLowerCase(); if (n) { counts.set(k, (counts.get(k) || 0) + 1); if (!shown.has(k)) shown.set(k, n); } });
  const dupButtons = [...counts].filter(([, c]) => c >= 3).map(([k, c]) => ({ name: shown.get(k).slice(0, 60), count: c })).sort((a, b) => b.count - a.count).slice(0, 5);
  const GENERIC = /^(read more|learn more|more|click here|here|details|more info|view more|see more|find out more|לקריאה נוספת|קרא עוד|עוד)$/i;
  const generic = links.filter((e) => GENERIC.test(name(e)));
  const genericText = generic.length ? name(generic[0]) : "";

  const fieldInfo = fields.map((e) => {
    const labeled = !!(labelText(e) || clean(e.getAttribute("aria-label")) || (e.getAttribute("aria-labelledby") && byIds(e.getAttribute("aria-labelledby"))) || clean(e.getAttribute("title")));
    const ph = clean(e.getAttribute("placeholder"));
    const hint = [e.getAttribute("name"), e.id, ph, e.getAttribute("aria-label"), labelText(e)].join(" ");
    const type = (e.getAttribute("type") || e.tagName).toLowerCase();
    const kind = type === "email" || /e-?mail|מייל|דוא"?ל/i.test(hint) ? "email"
      : type === "tel" || /phone|\btel\b|mobile|טלפון|נייד/i.test(hint) ? "tel"
      : /(^|[\s_-])(first|last|full|your)?[\s_-]?name\b|שם/i.test(hint) && !/user|company|file|domain|site/i.test(hint) ? "name" : null;
    return { tag: e.tagName.toLowerCase(), type, labeled, placeholder: ph.slice(0, 60), autocomplete: clean(e.getAttribute("autocomplete")).toLowerCase(), kind, name: (e.getAttribute("name") || "").slice(0, 40), html: outer(e) };
  });

  // Boxes that react to clicks (pointer cursor) but are not buttons or links
  let clickBoxes = 0; const clickSamples = [];
  const nodes = document.querySelectorAll("div,span,li,img,svg,p,section,article,td");
  for (let i = 0; i < nodes.length && i < MAXN; i++) {
    const e = nodes[i];
    if (e.closest("a,button,label,summary,select,input,textarea,[role],[contenteditable]")) continue;
    const s = getComputedStyle(e);
    if (s.cursor !== "pointer") continue;
    const p = e.parentElement;
    if (p && getComputedStyle(p).cursor === "pointer") continue;
    if (!visible(e)) continue;
    clickBoxes++;
    if (clickSamples.length < 5) clickSamples.push({ text: clean(e.innerText).slice(0, 40), html: outer(e) });
  }

  // What covers the screen when the page loads
  const overlays = [], els = [];
  const body = document.body ? document.body.querySelectorAll("*") : [];
  for (let i = 0; i < body.length && i < MAXN * 2; i++) {
    const e = body[i];
    const s = getComputedStyle(e);
    if (s.position !== "fixed" && s.position !== "sticky") continue;
    if (els.some((o) => o.contains(e))) continue;
    if (!visible(e)) continue;
    const r = e.getBoundingClientRect();
    const x0 = Math.max(0, r.left), y0 = Math.max(0, r.top), x1 = Math.min(W, r.right), y1 = Math.min(H, r.bottom);
    if (x1 <= x0 || y1 <= y0) continue;
    const area = ((x1 - x0) * (y1 - y0)) / (W * H);
    const coversCenter = r.left < W / 2 && r.right > W / 2 && r.top < H / 2 && r.bottom > H / 2;
    const text = clean(e.innerText).slice(0, 300);
    const frame = e.querySelector("iframe");
    const marks = [frame ? frame.src || "" : "", e.id, typeof e.className === "string" ? e.className : ""].join(" ");
    const topBar = r.top <= 1 && y1 - y0 < 160 && x1 - x0 > W * 0.7;
    const kind = /cookie|consent|gdpr|ccpa|עוגיות/i.test(text + " " + marks) ? "cookie"
      : /newsletter|subscribe|sign up for|% off|discount|coupon|הרשמ|הנחה/i.test(text) ? "newsletter"
      : /intercom|drift|zendesk|crisp|tawk|livechat|hubspot-messages|chat/i.test(marks) ? "chat"
      : coversCenter && /\b(18|age|country|region|location|language)\b/i.test(text) ? "gate"
      : e.matches('header,nav,[role="banner"],[role="navigation"]') || topBar ? "header" : "other";
    if (kind === "header" && !coversCenter) continue;          // a sticky menu bar is fine
    if (area < 0.02 && kind !== "chat") continue;
    els.push(e);
    const closers = [...e.querySelectorAll('button,[role="button"],a')].filter(visible);
    const closeNamed = closers.some((b) => /close|dismiss|accept|reject|decline|agree|allow|got it|no thanks|^ok$|×|✕|סגור|אישור|מסכים|הבנתי/i.test(name(b)));
    overlays.push({
      kind, area: Math.round(area * 100), coversCenter,
      rect: { x: Math.round(x0), y: Math.round(y0), w: Math.round(x1 - x0), h: Math.round(y1 - y0) },
      text: text.slice(0, 100), closeNamed, closeUnnamed: !closeNamed && closers.some((b) => !name(b)),
    });
    if (overlays.length >= 8) break;
  }

  // Search
  const searchInputs = all('input[type="search"],[role="searchbox"],form[role="search"] input,input[name="q"],input[name="s"],input[name="query"],input[name="search"],input[name="keyword"],input[name="keywords"],input[placeholder*="search" i],input[aria-label*="search" i],input[placeholder*="חיפוש"],input[placeholder*="חפש"]');
  let searchUrl = null;
  for (const inp of searchInputs) {
    const f = inp.form; if (!f || !inp.name) continue;
    if ((f.getAttribute("method") || "get").toLowerCase() !== "get") continue;
    try { const a = new URL(f.getAttribute("action") || location.pathname, location.href); searchUrl = a.origin + a.pathname + "?" + encodeURIComponent(inp.name) + "="; break; } catch (err) {}
  }
  const searchButtons = [...buttons, ...links].filter((e) => /^search\b|\bsearch$|^חיפוש|חפש/i.test(name(e))).length;

  // Shop signals
  const products = [];
  for (const sc of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const walk = (o) => {
        if (!o || typeof o !== "object") return;
        if (Array.isArray(o)) return o.forEach(walk);
        const t = [].concat(o["@type"] || []).join(",");
        if (/\bProduct\b/.test(t)) products.push(o);
        if (o["@graph"]) walk(o["@graph"]);
      };
      walk(JSON.parse(sc.textContent));
    } catch (err) {}
  }
  const prod = products[0];
  const of = prod ? [].concat(prod.offers || [])[0] : null;
  const offer = of && typeof of === "object" ? { price: of.price != null ? String(of.price) : of.lowPrice != null ? String(of.lowPrice) : null, currency: of.priceCurrency || null, availability: of.availability || null } : null;
  let text = "";
  if (document.body) {  // all visible-to-the-tree text, also in sections not laid out yet; no scripts or styles
    const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(n.parentElement ? n.parentElement.tagName : "") ? 2 : 1) });
    const parts = []; let len = 0;
    while (tw.nextNode() && len < 200000) { const v = tw.currentNode.nodeValue; if (v && v.trim()) { parts.push(v); len += v.length; } }
    text = clean(parts.join(" ")).slice(0, 200000);
  }
  const priceM = text.match(/(?:[$€£₪¥]\s?\d[\d,.]*|\d[\d,.]*\s?(?:USD|EUR|GBP|ILS|NIS|₪|€|£))/);
  const cart = [...buttons, ...links].find((e) => /add to (cart|bag|basket)|buy now|order now|הוסף?\s?ל(סל|עגלה)|קנה עכשיו|לרכישה/i.test(name(e)));
  const shop = {
    productData: !!prod, microdata: !!document.querySelector('[itemtype*="schema.org/Product"]'),
    productName: prod && typeof prod.name === "string" ? prod.name.slice(0, 80) : null, offer,
    priceText: priceM ? priceM[0].replace(/[.,]+$/, "").slice(0, 30) : null, cart: cart ? name(cart).slice(0, 40) : null,
    stockText: /\b(in stock|out of stock|sold out|only \d+ left|available now)\b|במלאי|אזל/i.test(text),
  };

  // The accessibility tree, as an agent reads it (top of the page, then any problem lines further down)
  const ROLE = { HEADER: "banner", NAV: "navigation", MAIN: "main", FOOTER: "contentinfo", ASIDE: "complementary", A: "link", BUTTON: "button", SUMMARY: "button", SELECT: "combobox", TEXTAREA: "textbox" };
  const roleOf = (e) => {
    const r = e.getAttribute("role"); if (r) return r;
    if (/^H[1-6]$/.test(e.tagName)) return "heading " + e.tagName[1];
    if (e.tagName === "INPUT") { const t = (e.type || "text").toLowerCase(); return t === "checkbox" ? "checkbox" : t === "radio" ? "radio" : ["submit", "button", "reset", "image"].includes(t) ? "button" : "textbox"; }
    return ROLE[e.tagName] || e.tagName.toLowerCase();
  };
  const LAND = /^(banner|navigation|main|contentinfo|complementary|search|form|region)$/;
  const treeEls = all('header,nav,main,footer,aside,[role="banner"],[role="navigation"],[role="main"],[role="search"],h1,h2,h3,a[href],button,[role="button"],input:not([type="hidden"]),select,textarea,summary');
  const dupSet = new Set(dupButtons.map((d) => d.name.toLowerCase()));
  const line = (e) => {
    const role = roleOf(e), land = LAND.test(role);
    const nm = land ? "" : name(e);
    const flag = land ? null : !nm ? "noname" : role === "button" && dupSet.has(nm.toLowerCase()) ? "dup" : null;
    return { role, name: nm.slice(0, 50), depth: land ? 0 : e.closest("header,nav,main,footer,aside,[role]") ? 1 : 0, flag };
  };
  const tree = treeEls.slice(0, 32).map(line);
  const more = treeEls.slice(32).map(line).filter((l) => l.flag).slice(0, 8);
  if (more.length) tree.push({ role: "...", name: "", depth: 0, flag: null }, ...more);

  return {
    url: location.href, title: clean(document.title).slice(0, 120), viewport: { w: W, h: H },
    buttons: buttons.length, links: links.length, unnamedButtons: unnamedButtons.length, unnamedLinks: unnamedLinks.length,
    unnamedSamples: [...unnamedButtons, ...unnamedLinks].slice(0, 5).map(outer),
    dupButtons, genericLinks: generic.length, genericText,
    fields: fieldInfo.slice(0, 40), fieldCount: fieldInfo.length,
    clickBoxes, clickSamples, overlays,
    h1: all("h1").length, emptyHeadings: all("h1,h2,h3").filter((e) => !name(e)).length,
    search: { inputs: searchInputs.length, buttons: searchButtons, url: searchUrl },
    hashLinks: links.filter((a) => /^#!?\//.test(a.getAttribute("href") || "")).length, hashRoute: /^#!?\//.test(location.hash),
    shop, tree,
  };
}

// Runs in the page twice, a few seconds apart: content that moves by itself (sliders, carousels).
function motionProbe() {
  const els = [...document.querySelectorAll('[aria-roledescription="carousel"],[class*="carousel"],[class*="slider"],[class*="swiper"],[class*="slick"]')]
    .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 200 && r.height > 80 && r.top < innerHeight * 2 && !e.closest('[aria-hidden="true"]'); })
    .filter((e, i, a) => !a.some((o) => o !== e && o.contains(e))).slice(0, 4);
  return els.map((e) => {
    const inner = e.querySelector('[class*="track"],[class*="wrapper"],[class*="slides"],ul') || e.firstElementChild || e;
    const active = [...e.querySelectorAll('.active,.is-active,.is-selected,.swiper-slide-active,.slick-active,[aria-current="true"]')].map((x) => (x.textContent || "").slice(0, 40)).join("/");
    return [(e.innerText || "").slice(0, 300), getComputedStyle(inner).transform, inner.scrollLeft || 0, active].join("|");
  });
}
/* eslint-enable no-undef */

const WHY = {
  find_search: "Agents search the way people do. A results page with its own address lets them search, compare and come back.",
  find_urls: "An agent opens and shares addresses. Addresses that start with #/ all load the same page, so the agent cannot go straight to a product or section.",
  read_names: "Agents that read the page find buttons and links by their names. A button with only an icon has no name, so for the agent it does not exist.",
  read_unique: "When several buttons have the same name, or links only say \"Read more\", the agent has to guess which one to use.",
  read_heading: "One main heading tells the agent what the page is about. None or several make it guess.",
  act_controls: "Agents click real buttons and links. A box that only looks clickable is a decoration to them.",
  act_labels: "An agent fills a field by its label. Placeholder text disappears when you type and many agents do not read it.",
  act_autocomplete: "The autocomplete and type attributes tell any agent what a field wants (email, phone, name), so it fills it right the first time.",
  popups: "Agents that look at the screen cannot use what a banner or pop-up covers. Each extra pop-up is a step where the agent can get stuck.",
  motion: "Content that changes while the agent reads it gives it a different page each time it looks.",
  shop_price: "An agent reports the price to its user before buying. A price as text with a currency sign is the price it can trust.",
  shop_offer: "Product data in JSON-LD gives the name, price, currency and stock without reading the layout at all.",
  shop_stock: "\"In stock\" or \"Sold out\" in words tells the agent if it can buy. A colored dot does not.",
  shop_cart: "The agent looks for a button named Add to cart or Buy now. An icon-only cart button is invisible to it.",
};
const FIX = {
  find_search: "Put the search box in a form with method=\"get\" so results open at an address like /search?q=shoes.",
  find_urls: "Use real paths (/products/red-shoes) instead of #/ routes. Most frameworks have a history mode for this.",
  read_names: "Give each icon-only button and link an aria-label, for example aria-label=\"Open cart\".",
  read_unique: "Make names specific: \"Save React Conf talk\", \"Read more about pricing\".",
  read_heading: "Use exactly one <h1> that says what the page is about. Turn the others into <h2>.",
  act_controls: "Use <button> for actions and <a href> for navigation instead of clickable <div> or <span> elements.",
  act_labels: "Add a <label for> (or aria-label) to every field. Keep the placeholder only as an example.",
  act_autocomplete: "Add the right type and autocomplete, for example type=\"email\" autocomplete=\"email\".",
  popups: "Show at most one small banner at load, with a Close or Accept button that has a text name, and remember the choice.",
  motion: "Stop sliders from changing by themselves, or pause them until the visitor presses Play.",
  shop_price: "Show each price as text with its currency, for example $129.00, not inside an image.",
  shop_offer: "Add Product JSON-LD with an Offer that has price, priceCurrency and availability.",
  shop_stock: "Write the stock state in words next to the price: In stock, Only 2 left, Sold out.",
  shop_cart: "Use a <button> with the text Add to cart (or aria-label=\"Add to cart\" on an icon button).",
};
const STEP = { find_search: "Find", find_urls: "Find", read_names: "Read", read_unique: "Read", read_heading: "Read", act_controls: "Act", act_labels: "Forms", act_autocomplete: "Forms", popups: "Pop-ups", motion: "Understand", shop_price: "Shop", shop_offer: "Shop", shop_stock: "Shop", shop_cart: "Shop" };
const LABEL = {
  find_search: "Search has a results page at its own address",
  find_urls: "Every page has its own address",
  read_names: "Every button and link has a name",
  read_unique: "Names are clear and unique",
  read_heading: "One main heading names the page",
  act_controls: "Clickable things are real buttons and links",
  act_labels: "Form fields have labels",
  act_autocomplete: "Fields say what they want",
  popups: "Nothing covers the page when it loads",
  motion: "Content does not change by itself",
  shop_price: "Prices are text with a currency",
  shop_offer: "Product data has price, currency and stock",
  shop_stock: "Stock is stated in words",
  shop_cart: "Add to cart is a named button",
};
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
const KIND = { cookie: "A cookie banner", newsletter: "A newsletter pop-up", chat: "A chat window", gate: "An age or country gate", other: "A pop-up", header: "A fixed header" };

function evaluate(s, extra = {}) {
  if (!s || typeof s !== "object") return null;
  const checks = [];
  const add = (id, status, message, data) => checks.push({ id, step: STEP[id], label: LABEL[id], status, message, why: WHY[id], fix: FIX[id], ...(data ? { data } : {}) });

  // Find
  const sr = s.search || {};
  if (sr.url) add("find_search", "pass", `Results open at ${sr.url}…`, { url: sr.url });
  else if (sr.inputs || sr.buttons) add("find_search", "fail", "Search works only inside the page. There is no results address an agent can open or come back to");
  else add("find_search", "na", "No search box on this page");
  if (s.hashRoute || s.hashLinks >= 3) add("find_urls", "fail", s.hashRoute ? "This page lives at a #/ address" : `${plural(s.hashLinks, "link")} use #/ addresses that load the same page`);
  else add("find_urls", "pass", "Links use real page addresses");

  // Read
  const unnamed = (s.unnamedButtons || 0) + (s.unnamedLinks || 0);
  const named = `${plural(s.buttons || 0, "button")} and ${plural(s.links || 0, "link")}`;
  if (!unnamed) add("read_names", "pass", `All ${named} have a name`);
  else add("read_names", unnamed >= 3 ? "fail" : "warn", `${[s.unnamedButtons ? plural(s.unnamedButtons, "button") : "", s.unnamedLinks ? plural(s.unnamedLinks, "link") : ""].filter(Boolean).join(" and ")} ${unnamed === 1 ? "has" : "have"} no name`, { samples: s.unnamedSamples || [] });
  const dup = (s.dupButtons || [])[0];
  if (dup) add("read_unique", "warn", `${dup.count} different buttons are all called "${dup.name}"`, { dup: s.dupButtons });
  else if ((s.genericLinks || 0) >= 3) add("read_unique", "warn", `${s.genericLinks} links only say "${s.genericText}"`);
  else add("read_unique", "pass", "Buttons and links have clear, different names");
  if (s.h1 === 1) add("read_heading", "pass", "One main heading");
  else add("read_heading", "warn", s.h1 ? `${s.h1} main headings, so the topic is unclear` : "No main heading");

  // Act and forms
  const cb = s.clickBoxes || 0;
  add("act_controls", cb >= 10 ? "fail" : cb >= 3 ? "warn" : "pass", cb >= 3 ? `${cb} boxes react to clicks but are not buttons or links` : "Clickable things are buttons and links", cb >= 3 ? { samples: s.clickSamples || [] } : null);
  const f = s.fields || [];
  if (!f.length) add("act_labels", "na", "No form fields on this page");
  else {
    const bad = f.filter((x) => !x.labeled), ph = bad.filter((x) => x.placeholder).length;
    if (!bad.length) add("act_labels", "pass", f.length === 1 ? "The form field has a label" : `All ${f.length} form fields have a label`);
    else add("act_labels", "fail", `${bad.length} of ${f.length} fields have no label${ph ? ` (${ph} only have placeholder text)` : ""}`, { fields: bad.slice(0, 6) });
  }
  const kinds = f.filter((x) => x.kind);
  if (!kinds.length) add("act_autocomplete", "na", "No email, phone or name fields on this page");
  else {
    const want = { email: "email", tel: "tel", name: "text" };
    const miss = kinds.filter((x) => !x.autocomplete || x.autocomplete === "off" || (x.kind !== "name" && x.type !== want[x.kind]));
    if (!miss.length) add("act_autocomplete", "pass", `${plural(kinds.length, "contact field")} ${kinds.length === 1 ? "says" : "say"} what ${kinds.length === 1 ? "it wants" : "they want"}`);
    else add("act_autocomplete", "warn", `${miss.length} of ${plural(kinds.length, "contact field")} miss the type or autocomplete attribute`, { fields: miss.slice(0, 6) });
  }

  // Pop-ups
  const ov = (s.overlays || []).filter((o) => o.kind !== "header" || o.coversCenter);
  const blocking = ov.filter((o) => o.coversCenter || o.area >= 30);
  const minor = ov.filter((o) => !blocking.includes(o) && (o.area >= 5 || o.closeUnnamed));
  if (blocking.length) add("popups", "fail", `${KIND[blocking[0].kind]} covers ${blocking[0].coversCenter ? "the middle of the screen" : blocking[0].area + "% of the screen"}${ov.length > 1 ? `, and ${plural(ov.length - 1, "more layer")} sit over the page` : ""}`, { overlays: ov });
  else if (minor.length) add("popups", "warn", `${KIND[minor[0].kind]} covers ${minor[0].area}% of the screen${minor[0].closeUnnamed ? " and its close button has no name" : ""}`, { overlays: ov });
  else add("popups", "pass", ov.length ? "Only a small widget sits over the page" : "No pop-up, banner or chat window covers the page", ov.length ? { overlays: ov } : null);

  // Motion
  const m = extra.motion;
  if (m && m.candidates) add("motion", m.moving ? "warn" : "pass", m.moving ? (m.moving === 1 ? "A slider changes by itself while the page is open" : `${m.moving} sliders change by themselves`) : "Sliders stay still until someone uses them");

  // Shop (only when the page sells something)
  const sh = s.shop || {};
  const isShop = !!(sh.productData || sh.microdata || sh.cart);
  if (isShop) {
    const pd = { name: sh.productName || null, offer: sh.offer || null };
    add("shop_price", sh.priceText ? "pass" : "fail", sh.priceText ? `Price found as text: ${sh.priceText}` : "No price with a currency sign was found as text", pd);
    const o = sh.offer;
    if (o && o.price && o.currency && o.availability) add("shop_offer", "pass", `Product data: ${o.price} ${o.currency}, ${String(o.availability).replace(/^https?:\/\/schema\.org\//, "")}`);
    else if (sh.productData) add("shop_offer", "warn", `Product data is missing ${[!(o && o.price) && "price", !(o && o.currency) && "currency", !(o && o.availability) && "availability"].filter(Boolean).join(", ")}`, pd);
    else add("shop_offer", "fail", "No Product data on this page", pd);
    add("shop_stock", sh.stockText || (o && o.availability) ? "pass" : "warn", sh.stockText ? "Stock is stated in words" : o && o.availability ? "Stock is in the product data, not in the text" : "Stock is not stated in words");
    add("shop_cart", sh.cart ? "pass" : "warn", sh.cart ? `Button found: "${sh.cart}"` : "No button named Add to cart or Buy now on this page");
  }

  const scored = checks.filter((c) => c.status !== "na");
  const pts = scored.reduce((t, c) => t + (c.status === "pass" ? 1 : c.status === "warn" ? 0.5 : 0), 0);
  const pct = scored.length ? Math.round((100 * pts) / scored.length) : null;
  const grade = pct == null ? null : pct >= 90 ? "A" : pct >= 75 ? "B" : pct >= 60 ? "C" : pct >= 40 ? "D" : "F";
  return {
    version: 1, url: s.url, title: s.title, grade, percent: pct,
    passed: scored.filter((c) => c.status === "pass").length, scored: scored.length,
    is_shop: isShop, checks, tree: (s.tree || []).slice(0, 42), overlays: s.overlays || [], viewport: s.viewport,
    counts: { buttons: s.buttons, links: s.links, fields: s.fieldCount, unnamed },
  };
}

module.exports = { snapshot, motionProbe, evaluate, WHY, FIX, LABEL };
