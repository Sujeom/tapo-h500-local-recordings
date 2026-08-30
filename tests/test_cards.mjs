/**
 * Card tests. Runs without a browser or Home Assistant:
 *
 *     node tests/test_cards.mjs
 *
 * The module is a browser ES module, so the few globals it touches at import
 * time are stubbed and the card bodies are rendered against fixture data.
 */
import assert from "node:assert/strict";

/** A control the card rendered, identified the way the card identifies it. */
class FakeButton {
  constructor(dataset) { this.dataset = dataset; this.focused = false; }
  focus() { this.focused = true; }
}
class FakeElement {
  constructor() { this.shadowRoot = null; this.children = []; }
  appendChild(child) { this.children.push(child); }
  addEventListener() {}
  dispatchEvent() {}
  attachShadow() {
    const card = new FakeCard();
    // The live region lives beside the card and is made once; the card is
    // rebuilt. Which is the whole point of it being out here.
    const status = { textContent: "" };
    const root = { innerHTML: "", activeElement: null };
    root.querySelector = (selector) => {
      if (selector === "ha-card") return card;
      // Only if the card actually put one there. Handing it back regardless
      // would hide the case where nobody made a live region at all.
      if (selector === '[role="status"]') {
        return root.innerHTML.includes('role="status"') ? status : null;
      }
      return null;
    };
    this.shadowRoot = root;
    return this.shadowRoot;
  }
}
class FakeVideo {
  constructor() {
    this.currentTime = 0; this.paused = false; this._listeners = {};
  }
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }
  pause() { this.paused = true; }
  /** What the browser does once the media has read its duration. */
  loadedmetadata() { (this._listeners.loadedmetadata || []).forEach(fn => fn()); }
}
class FakeCard {
  constructor() { this.innerHTML = ""; this.writes = 0; this.video = null; }
  addEventListener() {}
  querySelector(selector) { return selector === "video" ? this.video : null; }
  /** The buttons in whatever was last written, read out of the markup.
   *
   * Built once per write and kept, because the browser's are the same
   * objects every time you ask -- and focusing a throwaway proves nothing.
   */
  querySelectorAll() {
    if (this._buttons) return this._buttons;
    this._buttons = [...this.innerHTML.matchAll(/<button([^>]*)>/g)].map(([, attrs]) => {
      const dataset = {};
      for (const [, key, value] of attrs.matchAll(/data-([a-z]+)="([^"]*)"/g)) {
        dataset[key] = value;
      }
      return new FakeButton(dataset);
    }).filter((button) => button.dataset.action);
    return this._buttons;
  }
}
// The card writes innerHTML; counting the writes is how "it did not rebuild"
// is checked. Writing it also replaces any player, which is what the browser
// does and the whole reason an unchanged render must not write.
Object.defineProperty(FakeCard.prototype, "innerHTML", {
  get() { return this._html || ""; },
  set(value) {
    this._html = value;
    this.writes = (this.writes || 0) + 1;
    this.video = value.includes("<video") ? new FakeVideo() : null;
    this._buttons = null;
  },
});

globalThis.HTMLElement = FakeElement;
globalThis.customElements = { _defined: new Map(),
  get(name) { return this._defined.get(name); },
  define(name, cls) { this._defined.set(name, cls); } };
globalThis.window = {};
globalThis.document = {
  hidden: false,
  _listeners: {},
  createElement: (tag) => ({ tagName: tag, addEventListener() {} }),
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); },
  removeEventListener(type, fn) {
    this._listeners[type] = (this._listeners[type] || []).filter((f) => f !== fn);
  },
  /** What the browser does when a tab is shown or hidden. */
  setHidden(hidden) {
    this.hidden = hidden;
    (this._listeners.visibilitychange || []).slice().forEach((fn) => fn());
  },
};
// The minute timer, kept rather than run, so a test can fire it and see what
// the card does with it -- which is where the visibility check has to be.
const intervals = new Map();
let nextInterval = 1;
globalThis.setInterval = (fn) => {
  intervals.set(nextInterval, fn);
  return nextInterval++;
};
globalThis.clearInterval = (id) => { intervals.delete(id); };
const fireTimers = () => [...intervals.values()].forEach((fn) => fn());
globalThis.CustomEvent = class { constructor(type, opts) { this.type = type; Object.assign(this, opts); } };

const mod = await import("../custom_components/tapo_h500/www/tapo-h500-card.js");
const { esc, ago, groupByHour, groupByFace, facesByCount, faceNames,
        groupByPerson, eventsByHour, niceMax,
        editorSchema, mergeConfig, utcDay,
        windowDates, TapoH500Card, TapoH500HeroCard, TapoH500GridCard,
        TapoH500TimelineCard, TapoH500FacesCard, TapoH500SummaryCard,
        TapoH500FaceSummaryCard, TapoH500PeopleCard } = mod;

