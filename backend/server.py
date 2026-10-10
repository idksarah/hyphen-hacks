"""HTTP API (Contracts 4 and 5 in docs/CONTRACTS.md), plus the frontend.

Usage:
    .venv/bin/python -m uvicorn --app-dir backend server:app --host 0.0.0.0 --port 8000

Then open http://localhost:8000/. The server also serves frontend/, mocks/ and
the two lamp files, so the whole app runs on one port (handy for a tunnel
when testing GPS and share links on a phone).

The graph and lamp files are loaded once at startup (a few seconds), so
requests only do the route search. Restart after rebuilding graph.pkl or
regenerating dead_lamps.json. Shared walks live in memory and are lost on
restart.
"""
import logging
import math
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from routing import REPO, SF_BBOX, Router, RouteError
from scoring import DEFAULT_ALPHA, DEFAULT_BETA
from shares import ShareStore

log = logging.getLogger("uvicorn.error")
router: Router | None = None
shares = ShareStore()


@asynccontextmanager
async def lifespan(app):
    global router
    router = Router()
    log.info("loaded graph built %s (%s lamps, %s dead)", *(router.health()[k] for k in ("built_at", "lamps", "dead")))
    yield


app = FastAPI(title="Safe-Light Constellations", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


def error(status, code, message):
    return JSONResponse({"error": code, "message": message}, status_code=status)


@app.exception_handler(RouteError)
async def route_error(request: Request, exc: RouteError):
    return error(exc.status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def bad_params(request: Request, exc: RequestValidationError):
    fields = sorted({".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]) for e in exc.errors()})
    return error(400, "bad_params", f"Missing or invalid: {', '.join(fields)}.")


# --- Contract 4: routing --------------------------------------------------------

@app.get("/health")
def health():
    return router.health()


@app.get("/route")
def route(
    from_lon: float, from_lat: float, to_lon: float, to_lat: float,
    alpha: float = DEFAULT_ALPHA, beta: float = DEFAULT_BETA, outages: bool = False,
):
    try:
        return router.route(from_lon, from_lat, to_lon, to_lat, alpha=alpha, beta=beta, outages=outages)
    except RouteError:
        raise
    except Exception:
        # Handled here rather than in an app-level handler so CORS headers stay on the 500.
        log.exception("unhandled error on /route")
        return error(500, "internal", "Something went wrong on the routing server.")


# --- Contract 5: live walk sharing ------------------------------------------------

Coord = tuple[float, float]


def check_coord(c):
    lon, lat = c
    if not (math.isfinite(lon) and math.isfinite(lat)
            and SF_BBOX[0] <= lon <= SF_BBOX[2] and SF_BBOX[1] <= lat <= SF_BBOX[3]):
        raise ValueError("coordinate outside San Francisco")
    return c


class DarkGap(BaseModel):
    length_m: float = Field(ge=0)
    coords: list[Coord] = Field(min_length=2, max_length=5000)


class Star(BaseModel):
    id: str = Field(max_length=64)
    coord: Coord


class SharedRoute(BaseModel):
    """A Contract 4 route object, as the walker received it from /route."""
    coords: list[Coord] = Field(min_length=2, max_length=20000)
    length_m: float = Field(gt=0, lt=100_000)
    minutes: float = Field(ge=0)
    lit_frac: float = Field(ge=0, le=1)
    max_gap_m: float = Field(ge=0)
    dark_gaps: list[DarkGap] = Field(max_length=2000)
    lamp_ids: list[str] = Field(max_length=20000)
    constellation: list[Star] = Field(max_length=15)

    @field_validator("coords")
    @classmethod
    def in_sf(cls, v):
        for c in v:
            check_coord(c)
        return v


class ShareCreate(BaseModel):
    route: SharedRoute
    route_name: Literal["lit", "shortest"]
    from_label: str | None = Field(default=None, max_length=80)
    to_label: str | None = Field(default=None, max_length=80)


class LocationUpdate(BaseModel):
    token: str = Field(max_length=64)
    coord: Coord
    accuracy_m: float | None = Field(default=None, ge=0, lt=100_000)
    progress_m: float = Field(ge=0)

    @field_validator("coord")
    @classmethod
    def in_sf(cls, v):
        return check_coord(v)


class RouteUpdate(BaseModel):
    token: str = Field(max_length=64)
    route: SharedRoute
    route_name: Literal["lit", "shortest"]


class ShareEnd(BaseModel):
    token: str = Field(max_length=64)
    arrived: bool = False


@app.post("/shares", status_code=201)
def create_share(body: ShareCreate):
    return shares.create(body.route.model_dump(), body.route_name, body.from_label, body.to_label)


@app.post("/shares/{share_id}/location")
def update_share(share_id: str, body: LocationUpdate):
    shares.update(share_id, body.token, body.coord, body.accuracy_m, body.progress_m)
    return {"ok": True}


@app.post("/shares/{share_id}/route")
def reroute_share(share_id: str, body: RouteUpdate):
    shares.set_route(share_id, body.token, body.route.model_dump(), body.route_name)
    return {"ok": True}


@app.post("/shares/{share_id}/end")
def end_share(share_id: str, body: ShareEnd):
    shares.end(share_id, body.token, body.arrived)
    return {"ok": True}


@app.get("/shares/{share_id}")
def get_share(share_id: str):
    return shares.get(share_id)


# --- Frontend --------------------------------------------------------------------
# Only these paths are served; the rest of the repo (graph.pkl, .git, ...) is not.

@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/frontend/")


@app.get("/data/{name}", include_in_schema=False)
def data_file(name: Literal["lamps.geojson", "dead_lamps.json"]):
    path = REPO / "data" / name
    if not path.exists():
        return error(404, "not_found", f"{name} hasn't been built.")
    return FileResponse(path, media_type="application/json")


app.mount("/frontend", StaticFiles(directory=REPO / "frontend", html=True), name="frontend")
app.mount("/mocks", StaticFiles(directory=REPO / "mocks"), name="mocks")
