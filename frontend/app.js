// Polaris (Safe-Light Constellations) frontend. See docs/CONTRACTS.md for data
// shapes; visual design follows Sarah's mockup in mocks/index.html.

// Served from the repo root (python3 -m http.server), so data/ is one level up.
const LAMPS_URL = "../data/lamps.geojson";
const DEAD_URL = "../data/dead_lamps.json"; // optional; skipped if missing
const BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

// Contract 4: Sarah's routing server (backend/server.py → backend/routing.py).
const API_URL = "http://localhost:8000";

// Address search. Nominatim's usage policy: at most 1 request per second and no
// search-as-you-type, so we only search when the user presses Enter.
const GEOCODE_URL = "https://nominatim.openstreetmap.org/search";

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

// Start downloading data now, in parallel with the basemap, instead of waiting
// for its "load" event. index.html preloads the same URLs, so these reuse that.
const lampsPromise = fetch(LAMPS_URL).then((r) => {
  if (!r.ok) throw new Error(`lamps.geojson: HTTP ${r.status}`);
  return r.json();
});
lampsPromise.catch(() => {}); // handled when awaited below; avoid an early unhandled-rejection warning
const deadPromise = fetch(DEAD_URL).then((r) => (r.ok ? r.json() : null)).catch(() => null);

