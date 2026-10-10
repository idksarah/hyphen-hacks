// Asterism (Safe-Light Constellations) frontend. See docs/CONTRACTS.md for data
// shapes; visual design follows Sarah's mockup in mocks/index.html.

// Served from the repo root (python3 -m http.server), so data/ is one level up.
const LAMPS_URL = "../data/lamps.geojson";
const DEAD_URL = "../data/dead_lamps.json"; // optional; skipped if missing
const BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

// Contract 4: Sarah's API. If it's not running, fall back to her mock response.
const API_URL = "http://localhost:8000";
const MOCK_URL = "../mocks/route_response.json";

// Contract bounding box: lon -122.52..-122.35, lat 37.70..37.84
const SF_BBOX = { minLon: -122.52, minLat: 37.70, maxLon: -122.35, maxLat: 37.84 };
const SF_BOUNDS = [[SF_BBOX.minLon, SF_BBOX.minLat], [SF_BBOX.maxLon, SF_BBOX.maxLat]];

// Mockup palette (mirrors the CSS tokens in style.css).
const C = {
  night1: "#140e1a", night2: "#1e1627", night3: "#2a2035", border: "#4d3f60",
  text: "#f7efe4", faint: "#9a8aab", cream: "#fff8ee",
  coral: "#ff7a66", rose: "#ff8fc3", tangerine: "#ffa255", butter: "#ffd88a",
  mint: "#7fe3bf", sky: "#8ccaff", lilac: "#b9a0ff",
  lightOut: "#7d6a92", park: "#1a2a26", street: "#3e3150",
};
const ROUTE_COLOR = { lit: C.butter, shortest: C.lilac };

const statusEl = document.getElementById("status");
const routesEl = document.getElementById("routes");
const outagesBox = document.getElementById("outages");
const alphaSlider = document.getElementById("alpha");

