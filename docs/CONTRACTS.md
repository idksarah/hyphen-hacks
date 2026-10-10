# Safe-Light Constellations: Interface Contracts

Oct 10, 2026 · @sarah

## Ownership and conventions

Sarah owns everything behind the API: edge scoring, `graph.pkl`, route finding, and the FastAPI server. Claire owns `lamps.geojson`, `dead_lamps.json`, and the frontend. The API sits with Sarah because it is a thin Python wrapper over her routing code.

| Artifact | Producer | Consumers | Format |
|---|---|---|---|
| `lamps.geojson` | Claire | Sarah (edge scoring), frontend (star field) | GeoJSON file |
| `dead_lamps.json` | Claire | Sarah (outage-aware costs), frontend (dead stars) | JSON file |
| `graph.pkl` | Sarah | Sarah's API only | Python pickle |
| `GET /route`, `GET /health` | Sarah | Frontend | HTTP + JSON |

Repo layout:

```
data/lamps.geojson        # Claire
data/dead_lamps.json      # Claire
data/graph.pkl            # Sarah, gitignored, rebuilt by backend/build_graph.py
backend/                  # Sarah
frontend/                 # Claire
mocks/route_response.json # copy of the example in Contract 4
```

Conventions that apply to every contract:

- **Coordinates:** WGS84, always `[lon, lat]` (GeoJSON order) in files and JSON bodies. Query params use explicit names (`from_lon`, `from_lat`) so the order can't get swapped.
- **Units:** distances in meters, durations in minutes. Numeric field names carry the unit (`length_m`, `max_gap_m`).
- **Lamp IDs:** strings prefixed by source, `sfpuc:<assetnum>` or `osm:<node id>`. They must stay the same across regenerations, because routes and dead lamps refer to them.
- **Timestamps:** ISO 8601 in UTC.
- **Missing values:** `null`, never omitted keys.

## Contract 1: `lamps.geojson` (Claire → Sarah)

One GeoJSON FeatureCollection of Point features, one per physical lamp, already deduplicated (SFPUC and OSM lamps within 5 m of each other count as one; keep the SFPUC record).

| Property | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | `sfpuc:<assetnum>` or `osm:<node id>`; unique and stable |
| `source` | `"sfpuc"` \| `"osm"` | yes | Which dataset the record came from |

Example feature:

```json
{
  "type": "Feature",
  "geometry": { "type": "Point", "coordinates": [-122.4194, 37.7749] },
  "properties": { "id": "sfpuc:48213", "source": "sfpuc", "wattage": 85, "fixture": "led" }
}
```

Guarantees Claire provides:

- Every point lies inside SF's bounding box (lon −122.52 to −122.35, lat 37.70 to 37.84).
- No two features share an `id`.
- Extra properties are allowed; Sarah's code reads only `id` and geometry.

Until the real file exists, Sarah scores against a fake `lamps.geojson` with the same schema (random lamps placed along streets).

## Contract 2: `dead_lamps.json` (Claire → Sarah)

Open 311 streetlight-outage cases, each matched to the nearest lamp in `lamps.geojson` within 30 m. Cases with no lamp in range go in `unmatched` (the frontend can still draw them; routing ignores them).

```json
{
  "generated_at": "2026-10-10T18:00:00Z",
  "dead": [
    {
      "lamp_id": "sfpuc:48213",
      "case_id": "17790112",
      "opened": "2026-09-28T04:12:00Z",
      "match_dist_m": 11.4
    }
  ],
  "unmatched": [
    { "case_id": "17801455", "opened": "2026-10-02T22:40:00Z", "coord": [-122.4102, 37.7841] }
  ]
}
```

Rules:

- Every `lamp_id` must exist in `lamps.geojson`. If two cases match one lamp, keep one `dead` entry for that lamp.
- Only "light out"-type subtypes count; pole damage, painting, and similar cases are filtered out before matching.
- `case_id` is a string even though 311 IDs are numeric.
- Sarah's code reads only the set of `dead[].lamp_id` values. The API loads this file at startup, so a regenerated file needs a server restart.

## Contract 3: `graph.pkl` (Sarah only)