map.on("load", async () => {
  // Compact attribution starts expanded; collapse it to the ⓘ button (tap to open).
  document.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
  recolorBasemap();
  map.addImage("spark", sparkImage(), { pixelRatio: 2 });

  const [lamps, dead] = await Promise.all([lampsPromise, deadPromise]);

  // Contract 2: only the set of dead[].lamp_id matters for marking lamps.
  const deadIds = new Set(dead?.dead.map((d) => d.lamp_id) ?? []);
  for (const f of lamps.features) f.properties.dead = deadIds.has(f.properties.id);

  namedLamps = lamps.features.filter((f) => f.properties.street);
  for (const f of namedLamps) lampStreet.set(f.properties.id, titleCase(f.properties.street));

  map.addSource("lamps", { type: "geojson", data: lamps });
  map.addSource("unmatched-outages", { type: "geojson", data: unmatchedFC(dead) });
  addStarLayers();
  addOutageLayers();
  addRouteLayers();

  map.on("click", onMapClick);
  for (const name of ROUTES) {
    map.on("mouseenter", `${name}-ghost`, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", `${name}-ghost`, () => { map.getCanvas().style.cursor = ""; });
  }
  document.getElementById("add-stop").addEventListener("click", () => addStop());
  document.getElementById("swap").addEventListener("click", () => {
    stops.reverse();
    renderStops();
    updateRoute();
  });
  document.getElementById("clear").addEventListener("click", clearStops);
  outagesBox.addEventListener("change", updateRoute);
  alphaSlider.addEventListener("change", updateRoute);
  for (const card of document.querySelectorAll(".route-card")) {
    card.addEventListener("click", () => selectRoute(card.dataset.route));
  }
  renderStops();
  setStatus(HINT);
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

// ---- Stops ------------------------------------------------------------------------
// An ordered list of stops, like Google Maps directions. The route goes through
// every stop that has a point, in order; each consecutive pair is one /route leg.

const MAX_STOPS = 10;
const HINT = "Search for a place or tap the map.";

let nextStopId = 1;
let stops = [newStop(), newStop()];
let namedLamps = []; // lamps with a street name, for labelling picked points
const lampStreet = new Map(); // lamp id → street name, for "via …"
let routeRequest = 0; // ignore responses from superseded requests
let dragFrom = null; // index of the stop row being dragged

function newStop() {
  return { id: nextStopId++, lonLat: null, label: "", marker: null };
}

const letter = (i) => String.fromCharCode(65 + i); // A, B, C…

function placeholder(i) {
  if (i === 0) return "Choose starting point, or tap the map";
  if (i === stops.length - 1) return "Choose destination";
  return "Choose stop";
}

function renderStops() {
  const list = document.getElementById("stops");
  list.replaceChildren(...stops.map(stopRow));
  document.getElementById("swap").hidden = stops.length !== 2;
  document.getElementById("add-stop").hidden = stops.length >= MAX_STOPS;
  updateMarkers();
}

function stopRow(stop, i) {
  const li = document.createElement("li");
  li.className = "stop";
  li.dataset.id = stop.id;

  const handle = document.createElement("span");
  handle.className = `stop-icon ${stopKind(i)}`;
  handle.textContent = letter(i);
  handle.draggable = true;
  handle.title = "Drag to reorder";

  const form = document.createElement("form");
  form.className = "stop-form";
  form.setAttribute("role", "search");
  const input = document.createElement("input");
  Object.assign(input, {
    type: "search", value: stop.label, placeholder: placeholder(i), autocomplete: "off",
  });
  input.setAttribute("enterkeyhint", "search");
  input.setAttribute("aria-label", `Stop ${letter(i)}`);
  form.append(input);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    search(stop, input.value.trim());
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") showResults(stop, null);
  });

  // Only the starting point offers "Your location", like Google Maps.
  if (i === 0) {
    input.addEventListener("focus", () => showLocationOption(stop));
    input.addEventListener("input", () => {
      if (isShowingLocationOption(stop)) showResults(stop, null);
    });
    input.addEventListener("blur", () => {
      if (isShowingLocationOption(stop)) showResults(stop, null);
    });
  }

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "stop-remove";
  remove.setAttribute("aria-label", `Remove stop ${letter(i)}`);
  remove.textContent = "✕";
  remove.hidden = stops.length <= 2;
  remove.addEventListener("click", () => removeStop(stop.id));

  const results = document.createElement("ul");
  results.className = "results";
  results.hidden = true;

  li.append(handle, form, remove, results);

  // Drag the letter to reorder.
  handle.addEventListener("dragstart", (e) => {
    dragFrom = i;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setDragImage(li, 16, 16);
    li.classList.add("dragging");
  });
  handle.addEventListener("dragend", () => li.classList.remove("dragging"));
  li.addEventListener("dragover", (e) => {
    if (dragFrom === null) return;
    e.preventDefault();
    li.classList.add("drop-target");
  });
  li.addEventListener("dragleave", () => li.classList.remove("drop-target"));
  li.addEventListener("drop", (e) => {
    e.preventDefault();
    li.classList.remove("drop-target");
    if (dragFrom !== null && dragFrom !== i) {
      const [moved] = stops.splice(dragFrom, 1);
      stops.splice(i, 0, moved);
      renderStops();
      updateRoute();
    }
    dragFrom = null;
  });
  return li;
}

function stopKind(i) {
  return i === 0 ? "first" : i === stops.length - 1 ? "last" : "middle";
}

function stopRowEl(stop) {
  return document.querySelector(`.stop[data-id="${stop.id}"]`);
}

function addStop(lonLat) {
  if (stops.length >= MAX_STOPS) {
    setStatus(`You can add up to ${MAX_STOPS} stops.`);
    return null;
  }
  const stop = newStop();
  stops.push(stop);
  renderStops();
  if (lonLat) setStopPoint(stop, lonLat);
  else stopRowEl(stop).querySelector("input").focus();
  return stop;
}

function removeStop(id) {
  const i = stops.findIndex((s) => s.id === id);
  stops[i].marker?.remove();
  stops.splice(i, 1);
  renderStops();
  updateRoute();
}

function clearStops() {
  for (const s of stops) s.marker?.remove();
  stops = [newStop(), newStop()];
  renderStops();
  updateRoute();
}

// Map taps fill the first empty stop, or add a new final stop once all are set.
// A tap on the other route's line switches to that route instead.
function onMapClick(e) {
  const box = [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]];
  const hit = map.queryRenderedFeatures(box, { layers: ROUTES.map((n) => `${n}-ghost`) })[0];
  if (hit) return selectRoute(hit.layer.id.replace("-ghost", ""));

  const lonLat = [e.lngLat.lng, e.lngLat.lat];
  if (!inSF(lonLat)) return setStatus("That's outside San Francisco.");
  const empty = stops.find((s) => !s.lonLat);
  if (empty) setStopPoint(empty, lonLat);
  else addStop(lonLat);
}

