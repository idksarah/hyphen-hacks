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
  showStats(result.data, result.mock);
  setStatus("");
  fitRoutes(result.data); // after the stats card has grown to its final size
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

// ---- Drawing routes ----------------------------------------------------------

const ROUTE_COLORS = {
  lit: "#7fd4ff",
  shortest: "#8a93a8",
  gap: "#ff4d5e",
  constellation: "#fff1c9",
};

function addRouteLayers() {
  for (const id of ["route-shortest", "route-lit", "dark-gaps", "constellation"]) {
    map.addSource(id, { type: "geojson", data: emptyFC() });
  }

  // Street paths and gaps sit below the lamp stars...
  map.addLayer({
    id: "route-shortest",
    type: "line",
    source: "route-shortest",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ROUTE_COLORS.shortest, "line-width": 3, "line-dasharray": [1, 2] },
  }, "lamp-halo");
  map.addLayer({
    id: "route-lit",
    type: "line",
    source: "route-lit",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ROUTE_COLORS.lit, "line-width": 5, "line-opacity": 0.35 },
  }, "lamp-halo");
  map.addLayer({
    id: "dark-gaps",
    type: "line",
    source: "dark-gaps",
    layout: { "line-cap": "round" },
    paint: { "line-color": ROUTE_COLORS.gap, "line-width": 5, "line-opacity": 0.85 },
  }, "lamp-halo");

  // ...and the constellation sits on top of them.
  map.addLayer({
    id: "constellation-lines",
    type: "line",
    source: "constellation",
    filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": ROUTE_COLORS.constellation, "line-width": 1.2, "line-opacity": 0.8 },
  });
  map.addLayer({
    id: "constellation-glow",
    type: "circle",
    source: "constellation",
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-color": STAR_COLOR, "circle-radius": 11, "circle-blur": 1, "circle-opacity": 0.7 },
  });
  map.addLayer({
    id: "constellation-stars",
    type: "circle",
    source: "constellation",
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-color": "#ffffff", "circle-radius": 3.5 },
  });
}

function showRoute(data) {
  if (!data) {
    for (const id of ["route-shortest", "route-lit", "dark-gaps", "constellation"]) {
      map.getSource(id).setData(emptyFC());
    }
    document.getElementById("stats").hidden = true;
    return;
  }

  const { lit, shortest } = data.routes;
  map.getSource("route-shortest").setData(lineFC(shortest.coords));
  map.getSource("route-lit").setData(lineFC(lit.coords));
  map.getSource("dark-gaps").setData({
    type: "FeatureCollection",
    features: [...lit.dark_gaps, ...shortest.dark_gaps].map((g) => lineFeature(g.coords)),
  });
  map.getSource("constellation").setData(constellationFC(lit));
}

// Zoom to both routes, keeping them clear of the card in the top-left.
function fitRoutes(data) {
  const bounds = new maplibregl.LngLatBounds();
  for (const c of [...data.routes.lit.coords, ...data.routes.shortest.coords]) bounds.extend(c);
  const card = document.getElementById("card").getBoundingClientRect();
  map.fitBounds(bounds, {
    padding: { top: card.bottom + 24, bottom: 48, left: 48, right: 48 },
    maxZoom: 16,
  });
}

// Lamp vertices joined in walking order. Contract: constellation is empty when
// the route passes fewer than 2 lamps, so fall back to the street geometry.
function constellationFC(route) {
  const pts = route.constellation.map((s) => s.coord);
  const line = pts.length >= 2 ? pts : route.coords;
  return {
    type: "FeatureCollection",
    features: [
      lineFeature(line),
      ...route.constellation.map((s) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: s.coord },
        properties: { id: s.id },
      })),
    ],
  };
}

// ---- Stats card -------------------------------------------------------------

function showStats(data, mock) {
  for (const name of ["lit", "shortest"]) {
    const r = data.routes[name];
    setStat(name, "minutes", `${Math.round(r.minutes)} min`);
    setStat(name, "length", formatDistance(r.length_m));
    setStat(name, "lit", `${Math.round(r.lit_frac * 100)}%`);
    setStat(name, "gap", r.max_gap_m >= 1 ? `${Math.round(r.max_gap_m)} m` : "none");
  }

  const notes = [];
  if (mock) notes.push("Showing a sample route: the routing server is offline.");
  if (data.detour_capped) notes.push("No well-lit route found within a reasonable detour; showing the best compromise.");
  if (data.same_route) notes.push("The shortest route is already the best-lit one.");
  else {
    const extra = Math.round(data.routes.lit.minutes - data.routes.shortest.minutes);
    if (extra > 0) notes.push(`The lit route adds about ${extra} min.`);
  }
  if (data.alpha_used < data.request.alpha) {
    notes.push(`Lighting preference relaxed from ${data.request.alpha} to ${data.alpha_used} to keep the detour reasonable.`);
  }
  document.getElementById("notes").replaceChildren(
    ...notes.map((t) => Object.assign(document.createElement("li"), { textContent: t }))
  );
  document.getElementById("stats").hidden = false;
}

function setStat(route, stat, text) {
  document.querySelector(`[data-route="${route}"][data-stat="${stat}"]`).textContent = text;
}

function formatDistance(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}

// ---- Helpers ------------------------------------------------------------------

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