const map = new maplibregl.Map({
  container: "map",
  style: BASEMAP,
  bounds: SF_BOUNDS,
  maxBounds: [[-122.62, 37.65], [-122.25, 37.89]],
  attributionControl: false,
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
map.addControl(new maplibregl.AttributionControl({ compact: true }), "top-right");

map.on("load", async () => {
  // Compact attribution starts expanded; collapse it to the ⓘ button (tap to open).
  document.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
  recolorBasemap();
  map.addImage("spark", sparkImage(), { pixelRatio: 2 });

  const [lamps, dead] = await Promise.all([
    fetch(LAMPS_URL).then((r) => {
      if (!r.ok) throw new Error(`lamps.geojson: HTTP ${r.status}`);
      return r.json();
    }),
    fetch(DEAD_URL).then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);

  // Contract 2: only the set of dead[].lamp_id matters for marking lamps.
  const deadIds = new Set(dead?.dead.map((d) => d.lamp_id) ?? []);
  for (const f of lamps.features) f.properties.dead = deadIds.has(f.properties.id);

  namedLamps = lamps.features.filter((f) => f.properties.street);

  map.addSource("lamps", { type: "geojson", data: lamps });
  map.addSource("unmatched-outages", { type: "geojson", data: unmatchedFC(dead) });
  addStarLayers();
  addOutageLayers();
  addRouteLayers();

  map.on("click", (e) => pickPoint([e.lngLat.lng, e.lngLat.lat]));
  document.getElementById("reset").addEventListener("click", resetPicks);
  outagesBox.addEventListener("change", () => {
    if (picks.from && picks.to) requestRoute();
  });
  alphaSlider.addEventListener("change", () => {
    if (picks.from && picks.to) requestRoute();
  });
  for (const card of document.querySelectorAll(".route-card")) {
    card.addEventListener("click", () => selectRoute(card.dataset.route));
  }
  setStatus("Tap the map where you're starting.");
});

// ---- Basemap and lamps ----------------------------------------------------------

// Re-tint CARTO Dark Matter into the mockup's charcoal-plum night.
function recolorBasemap() {
  for (const layer of map.getStyle().layers) {
    const id = layer.id;
    try {
      if (layer.type === "background") {
        map.setPaintProperty(id, "background-color", C.night1);
      } else if (layer.type === "fill") {
        const color = /water/.test(id) ? "#0d0912"
          : /park|wood|grass|landcover/.test(id) ? C.park
          : /building/.test(id) ? "#211a2b"
          : C.night2;
        map.setPaintProperty(id, "fill-color", color);
      } else if (layer.type === "line") {
        const color = /water/.test(id) ? "#0d0912" : /boundary/.test(id) ? C.border : C.street;
        map.setPaintProperty(id, "line-color", color);
        map.setPaintProperty(id, "line-opacity", 0.55);
      } else if (layer.type === "symbol") {
        map.setPaintProperty(id, "text-color", C.faint);
        map.setPaintProperty(id, "text-halo-color", C.night1);
        map.setPaintProperty(id, "text-opacity", 0.6);
      }
    } catch {
      // Some layers use data-driven or pattern paint; leave those as they are.
    }
  }
}

// The mockup's four-point sparkle, drawn once for use as a map icon.
function sparkImage(size = 48) {
  const canvas = Object.assign(document.createElement("canvas"), { width: size, height: size });
  const ctx = canvas.getContext("2d");
  const s = size / 24; // path is authored on a 24-unit grid
  ctx.scale(s, s);
  ctx.fillStyle = C.cream;
  ctx.fill(new Path2D("M12 0C13 8 16 11 24 12C16 13 13 16 12 24C11 16 8 13 0 12C8 11 11 8 12 0Z"));
  return ctx.getImageData(0, 0, size, size);
}

// Working lamps: a butter glow with a cream core, becoming sparkles up close.
function addStarLayers() {
  const alive = ["!", ["get", "dead"]];
  map.addLayer({
    id: "lamp-halo",
    type: "circle",
    source: "lamps",
    filter: alive,
    paint: {
      "circle-color": C.butter,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 2, 14, 6, 17, 14],
      "circle-blur": 1,
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 11, 0.22, 15, 0.32],
    },
  });
  map.addLayer({
    id: "lamp-core",
    type: "circle",
    source: "lamps",
    filter: alive,
    maxzoom: 15,
    paint: {
      "circle-color": C.cream,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 0.6, 14, 1.4],
      "circle-opacity": 0.9,
    },
  });
  map.addLayer({
    id: "lamp-spark",
    type: "symbol",
    source: "lamps",
    filter: alive,
    minzoom: 15,
    layout: {
      "icon-image": "spark",
      "icon-size": ["interpolate", ["linear"], ["zoom"], 15, 0.3, 18, 0.65],
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
  });
}

// Dead lamps use the mockup's "light out" ring; unmatched 311 reports use its
// coral "report" marker.
function addOutageLayers() {
  map.addLayer({
    id: "dead-stars",
    type: "circle",
    source: "lamps",
    filter: ["get", "dead"],
    paint: {
      "circle-color": "rgba(0, 0, 0, 0)",
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 2.5, 14, 3.5, 17, 6],
      "circle-stroke-color": C.lightOut,
      "circle-stroke-width": 1.5,
    },
  });
  map.addLayer({
    id: "unmatched-ring",
    type: "circle",
    source: "unmatched-outages",
    paint: {
      "circle-color": "rgba(0, 0, 0, 0)",
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 3, 14, 6, 17, 9],
      "circle-stroke-color": C.coral,
      "circle-stroke-width": 1.5,
      "circle-stroke-opacity": 0.5,
    },
  });
  map.addLayer({
    id: "unmatched-core",
    type: "circle",
    source: "unmatched-outages",
    paint: { "circle-color": C.coral, "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 1, 15, 3] },
  });
}

function unmatchedFC(dead) {
  return {
    type: "FeatureCollection",
    features: (dead?.unmatched ?? []).map((u) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: u.coord },
      properties: { case_id: u.case_id, opened: u.opened },
    })),
  };
}

// ---- Start/end picking ----------------------------------------------------------

const picks = { from: null, to: null };
let namedLamps = []; // lamps with a street name, for labelling picked points
let routeRequest = 0; // ignore responses from superseded requests

function pickPoint(lonLat) {
  if (!inSF(lonLat)) {
    setStatus("That's outside San Francisco.");
    return;
  }
  if (picks.from && picks.to) resetPicks();

  const which = picks.from ? "to" : "from";
  picks[which] = makeMarker(which, lonLat);
  showField(which, lonLat);

  if (which === "from") setStatus("Now tap where you're going.");
  else requestRoute();
}

function makeMarker(which, lonLat) {
  const el = document.createElement("div");
  el.className = `marker-${which}`;
  el.title = which === "from" ? "Start" : "Destination";
  if (which === "to") {
    el.innerHTML = `<svg viewBox="-12 -12 24 24" width="30" height="30" aria-hidden="true">
      <path d="M0 -12C.6 -3 3 -.6 12 0C3 .6 .6 3 0 12C-.6 3 -3 .6 -12 0C-3 -.6 -.6 -3 0 -12Z" fill="${C.sky}"/></svg>`;
  }
  const marker = new maplibregl.Marker({ element: el, draggable: true })
    .setLngLat(lonLat)
    .addTo(map);
  marker.on("dragend", () => {
    const { lng, lat } = marker.getLngLat();
    if (!inSF([lng, lat])) {
      setStatus("That's outside San Francisco.");
      return;
    }
    showField(which, [lng, lat]);
    if (picks.from && picks.to) requestRoute();
  });
  return marker;
}