// Place (or move) a stop's marker. `label` comes from address search; map taps
// and drags label the point with the nearest street instead.
function setStopPoint(stop, lonLat, label) {
  stop.lonLat = lonLat;
  stop.label = label ?? nearLabel(lonLat);
  if (stop.marker) stop.marker.setLngLat(lonLat);
  else stop.marker = makeMarker(stop);
  const input = stopRowEl(stop)?.querySelector("input");
  if (input) input.value = stop.label;
  updateMarkers();
  updateRoute();
}

function makeMarker(stop) {
  const el = document.createElement("div");
  // Taps on a marker shouldn't also count as a map tap.
  el.addEventListener("click", (e) => e.stopPropagation());
  const marker = new maplibregl.Marker({ element: el, draggable: true })
    .setLngLat(stop.lonLat)
    .addTo(map);
  marker.on("dragend", () => {
    const { lng, lat } = marker.getLngLat();
    if (!inSF([lng, lat])) {
      marker.setLngLat(stop.lonLat);
      return setStatus("That's outside San Francisco.");
    }
    setStopPoint(stop, [lng, lat]);
  });
  return marker;
}

// Letters and colors follow list order, so refresh them after any reorder.
// Only touch our own classes: MapLibre's marker classes position the pin at
// its lon/lat, and replacing className drops them (pins then stack on screen).
function updateMarkers() {
  stops.forEach((s, i) => {
    if (!s.marker) return;
    const el = s.marker.getElement();
    el.classList.remove("first", "middle", "last");
    el.classList.add("stop-marker", stopKind(i));
    el.textContent = letter(i);
    el.title = s.label;
  });
}

function nearLabel(lonLat) {
  const street = nearestStreet(lonLat);
  return street ? `Near ${street}` : "Dropped pin";
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
  return best && titleCase(best);
}

function inSF([lon, lat]) {
  return lon >= SF_BBOX.minLon && lon <= SF_BBOX.maxLon &&
         lat >= SF_BBOX.minLat && lat <= SF_BBOX.maxLat;
}

// ---- Current location ------------------------------------------------------------

function showLocationOption(stop) {
  const list = stopRowEl(stop)?.querySelector(".results");
  if (!list || (!list.hidden && list.dataset.mode !== "location")) return; // search results win
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "results-location";
  btn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
    <circle cx="12" cy="12" r="4" fill="currentColor"/>
    <circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2"/>
    <path d="M12 1v3M12 20v3M1 12h3M20 12h3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
  btn.append("Your location");
  // Keep focus in the input so its blur handler doesn't hide this before the click lands.
  btn.addEventListener("pointerdown", (e) => e.preventDefault());
  btn.addEventListener("click", () => useCurrentLocation(stop));
  const li = document.createElement("li");
  li.append(btn);
  list.replaceChildren(li);
  list.dataset.mode = "location";
  list.hidden = false;
}

function isShowingLocationOption(stop) {
  const list = stopRowEl(stop)?.querySelector(".results");
  return list && !list.hidden && list.dataset.mode === "location";
}