let failures = 0;
// Async tests are collected and awaited before the summary. Calling fn()
// without awaiting reported every async test as "ok" the instant it was
// started -- its assertions had not run yet, and a failure became an
// unhandled rejection nobody looked at. Three tests passed that way.
const pending = [];
const test = (name, fn) => {
  const done = (err) => {
    if (err) {
      failures += 1;
      console.log(`  FAIL ${name}\n       ${err.message}`);
    } else {
      console.log(`  ok   ${name}`);
    }
  };
  try {
    const result = fn();
    if (result && typeof result.then === "function") {
      pending.push(result.then(() => done(), done));
    } else {
      done();
    }
  } catch (err) { done(err); }
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

// --- finding the hub the card belongs to ------------------------------------

test("a card told which entry to use never asks", () => {
  const card = build(TapoH500Card, { entry_id: "configured" });
  let asked = 0;
  card._hass = { callWS: async () => { asked += 1; return []; } };
  return card._entryId().then((entry) => {
    assert.equal(entry, "configured");
    assert.equal(asked, 0, "the configured entry is the answer");
  });
});

test("a card that was not told finds the entry itself", () => {
  // Most people have one hub and never open the card editor. Making
  // entry_id required would mean every card starts broken.
  const card = build(TapoH500Card, {});
  card._hass = { callWS: async () => [{ entry_id: "found" }] };
  return card._entryId().then((entry) => assert.equal(entry, "found"));
});

test("it asks once and remembers the answer", () => {
  // Every recording, thumbnail and action needs it. Asking per call turns
  // one screen into dozens of websocket round trips.
  const card = build(TapoH500Card, {});
  let asked = 0;
  card._hass = { callWS: async () => { asked += 1; return [{ entry_id: "e" }]; } };
  return card._entryId()
    .then(() => card._entryId())
    .then(() => card._entryId())
    .then(() => assert.equal(asked, 1, "asked once for three uses"));
});

test("no hub at all says so rather than failing later", () => {
  // Without this the undefined entry id travels into every service call and
  // fails there, where the message is about the call and not the cause.
  const card = build(TapoH500Card, {});
  card._hass = { callWS: async () => [] };
  return card._entryId().then(
    () => assert.fail("should have refused"),
    (err) => assert.match(err.message, /No Tapo H500 entry/));
});

test("a websocket that answers nothing is refused too", () => {
  const card = build(TapoH500Card, {});
  card._hass = { callWS: async () => null };
  return card._entryId().then(
    () => assert.fail("should have refused"),
    (err) => assert.match(err.message, /No Tapo H500 entry/));
});

// --- rebuilding only when something changed --------------------------------

test("a poll that found nothing new does not rebuild the card", () => {
  // Replacing innerHTML destroys and remakes every node under it: an open
  // recording restarts from zero with autoplay, keyboard focus falls back to
  // the document, and the scroll position goes. The poll runs every minute
  // whether or not the hub had anything to say.
  const card = build(TapoH500Card, {});
  card._render();
  const first = card._card.writes;
  card._render();
  card._render();
  assert.equal(card._card.writes, first, "three identical renders, one write");
});

test("a new recording does rebuild it", () => {
  const card = build(TapoH500Card, {});
  card._render();
  const before = card._card.writes;
  card._recordings = [{ ...CLIPS[0], start_time: CLIPS[0].start_time + 9 },
                      ...CLIPS];
  card._render();
  assert.equal(card._card.writes, before + 1);
});

test("switching camera rebuilds even if the list looks the same", () => {
  const card = build(TapoH500Card, {});
  card._cameras = [{ index: 0, alias: "Front" }, { index: 1, alias: "Side" }];
  card._render();
  const before = card._card.writes;
  card._index = 1;
  card._render();
  assert.equal(card._card.writes, before + 1,
    "the picker's pressed state is part of the markup");
});

test("a reconfigure forgets what was on screen", () => {
  // Otherwise the first render after a config change compares against markup
  // that belonged to the old styles and shape, and skips.
  const card = build(TapoH500Card, {});
  card._render();
  const before = card._card.writes;
  card.setConfig({});
  card._recordings = CLIPS;
  card._camera = { alias: "Front Doorbell" };
  card._render();
  assert.equal(card._card.writes, before + 1);
});

test("a clip being watched keeps its place across a rebuild", () => {
  // A new recording arriving is not a reason to send the one on screen back
  // to the beginning. The fake card replaces its player on every write, the
  // way a browser does.
  const card = build(TapoH500Card, {});
  card._playing = String(CLIPS[0].start_time);
  card._render();
  assert.ok(card._card.video, "the clip being played has a player");
  card._card.video.currentTime = 42;
  card._recordings = [{ ...CLIPS[0], start_time: CLIPS[0].start_time + 9 },
                      ...CLIPS];
  card._render();
  const fresh = card._card.video;
  fresh.loadedmetadata();
  assert.equal(fresh.currentTime, 42);
  assert.equal(fresh.paused, false, "it was playing, so it plays on");
});

test("a paused clip comes back paused across a rebuild", () => {
  const card = build(TapoH500Card, {});
  card._playing = String(CLIPS[0].start_time);
  card._render();
  card._card.video.currentTime = 12;
  card._card.video.paused = true;
  card._recordings = [{ ...CLIPS[0], start_time: CLIPS[0].start_time + 9 },
                      ...CLIPS];
  card._render();
  card._card.video.loadedmetadata();
  assert.equal(card._card.video.currentTime, 12);
  assert.equal(card._card.video.paused, true);
});

test("a clip at the very start is not treated as one to restore", () => {
  // currentTime 0 is where a fresh autoplay starts anyway, and seeking back
  // to 0 would only fight the autoplay attribute.
  const card = build(TapoH500Card, {});
  card._playing = String(CLIPS[0].start_time);
  card._render();
  card._recordings = [{ ...CLIPS[0], start_time: CLIPS[0].start_time + 9 },
                      ...CLIPS];
  card._render();
  assert.equal(card._card.video._listeners.loadedmetadata, undefined);
});

test("a button re-enables itself instead of waiting for the rebuild", async () => {
  // It used to be cleared as a side effect of innerHTML replacing the button.
  // That stopped being true the moment an unchanged render skipped the
  // rebuild, and left Refresh dead after one press on a quiet camera.
  const card = build(TapoH500Card, {});
  card._hass = { connection: { sendMessagePromise: async () => ({
    response: { recordings: [], camera: { alias: "Front" } } }) } };
  card._config.entry_id = "abc";
  const button = { dataset: { action: "refresh" }, disabled: false,
                   closest() { return button; } };
  await card._onClick({ target: button });
  assert.equal(button.disabled, false);
});

test("a button that threw still re-enables", async () => {
  const card = build(TapoH500Card, {});
  card._hass = { connection: { sendMessagePromise: async () => {
    throw new Error("hub not answering"); } } };
  card._config.entry_id = "abc";
  const button = { dataset: { action: "refresh" }, disabled: false,
                   closest() { return button; } };
  await card._onClick({ target: button });
  assert.equal(button.disabled, false);
  assert.equal(card._error, "hub not answering");
});

// --- every button the card draws ------------------------------------------

/** A card whose loads and service calls are recorded rather than made. */
const clickable = (config = {}) => {
  const card = build(TapoH500Card, { entry_id: "abc", ...config });
  card.calls = [];
  card.loads = 0;
  card._load = async () => { card.loads += 1; };
  card._call = async (service, data) => { card.calls.push([service, data]); };
  card._render = () => { card.renders = (card.renders || 0) + 1; };
  return card;
};

const press = (card, dataset) => {
  const button = { dataset, disabled: false, closest() { return button; } };
  return card._onClick({ target: button }).then(() => button);
};

test("a click on nothing in particular does nothing", async () => {
  const card = clickable();
  await card._onClick({ target: { closest: () => null } });
  assert.equal(card.loads, 0);
  assert.equal(card.calls.length, 0);
});

test("choosing a camera clears what was on screen before loading", async () => {
  // Leaving the previous camera's recordings up while the new ones arrive
  // shows one camera's clips under another camera's name.
  const card = clickable();
  card._playing = 1000;
  await press(card, { action: "camera", index: "2" });
  assert.equal(card._index, 2);
  assert.equal(card._recordings, null);
  assert.equal(card._playing, null, "and stops playing the old camera's clip");
  assert.equal(card.loads, 1);
});

test("the day buttons walk backwards and stop at today", async () => {
  const card = clickable();
  await press(card, { action: "day-back" });
  await press(card, { action: "day-back" });
  assert.equal(card._dayOffset, 2);
  await press(card, { action: "day-forward" });
  assert.equal(card._dayOffset, 1);
  await press(card, { action: "day-forward" });
  await press(card, { action: "day-forward" });
  assert.equal(card._dayOffset, 0, "there is no tomorrow to walk into");
});

test("today jumps straight back however far away it is", async () => {
  const card = clickable();
  for (let n = 0; n < 5; n += 1) await press(card, { action: "day-back" });
  await press(card, { action: "day-today" });
  assert.equal(card._dayOffset, 0);
});

test("filtering redraws without asking the hub again", async () => {
  // The recordings are already here. Refetching a list to hide part of it
  // costs a round trip and a visible flicker for nothing.
  const card = clickable();
  await press(card, { action: "filter", code: "6" });
  assert.equal(card._filter, 6);
  assert.equal(card.loads, 0, "no reload to filter what is already loaded");
  await press(card, { action: "filter", code: "" });
  assert.equal(card._filter, null, "and the empty code means everything");
});

test("the table view toggles", async () => {
  const card = clickable();
  await press(card, { action: "view" });
  assert.equal(card._showTable, true);
  await press(card, { action: "view" });
  assert.equal(card._showTable, false);
  assert.equal(card.loads, 0);
});

test("pressing play twice closes the recording again", async () => {
  const card = clickable();
  await press(card, { action: "play", start: "1000" });
  assert.equal(card._playing, "1000");
  await press(card, { action: "play", start: "1000" });
  assert.equal(card._playing, null, "the same button closes it");
});

test("playing a different recording swaps rather than closes", async () => {
  const card = clickable();
  await press(card, { action: "play", start: "1000" });
  await press(card, { action: "play", start: "2000" });
  assert.equal(card._playing, "2000");
});

test("download asks for exactly the clip that was pressed", async () => {
  const card = clickable();
  await press(card, { action: "download", start: "1000", end: "1015" });
  assert.deepEqual(card.calls, [["download_recording", {
    config_entry_id: "abc", camera_index: 0,
    start_time: 1000, end_time: 1015 }]]);
  assert.equal(card.loads, 1, "and the list is refreshed so it shows as held");
});

test("delete closes the recording it was playing", async () => {
  // Otherwise the player stays open on a file that is gone.
  const card = clickable();
  card._playing = "1000";
  await press(card, { action: "delete", start: "1000" });
  assert.deepEqual(card.calls[0], ["delete_recording", {
    config_entry_id: "abc", camera_index: 0, start_time: 1000 }]);
  assert.equal(card._playing, null);
  assert.equal(card.loads, 1);
});

test("deleting something else leaves the player alone", async () => {
  const card = clickable();
  card._playing = "2000";
  await press(card, { action: "delete", start: "1000" });
  assert.equal(card._playing, "2000");
});

test("an action nobody wrote does nothing rather than throwing", async () => {
  const card = clickable();
  const button = await press(card, { action: "teleport" });
  assert.equal(card.calls.length, 0);
  assert.equal(card.loads, 0);
  assert.equal(button.disabled, false, "and the button comes back");
});

// --- nothing from the hub reaches markup unescaped -------------------------

/** The source with every `esc(...)` call cut out, parens matched.
 *
 * An interpolation inside an escaped expression is already safe, and there is
 * no way to tell that from a regex over the raw text.
 */
const withoutEscaped = (source) => {
  let out = "";
  for (let i = 0; i < source.length; i += 1) {
    if (source.startsWith("esc(", i)) {
      let depth = 0;
      let j = i + 3;
      for (; j < source.length; j += 1) {
        if (source[j] === "(") depth += 1;
        else if (source[j] === ")") { depth -= 1; if (!depth) break; }
      }
      i = j;
      continue;
    }
    out += source[i];
  }
  return out;
};

/** Interpolations that are safe by their shape, whatever they hold.
 *
 * A cast, a padded or fixed number, a length, a comparison, a choice between
 * literals, or a call into another render function whose own output is
 * checked by this same test.
 */
const SAFE_SHAPE = /^(Number|String|pad)\(|\.length\b|\.toFixed\(|===|\?|=>|this\._/;

/** Names inspected once and found safe, with why.
 *
 * Default-deny, the same way the diagnostics allow-list works and for the
 * same reason: a rule that lists what is dangerous leaks whatever gets added
 * next. A new interpolation fails this test until somebody has looked at it.
 */
const SAFE_NAMES = new Set([
  // Markup built by another function in this file, already escaped there.
  "bars", "body", "grid", "picker", "rows", "strip", "tiles", "ticks",
  "frame", "mark", "hit", "live", "row", "tooltip",
  // Escaped where it is built, so it is markup by the time it is used. Named
  // apart from the `when` Date objects beside it, so that stays checkable.
  "spoken",
  // Layout arithmetic. Chart geometry, never anything anybody typed.
  "H", "L", "T", "W", "H - 4", "H - 6", "W - R", "barH", "plotH", "y", "d",
  "value", "total", "count", "date.getUTCFullYear()",
  // Stylesheets, which are this file's own constants.
  "BASE_STYLE", "this.constructor.style",
  // A CSS class name chosen by the caller, from a literal in this file.
  "className",
  // The unit word in a relative time -- "minute", "hour" -- from a constant.
  "name",
  // A face id in a browser prompt, which is not markup at all.
  "faceId",
]);

test("no value reaches markup unescaped", async () => {
  // Camera aliases, face names and detection labels are the hub's words or
  // the owner's, and they land in markup built by hand. "We remembered every
  // time" is not a property anybody keeps across 1,350 lines, so it is
  // checked instead -- including for values that took a turn through a local
  // on the way, which is how the first version of this test missed the card
  // title.
  const { readFileSync } = await import("node:fs");
  const source = withoutEscaped(readFileSync(
    "custom_components/tapo_h500/www/tapo-h500-card.js", "utf8"));
  const unsafe = [];
  for (const [, expression] of source.matchAll(/\$\{([^{}]*)\}/g)) {
    const text = expression.trim();
    // Empty is what an entirely escaped interpolation leaves behind.
    if (!text || SAFE_SHAPE.test(text) || SAFE_NAMES.has(text)) continue;
    unsafe.push(text);
  }
  assert.deepEqual([...new Set(unsafe)].sort(), [],
    "wrap in esc() for text, Number() for a number, or add to SAFE_NAMES "
    + "once you have checked what it holds");
});

test("the check would catch one", () => {
  // A guard nobody has seen fail is a guard nobody should trust. Both shapes
  // that have slipped past a version of this test: a property read straight
  // into markup, and a hub value that went through a local first.
  const source = withoutEscaped(
    'const heading = camera.alias;'
    + 'html = `<b title="${item.alias}">${esc(item.name)}</b>'
    + '<h2>${heading}</h2>`;');
  const found = [];
  for (const [, expression] of source.matchAll(/\$\{([^{}]*)\}/g)) {
    const text = expression.trim();
    // Empty is what an entirely escaped interpolation leaves behind.
    if (!text || SAFE_SHAPE.test(text) || SAFE_NAMES.has(text)) continue;
    found.push(text);
  }
  assert.deepEqual(found.sort(), ["heading", "item.alias"],
    "the escaped one is ignored; the raw property and the raw local are not");
});

test("clip times go out as numbers, which is the escape and the assertion", async () => {
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(
    "custom_components/tapo_h500/www/tapo-h500-card.js", "utf8");
  for (const attribute of ["data-start", "data-end"]) {
    for (const [, expression] of
         source.matchAll(new RegExp(`${attribute}="\\$\\{([^}]*)\\}"`, "g"))) {
      assert.match(expression, /^(Number|esc)\(/,
        `${attribute}="\${${expression}}" is neither cast nor escaped`);
    }
  }
});

// --- not polling a hub for a screen nobody is looking at --------------------

/** A card whose loads are counted rather than performed. */
const counting = (Cls, config = {}) => {
  const card = new Cls();
  card.setConfig({ ...config });
  card.loads = 0;
  card._load = () => { card.loads += 1; };
  return card;
};

test("the minute timer is the thing that checks, not just _tick", () => {
  // The check has to be on the path the timer actually takes. Calling _tick
  // by hand proves nothing about what setInterval was handed.
  const card = counting(TapoH500Card);
  card.connectedCallback();
  document.setHidden(true);
  fireTimers();
  assert.equal(card.loads, 0);
  document.setHidden(false);
  card.disconnectedCallback();
});

test("a hidden tab does not ask the hub anything", async () => {
  // A wall tablet with this on a dashboard nobody is looking at asked for a
  // listing every minute, all night, for a screen that was off. Each one is a
  // round trip to a device this project exists because it wedges under load.
  const card = counting(TapoH500Card);
  card.connectedCallback();
  document.setHidden(true);
  card._tick();
  card._tick();
  assert.equal(card.loads, 0);
  document.setHidden(false);
});

test("a visible tab still polls", () => {
  const card = counting(TapoH500Card);
  card.connectedCallback();
  card._tick();
  assert.equal(card.loads, 1);
});

test("coming back catches up rather than waiting for the next minute", () => {
  // A tab hidden for an hour is showing an hour-old list, and refreshing it
  // the moment it comes back is the whole reason for polling.
  const card = counting(TapoH500Card);
  card.connectedCallback();
  document.setHidden(true);
  card._tick();
  assert.equal(card.loads, 0);
  document.setHidden(false);
  assert.equal(card.loads, 1);
});

test("coming back to a tab that missed nothing does not reload", () => {
  const card = counting(TapoH500Card);
  card.connectedCallback();
  document.setHidden(true);
  document.setHidden(false);
  assert.equal(card.loads, 0, "no tick was skipped, so nothing to catch up");
});

test("a removed card stops listening", () => {
  // Otherwise every dashboard edit leaves another listener behind, each one
  // holding a whole card object.
  const card = counting(TapoH500Card);
  card.connectedCallback();
  card.disconnectedCallback();
  document.setHidden(true);
  card._tick();
  document.setHidden(false);
  assert.equal(card.loads, 0);
});

// --- what somebody using a screen reader or a keyboard gets ----------------

test("every recording button says which recording it is for", () => {
  // Thirty rows read aloud were thirty buttons all called "Play". The
  // timestamp beside them on screen carries none of that to anybody who
  // cannot see the row.
  const card = build(TapoH500Card, {});
  // Two on disk, so there are two Delete buttons to tell apart. The shared
  // fixture has one of each, which is the wrong shape for this question.
  card._recordings = CLIPS.map((clip) => ({ ...clip, downloaded: true }));
  card._render();
  const deletes = [...card._card.innerHTML.matchAll(/aria-label="([^"]*)"/g)]
    .map(([, label]) => label)
    .filter((label) => label.startsWith("Delete recording"));
  assert.ok(deletes.length >= 2, "the rows carry labels at all");
  assert.equal(new Set(deletes).size, deletes.length,
    "and one row's Delete does not sound like another's");
});

test("the labels name the action as well as the clip", () => {
  const card = build(TapoH500Card, {});
  card._render();
  const html = card._card.innerHTML;
  assert.match(html, /aria-label="Play recording from [^"]+"/);
  assert.match(html, /aria-label="Delete recording from [^"]+"/);
});

test("a hostile face name cannot escape through a label", () => {
  const card = build(TapoH500FacesCard, {});
  card._sharedNames = { "7": `"><img src=x onerror=alert(1)>` };
  card._recordings = CLIPS.map((clip) => ({ ...clip, face_ids: [7] }));
  const html = card.body();
  assert.ok(!html.includes("<img src=x"), "escaped inside the label too");
});

test("the live region is made once and only its text changes", () => {
  // A live region only announces changes to a region already there. One
  // inserted with its text already in it says nothing at all -- and the
  // card's whole contents are replaced on every real render.
  const card = build(TapoH500Card, {});
  const region = card.shadowRoot.querySelector('[role="status"]');
  assert.equal(region.textContent, "");
  card._hass = { states: {
    "event.front_activity": {
      attributes: { camera_index: 0, detection_types: [2] },
      state: new Date().toISOString() } } };
  card._render();
  assert.equal(region.textContent, "Recording now");
  card._hass = { states: {} };
  card._render();
  assert.equal(region.textContent, "");
});

test("the visible recording dot is not announced twice", () => {
  const card = build(TapoH500Card, {});
  card._hass = { states: {
    "event.front_activity": {
      attributes: { camera_index: 0, detection_types: [2] },
      state: new Date().toISOString() } } };
  card._render();
  const html = card._card.innerHTML;
  assert.match(html, /class="recording-now" aria-hidden="true"/);
  assert.ok(!html.includes('role="status"'),
    "the announcing one is outside the part that gets rebuilt");
});

test("the keyboard stays where it was across a rebuild", () => {
  // innerHTML replaces every node and the browser drops focus to the
  // document, which for somebody tabbing through a list means starting again
  // from the top every time anything changes.
  const card = build(TapoH500Card, {});
  card._render();
  const before = card._card.querySelectorAll();
  const target = before.find((button) => button.dataset.action === "delete");
  card.shadowRoot.activeElement = target;
  card._recordings = [{ ...CLIPS[0], start_time: CLIPS[0].start_time + 9 },
                      ...CLIPS];
  card._render();
  const after = card._card.querySelectorAll();
  const focused = after.filter((button) => button.focused);
  assert.equal(focused.length, 1, "exactly one control has the keyboard");
  assert.equal(focused[0].dataset.action, "delete");
  assert.equal(focused[0].dataset.start, target.dataset.start,
    "the same row's Delete, not a different row's");
});

test("focus is not moved when nothing had it", () => {
  const card = build(TapoH500Card, {});
  card._render();
  card.shadowRoot.activeElement = null;
  card._recordings = [{ ...CLIPS[0], start_time: CLIPS[0].start_time + 9 },
                      ...CLIPS];
  card._render();
  assert.equal(card._card.querySelectorAll().filter((b) => b.focused).length, 0);
});

test("a control that is gone after the rebuild takes nothing with it", () => {
  // Delete the clip you were on and its buttons do not come back. Focusing
  // some other row's Delete would be worse than losing it.
  const card = build(TapoH500Card, {});
  card._render();
  const target = card._card.querySelectorAll()
    .find((button) => button.dataset.action === "delete");
  card.shadowRoot.activeElement = target;
  card._recordings = CLIPS.filter(
    (clip) => String(clip.start_time) !== target.dataset.start);
  card._render();
  assert.equal(card._card.querySelectorAll().filter((b) => b.focused).length, 0);
});

// --- one request at a time, and none dropped -------------------------------

/** Let every pending microtask and timer callback run. */
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** A card whose service call resolves when the test says so. */
const deferred = (Cls, config = {}) => {
  const card = new Cls();
  card.setConfig({ entry_id: "abc", ...config });
  const calls = [];
  card._hass = { connection: { sendMessagePromise: (message) => {
    let settle;
    const promise = new Promise((resolve, reject) => {
      settle = { resolve, reject };
    });
    calls.push({ index: message.service_data.camera_index, ...settle });
    return promise;
  } } };
  const answer = (n, recordings) => calls[n].resolve({ response: {
    recordings, camera: { alias: `Camera ${calls[n].index}` }, days: 1 } });
  return { card, calls, answer };
};

const clipAt = (start) => ({ ...CLIPS[0], start_time: start, end_time: start + 9 });

test("choosing a camera mid-load is not thrown away", async () => {
  // The busy flag used to return, which looked like it was protecting the hub
  // and was dropping work: the load for the camera just chosen never ran, and
  // the card sat on the previous camera's recordings under the new name until
  // the next poll a minute later.
  const { card, calls, answer } = deferred(TapoH500Card);
  card._load();
  await tick();
  assert.equal(calls.length, 1);
  card._index = 1;
  card._load();
  await tick();
  assert.equal(calls.length, 1, "the second waits rather than piling on");
  answer(0, [clipAt(100)]);
  await tick();
  assert.equal(calls.length, 2, "and then it runs");
  assert.equal(calls[1].index, 1, "for the camera actually chosen");
});

test("a day change mid-load supersedes the answer too", async () => {
  const { card, calls, answer } = deferred(TapoH500Card);
  card._load();
  await tick();
  card._dayOffset = 3;
  card._load();
  answer(0, [clipAt(100)]);
  await tick();
  assert.equal(card._camera, undefined,
    "yesterday's answer under today's heading is the same bug");
});

test("a superseded answer is not painted", async () => {
  const { card, calls, answer } = deferred(TapoH500Card);
  card._load();
  await tick();
  card._index = 1;
  card._load();
  answer(0, [clipAt(100)]);
  await tick();
  assert.equal(card._camera, undefined,
    "camera 0's answer belongs to a question nobody is asking");
  answer(1, [clipAt(200), clipAt(300)]);
  await tick();
  assert.equal(card._recordings.length, 2, "camera 1's, not camera 0's");
  assert.equal(card._camera.alias, "Camera 1");
});

test("a superseded failure does not show over an answer still coming", async () => {
  const { card, calls, answer } = deferred(TapoH500Card);
  card._load();
  await tick();
  card._index = 1;
  card._load();
  calls[0].reject(new Error("hub not answering"));
  await tick();
  assert.equal(card._error, null,
    "camera 0's failure must not sit over camera 1's answer still coming");
  answer(1, [clipAt(200)]);
  await tick();
  assert.equal(card._error, null);
  assert.equal(card._recordings.length, 1);
});

test("a failure on the request that is still current does show", async () => {
  const { card, calls } = deferred(TapoH500Card);
  card._load();
  await tick();
  calls[0].reject(new Error("hub not answering"));
  await tick();
  assert.equal(card._error, "hub not answering");
});

test("several changes while one is in flight collapse to a single follow-up", async () => {
  // The point is the hub, which wedges under load. Queueing one is right;
  // queueing four is a burst.
  const { card, calls, answer } = deferred(TapoH500Card);
  card._load();
  await tick();
  card._load();
  card._load();
  card._load();
  await tick();
  assert.equal(calls.length, 1);
  answer(0, []);
  await tick();
  assert.equal(calls.length, 2);
});

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

test("only the faces card is offered a names map", () => {
  assert.ok(editorSchema("tapo-h500-faces-card").some((f) => f.name === "names"),
    "the faces card is the only one that uses names, and needs to edit them");
  for (const type of ["tapo-h500-card", "tapo-h500-hero-card",
                      "tapo-h500-grid-card", "tapo-h500-timeline-card",
                      "tapo-h500-summary-card"]) {
    assert.ok(!editorSchema(type).some((f) => f.name === "names"),
      `${type} does not use names and must not offer it`);
  }
});

test("an emptied names map is removed, not stored as {}", () => {
  // Otherwise the card carries a setting that reads as configured and does
  // nothing.
  assert.deepEqual(mergeConfig({ type: "x", names: { a: 1 } }, { names: {} }),
                   { type: "x" });
  assert.deepEqual(mergeConfig({ type: "x" }, { names: { 123: "Alice" } }),
                   { type: "x", names: { 123: "Alice" } });
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

test("faces are ranked by how often they were seen", () => {
  const items = [
    { start_time: 30, face_ids: [7] },
    { start_time: 20, face_ids: [9, 7] },
    { start_time: 10, face_ids: [7, 9] },
  ];
  const ranked = facesByCount(items);
  assert.deepEqual(ranked.map((f) => [f.id, f.sightings]), [["7", 3], ["9", 2]]);
});

test("a tie keeps a stable order instead of shuffling on redraw", () => {
  // Bars swapping places between refreshes reads as the data changing.
  const items = [{ start_time: 10, face_ids: [22, 3] }];
  const once = facesByCount(items).map((f) => f.id);
  const again = facesByCount([...items]).map((f) => f.id);
  assert.deepEqual(once, again);
  assert.deepEqual(once, ["3", "22"]);   // numeric, so 3 sorts before 22
});

test("the face chart bars are proportional to the counts", () => {
  const card = build(TapoH500FaceSummaryCard, { days: 7 });
  card._recordings = [
    { start_time: 30, face_ids: [1] },
    { start_time: 20, face_ids: [1] },
    { start_time: 10, face_ids: [2] },
  ];
  const widths = [...card.body().matchAll(/class="bar"[^>]*width="([\d.]+)"/g)]
    .map((m) => Number(m[1]));
  assert.equal(widths.length, 2);
  assert.ok(widths[0] > widths[1], "the face seen twice must draw the longer bar");
});

test("the face chart grows a row per face rather than clipping", () => {
  const one = build(TapoH500FaceSummaryCard, {});
  one._recordings = [{ start_time: 10, face_ids: [1] }];
  const many = build(TapoH500FaceSummaryCard, {});
  many._recordings = [{ start_time: 10, face_ids: [1, 2, 3, 4, 5] }];
  const height = (card) => Number(card.body().match(/viewBox="0 0 \d+ (\d+)"/)[1]);
  assert.ok(height(many) > height(one));
});

test("the face chart has a table twin, so no count is hover-only", () => {
  const card = build(TapoH500FaceSummaryCard, {});
  card._recordings = [{ start_time: 10, face_ids: [1] }];
  const html = card.body();
  assert.match(html, /<table>/);
  assert.match(html, /Times seen/);
});

test("an unnamed face shows its id, and a named one its name", () => {
  const card = build(TapoH500FaceSummaryCard, { names: { 1: "Alice" } });
  card._recordings = [{ start_time: 10, face_ids: [1, 2] }];
  const html = card.body();
  assert.match(html, /Alice/);
  assert.match(html, /Face 2/);
});

test("a hostile face name cannot break out of the chart markup", () => {
  const card = build(TapoH500FaceSummaryCard,
                     { names: { 1: '<script>alert(1)</script>' } });
  card._recordings = [{ start_time: 10, face_ids: [1] }];
  assert.ok(!card.body().includes("<script>"));
});

test("no faces is a message, not an empty chart", () => {
  const card = build(TapoH500FaceSummaryCard, {});
  card._recordings = [{ start_time: 10, face_ids: [] }];
  const html = card.body();
  assert.ok(!html.includes("<svg"));
  assert.match(html, /No faces recognised/);
});

test("the face summary is offered a names map and no scroll box", () => {
  const fields = editorSchema("tapo-h500-face-summary-card").map((f) => f.name);
  assert.ok(fields.includes("names"), "cannot name faces on a chart of faces");
  assert.ok(!fields.includes("max_height"), "a chart has no scrolling list");
});

test("the summary table scrolls inside the card instead of over its neighbours", () => {
  // 24 rows and a header are far taller than the chart the card is sized for.
  // Unwrapped it grew past the card, covered whatever sat below, and took the
  // button that switches back off screen with it.
  const card = build(TapoH500SummaryCard, { days: 7 });
  card._showTable = true;
  const html = card.body();
  const table = html.indexOf("<table>");
  const wrap = html.lastIndexOf('<div class="scroll">', table);
  assert.ok(wrap !== -1, "the table is not inside a scroll container");
});

test("the summary keeps its switch-back button reachable in table view", () => {
  const card = build(TapoH500SummaryCard, { days: 7 });
  card._showTable = true;
  const html = card.body();
  // The button must exist AND sit outside the scrolling area, or it scrolls
  // away with the rows it is meant to escape.
  assert.ok(html.includes('data-action="view"'), "no way back to the chart");
  // Take the scroll box's own contents -- from its opening tag to the next
  // closing div, since the table it holds contains no divs -- and require the
  // button to be outside it. Inside, it scrolls away with the rows it exists
  // to escape.
  const open = html.indexOf('<div class="scroll">');
  const inside = html.slice(open, html.indexOf("</div>", open));
  assert.ok(!inside.includes('data-action="view"'),
            "the button is inside the scroll box and will scroll out of reach");
  assert.match(html, /Chart<\/button>/);
});

test("the chart view is not wrapped in a scroll box", () => {
  // It is a fixed-ratio SVG that already fits; a scroll box would only add a
  // stray scrollbar.
  const card = build(TapoH500SummaryCard, { days: 7 });
  card._showTable = false;
  assert.ok(!card.body().includes('<div class="scroll">'));
});

test("the face summary scrolls rather than growing without limit", () => {
  const card = build(TapoH500FaceSummaryCard, {});
  card._recordings = Array.from({ length: 12 }, (_, i) => (
    { start_time: 1000 + i, face_ids: [i] }));
  const html = card.body();
  assert.ok(html.includes('<div class="scroll">'),
            "12 faces of chart plus table would overflow the card");
});

test("the hub's shared names reach a card that was never configured", () => {
  // The whole point of moving names off the cards: name someone once and
  // every card shows it.
  assert.deepEqual(faceNames({ 7: "Alice" }, undefined), { 7: "Alice" });
});

test("a card's own names still win over the shared map", () => {
  // A dashboard may relabel someone locally, and cards written before the
  // shared map existed must behave exactly as they did.
  assert.deepEqual(faceNames({ 7: "Alice" }, { 7: "Mum" }), { 7: "Mum" });
});

test("shared and local names merge rather than replace", () => {
  assert.deepEqual(faceNames({ 7: "Alice" }, { 9: "Bob" }),
                   { 7: "Alice", 9: "Bob" });
});

test("no names anywhere is an empty map, not a crash", () => {
  assert.deepEqual(faceNames(undefined, undefined), {});
});

test("each face offers a way to name it", () => {
  // Naming belongs where the faces are actually looked at, not only in a
  // settings screen two menus away.
  const card = build(TapoH500FacesCard, {});
  card._recordings = [{ start_time: 10, face_ids: [7], downloaded: true }];
  const html = card.body();
  assert.match(html, /data-action="name"/);
  assert.match(html, /data-face="7"/);
  assert.match(html, /Name this face/);
});

test("an already-named face offers a rename, seeded with the name", () => {
  const card = build(TapoH500FacesCard, { names: { 7: "Alice" } });
  card._recordings = [{ start_time: 10, face_ids: [7], downloaded: true }];
  const html = card.body();
  assert.match(html, /Rename/);
  assert.match(html, /data-name="Alice"/);
});

test("a hostile face name cannot break out of the name button", () => {
  const card = build(TapoH500FacesCard,
                     { names: { 7: '"><script>alert(1)</script>' } });
  card._recordings = [{ start_time: 10, face_ids: [7], downloaded: true }];
  assert.ok(!card.body().includes("<script>"));
});

// Drives _onClick directly with a fake button, so the naming branch is
// exercised rather than only its markup.
const clickName = async (card, answer, face = "7", current = "") => {
  const calls = [];
  card._error = null;
  card._call = async (service, data) => { calls.push([service, data]); };
  card._entryId = async () => "entry";
  card._load = async () => {};
  globalThis.window.prompt = () => answer;
  const button = { dataset: { action: "name", face, name: current },
                   disabled: false };
  await card._onClick({ target: { closest: () => button } });
  return calls;
};

test("cancelling the name prompt changes nothing", async () => {
  // window.prompt returns null on Cancel. Treating that as an empty answer
  // would DELETE the name the user was only looking at.
  const card = build(TapoH500FacesCard, {});
  const calls = await clickName(card, null, "7", "Alice");
  assert.deepEqual(calls, []);
  // ...and it must RETURN, not throw. Without the guard, null.trim() raises
  // and the handler's catch swallows it: no service call either way, so
  // asserting on calls alone passes for entirely the wrong reason.
  assert.equal(card._error, null, `cancel raised: ${card._error}`);
});

test("naming a face calls the integration, not the card config", async () => {
  const card = build(TapoH500FacesCard, {});
  const calls = await clickName(card, "Alice");
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "name_face");
  assert.equal(calls[0][1].face_id, "7");
  assert.equal(calls[0][1].name, "Alice");
});

test("an empty answer clears the name deliberately", async () => {
  // Distinct from Cancel: the user emptied the box on purpose.
  const card = build(TapoH500FacesCard, {});
  const calls = await clickName(card, "   ", "7", "Alice");
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].name, "");
});

test("recordings group by who is in them", () => {
  const items = [{ start_time: 30, face_ids: [7] },
                 { start_time: 20, face_ids: [7, 9] }];
  const people = groupByPerson(items, { 7: "Alice" });
  assert.equal(people.length, 2);
  assert.equal(people[0].name, "Alice");
  assert.equal(people[0].items.length, 2);
});

test("clips with nobody recognised are left out", () => {
  // That pile would be most of them, and every other card already shows it.
  assert.deepEqual(groupByPerson([{ start_time: 10, face_ids: [] }]), []);
});

test("named people sort ahead of numbered ones", () => {
  const items = [{ start_time: 90, face_ids: [9] },
                 { start_time: 10, face_ids: [7] }];
  const people = groupByPerson(items, { 7: "Alice" });
  assert.equal(people[0].name, "Alice", "a name is what you scan for");
});

test("the people card renders and offers naming", () => {
  const card = build(TapoH500PeopleCard, {});
  card._recordings = [{ start_time: 10, face_ids: [7], downloaded: true }];
  const html = card.body();
  assert.match(html, /data-action="name"/);
  assert.match(html, /Face 7/);
});

test("nobody recognised is a message, not an empty card", () => {
  const card = build(TapoH500PeopleCard, {});
  card._recordings = [{ start_time: 10, face_ids: [] }];
  assert.match(card.body(), /Nobody has been recognised/);
});

test("a hostile name cannot break out of the people card", () => {
  const card = build(TapoH500PeopleCard, { names: { 7: "<script>x</script>" } });
  card._recordings = [{ start_time: 10, face_ids: [7], downloaded: true }];
  assert.ok(!card.body().includes("<script>"));
});

test("every card type is registered exactly once", () => {
  const types = ["tapo-h500-card", "tapo-h500-hero-card", "tapo-h500-grid-card",
                 "tapo-h500-timeline-card", "tapo-h500-faces-card",
                 "tapo-h500-summary-card", "tapo-h500-face-summary-card",
                 "tapo-h500-people-card"];
  for (const type of types) assert.ok(customElements.get(type), `${type} missing`);
  assert.equal(globalThis.window.customCards.length, types.length);
});

test("a card without its own days asks for none, and one with them asks exactly", () => {
  const now = new Date("2026-08-13T05:46:40Z");
  // Explicitly configured: exact dates, whatever the class default.
  assert.deepEqual(mod.windowFor(true, 3, undefined, now),
                   mod.windowDates(3, now));
  // A class that carries its own default (the summary family) always sends.
  assert.deepEqual(mod.windowFor(false, 7, 7, now), mod.windowDates(7, now));
  // Nothing configured, no class default: the server decides.
  assert.deepEqual(mod.windowFor(false, 1, undefined, now), {});
});

test("byDetection keeps only the recordings carrying that code", () => {
  const rows = [
    { start_time: 1, detection_types: [2, 6, 17] },
    { start_time: 2, detection_types: [2, 8] },
    { start_time: 3 },
  ];
  assert.deepEqual(mod.byDetection(rows, 17).map((r) => r.start_time), [1]);
  assert.deepEqual(mod.byDetection(rows, 8).map((r) => r.start_time), [2]);
  assert.equal(mod.byDetection(rows, null).length, 3);
  // A press that is also a person appears under both chips.
  assert.deepEqual(mod.byDetection(rows, 6).map((r) => r.start_time), [1]);
});

test("the chip row offers the same four filters as the media browser", () => {
  assert.deepEqual(mod.CHIP_FILTERS.map(([label]) => label),
                   ["All", "Presses", "People", "Pets", "Vehicles"]);
});

test("the recordings card filters through its chips", () => {
  const card = build(TapoH500Card);
  card._recordings = [
    { start_time: 1, end_time: 16, duration: 15, event_type: "ring",
      detection_types: [6, 17], downloaded: false },
    { start_time: 2, end_time: 17, duration: 15, event_type: "motion",
      detection_types: [2, 8], downloaded: false },
  ];
  card._render();
  assert.ok(card._card.innerHTML.includes('data-action="filter"'),
            "the chip row is missing");
  card._filter = 17;
  card._render();
  assert.equal(card._card.innerHTML.split('class="row"').length - 1, 1,
               "the vehicle clip should be filtered out");
  card._filter = null;
  card._render();
  assert.equal(card._card.innerHTML.split('class="row"').length - 1, 2);
});

test("an empty filter result explains itself instead of showing nothing", () => {
  const card = build(TapoH500Card);
  card._recordings = [{ start_time: 1, end_time: 16, duration: 15,
                        event_type: "motion", detection_types: [2] }];
  card._filter = 9;
  card._render();
  assert.ok(card._card.innerHTML.includes("Nothing with that in it"));
});

test("dayWindow covers exactly one local day, evenings included", () => {
  const evening = new Date("2026-08-13T05:46:40Z");  // 22:46 local at -07:00
  const today = mod.dayWindow(0, evening);
  // The local day begins on the previous UTC date and ends on this one.
  assert.deepEqual(today, mod.windowDates(1, evening));
  const yesterday = mod.dayWindow(1, evening);
  assert.ok(yesterday.start_date < today.start_date);
  assert.ok(yesterday.end_date <= today.start_date,
            "one day back must not overlap today");
});

test("stepping back N days lands N days back", () => {
  const now = new Date("2026-08-13T20:00:00Z");
  const three = mod.dayWindow(3, now);
  const four = mod.dayWindow(4, now);
  assert.ok(four.start_date < three.start_date);
});

test("the recordings card grows day arrows, and today hides the forward one", () => {
  const card = build(TapoH500Card);
  card._render();
  const html = card._card.innerHTML;
  assert.ok(html.includes('data-action="day-back"'));
  assert.ok(!html.includes('data-action="day-forward"'),
            "there is no tomorrow to page into");
  card._dayOffset = 2;
  card._render();
  const paged = card._card.innerHTML;
  assert.ok(paged.includes('data-action="day-forward"'));
  assert.ok(paged.includes('data-action="day-today"'),
            "a jump home from two days back");
});

test("a day offset asks for that single day", () => {
  const card = build(TapoH500Card);
  card._dayOffset = 2;
  const window = card._window();
  assert.deepEqual(window, mod.dayWindow(2));
});

test("no offset keeps the normal rolling window", () => {
  const card = build(TapoH500Card, { days: 3 });
  const window = card._window();
  assert.deepEqual(window, mod.windowDates(3));
});

test("recordingNow spots a fresh event on this camera and only this one", () => {
  const now = Date.now();
  const states = {
    "event.front_activity": {
      state: new Date(now - 20000).toISOString(),
      attributes: { camera_index: 0, detection_types: [2, 6] },
    },
    "event.side_activity": {
      state: new Date(now - 3600000).toISOString(),
      attributes: { camera_index: 1, detection_types: [2] },
    },
    "event.someone_elses": {
      state: new Date(now - 1000).toISOString(),
      attributes: {},
    },
  };
  assert.equal(mod.recordingNow(states, 0, now), true);
  assert.equal(mod.recordingNow(states, 1, now), false, "an hour is not now");
  assert.equal(mod.recordingNow(states, 2, now), false);
  assert.equal(mod.recordingNow({}, 0, now), false);
  assert.equal(
    mod.recordingNow({ "event.x": { state: "unknown",
      attributes: { camera_index: 0, detection_types: [] } } }, 0, now),
    false, "an unknown state is not a moment");
});

test("a fresh event puts the pulse in the card header", () => {
  const card = build(TapoH500Card);
  card._hass = { states: { "event.front_activity": {
    state: new Date().toISOString(),
    attributes: { camera_index: 0, detection_types: [6] },
  } }, connection: { sendMessagePromise: async () => ({}) },
    callWS: async () => [] };
  card._render();
  assert.ok(card._card.innerHTML.includes("recording-now"));
  card._hass.states["event.front_activity"].state =
    new Date(Date.now() - 600000).toISOString();
  card._render();
  assert.ok(!card._card.innerHTML.includes("recording-now"),
            "ten minutes ago is not now");
});

await Promise.all(pending);
console.log(failures ? `\n${failures} failure(s)` : "\nall card tests passed");
process.exit(failures ? 1 : 0);