Claire never reads this file; it is listed so the API's inputs are pinned down. It is the osmnx walk graph for San Francisco, trimmed to its largest strongly connected component and projected to UTM zone 10N (EPSG:32610).

| Edge attribute | Type | Meaning |
|---|---|---|
| `length` | float, m | Edge length (osmnx default) |
| `lit_frac` | float, 0–1 | Share of 3 m samples within 20 m of a lamp |
| `max_gap` | float, m | Longest dark run inside the edge |
| `lead_dark`, `trail_dark` | float, m | Dark run at the edge's start and end, for merging gaps across intersections |
| `lamp_ids` | list[str] | Lamps within 20 m, ordered along the edge u→v |
| `cost` | float | Precomputed lit-route weight for default α and β: `length × (1 + α × (1 − lit_frac)) + β × max_gap²` |
| `*_outage` | same types | `lit_frac`, `max_gap`, `lead_dark`, `trail_dark`, `cost` recomputed with dead lamps removed |

Nodes keep osmnx's projected `x`, `y` (meters) plus `lon`, `lat` for output. `backend/requirements.txt` pins osmnx and networkx versions, since pickles break across versions.

## Contract 4: HTTP API (Sarah → frontend)

The server runs at `http://localhost:8000` with CORS open to all origins. Target latency is under 1 s per `/route` call.

### `GET /route`

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `from_lon`, `from_lat` | float | yes | — | Start point |
| `to_lon`, `to_lat` | float | yes | — | End point |
| `alpha` | float | no | 2.0 | Darkness penalty, 0–10 |
| `beta` | float | no | 0.005 | Long-gap penalty, 0–0.2 |
| `outages` | bool | no | false | Treat lamps in `dead_lamps.json` as off |

Example: `/route?from_lon=-122.4194&from_lat=37.7749&to_lon=-122.4108&to_lat=37.7596&outages=true`

Response 200 (coordinate arrays shortened):

```json
{
  "request": { "from": [-122.4194, 37.7749], "to": [-122.4108, 37.7596], "alpha": 2.0, "beta": 0.005, "outages": true },
  "snapped": { "from": [-122.41935, 37.77482], "to": [-122.41079, 37.75966] },
  "alpha_used": 2.0,
  "detour_capped": false,
  "same_route": false,
  "routes": {
    "shortest": {
      "coords": [[-122.41935, 37.77482], [-122.41890, 37.77401], [-122.41079, 37.75966]],
      "length_m": 1840.2,
      "minutes": 21.9,
      "lit_frac": 0.71,
      "max_gap_m": 148.0,
      "dark_gaps": [ { "length_m": 148.0, "coords": [[-122.4151, 37.7655], [-122.4146, 37.7642]] } ],
      "lamp_ids": ["sfpuc:48213", "osm:9912034"],
      "constellation": [ { "id": "sfpuc:48213", "coord": [-122.41930, 37.77470] } ]
    },
    "lit": {
      "coords": [[-122.41935, 37.77482], [-122.41702, 37.77311], [-122.41079, 37.75966]],
      "length_m": 2015.7,
      "minutes": 24.0,
      "lit_frac": 0.96,
      "max_gap_m": 18.0,
      "dark_gaps": [],
      "lamp_ids": ["sfpuc:48213", "sfpuc:48240", "osm:9912051"],
      "constellation": [
        { "id": "sfpuc:48213", "coord": [-122.41930, 37.77470] },
        { "id": "sfpuc:48240", "coord": [-122.41698, 37.77305] },
        { "id": "osm:9912051", "coord": [-122.41085, 37.75972] }
      ]
    }
  }
}
```

Route object fields:

| Field | Type | Meaning |
|---|---|---|
| `coords` | `[lon, lat][]` | Street geometry, start to end |
| `length_m` | float | Total length |
| `minutes` | float | `length_m` at 1.4 m/s |
| `lit_frac` | float, 0–1 | Length-weighted lit share |
| `max_gap_m` | float | Longest dark run, merged across intersections |
| `dark_gaps` | `{length_m, coords}[]` | Every dark run of 25 m or more, for the red gap overlay |
| `lamp_ids` | string[] | Lamps passed, in walking order, deduplicated |
| `constellation` | `{id, coord}[]` | 0–15 simplified lamp vertices in walking order; empty when the route passes fewer than 2 lamps, so the frontend falls back to `coords` |

