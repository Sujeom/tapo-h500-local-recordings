/**
 * Tapo H500 dashboard cards.
 *
 * Seven ways to look at the same recordings, all fed by the integration's own
 * response services, so none of them needs extra API surface:
 *
 *   custom:tapo-h500-card            list, with download/play/delete
 *   custom:tapo-h500-hero-card       the newest event, large
 *   custom:tapo-h500-grid-card       every event as a tile
 *   custom:tapo-h500-timeline-card   events grouped by hour
 *   custom:tapo-h500-faces-card      who has been recognised, with names
 *   custom:tapo-h500-summary-card    events by hour of day, as a bar chart
 *   custom:tapo-h500-face-summary-card  how often each face was seen
 *
 * Shared options:
 *   days: 1              # how many days back to list
 *   camera_index: 0      # optional; omit to get a picker for every paired camera
 *   entry_id: abc123     # optional; the first H500 entry is used by default
 *   max_height: 400      # list/grid/timeline/faces only; 0 to grow unbounded
 *   names: {id: Alice}   # faces cards only; the hub supplies no names
 *
 * One file on purpose: it is the single resource the integration registers, so
 * splitting the shared engine into a second module would need a second
 * resource and would lose the ?v= cache busting that only the registered URL
 * carries.
 */

const pad = (value) => String(value).padStart(2, "0");
export const utcDay = (date) =>
  `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}`;

// Camera aliases and hub-supplied labels reach innerHTML, so they are escaped.
export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));

/** The UTC dates to ask for so that N *local* days are covered.
 *
 * A local day is not a UTC day. West of UTC, today starts on yesterday's UTC
 * date, so asking for utcDay(now) alone drops everything before local 20:00 --
 * an 8pm doorbell press would simply not be listed. Widening to whichever UTC
 * dates the local window touches over-fetches by a few hours at the far end,
 * which the list shows happily; under-fetching loses events silently.
 */
export const windowDates = (days, now = new Date()) => {
  const first = new Date(now);
  first.setHours(0, 0, 0, 0);
  first.setDate(first.getDate() - (Math.max(1, days) - 1));
  return { start_date: utcDay(first), end_date: utcDay(now) };
};

/** "2 minutes ago". Floors rather than rounds, so it never reads ahead of itself. */
export const ago = (startSeconds, now = Date.now()) => {
  const delta = Math.floor((now - startSeconds * 1000) / 1000);
  for (const [name, size] of [["day", 86400], ["hour", 3600], ["minute", 60]]) {
    const count = Math.floor(delta / size);
    if (count >= 1) return `${count} ${name}${count === 1 ? "" : "s"} ago`;
  }
  return "just now";
};

/** One entry per recognised face, newest sighting first.
 *
 * The hub assigns a stable id per person and refuses to say who they are —
 * there is no face library to look the number up in. So the summary is built
 * from the sightings themselves: the newest clip supplies the picture, and the
 * name comes from the card's own `names` map.
 *
 * `items` must be newest-first, which is the order the cards already hold.
 */
export const groupByFace = (items, names = {}) => {
  const faces = new Map();
  for (const item of items) {
    for (const id of item.face_ids || []) {
      const key = String(id);
      if (!faces.has(key)) {
        faces.set(key, { id: key, name: names[key], newest: item, sightings: 0 });
      }
      faces.get(key).sightings += 1;
    }
  }
  return [...faces.values()];
};

/** The names a card should use: the hub's shared map, overridden per card.
 *
 * The shared map is the one edited through the name_face service and is what
 * every card sees without being configured. A card's own `names:` takes
 * precedence so a dashboard can relabel someone locally, and so cards written
 * before the shared map existed behave exactly as they did.
 */
export const faceNames = (shared, local) => ({ ...(shared || {}), ...(local || {}) });

/** Faces ranked by how often they were seen, most first.
 *
 * Sorted by count, then by name, then by id. Without the last two a redraw
 * could reorder faces that tie, and bars swapping places between refreshes
 * reads as data changing when nothing has.
 */
export const facesByCount = (items, names = {}) =>
  groupByFace(items, names).sort((a, b) =>
    b.sightings - a.sightings
    || String(a.name ?? "").localeCompare(String(b.name ?? ""))
    || String(a.id).localeCompare(String(b.id), undefined, { numeric: true }));

/** Recordings grouped by who is in them, most recently seen first.
 *
 * The other cards group by camera and by time, which answers "what happened"
 * but never "when was Alice last here". Clips with nobody recognised in them
 * are left out rather than lumped into an "unknown" pile: that pile would be
 * most of them, and it is what every other card already shows.
 */
export const groupByPerson = (items, names = {}) => {
  const people = new Map();
  for (const item of items) {
    for (const id of item.face_ids || []) {
      const key = String(id);
      if (!people.has(key)) {
        people.set(key, { id: key, name: names[key], items: [] });
      }
      people.get(key).items.push(item);
    }
  }
  return [...people.values()].sort((a, b) => {
    // Named people first -- they are the ones worth scanning for -- then by
    // how recently each was seen.
    const named = Boolean(b.name) - Boolean(a.name);
    if (named) return named;
    return (b.items[0]?.start_time || 0) - (a.items[0]?.start_time || 0);
  });
};

/** 24 counts, one per hour of the local day. */
export const eventsByHour = (items) => {
  const hours = new Array(24).fill(0);
  for (const item of items) {
    const hour = new Date(item.start_time * 1000).getHours();
    if (hour >= 0 && hour < 24) hours[hour] += 1;
  }
  return hours;
};

/** A round number at or above the peak, for the top gridline.
 *
 * Bars are read by length, so the scale starts at zero and the top is a number
 * a person can divide by eye — 5, 10, 20 — never the raw maximum, which would
 * put the tallest bar flush against the ceiling.
 */
