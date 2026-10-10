# Safe-Light Constellations

Walking routes through San Francisco that favor lit streets. Lamps render as a star field; each route is drawn as a constellation of the lamps it passes.

**The interface contracts live in `docs/CONTRACTS.md`. It is the source of truth; read it before touching any file format or the API, and don't duplicate it here.**

## Ownership

- Sarah: everything behind the API (`backend/`, edge scoring, `data/graph.pkl`, `GET /route`, `GET /health`) and `mocks/route_response.json`.
- Claire: `data/lamps.geojson`, `data/dead_lamps.json`, `data/build_lamps.py`, `frontend/`.

Contract changes follow the rules in `docs/CONTRACTS.md` ("Changing a contract"): adding fields is fine, renames/removals/type or unit changes need a message to the other side and an edit to that doc in the same change.

## Notes

- Python env: `.venv/` (osmnx 2.1.1, networkx 3.7). Pin these in `backend/requirements.txt`, since `graph.pkl` breaks across versions.
- osmnx caches Overpass/Nominatim downloads in `cache/` relative to the working directory (`tests/cache/` so far); gitignored.
- SFPUC lamp IDs are `sfpuc:<assetnum>` (e.g. `sfpuc:A10P0001`), not OBJECTID.
- `mocks/route_response.json` uses real streets and real lamps from `data/lamps.geojson`; regenerate it with `.venv/bin/python backend/make_mock.py` if the route shape or lamp IDs change.

## Datasets

- Lights: SFPUC streetlights (https://services.arcgis.com/Zs2aNLFN00jrS4gG/arcgis/rest/services/SFPUC_Streetlights/FeatureServer/0) + OpenStreetMap `street_lamp` nodes
- Broken streetlights (311): https://data.sf.gov/d/awbb-gcku, or https://data.sf.gov/resource/vw6y-z8j6.json?$where=service_name='Streetlights' AND status_description='Open'

With extra time:

- Foot traffic
- Street closures: https://data.sf.gov/browse?q=Temporary+Street+Closures+Map
- Crime (2018–present): https://data.sf.gov/Public-Safety/SF-Crime-Heat-Map/q6gg-sa2p/data
- Location search: Nominatim
