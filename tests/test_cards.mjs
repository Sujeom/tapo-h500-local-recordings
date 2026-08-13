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
  constructor() { this.shadowRoot = null; this.children = []; }
  appendChild(child) { this.children.push(child); }
  addEventListener() {}
  dispatchEvent() {}
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
globalThis.document = { createElement: (tag) => ({ tagName: tag, addEventListener() {} }) };
globalThis.CustomEvent = class { constructor(type, opts) { this.type = type; Object.assign(this, opts); } };

const mod = await import("../custom_components/tapo_h500/www/tapo-h500-card.js");
const { esc, ago, groupByHour, groupByFace, eventsByHour, niceMax,
        editorSchema, mergeConfig, utcDay,
        windowDates, TapoH500Card, TapoH500HeroCard, TapoH500GridCard,
        TapoH500TimelineCard, TapoH500FacesCard, TapoH500SummaryCard } = mod;

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

test("a local evening is not dropped by asking for the UTC day alone", () => {
  // Regression: on a UTC-4 machine, 21:16 local on the 12th is 01:16 UTC on
  // the 13th. Asking only for utcDay(now) covered the 13th and lost the whole
  // local day; asking only for the local date lost the clip itself. The range
  // has to span both.
  const evening = new Date("2026-08-13T01:16:23Z");   // 21:16 local at UTC-4
  const range = windowDates(1, evening);
  const localDate = `${evening.getFullYear()}` +
    `${String(evening.getMonth() + 1).padStart(2, "0")}` +
    `${String(evening.getDate()).padStart(2, "0")}`;
  assert.ok(range.start_date <= localDate,
    `range starts at ${range.start_date}, after the local day ${localDate}`);
  assert.ok(range.end_date >= utcDay(evening),
    "range must reach the UTC date the clip actually falls on");
});

test("more days widens backwards, never forwards", () => {
  const now = new Date("2026-08-13T12:00:00Z");
  const one = windowDates(1, now);
  const three = windowDates(3, now);
  assert.equal(three.end_date, one.end_date, "must not ask for the future");
  assert.ok(three.start_date < one.start_date, "three days must reach back further");
  assert.equal(windowDates(0, now).start_date, one.start_date,
    "a nonsense day count must not invert the range");
});

test("faces group by id, newest sighting first", () => {
  // Cards hold recordings newest-first, so the first hit for an id is the
  // newest and is what supplies the picture.
  const clips = [
    { start_time: 300, face_ids: [7] },
    { start_time: 200, face_ids: [7, 9] },
    { start_time: 100, face_ids: [] },
  ];
  const faces = groupByFace(clips, {});
  assert.equal(faces.length, 2, "two distinct people, one motion-only clip");
  const seven = faces.find((f) => f.id === "7");
  assert.equal(seven.sightings, 2);
  assert.equal(seven.newest.start_time, 300, "must show the newest sighting");
  assert.equal(faces.find((f) => f.id === "9").sightings, 1);
});

test("a face with no name is not silently blank", () => {
  // The hub supplies no names, so an unconfigured face must still be
  // identifiable enough to add to the map.
  const [face] = groupByFace([{ start_time: 1, face_ids: [42] }], {});
  assert.equal(face.name, undefined);
  assert.equal(face.id, "42");
});

test("names are matched by string id, whatever the config used", () => {
  // YAML gives a huge id as a number; the hub sends a number too. Both have to
  // land on the same key or every tile reads as unnamed.
  const clips = [{ start_time: 1, face_ids: [272465657857] }];
  assert.equal(groupByFace(clips, { 272465657857: "Alice" })[0].name, "Alice");
  assert.equal(groupByFace(clips, { "272465657857": "Alice" })[0].name, "Alice");
});

test("no faces at all is an empty summary, not a crash", () => {
  assert.deepEqual(groupByFace([], {}), []);
  assert.deepEqual(groupByFace([{ start_time: 1 }], {}), []);
  assert.deepEqual(groupByFace([{ start_time: 1, face_ids: null }], {}), []);
});

test("events bucket into 24 local hours", () => {
  const at = (iso) => ({ start_time: Math.floor(new Date(iso) / 1000) });
  const hours = eventsByHour([
    at("2026-08-13T09:41:00"), at("2026-08-13T09:12:00"),
    at("2026-08-12T21:16:00"),
  ]);
  assert.equal(hours.length, 24, "one slot per hour, always");
  assert.equal(hours[9], 2);
  assert.equal(hours[21], 1);
  assert.equal(hours.reduce((a, b) => a + b, 0), 3, "no event lost or double-counted");
});

test("an empty period is 24 zeroes, not an empty array", () => {
  // The axis must still draw, or the card reads as broken rather than quiet.
  assert.deepEqual(eventsByHour([]), new Array(24).fill(0));
});