export const niceMax = (peak) => {
  if (!(peak > 0)) return 1;
  for (const step of [1, 2, 5, 10, 20, 25, 50, 100]) {
    if (peak <= step) return step;
  }
  return Math.ceil(peak / 100) * 100;
};

/** Consecutive runs sharing a clock hour, in the order given. */
export const groupByHour = (items) => {
  const groups = [];
  let current = null;
  for (const item of items) {
    const when = new Date(item.start_time * 1000);
    const key = [when.getFullYear(), when.getMonth(), when.getDate(),
                 when.getHours()].join("-");
    if (!current || current.key !== key) {
      current = { key, when, items: [] };
      groups.push(current);
    }
    current.items.push(item);
  }
  return groups;
};

// --- visual editor ---------------------------------------------------------

const FIELD = {
  days: { name: "days", selector: { number: { min: 1, max: 30, mode: "box" } } },
  camera_index: { name: "camera_index",
    selector: { number: { min: 0, max: 15, mode: "box" } } },
  max_height: { name: "max_height",
    selector: { number: { min: 0, max: 2000, step: 20, mode: "box" } } },
  entry_id: { name: "entry_id", selector: { text: {} } },
  // A map of face id to name. There is no key/value selector, and the object
  // one gives a small YAML editor, which is the right shape for a dictionary
  // whose keys are 12-digit numbers the hub invented.
  names: { name: "names", selector: { object: {} } },
};

const LABELS = {
  days: "Days to show",
  camera_index: "Camera (leave empty for a picker)",
  max_height: "Scroll after (pixels, 0 for none)",
  entry_id: "Config entry (leave empty for the first hub)",
  names: "Face names, as id: name (the card shows the id of anyone unnamed)",
};

/** Which fields each card actually has. A card without a scrolling list has no
 *  use for max_height, and offering it would invite a setting that does
 *  nothing. */
export const editorSchema = (type) => {
  const scrolls = !["tapo-h500-hero-card", "tapo-h500-summary-card",
                    "tapo-h500-face-summary-card"].includes(type);
  const faces = ["tapo-h500-faces-card", "tapo-h500-face-summary-card",
                 "tapo-h500-people-card"].includes(type);
  return [FIELD.days, FIELD.camera_index,
          ...(scrolls ? [FIELD.max_height] : []),
          ...(faces ? [FIELD.names] : []), FIELD.entry_id];
};

/** The config to store after an edit.
 *
 * Merged over the existing config rather than replacing it, because the form
 * does not know about `names` or `grid_options` and replacing would silently
 * delete them. Cleared fields are removed so the card falls back to its own
 * default instead of storing an empty string.
 */
export const mergeConfig = (current, incoming) => {
  const merged = { ...current, ...incoming };
  for (const [key, value] of Object.entries(merged)) {
    const empty = value === undefined || value === "" || value === null
      // An emptied names map should remove the key, not store {}, or the card
      // would carry a setting that reads as configured and does nothing.
      || (value && typeof value === "object" && !Array.isArray(value)
          && Object.keys(value).length === 0);
    if (empty) delete merged[key];
  }
  return merged;
};

