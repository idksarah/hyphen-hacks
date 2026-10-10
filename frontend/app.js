// Safe-Light Constellations frontend. See docs/CONTRACTS.md for data shapes.

// Served from the repo root (python3 -m http.server), so data/ is one level up.
const LAMPS_URL = "../data/lamps.geojson";
const BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

// Contract bounding box: lon -122.52..-122.35, lat 37.70..37.84
const SF_BOUNDS = [[-122.52, 37.70], [-122.35, 37.84]];

const STAR_COLOR = "#ffd88a";

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