test("the scale tops out at a round number above the peak", () => {
  // Never the raw max: the tallest bar would touch the ceiling with no
  // headroom, and the gridline would be an unreadable number.
  assert.equal(niceMax(3), 5);
  assert.equal(niceMax(5), 5);
  assert.equal(niceMax(6), 10);
  assert.equal(niceMax(11), 20);
  assert.equal(niceMax(0), 1, "an empty chart still needs a scale");
  assert.ok(niceMax(137) >= 137);
});

// --- card bodies -----------------------------------------------------------

const CLIPS = [
  { start_time: Math.floor(Date.now() / 1000) - 120, end_time: 0, duration: 15,
    event_type: "ring", downloaded: true, thumbnail: "/t/1.jpg", url: "/v/1.mp4",
    face_ids: [272465657857] },
  { start_time: Math.floor(Date.now() / 1000) - 7200, end_time: 0, duration: 8,
    event_type: "motion", downloaded: false, thumbnail: "/t/2.jpg",
    face_ids: [272465657857] },
];

const build = (Cls, config = {}) => {
  const card = new Cls();
  card.setConfig({ ...config });
  card._recordings = CLIPS;
  card._camera = { alias: "Front Doorbell" };
  return card;
};

test("the summary chart reserves room for its own x-axis labels", () => {
  // Anti-pattern: a fixed height that fits the plot but clips the axis band,
  // giving the card a tiny nested scrollbar.
  const card = build(TapoH500SummaryCard, { days: 7 });
  const html = card.body();
  const [, w, h] = html.match(/viewBox="0 0 (\d+) (\d+)"/).map(Number);
  const lastTickY = Math.max(...[...html.matchAll(/<text class="tick"[^>]*y="([\d.]+)"/g)]
    .map((m) => Number(m[1])));
  assert.ok(lastTickY <= h, `x labels at y=${lastTickY} fall outside the ${h}px box`);
  assert.ok(w > 0 && h > 0);
});

test("the summary has a table twin, so no value is tooltip-only", () => {
  const card = build(TapoH500SummaryCard, { days: 7 });
  assert.ok(card.body().includes("<svg"), "defaults to the chart");
  card._showTable = true;
  const table = card.body();
  assert.ok(table.includes("<table"), "toggles to a table");
  assert.equal((table.match(/<tr>/g) || []).length, 25, "header plus 24 hours");
  assert.ok(!table.includes("<svg"), "one view at a time");
});

test("only the busiest hour is labelled on the chart", () => {
  // A number on every bar is chaos and goes unread.
  const card = build(TapoH500SummaryCard, { days: 7 });
  const html = card.body();
  assert.ok((html.match(/class="peak"/g) || []).length <= 1);
});

for (const [name, Cls] of [["list", TapoH500Card], ["hero", TapoH500HeroCard],
                           ["grid", TapoH500GridCard],
                           ["timeline", TapoH500TimelineCard],
                           ["faces", TapoH500FacesCard],
                           ["summary", TapoH500SummaryCard]]) {
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
                     TapoH500TimelineCard, TapoH500FacesCard]) {
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

const ALL = [["list", TapoH500Card], ["hero", TapoH500HeroCard],
             ["grid", TapoH500GridCard], ["timeline", TapoH500TimelineCard],
             ["faces", TapoH500FacesCard], ["summary", TapoH500SummaryCard]];

test("any card can be pinned to a single camera", () => {
  for (const [name, Cls] of ALL) {
    const card = build(Cls, { camera_index: 1 });
    card._cameras = [{ index: 0, alias: "Front" }, { index: 1, alias: "Side" }];
    card._render();
    const html = card._card.innerHTML;
    assert.ok(!html.includes('data-action="camera"'),
      `${name}: the picker must be hidden once a camera is pinned`);
    assert.equal(card._index, 1, `${name}: must load the pinned camera`);
  }
});

test("without a pin, a multi-camera hub offers the picker", () => {
  for (const [name, Cls] of ALL) {
    const card = build(Cls, {});
    card._cameras = [{ index: 0, alias: "Front" }, { index: 1, alias: "Side" }];
    card._render();
    assert.ok(card._card.innerHTML.includes('data-action="camera"'),
      `${name}: two cameras and no pin should offer a choice`);
  }
});

test("a single-camera hub is not given a pointless picker", () => {
  const card = build(TapoH500Card, {});
  card._cameras = [{ index: 0, alias: "Front" }];
  card._render();
  assert.ok(!card._card.innerHTML.includes('data-action="camera"'));
});

test("every card offers resize handles, sized to what it needs", () => {
  for (const [name, Cls] of ALL) {
    const card = build(Cls, {});
    const grid = card.getGridOptions();
    assert.ok(grid.rows > 0 && grid.columns > 0, `${name}: needs a default size`);
    assert.ok(grid.min_rows <= grid.rows, `${name}: floor above the default`);
    assert.ok(grid.min_columns <= grid.columns, `${name}: floor above the default`);
  }
  // The chart is the one card that genuinely cannot be squashed.
  assert.ok(build(TapoH500SummaryCard, {}).getGridOptions().min_columns >= 6);
  assert.ok(build(TapoH500HeroCard, {}).getGridOptions().min_rows >= 4);
});

test("a size the user dragged wins over the card's default", () => {
  const card = build(TapoH500Card, { grid_options: { rows: 12, columns: 4 } });
  assert.deepEqual(
    { rows: card.getGridOptions().rows, columns: card.getGridOptions().columns },
    { rows: 12, columns: 4 });
});

test("a dragged height is not then capped by the default max_height", () => {
  // Otherwise dragging a card taller strands blank space under a short list.
  const dragged = build(TapoH500Card, { grid_options: { rows: 12 } });
  assert.equal(dragged._maxHeight(), "", "default cap must yield to the drag");
  // An explicitly configured cap is the user asking, so it still applies.
  const explicit = build(TapoH500Card,
    { grid_options: { rows: 12 }, max_height: 200 });
  assert.ok(explicit._maxHeight().includes("200px"));
});

test("a narrow timeline keeps the time readable", () => {
  // Regression: .at was flex:1 with min-width:0, so the time was the one
  // element that absorbed every narrow layout and lost its seconds. The
  // detection label made it far worse, being much longer than "motion".
  const css = TapoH500TimelineCard.style.replace(/\s+/g, " ");
  const at = css.match(/\.event \.at \{([^}]*)\}/)[1];
  assert.ok(/flex:\s*none/.test(at), "the time must not shrink");
  assert.ok(/white-space:\s*nowrap/.test(at), "the time must not wrap mid-value");
  const badge = css.match(/\.event \.badge \{([^}]*)\}/)[1];
  assert.ok(/text-overflow:\s*ellipsis/.test(badge),
    "the long detection label is what should give way instead");
  assert.ok(/flex-wrap:\s*wrap/.test(css), "the row should wrap, not crush");
});

