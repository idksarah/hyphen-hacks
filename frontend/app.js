// Safe-Light Constellations frontend. See docs/CONTRACTS.md for data shapes.

// Served from the repo root (python3 -m http.server), so data/ is one level up.
const LAMPS_URL = "../data/lamps.geojson";
const BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

// Contract 4: Sarah's API. If it's not running, fall back to a mock response:
// Sarah's mocks/ copy first, then our temporary one in frontend/dev/.
const API_URL = "http://localhost:8000";
const MOCK_URLS = ["../mocks/route_response.json", "dev/route_response.json"];

// Contract bounding box: lon -122.52..-122.35, lat 37.70..37.84
const SF_BBOX = { minLon: -122.52, minLat: 37.70, maxLon: -122.35, maxLat: 37.84 };
const SF_BOUNDS = [[SF_BBOX.minLon, SF_BBOX.minLat], [SF_BBOX.maxLon, SF_BBOX.maxLat]];

const STAR_COLOR = "#ffd88a";

const statusEl = document.getElementById("status");
const resetBtn = document.getElementById("reset");

const map = new maplibregl.Map({
  container: "map",
  style: BASEMAP,
  bounds: SF_BOUNDS,
  maxBounds: [[-122.62, 37.65], [-122.25, 37.89]],
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

map.on("load", async () => {
  dimBasemap();

  const lamps = await fetch(LAMPS_URL).then((r) => {
    if (!r.ok) throw new Error(`lamps.geojson: HTTP ${r.status}`);
    return r.json();
  });
  document.getElementById("lamp-count").textContent =
    `${lamps.features.length.toLocaleString()} streetlights`;

  map.addSource("lamps", { type: "geojson", data: lamps });
  addStarLayers();
  addRouteLayers();

  map.on("click", (e) => pickPoint([e.lngLat.lng, e.lngLat.lat]));
  resetBtn.addEventListener("click", resetPicks);
  setStatus("Click the map to set a start point.");
});

// Push the basemap darker so the lamps read as stars.
function dimBasemap() {
  for (const layer of map.getStyle().layers) {
    if (layer.type === "line") map.setPaintProperty(layer.id, "line-opacity", 0.45);
    if (layer.type === "symbol") map.setPaintProperty(layer.id, "text-opacity", 0.5);
  }
}

// Each lamp is a soft halo plus a bright core; both grow with zoom.
function addStarLayers() {
  map.addLayer({
    id: "lamp-halo",
    type: "circle",
    source: "lamps",
    paint: {
      "circle-color": STAR_COLOR,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 2, 14, 6, 17, 16],
      "circle-blur": 1,
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 11, 0.25, 15, 0.35],
    },
  });
  map.addLayer({
    id: "lamp-core",
    type: "circle",
    source: "lamps",
    paint: {
      "circle-color": "#fff6e0",
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 0.6, 14, 1.4, 17, 3],
      "circle-opacity": 0.9,
    },
  });
}

// ---- Start/end picking -----------------------------------------------------

const picks = { from: null, to: null };
let routeRequest = 0; // ignore responses from superseded requests

function pickPoint(lonLat) {
  if (!inSF(lonLat)) {
    setStatus("That point is outside San Francisco. Pick somewhere in the city.");
    return;
  }
  if (picks.from && picks.to) resetPicks();

  const which = picks.from ? "to" : "from";
  picks[which] = makeMarker(which, lonLat);

  if (which === "from") setStatus("Now click to set the destination.");
  else requestRoute();
  resetBtn.hidden = false;
}

function makeMarker(which, lonLat) {
  const el = document.createElement("div");
  el.className = `pin pin-${which}`;
  el.title = which === "from" ? "Start" : "Destination";
  const marker = new maplibregl.Marker({ element: el, draggable: true })
    .setLngLat(lonLat)
    .addTo(map);
  marker.on("dragend", () => {
    const { lng, lat } = marker.getLngLat();
    if (!inSF([lng, lat])) {
      setStatus("That point is outside San Francisco. Pick somewhere in the city.");
      return;
    }
    if (picks.from && picks.to) requestRoute();
  });
  return marker;
}

function resetPicks() {
  picks.from?.remove();
  picks.to?.remove();
  picks.from = picks.to = null;
  routeRequest++;
  showRoute(null);
  resetBtn.hidden = true;
  setStatus("Click the map to set a start point.");
}

function inSF([lon, lat]) {
  return lon >= SF_BBOX.minLon && lon <= SF_BBOX.maxLon &&
         lat >= SF_BBOX.minLat && lat <= SF_BBOX.maxLat;
}

// ---- Fetching routes -------------------------------------------------------

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

  showRoute(result.data);
  const lit = result.data.routes.lit;
  setStatus(
    (result.mock ? "Mock route (backend offline). " : "") +
    `Lit route: ${Math.round(lit.length_m)} m, ${Math.round(lit.lit_frac * 100)}% lit.`
  );
}

// Returns { data, mock }. Throws an Error with a human-readable message.
async function fetchRoute(from, to) {
  const params = new URLSearchParams({
    from_lon: from.lng, from_lat: from.lat,
    to_lon: to.lng, to_lat: to.lat,
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
  for (const url of MOCK_URLS) {
    const res = await fetch(url).catch(() => null);
    if (res?.ok) return res.json();
  }
  throw new Error("Backend offline and no mock route found.");
}

// ---- Drawing routes (placeholder lines; full rendering is todo #4) ---------

function addRouteLayers() {
  map.addSource("route-shortest", { type: "geojson", data: emptyFC() });
  map.addSource("route-lit", { type: "geojson", data: emptyFC() });
  // Below the stars so lamps stay visible on top of the route.
  map.addLayer({
    id: "route-shortest",
    type: "line",
    source: "route-shortest",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#8a93a8", "line-width": 3, "line-dasharray": [1, 2] },
  }, "lamp-halo");
  map.addLayer({
    id: "route-lit",
    type: "line",
    source: "route-lit",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#7fd4ff", "line-width": 4, "line-opacity": 0.85 },
  }, "lamp-halo");
}

function showRoute(data) {
  map.getSource("route-shortest").setData(data ? lineFC(data.routes.shortest.coords) : emptyFC());
  map.getSource("route-lit").setData(data ? lineFC(data.routes.lit.coords) : emptyFC());
}

function lineFC(coords) {
  return {
    type: "FeatureCollection",
    features: [{ type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: {} }],
  };
}

function emptyFC() {
  return { type: "FeatureCollection", features: [] };
}

function setStatus(text) {
  statusEl.textContent = text;
}
