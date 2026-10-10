"""HTTP API (Contract 4 in docs/CONTRACTS.md).

Usage:
    .venv/bin/python -m uvicorn --app-dir backend server:app --port 8000

The graph and lamp files are loaded once at startup (a few seconds), so
requests only do the route search. Restart after rebuilding graph.pkl or
regenerating dead_lamps.json.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routing import Router, RouteError
from scoring import DEFAULT_ALPHA, DEFAULT_BETA

log = logging.getLogger("uvicorn.error")
router: Router | None = None


@asynccontextmanager
async def lifespan(app):
    global router
    router = Router()
    log.info("loaded graph built %s (%s lamps, %s dead)", *(router.health()[k] for k in ("built_at", "lamps", "dead")))
    yield


app = FastAPI(title="Safe-Light Constellations", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def error(status, code, message):
    return JSONResponse({"error": code, "message": message}, status_code=status)


@app.exception_handler(RouteError)
async def route_error(request: Request, exc: RouteError):
    return error(exc.status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def bad_params(request: Request, exc: RequestValidationError):
    fields = sorted({str(e["loc"][-1]) for e in exc.errors()})
    return error(400, "bad_params", f"Missing or invalid: {', '.join(fields)}.")


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
