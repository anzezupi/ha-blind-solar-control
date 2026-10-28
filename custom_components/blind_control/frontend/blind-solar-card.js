/*
 * Blind Solar Card – sun path vs. window and hourly solar heat forecast.
 *
 *   type: custom:blind-solar-card
 *   entity: sensor.living_room_solar_gain   # the "Solar gain" sensor of a blind
 *   title: Living room                      # optional
 *
 * No dependencies; re-renders only when the entity state object changes.
 */

const W = 320;
const H = 150;
const PAD = { l: 26, r: 8, t: 8, b: 18 };
const COMPASS = { 0: "N", 45: "NE", 90: "E", 135: "SE", 180: "S", 225: "SW", 270: "W", 315: "NW", 360: "N" };

const diff = (a, b) => ((a - b + 540) % 360) - 180;

function blocked(horizon, az, el) {
  return (horizon || []).some(([s, e, h]) => (s <= e ? az >= s && az <= e : az >= s || az <= e) && el <= h);
}

function inWindow(w, az, el) {
  if (el <= Math.max(w.min_elevation, 0) || el > w.max_elevation) return false;
  const d = diff(az, w.azimuth);
  return d >= -w.fov_left && d <= w.fov_right && !blocked(w.horizon, az, el);
}

/** Split an azimuth span that may cross north into drawable [from, to] parts. */
function spans(from, to) {
  from = ((from % 360) + 360) % 360;
  to = ((to % 360) + 360) % 360;
  return from <= to ? [[from, to]] : [[from, 360], [0, to]];
}

class BlindSolarCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error("Please define an entity (the 'Solar gain' sensor).");
    this._config = config;
    this._stateObj = undefined;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const stateObj = hass.states[this._config.entity];
    if (stateObj === this._stateObj) return;
    this._stateObj = stateObj;
    this._render();
  }

  getCardSize() {
    return 5;
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find((e) => e.startsWith("sensor.") && hass.states[e].attributes.sun_path);
    return { entity: entity || "" };
  }

  _render() {
    const s = this._stateObj;
    if (!s) {
      this.shadowRoot.innerHTML = `<ha-card><div class="warn">Entity ${this._config.entity} not found</div></ha-card>`;
      return;
    }
    const a = s.attributes;
    const w = a.window;
    const path = a.sun_path || [];
    if (!w || !path.length) {
      this.shadowRoot.innerHTML = `<ha-card><div class="warn">${this._config.entity} has no sun path data</div></ha-card>`;
      return;
    }
    const title = this._config.title || (a.friendly_name || "").replace(/ solar gain$/i, "");
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="head">
          <div class="title">${title}</div>
          <div class="now"><b>${Math.round(Number(s.state) || 0)} W</b>
            <span>${(a.today_kwh ?? 0).toFixed(2)} kWh today</span></div>
        </div>
        ${this._sky(w, path, a.sun)}
        ${this._bars(a.hourly || [])}
      </ha-card>`;
  }

  _sky(w, path, sun) {
    const azs = path.map((p) => p[0]);
    const azMin = Math.max(0, Math.min(...azs) - 10);
    const azMax = Math.min(360, Math.max(...azs) + 10);
    const elMax = Math.min(90, Math.max(30, Math.ceil((Math.max(...path.map((p) => p[1])) + 8) / 10) * 10));
    const x = (az) => PAD.l + ((az - azMin) / (azMax - azMin)) * (W - PAD.l - PAD.r);
    const y = (el) => H - PAD.b - (Math.max(0, el) / elMax) * (H - PAD.t - PAD.b);
    const rect = (a1, a2, e1, e2, cls) =>
      spans(a1, a2)
        .map(([f, t]) => [Math.max(f, azMin), Math.min(t, azMax)])
        .filter(([f, t]) => t > f)
        .map(([f, t]) => `<rect class="${cls}" x="${x(f)}" y="${y(e2)}" width="${x(t) - x(f)}" height="${y(e1) - y(e2)}"/>`)
        .join("");

    const fov = rect(w.azimuth - w.fov_left, w.azimuth + w.fov_right, Math.max(0, w.min_elevation), Math.min(elMax, w.max_elevation), "fov");
    const obstacles = (w.horizon || []).map(([f, t, h]) => rect(f, t, 0, Math.min(h, elMax), "obstacle")).join("");

    // Path: thin grey line, thick orange where the sun shines on the window.
    let line = "";
    let lit = "";
    for (let i = 1; i < path.length; i++) {
      const [a0, e0] = path[i - 1];
      const [a1, e1] = path[i];
      if (e0 < 0 && e1 < 0) continue;
      const seg = `M${x(a0).toFixed(1)},${y(e0).toFixed(1)}L${x(a1).toFixed(1)},${y(e1).toFixed(1)}`;
      line += seg;
      if (inWindow(w, a0, e0) && inWindow(w, a1, e1)) lit += seg;
    }

    const ticks = Object.entries(COMPASS)
      .filter(([az]) => az >= azMin && az <= azMax)
      .map(([az, label]) => `<text class="tick" x="${x(az)}" y="${H - 4}" text-anchor="middle">${label}</text>
        <line class="grid" x1="${x(az)}" x2="${x(az)}" y1="${PAD.t}" y2="${H - PAD.b}"/>`)
      .join("");
    const elTicks = [30, 60, 90]
      .filter((e) => e < elMax)
      .map((e) => `<text class="tick" x="${PAD.l - 4}" y="${y(e) + 3}" text-anchor="end">${e}°</text>
        <line class="grid" x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(e)}" y2="${y(e)}"/>`)
      .join("");
    const sunDot = sun && sun[1] > 0 && sun[0] >= azMin && sun[0] <= azMax
      ? `<circle class="${inWindow(w, sun[0], sun[1]) ? "sun lit" : "sun"}" cx="${x(sun[0])}" cy="${y(sun[1])}" r="6"/>`
      : "";

    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Sun path relative to the window">
      ${ticks}${elTicks}${fov}${obstacles}
      <line class="ground" x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(0)}" y2="${y(0)}"/>
      <path class="path" d="${line}"/><path class="path lit" d="${lit}"/>
      ${sunDot}
    </svg>`;
  }

  _bars(hourly) {
    if (!hourly.length) return "";
    const max = Math.max(0.05, ...hourly.map((h) => h.kwh));
    const bw = (W - PAD.l - PAD.r) / hourly.length;
    const bh = 54;
    const bars = hourly
      .map((h, i) => {
        const hgt = (h.kwh / max) * (bh - 14);
        const hour = new Date(h.time).getHours();
        const label = hour % 3 === 0 ? `<text class="tick" x="${PAD.l + i * bw + bw / 2}" y="${bh}" text-anchor="middle">${hour}</text>` : "";
        return `<rect class="bar" x="${PAD.l + i * bw + 1}" y="${bh - 12 - hgt}" width="${bw - 2}" height="${hgt}"><title>${hour}:00 – ${h.kwh.toFixed(2)} kWh</title></rect>${label}`;
      })
      .join("");
    const total = hourly.reduce((sum, h) => sum + h.kwh, 0);
    return `<div class="sub">Solar heat forecast (window open): ${total.toFixed(1)} kWh / 24 h</div>
      <svg viewBox="0 0 ${W} ${bh}" role="img" aria-label="Hourly solar heat forecast">${bars}</svg>`;
  }
}

const STYLE = `
  ha-card { padding: 12px 12px 8px; }
  .head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; }
  .title { font-size: 1.1em; font-weight: 500; color: var(--primary-text-color); }
  .now { text-align: right; color: var(--secondary-text-color); font-size: .85em; }
  .now b { display: block; font-size: 1.5em; color: var(--primary-text-color); }
  .sub { font-size: .8em; color: var(--secondary-text-color); margin-top: 6px; }
  .warn { padding: 16px; color: var(--error-color); }
  svg { width: 100%; height: auto; display: block; overflow: visible; }
  .tick { font-size: 8px; fill: var(--secondary-text-color); }
  .grid { stroke: var(--divider-color); stroke-width: .5; }
  .ground { stroke: var(--secondary-text-color); stroke-width: 1; }
  .fov { fill: var(--warning-color, #ffa600); opacity: .12; }
  .obstacle { fill: var(--secondary-text-color); opacity: .35; }
  .path { fill: none; stroke: var(--secondary-text-color); stroke-width: 1.2; stroke-dasharray: 2 2; }
  .path.lit { stroke: var(--warning-color, #ffa600); stroke-width: 3; stroke-dasharray: none; stroke-linecap: round; }
  .sun { fill: var(--secondary-text-color); stroke: var(--card-background-color, #fff); stroke-width: 2; }
  .sun.lit { fill: #ffc107; }
  .bar { fill: var(--warning-color, #ffa600); opacity: .8; }
`;

if (!customElements.get("blind-solar-card")) {
  customElements.define("blind-solar-card", BlindSolarCard);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "blind-solar-card",
  name: "Blind Solar Card",
  description: "Sun path relative to a window and its hourly solar heat forecast (Blind Control).",
  preview: false,
});