function useCurrentLocation(stop) {
  showResults(stop, null);
  stopRowEl(stop)?.querySelector("input")?.blur();
  if (!("geolocation" in navigator)) return setStatus("This browser can't share your location.");

  setStatus("Finding your location…");
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      const lonLat = [coords.longitude, coords.latitude];
      if (!inSF(lonLat)) {
        return setStatus("You're outside San Francisco, so we can't route from here. Search for a start instead.");
      }
      setStopPoint(stop, lonLat, "Your location");
      if (stops.filter((s) => s.lonLat).length < 2) {
        map.flyTo({ center: lonLat, zoom: Math.max(map.getZoom(), 15) });
      }
    },
    (err) => {
      setStatus(err.code === err.PERMISSION_DENIED
        ? "Location access is off. Allow it in your browser, or search for a start instead."
        : "Couldn't find your location. Search for a start or tap the map instead.");
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
  );
}

// ---- Address search (Nominatim) -------------------------------------------------

let nextGeocodeAt = 0; // Nominatim allows one request per second

async function search(stop, q) {
  if (!q || q.startsWith("Near ") || q === "Dropped pin") return;
  showResults(stop, "Searching…");
  try {
    showResults(stop, await geocode(q));
  } catch {
    showResults(stop, "Search isn't working right now. Tap the map instead.");
  }
}

async function geocode(q) {
  const wait = nextGeocodeAt - Date.now();
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  nextGeocodeAt = Date.now() + 1000;

  const params = new URLSearchParams({
    q,
    format: "jsonv2",
    addressdetails: 1,
    limit: 5,
    // Only places inside the contract's SF bounding box.
    viewbox: `${SF_BBOX.minLon},${SF_BBOX.maxLat},${SF_BBOX.maxLon},${SF_BBOX.minLat}`,
    bounded: 1,
  });
  const res = await fetch(`${GEOCODE_URL}?${params}`, { headers: { "Accept-Language": "en" } });
  if (!res.ok) throw new Error(`Nominatim: HTTP ${res.status}`);
  return (await res.json()).map((r) => ({
    lonLat: [Number(r.lon), Number(r.lat)],
    ...placeLabel(r),
  }));
}

// "2000 Mission St" + "Mission District" instead of Nominatim's long display_name.
function placeLabel(r) {
  const a = r.address ?? {};
  const street = [a.house_number, a.road].filter(Boolean).join(" ");
  const title = r.name || street || r.display_name.split(",")[0];
  const area = a.neighbourhood || a.suburb || a.quarter || "San Francisco";
  return { title, sub: title === street ? area : street || area };
}

// results: null hides the list, a string shows a message, an array shows matches.
function showResults(stop, results) {
  const list = stopRowEl(stop)?.querySelector(".results");
  if (!list) return;
  delete list.dataset.mode;
  list.hidden = results === null;
  if (results === null) return list.replaceChildren();

  if (typeof results === "string" || results.length === 0) {
    const li = document.createElement("li");
    li.className = "results-msg";
    li.textContent = typeof results === "string" ? results : "Nothing found in San Francisco.";
    return list.replaceChildren(li);
  }
  list.replaceChildren(...results.map((r) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.append(r.title);
    if (r.sub) btn.append(Object.assign(document.createElement("span"), { textContent: r.sub }));
    btn.addEventListener("click", () => {
      showResults(stop, null);
      setStopPoint(stop, r.lonLat, r.title);
      if (stops.filter((s) => s.lonLat).length < 2) {
        map.flyTo({ center: r.lonLat, zoom: Math.max(map.getZoom(), 15) });
      }
    });
    li.append(btn);
    return li;
  }));
}

// ---- Fetching routes ------------------------------------------------------------

// Route through every stop that has a point: one /route call per leg, joined.
async function updateRoute() {
  const placed = stops.map((s, i) => ({ s, i })).filter(({ s }) => s.lonLat);
  if (placed.length < 2) {
    routeRequest++;
    showRoute(null);
    setStatus(placed.length ? "Now choose where you're going." : HINT);
    return;
  }

  const id = ++routeRequest;
  setStatus("Finding a route…");
  let legs;
  try {
    legs = await Promise.all(placed.slice(1).map(async (b, k) => {
      const a = placed[k];
      try {
        return await fetchRoute(a.s.lonLat, b.s.lonLat);
      } catch (err) {
        // Name the leg when there's more than one, e.g. "B to C: …".
        if (placed.length > 2) err.message = `${letter(a.i)} to ${letter(b.i)}: ${err.message}`;
        throw err;
      }
    }));
  } catch (err) {
    if (id !== routeRequest) return;
    showRoute(null);
    setStatus(err.message);
    return;
  }
  if (id !== routeRequest) return;

  const data = joinLegs(legs);
  setStatus("");
  showRoute(data);
  showStats(data);
  fitRoutes(data);
}