function showField(which, lonLat) {
  const el = document.getElementById(`${which}-value`);
  if (lonLat) {
    const street = nearestStreet(lonLat);
    el.textContent = street ? `Near ${street}` : "Dropped pin";
    el.classList.add("set");
  } else {
    el.textContent = which === "from" ? "Where from?" : "Where to?";
    el.classList.remove("set");
  }
}

// Street name of the closest named lamp within ~150 m, e.g. "Mission St".
function nearestStreet([lon, lat]) {
  const kx = Math.cos((lat * Math.PI) / 180);
  let best = null;
  let bestD = (150 / 111_320) ** 2;
  for (const f of namedLamps) {
    const [x, y] = f.geometry.coordinates;
    const d = ((x - lon) * kx) ** 2 + (y - lat) ** 2;
    if (d < bestD) [best, bestD] = [f.properties.street, d];
  }
  return best && best.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

function resetPicks() {
  picks.from?.remove();
  picks.to?.remove();
  picks.from = picks.to = null;
  routeRequest++;
  showField("from", null);
  showField("to", null);
  showRoute(null);
  setStatus("Tap the map where you're starting.");
}

function inSF([lon, lat]) {
  return lon >= SF_BBOX.minLon && lon <= SF_BBOX.maxLon &&
         lat >= SF_BBOX.minLat && lat <= SF_BBOX.maxLat;
}

// ---- Fetching routes ------------------------------------------------------------

async function requestRoute() {
  const id = ++routeRequest;
  const from = picks.from.getLngLat();
  const to = picks.to.getLngLat();
  setStatus("Finding a route…");

  let result;
  try {
    result = await fetchRoute({ lng: from.lng, lat: from.lat }, { lng: to.lng, lat: to.lat });
  } catch (err) {
    if (id !== routeRequest) return;
    showRoute(null);
    setStatus(err.message);
    return;
  }
  if (id !== routeRequest) return;

  setStatus("");
  showRoute(result.data);
  showStats(result.data, result.mock);
  fitRoutes(result.data);
}

// Returns { data, mock }. Throws an Error with a human-readable message.
async function fetchRoute(from, to) {
  const params = new URLSearchParams({
    from_lon: from.lng, from_lat: from.lat,
    to_lon: to.lng, to_lat: to.lat,
    alpha: (alphaSlider.value / 10).toFixed(1), // slider 0–100 → contract alpha 0–10
    outages: outagesBox.checked,
  });

  let res;
  try {
    res = await fetch(`${API_URL}/route?${params}`);
  } catch {
    return { data: await fetchMock(), mock: true }; // backend not running
  }

  const body = await res.json().catch(() => null);
  if (res.ok && body) return { data: body, mock: false };
  // Contract 4 errors: {"error": "<code>", "message": "<human text>"}
  throw new Error(body?.message || `Routing failed (HTTP ${res.status}).`);
}

async function fetchMock() {
  const res = await fetch(MOCK_URL).catch(() => null);
  if (res?.ok) return res.json();
  throw new Error("Backend offline and no mock route found.");
}

// ---- Drawing routes -------------------------------------------------------------

const ROUTES = ["shortest", "lit"];
let routeData = null;
let activeRoute = "lit";

function addRouteLayers() {
  for (const name of ROUTES) {
    // lineMetrics enables the beam gradient along the lit route.
    map.addSource(`route-${name}`, { type: "geojson", data: emptyFC(), lineMetrics: true });
  }
  map.addSource("dark-gaps", { type: "geojson", data: emptyFC() });
  map.addSource("constellation", { type: "geojson", data: emptyFC() });

  const round = { "line-cap": "round", "line-join": "round" };
  for (const name of ROUTES) {
    const color = ROUTE_COLOR[name];
    map.addLayer({
      id: `${name}-ghost`,
      type: "line",
      source: `route-${name}`,
      layout: round,
      paint: { "line-color": color, "line-width": 2.5, "line-opacity": 0.55, "line-dasharray": [0.5, 3.5] },
    });
    map.addLayer({
      id: `${name}-halo`,
      type: "line",
      source: `route-${name}`,
      layout: round,
      paint: { "line-color": color, "line-width": 16, "line-blur": 10, "line-opacity": 0.4 },
    });
    map.addLayer({
      id: `${name}-main`,
      type: "line",
      source: `route-${name}`,
      layout: round,
      paint: name === "lit"
        ? {
            "line-width": 5,
            "line-gradient": ["interpolate", ["linear"], ["line-progress"],
              0, C.rose, 0.2, C.coral, 0.4, C.tangerine, 0.6, C.butter, 0.8, C.mint, 1, C.sky],
          }
        : { "line-color": color, "line-width": 5 },
    });
  }

  map.addLayer({
    id: "dark-gaps",
    type: "line",
    source: "dark-gaps",
    layout: { "line-cap": "round" },
    paint: { "line-color": C.coral, "line-width": 6, "line-opacity": 0.9 },
  });
  map.addLayer({
    id: "constellation-lines",
    type: "line",
    source: "constellation",
    filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": C.cream, "line-width": 1.2, "line-opacity": 0.75 },
  });
  map.addLayer({
    id: "constellation-glow",
    type: "circle",
    source: "constellation",
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-color": ["get", "color"], "circle-radius": 12, "circle-blur": 1, "circle-opacity": 0.45 },
  });
  map.addLayer({
    id: "constellation-stars",
    type: "symbol",
    source: "constellation",
    filter: ["==", ["geometry-type"], "Point"],
    layout: {
      "icon-image": "spark",
      "icon-size": 0.7,
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
  });
}

function showRoute(data) {
  routeData = data;
  if (!data) {
    for (const id of [...ROUTES.map((n) => `route-${n}`), "dark-gaps", "constellation"]) {
      map.getSource(id).setData(emptyFC());
    }
    routesEl.hidden = true;
    return;
  }
  for (const name of ROUTES) {
    map.getSource(`route-${name}`).setData(lineFC(data.routes[name].coords));
  }
  selectRoute(data.same_route ? "lit" : activeRoute);
}

// The active route is drawn solid with a halo, its dark gaps and constellation;
// the other is a dashed ghost you can switch to from its card.
function selectRoute(name) {
  activeRoute = name;
  for (const n of ROUTES) {
    const on = n === name;
    map.setLayoutProperty(`${n}-ghost`, "visibility", on ? "none" : "visible");
    map.setLayoutProperty(`${n}-halo`, "visibility", on ? "visible" : "none");
    map.setLayoutProperty(`${n}-main`, "visibility", on ? "visible" : "none");
    document.querySelector(`.route-card[data-route="${n}"]`).setAttribute("aria-pressed", on);
  }
  if (!routeData) return;
  const route = routeData.routes[name];
  map.getSource("dark-gaps").setData({
    type: "FeatureCollection",
    features: route.dark_gaps.map((g) => lineFeature(g.coords)),
  });
  map.getSource("constellation").setData(constellationFC(route, ROUTE_COLOR[name]));
}

// Lamp vertices joined in walking order. Contract: constellation is empty when
// the route passes fewer than 2 lamps, so fall back to the street geometry.
function constellationFC(route, color) {
  const pts = route.constellation.map((s) => s.coord);
  return {
    type: "FeatureCollection",
    features: [
      lineFeature(pts.length >= 2 ? pts : route.coords),
      ...route.constellation.map((s) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: s.coord },
        properties: { id: s.id, color },
      })),
    ],
  };
}

