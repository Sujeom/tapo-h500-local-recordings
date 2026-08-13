/**
 * Tapo H500 dashboard cards.
 *
 * Four ways to look at the same recordings, all fed by the integration's own
 * response services, so none of them needs extra API surface:
 *
 *   custom:tapo-h500-card            list, with download/play/delete
 *   custom:tapo-h500-hero-card       the newest event, large
 *   custom:tapo-h500-grid-card       every event as a tile
 *   custom:tapo-h500-timeline-card   events grouped by hour
 *
 * Shared options:
 *   days: 1              # how many days back to list
 *   camera_index: 0      # optional; omit to get a picker for every paired camera
 *   entry_id: abc123     # optional; the first H500 entry is used by default
 *   max_height: 400      # list/grid/timeline only; 0 to grow unbounded
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

/** "2 minutes ago". Floors rather than rounds, so it never reads ahead of itself. */
export const ago = (startSeconds, now = Date.now()) => {
  const delta = Math.floor((now - startSeconds * 1000) / 1000);
  for (const [name, size] of [["day", 86400], ["hour", 3600], ["minute", 60]]) {
    const count = Math.floor(delta / size);
    if (count >= 1) return `${count} ${name}${count === 1 ? "" : "s"} ago`;
  }
  return "just now";
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

const BASE_STYLE = `
  ha-card { padding: 12px 16px 16px; }
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
 * Everything the four cards share: config, polling, the service calls, the
 * camera picker and the click routing. A subclass supplies only `body()`.
 */
class H500Base extends HTMLElement {
  static defaults = {};
  static style = "";

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
    this._cameras = null;
    this._error = null;
    // A pinned camera_index keeps the single-camera behaviour; without one the
    // card offers every paired camera.
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
      const now = new Date();
      const response = await this._call("list_recordings", {
        config_entry_id: await this._entryId(),
        camera_index: this._index,
        start_date: utcDay(new Date(now.getTime() - (this._config.days - 1) * 86400000)),
        end_date: utcDay(now),
      });
      this._camera = response.camera;
      this._cameras = response.cameras || null;
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
    return `<span class="badge ${esc(item.event_type)}"
      title="${esc(item.event_type)}">${esc(label)}</span>`;
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
    .event .at { flex: 1; min-width: 0; font-size: 0.9rem; }
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

export { H500Base, TapoH500Card, TapoH500HeroCard, TapoH500GridCard,
         TapoH500TimelineCard };
