/**
 * Card tests. Runs without a browser or Home Assistant:
 *
 *     node tests/test_cards.mjs
 *
 * The module is a browser ES module, so the few globals it touches at import
 * time are stubbed and the card bodies are rendered against fixture data.
 */
import assert from "node:assert/strict";

class FakeElement {
  constructor() { this.shadowRoot = null; }
  attachShadow() {
    this.shadowRoot = { innerHTML: "", querySelector: () => new FakeCard() };
    return this.shadowRoot;
  }
}
class FakeCard { constructor() { this.innerHTML = ""; }
  addEventListener() {} }

globalThis.HTMLElement = FakeElement;
globalThis.customElements = { _defined: new Map(),
  get(name) { return this._defined.get(name); },
  define(name, cls) { this._defined.set(name, cls); } };
globalThis.window = {};

const mod = await import("../custom_components/tapo_h500/www/tapo-h500-card.js");
const { esc, ago, groupByHour, utcDay, TapoH500Card, TapoH500HeroCard,
        TapoH500GridCard, TapoH500TimelineCard } = mod;

let failures = 0;
const test = (name, fn) => {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures += 1; console.log(`  FAIL ${name}\n       ${err.message}`); }
};

// --- pure helpers ----------------------------------------------------------

test("escaping closes the innerHTML hole", () => {
  assert.equal(esc(`<img src=x onerror="alert(1)">`),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  assert.equal(esc("Tom & Jerry's"), "Tom &amp; Jerry&#39;s");
  assert.equal(esc(null), "");
});

test("relative time floors instead of rounding ahead", () => {
  const now = 1_000_000_000_000;
  const at = (secondsAgo) => ago(now / 1000 - secondsAgo, now);
  assert.equal(at(5), "just now");
  assert.equal(at(59), "just now");
  assert.equal(at(60), "1 minute ago");
  assert.equal(at(119), "1 minute ago", "90s must not read as 2 minutes");
  assert.equal(at(3600), "1 hour ago");
  assert.equal(at(86400 * 3), "3 days ago");
  // A clip whose clock is slightly ahead of the browser must not say
  // "-1 minutes ago".
  assert.equal(at(-30), "just now");
});

test("hour grouping keeps order and splits on the hour", () => {
  const at = (iso) => ({ start_time: Math.floor(new Date(iso) / 1000) });
  const groups = groupByHour([
    at("2026-08-13T09:41:00"), at("2026-08-13T09:12:00"),
    at("2026-08-13T08:55:00"),
  ]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].items.length, 2);
  assert.equal(groups[1].items.length, 1);
  assert.equal(groups[0].when.getHours(), 9);
});

test("hour grouping separates the same hour on different days", () => {
  const at = (iso) => ({ start_time: Math.floor(new Date(iso) / 1000) });
  const groups = groupByHour([at("2026-08-13T09:10:00"), at("2026-08-12T09:10:00")]);
  assert.equal(groups.length, 2, "same hour, different day, must not merge");
});

test("utcDay formats the window the service expects", () => {
  assert.equal(utcDay(new Date(Date.UTC(2026, 7, 3))), "20260803");
});

// --- card bodies -----------------------------------------------------------

const CLIPS = [
  { start_time: Math.floor(Date.now() / 1000) - 120, end_time: 0, duration: 15,
    event_type: "ring", downloaded: true, thumbnail: "/t/1.jpg", url: "/v/1.mp4" },
  { start_time: Math.floor(Date.now() / 1000) - 7200, end_time: 0, duration: 8,
    event_type: "motion", downloaded: false, thumbnail: "/t/2.jpg" },
];

const build = (Cls, config = {}) => {
  const card = new Cls();
  card.setConfig({ ...config });
  card._recordings = CLIPS;
  card._camera = { alias: "Front Doorbell" };
  return card;
};

for (const [name, Cls] of [["list", TapoH500Card], ["hero", TapoH500HeroCard],
                           ["grid", TapoH500GridCard],
                           ["timeline", TapoH500TimelineCard]]) {
  test(`${name} card renders without throwing`, () => {
    const html = build(Cls).body();
    assert.ok(html.length > 0);
    assert.ok(!html.includes("undefined"), "no undefined leaked into the markup");
    assert.ok(!html.includes("NaN"), "no NaN leaked into the markup");
  });
}

test("an undownloaded clip is never offered a player", () => {
  // Selection outlives a reload, so every card must cope with the selected id
  // pointing at a clip that has no url -- otherwise <video src="undefined">.
  for (const Cls of [TapoH500Card, TapoH500HeroCard, TapoH500GridCard,
                     TapoH500TimelineCard]) {
    // The undownloaded clip is put first as well, so the hero is genuinely
    // exercised rather than passing because it only ever shows [0].
    for (const order of [CLIPS, [CLIPS[1], CLIPS[0]]]) {
      const card = build(Cls);
      card._recordings = order;
      card._playing = String(CLIPS[1].start_time);
      const html = card.body();
      assert.ok(!html.includes("<video"),
        `${Cls.name} played an undownloaded clip`);
      assert.ok(!html.includes("undefined"), `${Cls.name} leaked undefined`);
    }
  }
});

test("the hero shows the newest clip, not the oldest", () => {
  const html = build(TapoH500HeroCard).body();
  assert.ok(html.includes("2 minutes ago"), "expected the 2-minute-old clip");
  assert.ok(html.includes("ring"));
});

test("hero swaps the still for the player rather than stacking both", () => {
  const card = build(TapoH500HeroCard);
  card._playing = String(CLIPS[0].start_time);
  const html = card.body();
  assert.ok(html.includes("<video"));
  assert.ok(!html.includes("class=\"frame\""), "still frame should be replaced");
});

test("grid keeps one player below the tiles", () => {
  const card = build(TapoH500GridCard);
  card._playing = String(CLIPS[0].start_time);
  const html = card.body();
  assert.equal(html.split("<video").length - 1, 1, "exactly one player");
  assert.ok(html.indexOf("<video") > html.lastIndexOf("class=\"tile\""),
    "player must come after the tiles");
});

test("a hostile camera alias cannot break out of the markup", () => {
  const card = build(TapoH500TimelineCard);
  card._recordings = [{ ...CLIPS[0], event_type: `x"><script>alert(1)</script>` }];
  const html = card.body();
  assert.ok(!html.includes("<script>"), "event_type reached innerHTML unescaped");
});

test("every card type is registered exactly once", () => {
  const types = ["tapo-h500-card", "tapo-h500-hero-card", "tapo-h500-grid-card",
                 "tapo-h500-timeline-card"];
  for (const type of types) assert.ok(customElements.get(type), `${type} missing`);
  assert.equal(globalThis.window.customCards.length, types.length);
});

console.log(failures ? `\n${failures} failure(s)` : "\nall card tests passed");
process.exit(failures ? 1 : 0);