// Zoom to both routes.
function fitRoutes(data) {
  const bounds = new maplibregl.LngLatBounds();
  for (const name of ROUTES) for (const c of data.routes[name].coords) bounds.extend(c);
  map.fitBounds(bounds, { padding: 60, maxZoom: 16 });
}

// ---- Route cards ----------------------------------------------------------------

function showStats(data, mock) {
  if (mock) console.info("Routing server offline; showing mocks/route_response.json.");

  for (const name of ROUTES) {
    const r = data.routes[name];
    const card = document.querySelector(`.route-card[data-route="${name}"]`);
    card.querySelector('[data-stat="minutes"]').textContent = `${Math.round(r.minutes)} min`;
    card.querySelector('[data-stat="tag"]').textContent =
      `${formatDistance(r.length_m)} · ${Math.round(r.lit_frac * 100)}% lit`;
    card.querySelector('[data-stat="lit-bar"]').style.width = `${r.lit_frac * 100}%`;
  }
  document.querySelector('.route-card[data-route="shortest"]').hidden = data.same_route;

  // Contract 4: when the detour cap kicked in, the UI should say so.
  const extra = Math.round(data.routes.lit.minutes - data.routes.shortest.minutes);
  document.getElementById("note").textContent =
    data.detour_capped ? "There's no well-lit way there without a long detour. This is the best we found."
    : data.same_route ? "The shortest way is already the brightest."
    : extra > 0 ? `${extra} extra min for a brighter walk.`
    : "Brighter, and no slower.";
  routesEl.hidden = false;
}

function formatDistance(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}

// ---- Helpers ----------------------------------------------------------------------

function lineFeature(coords) {
  return { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: {} };
}

function lineFC(coords) {
  return { type: "FeatureCollection", features: [lineFeature(coords)] };
}

function emptyFC() {
  return { type: "FeatureCollection", features: [] };
}

function setStatus(text) {
  statusEl.textContent = text;
}
