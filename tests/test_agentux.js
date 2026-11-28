// Unit tests for api/_agentux.js evaluate(). Run: node tests/test_agentux.js
// With CHROME_PATH set, it also runs the real function on the two fixture pages (start tests/devserver.py on 8765 first).
const assert = require("node:assert/strict");
const { evaluate } = require("../api/_agentux.js");
let n = 0;
const t = (name, fn) => { fn(); n++; };
const base = () => ({ url: "https://x.test/", title: "X", viewport: { w: 1280, h: 800 }, buttons: 4, links: 10, unnamedButtons: 0, unnamedLinks: 0, unnamedSamples: [],
  dupButtons: [], genericLinks: 0, genericText: "", fields: [], fieldCount: 0, clickBoxes: 0, clickSamples: [], overlays: [], h1: 1, emptyHeadings: 0,
  search: { inputs: 0, buttons: 0, url: null }, hashLinks: 0, hashRoute: false, shop: { productData: false, microdata: false, offer: null, priceText: null, cart: null, stockText: false }, tree: [] });
const st = (r, id) => (r.checks.find((c) => c.id === id) || {}).status;

t("clean page passes and skips shop checks", () => {
  const r = evaluate(base());
  assert.equal(r.grade, "A"); assert.equal(r.is_shop, false);
  assert.equal(st(r, "find_search"), "na"); assert.equal(st(r, "act_labels"), "na"); assert.equal(st(r, "shop_price"), undefined);
});
t("search without a results address fails", () => {
  const s = base(); s.search = { inputs: 0, buttons: 1, url: null };
  assert.equal(st(evaluate(s), "find_search"), "fail");
  s.search.url = "https://x.test/search?q="; assert.equal(st(evaluate(s), "find_search"), "pass");
});
t("unnamed controls: 1 to 2 warn, 3 or more fail", () => {
  const s = base(); s.unnamedButtons = 2; assert.equal(st(evaluate(s), "read_names"), "warn");
  s.unnamedLinks = 1; assert.equal(st(evaluate(s), "read_names"), "fail");
  assert.match(evaluate(s).checks.find((c) => c.id === "read_names").message, /2 buttons and 1 link have no name/);
});
t("same-name buttons and generic links warn", () => {
  const s = base(); s.dupButtons = [{ name: "Save", count: 4 }];
  assert.match(evaluate(s).checks.find((c) => c.id === "read_unique").message, /4 different buttons are all called "Save"/);
  const g = base(); g.genericLinks = 5; g.genericText = "Read more"; assert.equal(st(evaluate(g), "read_unique"), "warn");
});
t("fields without labels fail, placeholder-only is counted", () => {
  const s = base(); s.fields = [{ labeled: false, placeholder: "Email", kind: "email", type: "text", autocomplete: "" }, { labeled: true, placeholder: "", kind: null, type: "text", autocomplete: "" }];
  const r = evaluate(s);
  assert.equal(st(r, "act_labels"), "fail"); assert.match(r.checks.find((c) => c.id === "act_labels").message, /1 of 2 fields have no label \(1 only have placeholder text\)/);
  assert.equal(st(r, "act_autocomplete"), "warn");
});
t("email field with type and autocomplete passes", () => {
  const s = base(); s.fields = [{ labeled: true, placeholder: "", kind: "email", type: "email", autocomplete: "email" }];
  assert.equal(st(evaluate(s), "act_autocomplete"), "pass");
});
t("pop-ups: center blocks, a small bottom bar warns, a chat bubble passes", () => {
  const s = base(); s.overlays = [{ kind: "cookie", area: 50, coversCenter: true, closeNamed: true, closeUnnamed: false }];
  assert.equal(st(evaluate(s), "popups"), "fail");
  s.overlays = [{ kind: "cookie", area: 12, coversCenter: false, closeNamed: true, closeUnnamed: false }]; assert.equal(st(evaluate(s), "popups"), "warn");
  s.overlays = [{ kind: "chat", area: 1, coversCenter: false, closeNamed: false, closeUnnamed: false }]; assert.equal(st(evaluate(s), "popups"), "pass");
});
t("sliders that move warn, no sliders means no check", () => {
  assert.equal(st(evaluate(base(), { motion: { candidates: 1, moving: 1 } }), "motion"), "warn");
  assert.equal(st(evaluate(base(), { motion: { candidates: 0, moving: 0 } }), "motion"), undefined);
});
t("a shop page gets the four shop checks", () => {
  const s = base(); s.shop = { productData: true, offer: { price: "129.00", currency: null, availability: null }, priceText: null, cart: null, stockText: false };
  const r = evaluate(s);
  assert.equal(r.is_shop, true);
  assert.equal(st(r, "shop_price"), "fail"); assert.equal(st(r, "shop_offer"), "warn"); assert.equal(st(r, "shop_stock"), "warn"); assert.equal(st(r, "shop_cart"), "warn");
  assert.match(r.checks.find((c) => c.id === "shop_offer").message, /missing currency, availability/);
});
t("grade: pass 1, warn half, n/a not counted", () => {
  const s = base(); s.h1 = 2; s.hashRoute = true;   // read_heading warn, find_urls fail
  const r = evaluate(s);
  assert.equal(r.scored, 6); assert.equal(r.passed, 4); assert.equal(r.percent, Math.round(100 * 4.5 / 6)); assert.equal(r.grade, "B");
});
t("every check has a step, label, why and fix", () => {
  const s = base(); s.shop.productData = true;
  evaluate(s, { motion: { candidates: 1, moving: 0 } }).checks.forEach((c) => { assert.ok(c.step && c.label && c.why && c.fix, c.id); assert.ok(!/—/.test(c.message + c.why + c.fix), "no long dash: " + c.id); });
});

(async () => {
  if (process.env.CHROME_PATH) {
    process.env.AGENTUX_ALLOW_LOCAL = "1";
    const h = require("../api/agentux.js");
    const call = (u) => new Promise((ok) => h({ url: "/api/agentux?url=" + encodeURIComponent(u) }, { statusCode: 0, setHeader() {}, end(b) { ok({ code: this.statusCode, j: JSON.parse(b) }); } }));
    const bad = await call("http://127.0.0.1:8765/tests/fixtures/agent-bad.html");
    assert.equal(bad.code, 200); assert.equal(bad.j.grade, "F"); assert.ok(bad.j.screenshot.startsWith("data:image/jpeg;base64,"));
    assert.equal(st(bad.j, "popups"), "fail"); assert.equal(st(bad.j, "motion"), "warn"); assert.equal(st(bad.j, "find_search"), "fail"); assert.equal(st(bad.j, "find_urls"), "fail");
    const good = await call("http://127.0.0.1:8765/tests/fixtures/agent-good.html");
    assert.equal(good.j.grade, "A"); assert.equal(st(good.j, "shop_price"), "pass"); assert.equal(st(good.j, "find_search"), "pass");
    n += 2;
  }
  console.log(n + " agent usability tests passed");
})().catch((e) => { console.error(e); process.exit(1); });
