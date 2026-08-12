/**
 * Tapo H500 recording browser card.
 *
 * Lists the hub's indexed clips for one paired camera and lets you download,
 * play and delete them. Everything it shows comes from the integration's own
 * response services, so it needs no extra API surface.
 *
 * type: custom:tapo-h500-card
 * camera_index: 0      # position in the hub's paired-device list
 * days: 1              # how many days back to list
 * entry_id: abc123     # optional; the first H500 entry is used by default
 */

const pad = (value) => String(value).padStart(2, "0");
const utcDay = (date) =>
  `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}`;

// Camera aliases and hub-supplied labels reach innerHTML, so they are escaped.
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));

const STYLE = `
  ha-card { padding: 12px 16px 16px; }
  .head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .head h2 { flex: 1; margin: 0; font-size: 1.1rem; font-weight: 500; }
  .muted { color: var(--secondary-text-color); font-size: 0.85rem; }
  button {
    background: none; border: none; cursor: pointer; padding: 4px 8px;
    border-radius: 4px; color: var(--primary-color); font: inherit;
    font-size: 0.85rem;
  }
  button:hover { background: var(--secondary-background-color); }
  button[disabled] { color: var(--disabled-text-color); cursor: default; }
  button.danger { color: var(--error-color, #db4437); }
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
  .badge {
    display: inline-block; padding: 0 6px; border-radius: 8px;
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em;
    background: var(--secondary-background-color);
  }
  .badge.ring { background: var(--primary-color); color: var(--text-primary-color); }
  video { width: 100%; margin: 8px 0; border-radius: 4px; background: #000; }
  .error { color: var(--error-color, #db4437); padding: 8px 0; }
`;

class TapoH500Card extends HTMLElement {
  setConfig(config) {
    this._config = { camera_index: 0, days: 1, ...config };
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card></ha-card>`;
      this._card = this.shadowRoot.querySelector("ha-card");
      this._card.addEventListener("click", (event) => this._onClick(event));
    }
    this._recordings = null;
    this._error = null;
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

  getCardSize() {
    return 3 + (this._recordings ? this._recordings.length : 0);
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
        camera_index: this._config.camera_index,
        start_date: utcDay(new Date(now.getTime() - (this._config.days - 1) * 86400000)),
        end_date: utcDay(now),
      });
      this._camera = response.camera;
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
      } else if (action === "play") {
        this._playing = this._playing === start ? null : start;
        this._render();
      } else if (action === "download") {
        await this._call("download_recording", {
          config_entry_id: await this._entryId(),
          camera_index: this._config.camera_index,
          start_time: Number(start),
          end_time: Number(end),
        });
        await this._load();
      } else if (action === "delete") {
        await this._call("delete_recording", {
          config_entry_id: await this._entryId(),
          camera_index: this._config.camera_index,
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

  _row(item) {
    const when = new Date(item.start_time * 1000);
    const label = this._config.days > 1
      ? when.toLocaleString()
      : when.toLocaleTimeString();
    const thumbnail = item.thumbnail
      ? `<img src="${esc(item.thumbnail)}" alt="" loading="lazy"
           onerror="this.removeAttribute('src')">`
      : `<div class="blank"></div>`;
    const actions = item.downloaded
      ? `<button data-action="play" data-start="${item.start_time}">
           ${this._playing === String(item.start_time) ? "Hide" : "Play"}
         </button>
         <button class="danger" data-action="delete" data-start="${item.start_time}">
           Delete
         </button>`
      : `<button data-action="download" data-start="${item.start_time}"
           data-end="${item.end_time}">Download</button>`;
    const player = this._playing === String(item.start_time)
      ? `<video controls autoplay src="${esc(item.url)}"></video>`
      : "";
    return `
      <div class="row">
        ${thumbnail}
        <div class="info">
          <div class="when">${esc(label)}</div>
          <div class="muted">
            <span class="badge ${esc(item.event_type)}">${esc(item.event_type)}</span>
            ${Number(item.duration)}s
          </div>
        </div>
        ${actions}
      </div>
      ${player}`;
  }

  _render() {
    if (!this._card) return;
    const title = (this._camera && this._camera.alias) || "Tapo H500";
    const body = this._error
      ? `<div class="error">${esc(this._error)}</div>`
      : this._recordings === null
        ? `<div class="muted">Loading recordings…</div>`
        : this._recordings.length === 0
          ? `<div class="muted">No recordings in this period.</div>`
          : this._recordings.map((item) => this._row(item)).join("");
    this._card.innerHTML = `
      <div class="head">
        <h2>${esc(title)}</h2>
        <button data-action="refresh">Refresh</button>
      </div>
      ${body}`;
  }
}

customElements.define("tapo-h500-card", TapoH500Card);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "tapo-h500-card",
  name: "Tapo H500 Recordings",
  description: "Browse, download, play and delete H500 recordings.",
});