class TapoH500CardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  _update() {
    if (!this._config || !this._hass) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (field) => LABELS[field.name] || field.name;
      this._form.addEventListener("value-changed", (event) => {
        this.dispatchEvent(new CustomEvent("config-changed", {
          detail: { config: mergeConfig(this._config, event.detail.value) },
          bubbles: true, composed: true,
        }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = editorSchema(this._config.type);
    this._form.data = this._config;
  }
}

if (!customElements.get("tapo-h500-card-editor")) {
  customElements.define("tapo-h500-card-editor", TapoH500CardEditor);
}

const BASE_STYLE = `
  /* The sections view gives a resized card a height; filling it is what makes
     dragging the handle do anything. In the masonry view nothing above sets a
     height, so these resolve to auto and the card is content-sized as before. */
  :host { height: 100%; }
  ha-card {
    padding: 12px 16px 16px; height: 100%; box-sizing: border-box;
    display: flex; flex-direction: column;
  }
  /* min-height:0 or a flex child refuses to shrink below its content and the
     card overflows instead of scrolling. */
  .list, .scroll { flex: 1 1 auto; min-height: 0; }
  .head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .head h2 { flex: 1; margin: 0; font-size: 1.1rem; font-weight: 500; }
  .cameras { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 4px; }
  .cameras button[aria-pressed="true"] {
    background: var(--primary-color); color: var(--text-primary-color);
  }
  .muted { color: var(--secondary-text-color); font-size: 0.85rem; }
  button {
    background: none; border: none; cursor: pointer; padding: 4px 8px;
    border-radius: 4px; color: var(--primary-color); font: inherit;
    font-size: 0.85rem;
  }
  button:hover { background: var(--secondary-background-color); }
  button[disabled] { color: var(--disabled-text-color); cursor: default; }
  button.danger { color: var(--error-color, #db4437); }
  .badge {
    display: inline-block; padding: 0 6px; border-radius: 8px;
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em;
    background: var(--secondary-background-color);
  }
  .badge.ring { background: var(--primary-color); color: var(--text-primary-color); }
  video { width: 100%; margin: 8px 0; border-radius: 4px; background: #000; }
  .error { color: var(--error-color, #db4437); padding: 8px 0; }
  .scroll { overflow-y: auto; overscroll-behavior: contain; }
`;

/**
 * Everything the cards share: config, polling, the service calls, the
 * camera picker and the click routing. A subclass supplies only `body()`.
 */
class H500Base extends HTMLElement {
  static defaults = {};
  static style = "";
  // Sections-view sizing: what the resize handles allow, and where they start.
  static grid = { rows: 6, min_rows: 3, columns: 12, min_columns: 6 };

  /** Without this Home Assistant reports "no visual editor available". */
  static getConfigElement() {
    return document.createElement("tapo-h500-card-editor");
  }

  /** The config a card starts with when picked from the card list. */
  static getStubConfig() {
    return { ...this.defaults };
  }

  /** Lets the sections view offer resize handles and remember the result. */
  getGridOptions() {
    return { ...this.constructor.grid, ...(this._config.grid_options || {}) };
  }

  setConfig(config) {
    this._config = { days: 1, max_height: 400, ...this.constructor.defaults,
                     ...config };
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML =
        `<style>${BASE_STYLE}${this.constructor.style}</style><ha-card></ha-card>`;
      this._card = this.shadowRoot.querySelector("ha-card");
      this._card.addEventListener("click", (event) => this._onClick(event));
    }
    this._recordings = null;
    this._sharedNames = {};
    this._cameras = null;
    this._error = null;
    // A pinned camera_index keeps the single-camera behaviour; without one the
    // card offers every paired camera.
    // A default cap must not fight a height the user dragged; an explicit one
    // is the user asking for it and still wins.
    this._cappedByUser = config.max_height !== undefined;
    this._pinned = config.camera_index !== undefined;
    this._index = config.camera_index ?? 0;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._started) {
      this._started = true;
      this._load();
    }
  }

  connectedCallback() {
    this._timer = setInterval(() => this._load(), 60000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  async _call(service, data) {
    const result = await this._hass.connection.sendMessagePromise({
      type: "call_service",
      domain: "tapo_h500",
      service,
      service_data: data,
      return_response: true,
    });
    return result.response;
  }

  async _entryId() {
    if (this._config.entry_id) return this._config.entry_id;
    if (this._resolvedEntry) return this._resolvedEntry;
    const entries = await this._hass.callWS({
      type: "config_entries/get",
      domain: "tapo_h500",
    });
    if (!entries || !entries.length) throw new Error("No Tapo H500 entry found");
    this._resolvedEntry = entries[0].entry_id;
    return this._resolvedEntry;
  }

  async _load() {
    if (!this._hass || this._busy) return;
    this._busy = true;
    try {
      const response = await this._call("list_recordings", {
        config_entry_id: await this._entryId(),
        camera_index: this._index,
        ...windowDates(this._config.days),
      });
      this._camera = response.camera;
      this._cameras = response.cameras || null;
      // The hub's shared name map, set once via the name_face service. A
      // card's own `names:` still wins, so an existing card keeps working.
      this._sharedNames = response.face_names || {};
      this._recordings = response.recordings.slice().reverse();
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _onClick(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const { action, start, end } = button.dataset;
    button.disabled = true;
    try {
      if (action === "refresh") {
        await this._load();
      } else if (action === "camera") {
        this._index = Number(button.dataset.index);
        this._recordings = null;
        this._playing = null;
        this._render();
        await this._load();
      } else if (action === "view") {
        // The chart's table twin. Every value a tooltip shows is reachable
        // here too, which a tooltip alone cannot promise.
        this._showTable = !this._showTable;
        this._render();
      } else if (action === "play") {
        this._playing = this._playing === start ? null : start;
        this._render();
      } else if (action === "download") {
        await this._call("download_recording", {
          config_entry_id: await this._entryId(),
          camera_index: this._index,
          start_time: Number(start),
          end_time: Number(end),
        });
        await this._load();
      } else if (action === "name") {
        // Naming happens here rather than only in the options screen because
        // this is where the faces are actually looked at. The prompt is
        // seeded with the current name so it edits rather than retypes, and
        // an empty answer clears the name -- the same rule the options form
        // and the service both use.
        const faceId = button.dataset.face;
        const current = button.dataset.name || "";
        const answer = window.prompt(
          `Name for face ${faceId}\n\nLeave empty to remove the name.`,
          current);
        // null is Cancel, which must not be read as "clear it".
        if (answer === null) return;
        await this._call("name_face", {
          config_entry_id: await this._entryId(),
          face_id: String(faceId),
          name: answer.trim(),
        });
        // The name lives on the config entry, so Home Assistant reloads the
        // integration; reloading the list is what picks the new name up.
        await this._load();
      } else if (action === "delete") {
        await this._call("delete_recording", {
          config_entry_id: await this._entryId(),
          camera_index: this._index,
          start_time: Number(start),
        });
        if (this._playing === start) this._playing = null;
        await this._load();
      }
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    }
  }

  /** What the hub says triggered this recording.
   *
   * `detection` comes from the hub's detection log and names what fired --
   * "motion", "motion + face", or an unnamed code as "type 22". `event_type`
   * is only the ring/motion class, and every clip's own video_type is "2", so
   * the detection is the specific one. The class stays event_type so a ring
   * keeps its highlight.
   */
  _badge(item) {
    const label = item.detection || item.event_type;
    // The title carries the full label, because a narrow card ellipsizes the
    // badge and the hidden half is exactly the part worth reading.
    return `<span class="badge ${esc(item.event_type)}"
      title="${esc(label)}">${esc(label)}</span>`;
  }

  /** The thumbnail, or a placeholder that keeps the layout from jumping. */
  _image(item, className = "") {
    return item.thumbnail
      ? `<img class="${className}" src="${esc(item.thumbnail)}" alt=""
           loading="lazy" onerror="this.removeAttribute('src')">`
      : `<div class="blank ${className}"></div>`;
  }

  _isPlaying(item) {
    return this._playing === String(item.start_time);
  }

  /** The inline player for one item, or nothing.
   *
   * A clip still only on the hub has no url. The buttons already refuse to
   * play one, but selection outlives a reload -- play a clip, delete it, and
   * the id is still selected -- so the guard belongs here rather than in each
   * card, where <video src="undefined"> would render as a broken player.
   */
  _player(item) {
    return this._isPlaying(item) && item.url
      ? `<video controls autoplay src="${esc(item.url)}"></video>` : "";
  }

  /** Play when the clip is on disk, otherwise offer to fetch it. */
  _actions(item) {
    return item.downloaded
      ? `<button data-action="play" data-start="${item.start_time}">
           ${this._isPlaying(item) ? "Hide" : "Play"}
         </button>
         <button class="danger" data-action="delete" data-start="${item.start_time}">
           Delete
         </button>`
      : `<button data-action="download" data-start="${item.start_time}"
           data-end="${item.end_time}">Download</button>`;
  }

  _maxHeight() {
    // A resized card already has a height of its own. Applying the default cap
    // on top would strand blank space below a short list.
    const resized = Boolean(this._config.grid_options?.rows);
    if (resized && !this._cappedByUser) return "";
    return this._config.max_height > 0
      ? ` style="max-height:${Number(this._config.max_height)}px"` : "";
  }

  body() {
    throw new Error("H500Base subclasses must implement body()");
  }

  _render() {
    if (!this._card) return;
    const title = (this._camera && this._camera.alias) || "Tapo H500";
    const body = this._error
      ? `<div class="error">${esc(this._error)}</div>`
      : this._recordings === null
        ? `<div class="muted">Loading recordings...</div>`
        : this._recordings.length === 0
          ? `<div class="muted">No recordings in this period.</div>`
          : this.body();
    const picker = (!this._pinned && this._cameras && this._cameras.length > 1)
      ? `<div class="cameras">${this._cameras.map((cam) => `
          <button data-action="camera" data-index="${Number(cam.index)}"
            aria-pressed="${cam.index === this._index}">${esc(cam.alias)}</button>
        `).join("")}</div>`
      : "";
    this._card.innerHTML = `
      <div class="head">
        <h2>${esc(title)}</h2>
        <button data-action="refresh">Refresh</button>
      </div>
      ${picker}
      ${body}`;
  }
}

/** The original: one row per clip, with the management buttons. */
class TapoH500Card extends H500Base {
  // Rows are what a list wants; a narrow one still reads, so columns can go low.
  static grid = { rows: 6, min_rows: 2, columns: 12, min_columns: 4 };
  static style = `
    .list { overflow-y: auto; overscroll-behavior: contain; }
    .row {
      display: flex; align-items: center; gap: 12px; padding: 8px 0;
      border-top: 1px solid var(--divider-color);
    }
    .row img, .row .blank {
      width: 96px; height: 54px; object-fit: cover; border-radius: 4px;
      background: var(--secondary-background-color); flex: none;
    }
    .info { flex: 1; min-width: 0; }
    .when { font-size: 0.95rem; }
  `;

  getCardSize() {
    const rows = this._recordings ? this._recordings.length : 0;
    // Roughly 50px a row. Capped at max_height so a busy camera does not claim
    // a whole column of the dashboard.
    const height = this._config.max_height > 0
      ? Math.min(rows * 50, this._config.max_height) : rows * 50;
    return 3 + Math.ceil(height / 50);
  }

  _row(item) {
    const when = new Date(item.start_time * 1000);
    const label = this._config.days > 1
      ? when.toLocaleString() : when.toLocaleTimeString();
    return `
      <div class="row">
        ${this._image(item)}
        <div class="info">
          <div class="when">${esc(label)}</div>
          <div class="muted">
            ${this._badge(item)}
            ${Number(item.duration)}s
          </div>
        </div>
        ${this._actions(item)}
      </div>
      ${this._player(item)}`;
  }

  body() {
    return `<div class="list"${this._maxHeight()}>${
      this._recordings.map((item) => this._row(item)).join("")}</div>`;
  }
}

/** The newest event, large enough to read from across the room. */
class TapoH500HeroCard extends H500Base {
  // One 16:9 frame plus a line of meta. Squashing it below 4 rows crops the
  // picture, which is the whole point of this card.
  static grid = { rows: 6, min_rows: 4, columns: 12, min_columns: 4 };
  static defaults = { max_height: 0 };
  static style = `
    .frame {
      display: block; position: relative; width: 100%; padding: 0;
      border-radius: 8px; overflow: hidden; line-height: 0;
      background: var(--secondary-background-color);
    }
    .frame:hover { background: var(--secondary-background-color); }
    .frame img, .frame .blank {
      width: 100%; aspect-ratio: 16 / 9; object-fit: cover; display: block;
    }
    .frame .play {
      position: absolute; inset: 0; display: flex; align-items: center;
      justify-content: center; font-size: 3rem; line-height: 1;
      color: #fff; text-shadow: 0 1px 6px rgba(0, 0, 0, 0.6);
      opacity: 0.85; pointer-events: none;
    }
    .frame .badge {
      position: absolute; right: 8px; bottom: 8px; pointer-events: none;
    }
    .meta {
      display: flex; align-items: center; gap: 8px; margin-top: 8px;
    }
    .meta .when { flex: 1; font-size: 1rem; }
  `;

  getCardSize() {
    return 6;
  }

  body() {
    const item = this._recordings[0];
    // Playing replaces the still rather than sitting under it: this card is
    // meant to be one thing, not a stack.
    const frame = this._isPlaying(item)
      ? this._player(item)
      : `<button class="frame" data-action="play" data-start="${item.start_time}"
           ${item.downloaded ? "" : "disabled"}>
           ${this._image(item)}
           ${item.downloaded ? `<span class="play">&#9654;</span>` : ""}
           ${this._badge(item)}
         </button>`;
    return `
      ${frame}
      <div class="meta">
        <span class="when">${esc(ago(item.start_time))}</span>
        <span class="muted">${Number(item.duration)}s</span>
        ${this._actions(item)}
      </div>`;
  }
}

/** Every event as a tile, for scanning a busy day quickly. */
class TapoH500GridCard extends H500Base {
  // Tiles reflow, so this one takes any shape; wide and short still works.
  static grid = { rows: 6, min_rows: 2, columns: 12, min_columns: 3 };
  static style = `
    .grid {
      display: grid; gap: 8px;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    }
    .tile {
      position: relative; padding: 0; border-radius: 6px; overflow: hidden;
      line-height: 0; background: var(--secondary-background-color);
    }
    .tile img, .tile .blank {
      width: 100%; aspect-ratio: 16 / 9; object-fit: cover; display: block;
    }
    .tile .when {
      position: absolute; left: 4px; bottom: 4px; font-size: 0.75rem;
      color: #fff; text-shadow: 0 1px 4px rgba(0, 0, 0, 0.8); line-height: 1.2;
    }
    .tile .badge { position: absolute; right: 4px; top: 4px; }
    .tile[aria-pressed="true"] { outline: 2px solid var(--primary-color); }
  `;

  getCardSize() {
    const tiles = this._recordings ? this._recordings.length : 0;
    return 3 + Math.ceil(tiles / 3);
  }

  _tile(item) {
    const when = new Date(item.start_time * 1000);
    return `
      <button class="tile" data-action="play" data-start="${item.start_time}"
        aria-pressed="${this._isPlaying(item)}" ${item.downloaded ? "" : "disabled"}>
        ${this._image(item)}
        <span class="when">${esc(when.toLocaleTimeString())}</span>
        ${this._badge(item)}
      </button>`;
  }

  body() {
    // One player under the grid, so choosing another tile does not reflow the
    // tiles around it.
    const playing = this._recordings.find((item) => this._isPlaying(item));
    return `
      <div class="grid scroll"${this._maxHeight()}>${
        this._recordings.map((item) => this._tile(item)).join("")}</div>
      ${playing ? this._player(playing) : ""}`;
  }
}

/** Events under hour headings, so gaps in the day are visible. */
class TapoH500TimelineCard extends H500Base {
  // Hour headings plus rows: height is what this one trades on.
  static grid = { rows: 8, min_rows: 3, columns: 12, min_columns: 4 };
  static style = `
    .hour {
      display: flex; align-items: center; gap: 8px; margin: 12px 0 4px;
      color: var(--secondary-text-color); font-size: 0.8rem;
      text-transform: uppercase; letter-spacing: 0.06em;
    }
    .hour::after {
      content: ""; flex: 1; height: 1px; background: var(--divider-color);
    }
    .event {
      display: flex; align-items: center; gap: 10px; padding: 4px 0 4px 6px;
      border-left: 2px solid var(--divider-color); margin-left: 4px;
      /* Narrow cards drop the tail of the row onto a second line rather than
         crushing everything on one. */
      flex-wrap: wrap;
    }
    .event .dot {
      width: 8px; height: 8px; border-radius: 50%; flex: none;
      background: var(--secondary-text-color);
    }
    .event.ring .dot { background: var(--primary-color); }
    .event img, .event .blank {
      width: 64px; height: 36px; object-fit: cover; border-radius: 3px;
      background: var(--secondary-background-color); flex: none;
    }
    /* The time is the point of a timeline, so it never shrinks. It used to be
       flex:1 with min-width:0, which made it the one thing that absorbed every
       narrow layout and clipped the seconds off. tabular-nums lines the times
       up down the column, which is most of why a timeline is scannable. */
    .event .at {
      flex: none; white-space: nowrap; font-size: 0.9rem;
      font-variant-numeric: tabular-nums;
    }
    /* The detection label is the long and variable part -- "motion + person +
       vehicle + type 22" -- so it is what gives way instead. */
    .event .badge {
      flex: 1 1 auto; min-width: 0; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap;
    }
    .event .muted { flex: none; }
  `;

  getCardSize() {
    const rows = this._recordings ? this._recordings.length : 0;
    return 3 + Math.ceil(rows * 40 / 50);
  }

  _event(item) {
    const when = new Date(item.start_time * 1000);
    return `
      <div class="event ${esc(item.event_type)}">
        <span class="dot"></span>
        <span class="at">${esc(when.toLocaleTimeString())}</span>
        ${this._image(item)}
        ${this._badge(item)}
        <span class="muted">${Number(item.duration)}s</span>
        ${this._actions(item)}
      </div>
      ${this._player(item)}`;
  }

  body() {
    const groups = groupByHour(this._recordings);
    return `<div class="scroll"${this._maxHeight()}>${groups.map((group) => `
      <div class="hour">${esc(group.when.toLocaleDateString([], {
        month: "short", day: "numeric",
      }))} ${pad(group.when.getHours())}:00</div>
      ${group.items.map((item) => this._event(item)).join("")}
    `).join("")}</div>`;
  }
}

/** When things happen: one bar per hour of the local day.
 *
 * One series, so one colour for every bar — shading them by height would
 * double-encode the length the bar already shows. The scale starts at zero and
 * tops out at a round number, only the busiest hour is labelled, and the whole
 * thing has a table twin because a value that exists only in a tooltip is a
 * value some readers cannot reach.
 */
class TapoH500SummaryCard extends H500Base {
  // A chart needs vertical room to be readable and horizontal room for 24
  // hourly bars, so this is the one card with a real floor on both axes.
  static grid = { rows: 5, min_rows: 4, columns: 12, min_columns: 6 };
  static defaults = { days: 7, max_height: 0 };
  static style = `
    .chart { width: 100%; height: auto; display: block; }
    .grid { stroke: var(--divider-color); stroke-width: 1; }
    .bar { fill: var(--primary-color); }
    .hit { fill: transparent; }
    .hit:hover ~ .bar, .bar:hover { fill: var(--primary-color); opacity: 0.75; }
    .tick { fill: var(--secondary-text-color); font-size: 9px; }
    .peak { fill: var(--primary-text-color); font-size: 10px; font-weight: 500; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th, td { text-align: left; padding: 2px 6px 2px 0;
             border-bottom: 1px solid var(--divider-color); }
    td.n { text-align: right; font-variant-numeric: tabular-nums; }
    .total { margin-bottom: 6px; }
  `;

  getCardSize() {
    return 5;
  }

  _table(hours) {
    const rows = hours.map((count, hour) => `
      <tr><td>${pad(hour)}:00</td><td class="n">${Number(count)}</td></tr>`).join("");
    return `<table><thead><tr><th>Hour</th><th class="n">Events</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  _chart(hours) {
    // Geometry. The box includes the x-axis band, so the labels are never
    // clipped by the container and the card needs no nested scrollbar.
    const W = 336, H = 168, L = 26, R = 6, T = 12, B = 20;
    const plotW = W - L - R, plotH = H - T - B;
    const top = niceMax(Math.max(...hours));
    const slot = plotW / 24, gap = 2;
    const width = Math.max(1, slot - gap);
    const y = (value) => T + plotH - (value / top) * plotH;

    const grid = [0, top / 2, top].map((value) => `
      <line class="grid" x1="${L}" x2="${W - R}" y1="${y(value).toFixed(1)}"
            y2="${y(value).toFixed(1)}"/>
      <text class="tick" x="0" y="${(y(value) + 3).toFixed(1)}">${
        Number.isInteger(value) ? value : value.toFixed(1)}</text>`).join("");

    const peak = Math.max(...hours);
    // The FIRST hour reaching the peak, not every hour tied with it. On a quiet
    // camera most hours hold one event, so labelling every tie would put a
    // number on nearly every bar.
    const busiest = hours.indexOf(peak);
    const bars = hours.map((count, hour) => {
      const x = L + hour * slot + gap / 2;
      const label = `${pad(hour)}:00 — ${count} event${count === 1 ? "" : "s"}`;
      // A full-column transparent hit area, so hovering does not require
      // landing on a one-event bar three pixels tall.
      const hit = `<rect class="hit" x="${(L + hour * slot).toFixed(1)}" y="${T}"
        width="${slot.toFixed(1)}" height="${plotH}"><title>${esc(label)}</title></rect>`;
      if (!count) return hit;
      const barTop = y(count), height = T + plotH - barTop;
      const r = Math.min(4, width / 2, height);
      // Rounded at the value end, square on the baseline.
      const d = `M${x.toFixed(1)},${(T + plotH).toFixed(1)}`
        + `L${x.toFixed(1)},${(barTop + r).toFixed(1)}`
        + `Q${x.toFixed(1)},${barTop.toFixed(1)} ${(x + r).toFixed(1)},${barTop.toFixed(1)}`
        + `L${(x + width - r).toFixed(1)},${barTop.toFixed(1)}`
        + `Q${(x + width).toFixed(1)},${barTop.toFixed(1)} ${(x + width).toFixed(1)},${(barTop + r).toFixed(1)}`
        + `L${(x + width).toFixed(1)},${(T + plotH).toFixed(1)}Z`;
      // Only the busiest hour is labelled; the axis and tooltip carry the rest.
      const mark = hour === busiest && peak > 0
        ? `<text class="peak" x="${(x + width / 2).toFixed(1)}"
             y="${(barTop - 3).toFixed(1)}" text-anchor="middle">${count}</text>` : "";
      return `${hit}<path class="bar" d="${d}"><title>${esc(label)}</title></path>${mark}`;
    }).join("");

    // Every third hour, or 24 labels collide on a phone.
    const ticks = hours.map((_, hour) => hour % 3 === 0
      ? `<text class="tick" x="${(L + hour * slot + slot / 2).toFixed(1)}"
           y="${H - 6}" text-anchor="middle">${pad(hour)}</text>` : "").join("");

    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Events by hour of day">${grid}${bars}${ticks}</svg>`;
  }

  body() {
    const hours = eventsByHour(this._recordings);
    const total = hours.reduce((sum, n) => sum + n, 0);
    const peak = Math.max(...hours);
    const busiest = hours.indexOf(peak);
    return `
      <div class="muted total">${total} event${total === 1 ? "" : "s"} over
        ${Number(this._config.days)} day${this._config.days === 1 ? "" : "s"}${
        peak > 0 ? `, busiest around ${pad(busiest)}:00` : ""}</div>
      ${this._showTable
        // 24 rows and a header are far taller than the chart this card is
        // sized for. Unwrapped, the table pushed itself past the card's
        // height -- over whatever sat below it -- and carried the button
        // that switches back out of view with it, so the view could not be
        // undone. .scroll makes it the flex child that gives way, which
        // keeps the button on screen and the overflow inside the card.
        ? `<div class="scroll">${this._table(hours)}</div>`
        : this._chart(hours)}
      <button data-action="view">${this._showTable ? "Chart" : "Table"}</button>`;
  }
}

/** Who has been seen, the local answer to the app's recognised-faces summary.
 *
 * The hub recognises but will not identify: it assigns a stable id per person
 * and holds no name or photo, because those live in TP-Link's cloud. This
 * supplies the missing half locally — the picture from the person's newest
 * clip, and the name from the card's own config.
 */
class TapoH500FacesCard extends H500Base {
  // Square tiles that reflow; a couple of rows is enough for a quiet door.
  static grid = { rows: 5, min_rows: 2, columns: 12, min_columns: 3 };
  static defaults = { days: 7 };
  static style = `
    .faces { display: grid; gap: 8px;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
    .facewrap { display: flex; flex-direction: column; }
    .facewrap .name { align-self: flex-start; }
    .face { position: relative; padding: 0; border-radius: 6px; overflow: hidden;
      line-height: 0; background: var(--secondary-background-color);
      text-align: left; }
    .face img, .face .blank {
      width: 100%; aspect-ratio: 1 / 1; object-fit: cover; display: block; }
    .face .who { display: block; padding: 6px 8px 2px; line-height: 1.2;
      font-size: 0.95rem; color: var(--primary-text-color); }
    .face .seen { display: block; padding: 0 8px 8px; line-height: 1.3; }
    .unnamed { font-family: monospace; font-size: 0.8rem; }
    .hint { margin-top: 10px; }
  `;

  getCardSize() {
    return 3 + Math.ceil((this._faces || []).length / 2);
  }

  body() {
    // Only clips the hub attached a face to; motion-only ones are not people.
    this._faces = groupByFace(this._recordings,
      faceNames(this._sharedNames, this._config.names));
    if (!this._faces.length) {
      return `<div class="muted">No faces recognised in this period. The hub
        only reports one when its own face detection fires.</div>`;
    }
    const tiles = this._faces.map((face) => {
      const named = face.name !== undefined && face.name !== null;
      return `
        <div class="facewrap">
        <button class="face" data-action="play"
          data-start="${face.newest.start_time}"
          ${face.newest.downloaded ? "" : "disabled"}>
          ${this._image(face.newest)}
          <span class="who${named ? "" : " unnamed"}">${
            esc(named ? face.name : `Face ${face.id}`)}</span>
          <span class="seen muted">${esc(ago(face.newest.start_time))}
            · ${Number(face.sightings)} seen</span>
        </button>
        <button class="name" data-action="name" data-face="${esc(face.id)}"
          data-name="${esc(named ? face.name : "")}"
          >${named ? "Rename" : "Name this face"}</button>
        </div>`;
    }).join("");
    const playing = this._recordings.find((item) => this._isPlaying(item));
    // Without the map every tile reads as a number, so say where names come
    // from rather than leaving it a mystery.
    const unnamed = this._faces.filter((face) => face.name === undefined);
    const hint = unnamed.length
      ? `<div class="muted hint">Name ${unnamed.length === 1 ? "it" : "them"}
         by adding <code>names:</code> to this card:
         <code>${esc(unnamed[0].id)}: Alice</code></div>`
      : "";
    return `
      <div class="faces scroll"${this._maxHeight()}>${tiles}</div>
      ${playing ? this._player(playing) : ""}
      ${hint}`;
  }
}

class TapoH500FaceSummaryCard extends H500Base {
  // Height depends on how many faces there are, so the floor is low and the
  // card grows with its own content rather than reserving a chart-sized block.
  static grid = { rows: 4, min_rows: 2, columns: 12, min_columns: 6 };
  static defaults = { days: 7, max_height: 0 };
  static style = `
    .chart { width: 100%; height: auto; display: block; }
    .grid { stroke: var(--divider-color); stroke-width: 1; }
    .bar { fill: var(--primary-color); }
    .hit { fill: transparent; }
    .hit:hover ~ .bar, .bar:hover { fill: var(--primary-color); opacity: 0.75; }
    .who { fill: var(--primary-text-color); font-size: 10px; }
    .who.unnamed { font-family: monospace; font-size: 9px;
                   fill: var(--secondary-text-color); }
    .n { fill: var(--primary-text-color); font-size: 10px; font-weight: 500; }
    .tick { fill: var(--secondary-text-color); font-size: 9px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th, td { text-align: left; padding: 2px 6px 2px 0;
             border-bottom: 1px solid var(--divider-color); }
    td.n { text-align: right; font-variant-numeric: tabular-nums; }
    .total { margin-bottom: 6px; }
    .hint { margin-top: 10px; }
  `;

  getCardSize() {
    return 2 + Math.ceil((this._faces || []).length / 2);
  }

  _label(face) {
    const named = face.name !== undefined && face.name !== null;
    return { named, text: named ? String(face.name) : `Face ${face.id}` };
  }

  _table(faces) {
    const rows = faces.map((face) => `
      <tr><td>${esc(this._label(face).text)}</td>
          <td class="n">${Number(face.sightings)}</td></tr>`).join("");
    return `<table><thead><tr><th>Face</th><th class="n">Times seen</th></tr>
      </thead><tbody>${rows}</tbody></table>`;
  }

  _chart(faces) {
    // Horizontal bars, because the categories are names of arbitrary length.
    // Vertical bars would need the labels rotated or truncated; here a long
    // name simply uses more of the gutter.
    const W = 336, R = 22, T = 6, B = 16;
    const L = 96;                       // gutter for the names
    const row = 22, barH = 12;          // one row per face, thin marks
    const H = T + faces.length * row + B;
    const plotW = W - L - R;
    const top = niceMax(Math.max(...faces.map((f) => f.sightings)));
    const x = (value) => L + (value / top) * plotW;

    // Two gridlines only: zero and the top. Bars are read by length, and more
    // rules than that compete with the bars themselves.
    const grid = [0, top].map((value) => `
      <line class="grid" x1="${x(value).toFixed(1)}" x2="${x(value).toFixed(1)}"
            y1="${T}" y2="${(H - B).toFixed(1)}"/>
      <text class="tick" x="${x(value).toFixed(1)}" y="${H - 4}"
            text-anchor="middle">${value}</text>`).join("");

    const bars = faces.map((face, index) => {
      const y = T + index * row;
      const mid = y + row / 2;
      const { named, text } = this._label(face);
      const count = Number(face.sightings);
      const title = `${text} — seen ${count} time${count === 1 ? "" : "s"}`;
      const width = Math.max(2, x(count) - L);
      return `
        <rect class="hit" x="0" y="${y}" width="${W}" height="${row}">
          <title>${esc(title)}</title></rect>
        <text class="who${named ? "" : " unnamed"}" x="0" y="${(mid + 3).toFixed(1)}"
          >${esc(text.length > 16 ? `${text.slice(0, 15)}…` : text)}</text>
        <rect class="bar" x="${L}" y="${(mid - barH / 2).toFixed(1)}"
          width="${width.toFixed(1)}" height="${barH}" rx="4"/>
        <text class="n" x="${(L + width + 4).toFixed(1)}"
          y="${(mid + 3).toFixed(1)}">${count}</text>`;
    }).join("");

    return `<svg class="chart" viewBox="0 0 ${W} ${H}"
      preserveAspectRatio="xMidYMid meet" role="img"
      aria-label="Times each face was seen">${grid}${bars}</svg>`;
  }

  body() {
    this._faces = facesByCount(this._recordings,
      faceNames(this._sharedNames, this._config.names));
    if (!this._faces.length) {
      return `<div class="muted">No faces recognised in this period. The hub
        only reports one when its own face detection fires.</div>`;
    }
    const total = this._faces.reduce((sum, face) => sum + face.sightings, 0);
    const unnamed = this._faces.filter((face) => this._label(face).named === false);
    const hint = unnamed.length
      ? `<div class="muted hint">Name a face by adding <code>names:</code> to
         this card: <code>${esc(unnamed[0].id)}: Alice</code></div>`
      : "";
    // The chart has a table twin, so no value is available only on hover.
    //
    // Chart, table and hint stacked together are taller than the card is
    // sized for as soon as there are a few faces, and the same unbounded
    // stack is what pushed the summary card over its neighbours. The scroll
    // wrapper is the flex child that gives way, so the overflow stays inside
    // this card however many faces the hub has seen.
    return `
      <div class="total muted">${this._faces.length} face${
        this._faces.length === 1 ? "" : "s"}, ${total} sighting${
        total === 1 ? "" : "s"}</div>
      <div class="scroll">
        ${this._chart(this._faces)}
        ${this._table(this._faces)}
        ${hint}
      </div>`;
  }
}

class TapoH500PeopleCard extends H500Base {
  static grid = { rows: 6, min_rows: 3, columns: 12, min_columns: 4 };
  static defaults = { days: 7, max_height: 0 };
  static style = `
    .person { margin-bottom: 12px; }
    .who { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }
    .who .label { font-size: 1rem; color: var(--primary-text-color); }
    .who .label.unnamed { font-family: monospace; font-size: 0.85rem;
                          color: var(--secondary-text-color); }
    .strip { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 4px; }
    .strip button { flex: 0 0 auto; padding: 0; border-radius: 6px;
      overflow: hidden; line-height: 0; background: var(--secondary-background-color); }
    .strip img, .strip .blank { width: 96px; aspect-ratio: 16 / 9;
      object-fit: cover; display: block; }
    .stamp { display: block; padding: 2px 4px; line-height: 1.2;
             font-size: 0.72rem; color: var(--secondary-text-color); }
  `;

  getCardSize() {
    return 3 + (this._people || []).length * 2;
  }

  body() {
    this._people = groupByPerson(this._recordings,
      faceNames(this._sharedNames, this._config.names));
    if (!this._people.length) {
      return `<div class="muted">Nobody has been recognised in this period.
        Clips with no face in them are on the other cards.</div>`;
    }
    const playing = this._recordings.find((item) => this._isPlaying(item));
    const rows = this._people.map((person) => {
      const named = person.name !== undefined && person.name !== null;
      const strip = person.items.map((item) => `
        <button data-action="play" data-start="${item.start_time}"
          ${item.downloaded ? "" : "disabled"}>
          ${this._image(item)}
          <span class="stamp">${esc(ago(item.start_time))}</span>
        </button>`).join("");
      return `
        <div class="person">
          <div class="who">
            <span class="label${named ? "" : " unnamed"}">${
              esc(named ? person.name : `Face ${person.id}`)}</span>
            <span class="muted">${person.items.length} recording${
              person.items.length === 1 ? "" : "s"}</span>
            <button data-action="name" data-face="${esc(person.id)}"
              data-name="${esc(named ? person.name : "")}"
              >${named ? "Rename" : "Name"}</button>
          </div>
          <div class="strip">${strip}</div>
        </div>`;
    }).join("");
    return `
      <div class="scroll"${this._maxHeight()}>${rows}</div>
      ${playing ? this._player(playing) : ""}`;
  }
}

// Defined only once each. The same file can legitimately be loaded more than
// once -- the integration registers a dashboard resource, a user may have added
// another by hand, and differing URLs count as separate modules. A second
// define() throws "the name has already been used with this registry".
const register = (type, cls, name, description) => {
  if (customElements.get(type)) return;
  customElements.define(type, cls);
  window.customCards = window.customCards || [];
  window.customCards.push({ type, name, description });
};

register("tapo-h500-card", TapoH500Card, "Tapo H500 Recordings",
  "Browse, download, play and delete H500 recordings.");
register("tapo-h500-hero-card", TapoH500HeroCard, "Tapo H500 Latest Event",
  "The newest event, large, with a tap to play.");
register("tapo-h500-grid-card", TapoH500GridCard, "Tapo H500 Event Grid",
  "Every recording as a thumbnail tile.");
register("tapo-h500-timeline-card", TapoH500TimelineCard, "Tapo H500 Timeline",
  "Recordings grouped by the hour they happened.");
register("tapo-h500-faces-card", TapoH500FacesCard, "Tapo H500 Faces",
  "Who the hub has recognised, with the names it will not supply itself.");
register("tapo-h500-summary-card", TapoH500SummaryCard, "Tapo H500 Summary",
  "Events by hour of day, as a bar chart.");
register("tapo-h500-people-card", TapoH500PeopleCard, "Tapo H500 People",
  "Recordings grouped by who is in them.");
register("tapo-h500-face-summary-card", TapoH500FaceSummaryCard,
  "Tapo H500 Face Summary",
  "How often each face was seen, as a bar chart.");

export { H500Base, TapoH500Card, TapoH500HeroCard, TapoH500GridCard,
         TapoH500TimelineCard, TapoH500FacesCard, TapoH500SummaryCard,
         TapoH500FaceSummaryCard, TapoH500PeopleCard };