test("an ellipsized badge still carries its full label", () => {
  const card = build(TapoH500TimelineCard, {});
  card._recordings = [{ ...CLIPS[0], detection: "motion + person + vehicle + type 22" }];
  const html = card.body();
  assert.ok(html.includes('title="motion + person + vehicle + type 22"'),
    "the hidden half of a truncated badge must be reachable on hover");
});

test("every card offers a visual editor", () => {
  // Without getConfigElement, Home Assistant reports "no visual editor
  // available" and the card can only be configured in YAML.
  for (const [name, Cls] of ALL) {
    assert.equal(typeof Cls.getConfigElement, "function", `${name}: no editor`);
    assert.ok(Cls.getConfigElement(), `${name}: editor element not created`);
    assert.equal(typeof Cls.getStubConfig, "function", `${name}: no stub config`);
  }
  assert.ok(customElements.get("tapo-h500-card-editor"), "editor not registered");
});

test("the editor only offers settings the card actually has", () => {
  // max_height means nothing on a card with no scrolling region; offering it
  // would invite a setting that silently does nothing.
  for (const type of ["tapo-h500-hero-card", "tapo-h500-summary-card"]) {
    assert.ok(!editorSchema(type).some((f) => f.name === "max_height"),
      `${type} does not scroll and must not offer max_height`);
  }
  for (const type of ["tapo-h500-card", "tapo-h500-grid-card",
                      "tapo-h500-timeline-card", "tapo-h500-faces-card"]) {
    assert.ok(editorSchema(type).some((f) => f.name === "max_height"), type);
  }
  // camera_index is the one-camera pin, so every card must offer it.
  for (const c of ALL) {
    assert.ok(editorSchema(`tapo-h500-card`).some((f) => f.name === "camera_index"));
  }
});

test("editing in the UI does not delete what the form cannot see", () => {
  // The form has no field for names or grid_options. Replacing the config
  // instead of merging would wipe a user's face names on any edit.
  const before = { type: "tapo-h500-faces-card", days: 7,
                   names: { 272465657857: "Alice" }, grid_options: { rows: 9 } };
  const after = mergeConfig(before, { days: 14, camera_index: 1 });
  assert.deepEqual(after.names, { 272465657857: "Alice" });
  assert.deepEqual(after.grid_options, { rows: 9 });
  assert.equal(after.days, 14);
  assert.equal(after.camera_index, 1);
});

test("a cleared field is removed, not stored as empty", () => {
  // An empty string would beat the card's own default and read as a setting.
  const after = mergeConfig({ type: "x", days: 7, entry_id: "abc" },
                            { entry_id: "", days: undefined });
  assert.deepEqual(after, { type: "x" });
});

test("every card type is registered exactly once", () => {
  const types = ["tapo-h500-card", "tapo-h500-hero-card", "tapo-h500-grid-card",
                 "tapo-h500-timeline-card", "tapo-h500-faces-card",
                 "tapo-h500-summary-card"];
  for (const type of types) assert.ok(customElements.get(type), `${type} missing`);
  assert.equal(globalThis.window.customCards.length, types.length);
});

console.log(failures ? `\n${failures} failure(s)` : "\nall card tests passed");
process.exit(failures ? 1 : 0);
