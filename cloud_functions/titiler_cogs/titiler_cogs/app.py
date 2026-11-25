"""Private Lambda renderer: only registered COG tile requests are accepted."""

import base64
import logging
import os
import re
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from mangum import Mangum
from rio_tiler.errors import TileOutsideBounds
from titiler.core.errors import DEFAULT_STATUS_CODES
from titiler.core.factory import TilerFactory

logger = logging.getLogger(__name__)
logging.getLogger("mangum.http").setLevel(logging.CRITICAL)
MAX_TILE_BYTES = 2 * 1024**2
TILE_PATH = re.compile(r"/cog/tiles/WebMercatorQuad/([0-9]+)/([0-9]+)/([0-9]+)\.png")
ALLOWED_QUERY = {"url", "bidx", "resampling", "rescale", "colormap_name", "colormap"}


def registered_cog(url: str = Query(...)) -> str:
    bucket = os.environ["PUBLIC_SHARES_S3_BUCKET"]
    prefix = os.environ.get("PUBLIC_SHARES_S3_PREFIX", "public-shares")
    parsed = urlsplit(url)
    suffix = parsed.path.removeprefix(f"/{prefix}/")
    parts = suffix.split("/")
    valid = (
        parsed.scheme == "s3"
        and parsed.netloc == bucket
        and not parsed.query
        and not parsed.fragment
        and parsed.path.startswith(f"/{prefix}/")
        and len(parts) == 2
        and parts[1].endswith(".tif")
    )
    if valid:
        try:
            valid = (
                str(uuid.UUID(parts[0])) == parts[0]
                and str(uuid.UUID(parts[1][:-4])) == parts[1][:-4]
            )
        except ValueError:
            valid = False
    if not valid:
        logger.warning("Rejected unregistered raster source")
        raise HTTPException(403, "Raster source is not allowed")
    return url


app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
cog = TilerFactory(path_dependency=registered_cog)
app.include_router(cog.router, prefix="/cog")


@app.middleware("http")
async def tile_requests_only(request: Request, call_next):
    match = TILE_PATH.fullmatch(request.url.path)
    valid = request.method == "GET" and match is not None
    if valid:
        valid = all(len(value) <= 7 for value in match.groups())
    if valid:
        z, x, y = (int(value) for value in match.groups())
        valid = z <= 22 and x < 2**z and y < 2**z
    params = request.query_params
    if (
        not valid
        or set(params) - ALLOWED_QUERY
        or len(params.multi_items()) != len(params)
        or len(request.scope.get("query_string", b"")) > 8192
    ):
        logger.warning("Rejected unsupported renderer request")
        return JSONResponse({"detail": "Request is not allowed"}, status_code=400)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


async def renderer_error(request: Request, exc: Exception):
    if isinstance(exc, TileOutsideBounds):
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    logger.warning("Raster rendering failed (%s)", type(exc).__name__)
    return JSONResponse(
        {"detail": "Raster rendering failed"},
        status_code=DEFAULT_STATUS_CODES.get(type(exc), 502),
        headers={"Cache-Control": "no-store"},
    )


for exception_class in DEFAULT_STATUS_CODES:
    app.add_exception_handler(exception_class, renderer_error)
app.add_exception_handler(Exception, renderer_error)
adapter = Mangum(app, lifespan="off")


def handler(event: dict, context) -> dict:
    """Use the API Gateway v2 envelope only as a private Invoke payload."""
    if (
        event.get("version") != "2.0"
        or event.get("body")
        or len(event.get("rawQueryString", "")) > 8192
    ):
        logger.warning("Rejected unsupported Lambda payload")
        return {"statusCode": 400, "body": "Invalid renderer request"}
    response = adapter(event, context)
    if response["statusCode"] == 200:
        body = response.get("body", "")
        try:
            valid = (
                response.get("isBase64Encoded") is True
                and response.get("headers", {}).get("content-type") == "image/png"
                and len(body) <= 4 * ((MAX_TILE_BYTES + 2) // 3)
            )
            if valid:
                tile = base64.b64decode(body, validate=True)
                valid = len(tile) <= MAX_TILE_BYTES and tile.startswith(
                    b"\x89PNG\r\n\x1a\n"
                )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            logger.error("Renderer produced an invalid or oversized tile")
            return {"statusCode": 502, "body": "Invalid renderer response"}
    return response