// Returns the Contract 4 response. Throws an Error with a human-readable message.
async function fetchRoute([fromLon, fromLat], [toLon, toLat]) {
  const params = new URLSearchParams({
    from_lon: fromLon, from_lat: fromLat,
    to_lon: toLon, to_lat: toLat,
    alpha: (alphaSlider.value / 10).toFixed(1), // slider 0–100 → contract alpha 0–10
    outages: outagesBox.checked,
  });

  let res;
  try {
    res = await fetch(`${API_URL}/route?${params}`);
  } catch {
    throw new Error("Can't reach the routing server. Is it running on port 8000?");
  }

  const body = await res.json().catch(() => null);
  if (res.ok && body) return body;
  // Contract 4 errors: {"error": "<code>", "message": "<human text>"}
  throw new Error(body?.message || `Routing failed (HTTP ${res.status}).`);
}

// Join per-leg responses into one response of the same shape. Lengths and times
// add up, lit share is length-weighted, and the longest gap is the longest of any
// leg (gaps aren't merged across stops, since you pause there anyway).
function joinLegs(legs) {
  if (legs.length === 1) return legs[0];
  const join = (name) => {
    const rs = legs.map((l) => l.routes[name]);
    const length = rs.reduce((t, r) => t + r.length_m, 0);
    const seen = new Set();
    return {
      coords: rs.flatMap((r, k) => (k ? r.coords.slice(1) : r.coords)),
      length_m: length,
      minutes: rs.reduce((t, r) => t + r.minutes, 0),
      lit_frac: rs.reduce((t, r) => t + r.lit_frac * r.length_m, 0) / length,
      max_gap_m: Math.max(...rs.map((r) => r.max_gap_m)),
      dark_gaps: rs.flatMap((r) => r.dark_gaps),
      lamp_ids: [...new Set(rs.flatMap((r) => r.lamp_ids))],
      constellation: rs.flatMap((r) => r.constellation).filter((c) => !seen.has(c.id) && seen.add(c.id)),
    };
  };
  return {
    request: legs[0].request,
    snapped: { from: legs[0].snapped.from, to: legs.at(-1).snapped.to },
    alpha_used: Math.min(...legs.map((l) => l.alpha_used)),
    detour_capped: legs.some((l) => l.detour_capped),
    same_route: legs.every((l) => l.same_route),
    routes: { shortest: join("shortest"), lit: join("lit") },
  };
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

function showStats(data) {
  for (const name of ROUTES) {
    const r = data.routes[name];
    const card = document.querySelector(`.route-card[data-route="${name}"]`);
    card.querySelector('[data-stat="minutes"]').textContent = `${Math.round(r.minutes)} min`;
    card.querySelector('[data-stat="tag"]').textContent =
      `${formatDistance(r.length_m)} · ${Math.round(r.lit_frac * 100)}% lit`;
    const via = viaStreet(r.lamp_ids);
    card.querySelector('[data-stat="via"]').textContent = via ? `via ${via}` : "";
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

// The street most of the route's lamps are on, like Google's "via Mission St".
function viaStreet(lampIds) {
  const counts = new Map();
  for (const id of lampIds) {
    const street = lampStreet.get(id);
    if (street) counts.set(street, (counts.get(street) ?? 0) + 1);
  }
  return [...counts].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
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

function titleCase(text) {
  return text.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

function setStatus(text) {
  statusEl.textContent = text;
}