Top-level flags:

- `alpha_used` is the α actually applied. It is lower than the requested value when the detour cap stepped it down.
- `detour_capped` is true when no route within 1.5× the shortest length met the requested α; the UI should say no well-lit route was found within reason.
- `same_route` is true when the lit and shortest routes are identical (both are still returned).

Errors return `{"error": "<code>", "message": "<human text>"}`:

| Status | `error` | When |
|---|---|---|
| 400 | `bad_params` | Missing or non-numeric params, or α/β out of range |
| 400 | `out_of_bounds` | A point is outside SF's bounding box |
| 400 | `same_point` | Start and end snap to the same node |
| 404 | `no_route` | No path exists between the snapped nodes |
| 500 | `internal` | Anything else |

### `GET /health`

Returns `{"ok": true, "lamps": 41230, "dead": 312, "built_at": "2026-10-10T18:00:00Z"}`, with the lamp count from the graph build, the dead count from the loaded file, and when `graph.pkl` was built. The frontend can use it to show a "backend offline" banner.

## Working async

Neither person waits on the other: each side builds against a stand-in until the real thing lands, then swaps it in without code changes.

Stand-ins:

- Frontend uses `mocks/route_response.json` (the Contract 4 example, with real-looking coords) until `/route` is live.
- Sarah uses fake lamps with the Contract 1 schema until `lamps.geojson` is real, and an empty dead list until `dead_lamps.json` exists.

Claire's lane:

1. `lamps.geojson` v0, SFPUC only (unblocks Sarah's real scoring)
2. Star field rendering from `lamps.geojson`
3. Click-to-pick start and end, wired to the mock response
4. Constellation and shortest-route rendering, stats card
5. `lamps.geojson` v1 with OSM merged and deduped
6. `dead_lamps.json` and the dead-star layer

Sarah's lane:

1. Commit `mocks/route_response.json` (unblocks the frontend)
2. Edge scoring and `graph.pkl` on fake lamps
3. Route finding and `/route` returning the full Contract 4 shape
4. Rebuild on real `lamps.geojson`; tune α and β
5. Outage-aware costs once `dead_lamps.json` exists

## Changing a contract

- Adding a field is fine without asking; the other side ignores what it doesn't use.
- Renaming, removing, or changing a field's type or units needs a message first and an edit to this doc in the same change.
- Regenerating `lamps.geojson` must keep existing IDs, or Sarah rebuilds `graph.pkl` and tells Claire.

## Change log

- 2026-10-10 (Claire): SFPUC lamp IDs use `assetnum` instead of `OBJECTID`, e.g. `sfpuc:A10P0001` (the numeric `sfpuc:48213` in the examples above is just illustrative). The source is the [SFPUC Streetlights layer](https://services.arcgis.com/Zs2aNLFN00jrS4gG/arcgis/rest/services/SFPUC_Streetlights/FeatureServer/0), where `objectid` is system-maintained and can be renumbered on republish; `assetnum` is SFPUC's asset tag (non-null and unique across all 24,802 lamps). Type is still string; Sarah's code reads `id` only.
- 2026-10-10 (Sarah): Defined the edge `cost` formula in Contract 3. A dark meter costs 1 + α times a lit one, and β × gap² penalizes long dark runs, so at the defaults (α = 3, β = 0.02) a 150 m gap adds the equivalent of 450 m of walking. The defaults may change after tuning on real lamps; the API params and their ranges don't.
- 2026-10-10 (Sarah): Default α is now 2.0 (was 3.0) and default β 0.005 (was 0.02), tuned on 120 random 0.8–3 km trips with real lamps (`backend/tune.py`). Past α = 2 the lit share barely improves (+0.25 → +0.27) while the median detour grows from 6% to 10%, and β mostly lengthens detours without removing long gaps. Ranges and types are unchanged. The frontend's slider default moves from 30 to 20 to match.
