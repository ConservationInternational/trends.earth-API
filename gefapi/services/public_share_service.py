"""Storage, lifecycle, and validation for public web-map shares."""

import base64
from contextlib import closing
from datetime import UTC, datetime, timedelta
from email.message import Message
import hashlib
import hmac
from io import BytesIO
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
import uuid

import boto3
import botocore
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from shapely import get_num_coordinates
from shapely.affinity import translate
from shapely.errors import GEOSException
from shapely.geometry import Polygon, box, mapping
from shapely.ops import unary_union

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import PublicShare, PublicShareLayer, User

SHARE_SECRET_BYTES = 32
PUBLIC_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
VECTOR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,119}$")
MAX_LEGEND_LABEL = 200
MAX_MASK_BYTES = 512 * 1024
MAX_MASK_COORDINATES = 30000
COLORMAP_LIMIT = 3.4e38
ALLOWED_PALETTES = frozenset(
    {"viridis", "plasma", "magma", "inferno", "cividis", "turbo"}
)
ACTIVE_STATES = ("draft", "uploading", "validating", "ready", "failed")
LAMBDA_ARN_RE = re.compile(
    r"^arn:(aws|aws-us-gov|aws-cn):lambda:([a-z0-9-]+):[0-9]{12}:"
    r"function:[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)?$"
)
logger = logging.getLogger(__name__)


class PublicShareError(Exception):
    """An expected public-share operation failure with an HTTP status."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


def _config():
    return SETTINGS["PUBLIC_SHARES"]


def ensure_public_shares_enabled():
    """Fail closed unless the complete public-share deployment is configured."""
    config = _config()
    required = (
        config.get("S3_BUCKET"),
        config.get("VIEWER_BASE_URL"),
        config.get("PUBLIC_ORIGIN"),
        config.get("TITILER_LAMBDA_ARN") or config.get("TITILER_INTERNAL_URL"),
        config.get("SESSION_SECRET"),
    )
    if not config.get("ENABLED") or not all(required):
        raise PublicShareError(503, "Public web-map sharing is not enabled")
    origin = urlsplit(config["PUBLIC_ORIGIN"])
    viewer = urlsplit(config["VIEWER_BASE_URL"])
    lambda_arn = config.get("TITILER_LAMBDA_ARN")
    tiler = urlsplit(config.get("TITILER_INTERNAL_URL") or "")
    if lambda_arn and not LAMBDA_ARN_RE.fullmatch(lambda_arn):
        raise PublicShareError(503, "Public web-map Lambda is not configured safely")
    if (
        origin.scheme != "https"
        or not origin.netloc
        or origin.path
        or origin.query
        or origin.fragment
        or origin.username
        or origin.password
        or viewer.scheme != "https"
        or viewer.netloc != origin.netloc
        or viewer.username
        or viewer.password
        or viewer.query
        or viewer.fragment
        or (
            not lambda_arn
            and (
                tiler.scheme not in ("http", "https")
                or not tiler.netloc
                or tiler.username
                or tiler.password
                or tiler.query
                or tiler.fragment
            )
        )
    ):
        raise PublicShareError(503, "Public web-map origin is not configured safely")
    if len(config["SESSION_SECRET"]) < 32:
        raise PublicShareError(
            503, "Public web-map session key is not configured safely"
        )


def _s3_client():
    return boto3.client("s3")


def _share_query(share_id, user_id, lock=False):
    try:
        uuid.UUID(str(share_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PublicShareError(404, "Share not found") from exc
    query = PublicShare.query.filter_by(id=share_id, user_id=user_id)
    if lock:
        query = query.with_for_update()
    share = query.first()
    if share is None:
        raise PublicShareError(404, "Share not found")
    return share


def _validate_mask(mask):
    if mask is None:
        return None
    if not isinstance(mask, dict) or set(mask) != {"type", "coordinates"}:
        raise PublicShareError(400, "Mask must be a GeoJSON Polygon or MultiPolygon")
    if mask["type"] not in ("Polygon", "MultiPolygon"):
        raise PublicShareError(400, "Mask must be a GeoJSON Polygon or MultiPolygon")
    try:
        encoded = json.dumps(mask, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError):
        raise PublicShareError(400, "Mask contains invalid coordinates") from None
    if len(encoded) > MAX_MASK_BYTES:
        raise PublicShareError(400, "Mask exceeds the maximum size")
    polygons = (
        [mask["coordinates"]] if mask["type"] == "Polygon" else mask["coordinates"]
    )
    if not isinstance(polygons, list) or not polygons:
        raise PublicShareError(400, "Mask contains no polygons")
    count = 0
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise PublicShareError(400, "Mask contains an invalid polygon")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise PublicShareError(400, "Mask contains an invalid ring")
            for point in ring:
                if (
                    not isinstance(point, list)
                    or len(point) != 2
                    or any(type(value) not in (int, float) for value in point)
                    or not -180 <= point[0] <= 180
                    or not -90 <= point[1] <= 90
                ):
                    raise PublicShareError(400, "Mask contains invalid coordinates")
            count += len(ring)
            if count > MAX_MASK_COORDINATES:
                raise PublicShareError(400, "Mask contains too many coordinates")
    try:
        pieces = []
        for polygon in polygons:
            unwrapped = []
            for ring in polygon:
                coordinates = [ring[0]]
                for longitude, latitude in ring[1:]:
                    previous = coordinates[-1][0]
                    longitude += 360 * round((previous - longitude) / 360)
                    coordinates.append([longitude, latitude])
                if coordinates[0] != coordinates[-1]:
                    raise PublicShareError(400, "Mask ring wraps around the world")
                if unwrapped:
                    offset = 360 * round((unwrapped[0][0][0] - coordinates[0][0]) / 360)
                    coordinates = [[x + offset, y] for x, y in coordinates]
                unwrapped.append(coordinates)
            geometry = Polygon(unwrapped[0], unwrapped[1:])
            if geometry.is_empty or not geometry.is_valid or geometry.area == 0:
                raise PublicShareError(400, "Mask geometry is empty or invalid")
            min_x, _, max_x, _ = geometry.bounds
            if max_x - min_x > 360:
                raise PublicShareError(400, "Mask polygon wraps around the world")
            for world in range(
                math.floor((min_x + 180) / 360), math.floor((max_x + 180) / 360) + 1
            ):
                part = geometry.intersection(
                    box(-180 + 360 * world, -90, 180 + 360 * world, 90)
                )
                if not part.is_empty and part.area:
                    pieces.append(translate(part, xoff=-360 * world))
        geometry = unary_union(pieces)
        if geometry.is_empty or not geometry.is_valid or geometry.area == 0:
            raise PublicShareError(400, "Mask geometry is empty or invalid")
        if geometry.geom_type not in ("Polygon", "MultiPolygon"):
            raise PublicShareError(400, "Mask geometry is not polygonal")
        if get_num_coordinates(geometry) > MAX_MASK_COORDINATES:
            raise PublicShareError(400, "Mask contains too many coordinates")
        result = mapping(geometry)
        if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_MASK_BYTES:
            raise PublicShareError(400, "Mask exceeds the maximum size")
    except (ValueError, TypeError, IndexError, GEOSException) as exc:
        raise PublicShareError(400, "Mask geometry is invalid") from exc
    return result


def update_share_mask(share_id, user_id, body):
    ensure_public_shares_enabled()
    if not isinstance(body, dict) or set(body) != {"mask", "mask_version"}:
        raise PublicShareError(400, "mask and mask_version are required")
    if type(body["mask_version"]) is not int or body["mask_version"] < 0:
        raise PublicShareError(400, "Invalid mask version")
    mask = _validate_mask(body["mask"])
    share = _share_query(share_id, user_id, lock=True)
    if not share.is_accessible(_now()):
        raise PublicShareError(
            409, "Only active published shares can update their mask"
        )
    if share.mask_version != body["mask_version"]:
        raise PublicShareError(409, "The mask changed; refresh and try again")
    share.mask = mask
    share.mask_version += 1
    db.session.commit()
    return share


def _validate_style(style):
    if not isinstance(style, dict):
        raise PublicShareError(400, "Each layer requires a supported style")
    kind = style.get("kind")
    if kind == "continuous":
        if style.keys() not in (
            {"kind", "min", "max", "palette"},
            {"kind", "min", "max", "stops"},
        ):
            raise PublicShareError(400, "Continuous style fields are invalid")
        minimum, maximum = style.get("min"), style.get("max")
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, (int, float))
            or not math.isfinite(minimum)
            or isinstance(maximum, bool)
            or not isinstance(maximum, (int, float))
            or not math.isfinite(maximum)
            or minimum >= maximum
        ):
            raise PublicShareError(400, "Continuous style values are invalid")
        safe_style = {
            "kind": kind,
            "min": float(minimum),
            "max": float(maximum),
        }
        if "palette" in style:
            if style["palette"] not in ALLOWED_PALETTES:
                raise PublicShareError(400, "Continuous style palette is invalid")
            safe_style["palette"] = style["palette"]
            return safe_style
        stops = style.get("stops")
        if not isinstance(stops, list) or not 2 <= len(stops) <= 32:
            raise PublicShareError(400, "Continuous styles need 2 to 32 color stops")
        safe_stops = []
        for stop in stops:
            if (
                not isinstance(stop, dict)
                or not {"value", "color"} <= stop.keys() <= {"value", "color", "label"}
                or isinstance(stop["value"], bool)
                or not isinstance(stop["value"], (int, float))
                or not math.isfinite(stop["value"])
                or not isinstance(stop["color"], str)
                or not COLOR_RE.fullmatch(stop["color"])
            ):
                raise PublicShareError(400, "Continuous style stops are invalid")
            if (
                stop["value"] < minimum
                or stop["value"] > maximum
                or (safe_stops and stop["value"] <= safe_stops[-1]["value"])
            ):
                raise PublicShareError(400, "Continuous style stops are unordered")
            safe_stop = {"value": float(stop["value"]), "color": stop["color"].lower()}
            if "label" in stop:
                safe_stop["label"] = _safe_label(stop["label"])
            safe_stops.append(safe_stop)
        if safe_stops[0]["value"] != minimum or safe_stops[-1]["value"] != maximum:
            raise PublicShareError(
                400, "Continuous style stops must span the full color range"
            )
        safe_style["stops"] = safe_stops
        return safe_style
    if kind == "categorical":
        values = style.get("values")
        if (
            not {"kind", "values"}
            <= style.keys()
            <= {"kind", "values", "labels", "classification"}
            or not isinstance(values, dict)
            or style.get("classification", "exact") not in ("exact", "discrete")
        ):
            raise PublicShareError(400, "Categorical style fields are invalid")
        if not values or len(values) > 256:
            raise PublicShareError(400, "Categorical styles need 1 to 256 values")
        safe_values = {}
        for value, color in values.items():
            try:
                integer_value = int(value)
            except (TypeError, ValueError) as exc:
                raise PublicShareError(400, "Category values must be integers") from exc
            if str(integer_value) != str(value) or not isinstance(color, str):
                raise PublicShareError(400, "Categorical style values are invalid")
            if not COLOR_RE.fullmatch(color):
                raise PublicShareError(400, "Category colors must use #RRGGBB")
            safe_values[str(integer_value)] = color.lower()
        safe_style = {"kind": kind, "values": safe_values}
        if "labels" in style:
            safe_style["labels"] = _safe_labels(style["labels"], safe_values)
        if style.get("classification") == "discrete":
            safe_style["classification"] = "discrete"
        return safe_style
    raise PublicShareError(400, "Style kind must be continuous or categorical")


def _safe_label(label):
    if (
        not isinstance(label, str)
        or not 1 <= len(label) <= MAX_LEGEND_LABEL
        or any(ord(character) < 32 for character in label)
    ):
        raise PublicShareError(400, "Legend labels are invalid")
    return label


def _safe_labels(labels, values):
    if not isinstance(labels, dict) or labels.keys() - values.keys():
        raise PublicShareError(400, "Legend labels must match style values")
    return {key: _safe_label(label) for key, label in labels.items()}


def _validate_vector_style(style):
    required = {"kind", "stroke", "fill", "width", "fill_opacity", "radius"}
    if (
        not isinstance(style, dict)
        or not required <= style.keys()
        or style.keys() - required - {"category"}
        or style["kind"] != "vector"
    ):
        raise PublicShareError(400, "Vector style fields are invalid")
    safe = {"kind": "vector"}
    for key in ("stroke", "fill"):
        if not isinstance(style[key], str) or not COLOR_RE.fullmatch(style[key]):
            raise PublicShareError(400, "Vector colors must use #RRGGBB")
        safe[key] = style[key].lower()
    for key, maximum in (("width", 10), ("fill_opacity", 1), ("radius", 20)):
        value = style[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= maximum
        ):
            raise PublicShareError(400, "Vector style values are invalid")
        safe[key] = float(value)
    if "category" in style:
        category = style["category"]
        if (
            not isinstance(category, dict)
            or not {"field", "values"}
            <= category.keys()
            <= {"field", "values", "labels", "other_label"}
            or not isinstance(category["field"], str)
            or not VECTOR_NAME_RE.fullmatch(category["field"])
            or not isinstance(category["values"], dict)
            or not 1 <= len(category["values"]) <= 256
        ):
            raise PublicShareError(400, "Vector categories are invalid")
        values = category["values"]
        if any(
            not isinstance(key, str)
            or not 1 <= len(key) <= 120
            or not isinstance(color, str)
            or not COLOR_RE.fullmatch(color)
            for key, color in values.items()
        ):
            raise PublicShareError(400, "Vector category values are invalid")
        safe["category"] = {
            "field": category["field"],
            "values": {key: color.lower() for key, color in values.items()},
        }
        if "labels" in category:
            safe["category"]["labels"] = _safe_labels(category["labels"], values)
        if "other_label" in category:
            safe["category"]["other_label"] = _safe_label(category["other_label"])
    return safe


def _martin_base_url():
    url = _config().get("MARTIN_INTERNAL_URL")
    if not isinstance(url, str):
        raise PublicShareError(503, "Vector tile service is not configured")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise PublicShareError(503, "Vector tile service is not configured safely")
    return url.rstrip("/")


def _validate_layer_declarations(layers, existing_names=()):
    config = _config()
    if not isinstance(layers, list) or not layers:
        raise PublicShareError(400, "layers must contain at least one item")
    declarations = []
    total_bytes = 0
    layer_names = {name.casefold() for name in existing_names}
    for declaration in layers:
        if not isinstance(declaration, dict):
            raise PublicShareError(400, "Each layer declaration must be an object")
        if declaration.keys() - {
            "name",
            "kind",
            "source_layer",
            "band",
            "size_bytes",
            "sha256",
            "style",
            "attribution",
        }:
            raise PublicShareError(400, "Unsupported layer fields")
        name = declaration.get("name")
        kind = declaration.get("kind", "raster")
        if kind not in ("raster", "vector"):
            raise PublicShareError(400, "Layer kind must be raster or vector")
        source_layer = declaration.get("source_layer")
        if kind == "vector":
            _martin_base_url()
            if not isinstance(source_layer, str) or not VECTOR_NAME_RE.fullmatch(
                source_layer
            ):
                raise PublicShareError(400, "Vector source_layer is invalid")
        elif source_layer is not None:
            raise PublicShareError(400, "Raster layers cannot specify source_layer")
        band = declaration.get("band")
        size = declaration.get("size_bytes")
        checksum = declaration.get("sha256")
        attribution = declaration.get("attribution", "")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise PublicShareError(
                400, "Each layer name must contain 1 to 120 characters"
            )
        normalized_name = name.strip()
        if normalized_name.casefold() in layer_names:
            raise PublicShareError(400, "Layer names must be unique")
        layer_names.add(normalized_name.casefold())
        if kind == "raster" and (type(band) is not int or band < 1):
            raise PublicShareError(400, "Layer band must be a positive integer")
        if kind == "vector" and band is not None:
            raise PublicShareError(400, "Vector layers cannot specify band")
        if type(size) is not int or not 1 <= size <= config["MAX_FILE_BYTES"]:
            raise PublicShareError(400, "Layer size is outside the configured limit")
        if not isinstance(checksum, str) or not SHA256_RE.fullmatch(checksum):
            raise PublicShareError(
                400, "Layer sha256 must be a lowercase SHA-256 digest"
            )
        if not isinstance(attribution, str) or len(attribution) > 500:
            raise PublicShareError(
                400, "Layer attribution must be at most 500 characters"
            )
        style = (
            _validate_style(declaration.get("style"))
            if kind == "raster"
            else _validate_vector_style(declaration.get("style"))
        )
        total_bytes += size
        declarations.append(
            {
                "name": normalized_name,
                "kind": kind,
                "source_layer": source_layer,
                "band": band,
                "size": size,
                "checksum": checksum,
                "style": style,
                "attribution": attribution.strip(),
            }
        )
    if total_bytes > config["MAX_SHARE_BYTES"]:
        raise PublicShareError(400, "Share size exceeds the configured limit")
    return declarations, total_bytes


def create_share(user, body):
    """Create a quota-reserved draft with server-generated layer object keys."""
    ensure_public_shares_enabled()
    if not isinstance(body, dict):
        raise PublicShareError(400, "JSON object required")
    if body.keys() - {"title", "retention_days", "layers", "mask"}:
        raise PublicShareError(400, "Unsupported share fields")
    mask = _validate_mask(body.get("mask"))
    title = body.get("title")
    retention_days = body.get("retention_days")
    layers = body.get("layers")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
        raise PublicShareError(400, "title must contain 1 to 120 characters")
    if type(retention_days) is not int or retention_days not in (7, 30, 90):
        raise PublicShareError(400, "retention_days must be 7, 30, or 90")
    config = _config()
    if not isinstance(layers, list) or len(layers) > config["MAX_LAYERS"]:
        raise PublicShareError(
            400, f"layers must contain at most {config['MAX_LAYERS']} items"
        )

    declarations = []
    total_bytes = 0
    layer_names = set()
    for declaration in layers:
        if not isinstance(declaration, dict):
            raise PublicShareError(400, "Each layer declaration must be an object")
        if declaration.keys() - {
            "name",
            "kind",
            "source_layer",
            "band",
            "size_bytes",
            "sha256",
            "style",
            "attribution",
        }:
            raise PublicShareError(400, "Unsupported layer fields")
        name = declaration.get("name")
        kind = declaration.get("kind", "raster")
        if kind not in ("raster", "vector"):
            raise PublicShareError(400, "Layer kind must be raster or vector")
        source_layer = declaration.get("source_layer")
        if kind == "vector":
            _martin_base_url()
            if not isinstance(source_layer, str) or not VECTOR_NAME_RE.fullmatch(
                source_layer
            ):
                raise PublicShareError(400, "Vector source_layer is invalid")
        elif source_layer is not None:
            raise PublicShareError(400, "Raster layers cannot specify source_layer")
        band = declaration.get("band")
        size = declaration.get("size_bytes")
        checksum = declaration.get("sha256")
        attribution = declaration.get("attribution", "")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise PublicShareError(
                400, "Each layer name must contain 1 to 120 characters"
            )
        if name.strip() in layer_names:
            raise PublicShareError(400, "Layer names must be unique")
        layer_names.add(name.strip())
        if kind == "raster" and (type(band) is not int or band < 1):
            raise PublicShareError(400, "Layer band must be a positive integer")
        if kind == "vector" and band is not None:
            raise PublicShareError(400, "Vector layers cannot specify band")
        if type(size) is not int or not 1 <= size <= config["MAX_FILE_BYTES"]:
            raise PublicShareError(400, "Layer size is outside the configured limit")
        if not isinstance(checksum, str) or not SHA256_RE.fullmatch(checksum):
            raise PublicShareError(
                400, "Layer sha256 must be a lowercase SHA-256 digest"
            )
        if not isinstance(attribution, str) or len(attribution) > 500:
            raise PublicShareError(
                400, "Layer attribution must be at most 500 characters"
            )
        style = (
            _validate_style(declaration.get("style"))
            if kind == "raster"
            else _validate_vector_style(declaration.get("style"))
        )
        total_bytes += size
        declarations.append(
            {
                "name": name.strip(),
                "kind": kind,
                "source_layer": source_layer,
                "band": band,
                "size": size,
                "checksum": checksum,
                "style": style,
                "attribution": attribution.strip(),
            }
        )
    if total_bytes > config["MAX_SHARE_BYTES"]:
        raise PublicShareError(400, "Share size exceeds the configured limit")

    # Lock the owner row so concurrent share requests cannot race quota checks.
    locked_user = User.query.filter_by(id=user.id).with_for_update().first()
    if locked_user is None:
        raise PublicShareError(401, "Authenticated owner no longer exists")
    quota_limit = (
        locked_user.public_share_quota_bytes
        if locked_user.public_share_quota_bytes is not None
        else config["MAX_RESERVED_BYTES_PER_USER"]
    )
    active_count = PublicShare.query.filter(
        PublicShare.user_id == user.id,
        PublicShare.state.in_(ACTIVE_STATES),
        db.or_(PublicShare.expires_at.is_(None), PublicShare.expires_at > _now()),
    ).count()
    reserved_bytes = (
        db.session.query(
            db.func.coalesce(db.func.sum(PublicShare.quota_reserved_bytes), 0)
        )
        .filter(
            PublicShare.user_id == user.id,
            PublicShare.cleanup_completed_at.is_(None),
        )
        .scalar()
    )
    if active_count >= config["MAX_ACTIVE_SHARES_PER_USER"]:
        raise PublicShareError(429, "Active share limit reached")
    if reserved_bytes + total_bytes > quota_limit:
        raise PublicShareError(429, "Reserved storage quota exceeded")

    share_id = str(uuid.uuid4())
    share = PublicShare(
        id=share_id,
        user_id=user.id,
        title=title.strip(),
        mask=mask,
        retention_days=retention_days,
        # An empty map has nothing to upload or validate; layers are added
        # after publication.
        state="draft" if declarations else "ready",
        quota_reserved_bytes=total_bytes,
    )
    prefix = config.get("S3_PREFIX", "public-shares").strip("/")
    if not prefix or ".." in prefix.split("/"):
        raise PublicShareError(503, "Public share storage prefix is invalid")
    for declaration in declarations:
        layer_id = str(uuid.uuid4())
        share.layers.append(
            PublicShareLayer(
                id=layer_id,
                name=declaration["name"],
                kind=declaration["kind"],
                band=declaration["band"],
                vector_metadata=(
                    {"source_layer": declaration["source_layer"]}
                    if declaration["kind"] == "vector"
                    else None
                ),
                expected_size_bytes=declaration["size"],
                expected_sha256=declaration["checksum"],
                object_key=(
                    f"{prefix}/{share_id}/{layer_id}.tif"
                    if declaration["kind"] == "raster"
                    else f"{prefix}/{share_id}/{layer_id}.pmtiles"
                ),
                style=declaration["style"],
                attribution=declaration["attribution"],
            )
        )
    db.session.add(share)
    db.session.commit()
    return share


def add_layers_to_share(share_id, user_id, body):
    """Reserve and declare additive layers without changing a published link."""
    ensure_public_shares_enabled()
    if not isinstance(body, dict) or set(body) != {"layers"}:
        raise PublicShareError(400, "Request must contain only a layers array")

    try:
        initial_share = PublicShare.query.filter_by(
            id=share_id, user_id=user_id
        ).first()
    except (ValueError, TypeError):
        initial_share = None
    if initial_share is None:
        raise PublicShareError(404, "Share not found")
    locked_user = User.query.filter_by(id=user_id).with_for_update().first()
    if locked_user is None:
        raise PublicShareError(401, "Authenticated owner no longer exists")
    share = _share_query(share_id, user_id, lock=True)
    if not share.is_accessible(_now()):
        raise PublicShareError(409, "Only active published shares can add layers")
    if share.update_state != "idle":
        raise PublicShareError(409, "A share update is already in progress")

    config = _config()
    active_layers = [layer for layer in share.layers if layer.removed_at is None]
    declarations, total_bytes = _validate_layer_declarations(
        body["layers"], (layer.name for layer in active_layers)
    )
    if len(active_layers) + len(declarations) > config["MAX_LAYERS"]:
        raise PublicShareError(
            400, f"A share can contain at most {config['MAX_LAYERS']} layers"
        )
    if share.quota_reserved_bytes + total_bytes > config["MAX_SHARE_BYTES"]:
        raise PublicShareError(400, "Share size exceeds the configured limit")

    quota_limit = (
        locked_user.public_share_quota_bytes
        if locked_user.public_share_quota_bytes is not None
        else config["MAX_RESERVED_BYTES_PER_USER"]
    )
    reserved_bytes = (
        db.session.query(
            db.func.coalesce(db.func.sum(PublicShare.quota_reserved_bytes), 0)
        )
        .filter(
            PublicShare.user_id == user_id,
            PublicShare.cleanup_completed_at.is_(None),
        )
        .scalar()
    )
    if reserved_bytes + total_bytes > quota_limit:
        raise PublicShareError(429, "Reserved storage quota exceeded")

    prefix = config.get("S3_PREFIX", "public-shares").strip("/")
    if not prefix or ".." in prefix.split("/"):
        raise PublicShareError(503, "Public share storage prefix is invalid")
    for declaration in declarations:
        layer_id = str(uuid.uuid4())
        share.layers.append(
            PublicShareLayer(
                id=layer_id,
                name=declaration["name"],
                kind=declaration["kind"],
                band=declaration["band"],
                vector_metadata=(
                    {"source_layer": declaration["source_layer"]}
                    if declaration["kind"] == "vector"
                    else None
                ),
                expected_size_bytes=declaration["size"],
                expected_sha256=declaration["checksum"],
                object_key=(
                    f"{prefix}/{share_id}/{layer_id}.tif"
                    if declaration["kind"] == "raster"
                    else f"{prefix}/{share_id}/{layer_id}.pmtiles"
                ),
                style=declaration["style"],
                attribution=declaration["attribution"],
            )
        )
    share.quota_reserved_bytes += total_bytes
    share.update_state = "uploading"
    db.session.commit()
    return share


def remove_layers_from_share(share_id, user_id, body):
    """Hide published layers immediately and retain them for retryable cleanup."""
    ensure_public_shares_enabled()
    if (
        not isinstance(body, dict)
        or set(body) != {"layer_ids"}
        or not isinstance(body["layer_ids"], list)
        or not body["layer_ids"]
        or len(body["layer_ids"]) > _config()["MAX_LAYERS"]
    ):
        raise PublicShareError(400, "Request must contain a nonempty layer_ids array")
    try:
        layer_ids = [str(uuid.UUID(value)) for value in body["layer_ids"]]
    except (ValueError, TypeError, AttributeError):
        raise PublicShareError(400, "Invalid layer ID") from None
    if len(set(layer_ids)) != len(layer_ids):
        raise PublicShareError(400, "Duplicate layer IDs")
    share = _share_query(share_id, user_id, lock=True)
    if not share.is_accessible(_now()):
        raise PublicShareError(409, "Only active published shares can remove layers")
    if share.update_state != "idle":
        raise PublicShareError(409, "A share update is already in progress")
    layers = {str(layer.id): layer for layer in share.layers}
    if any(
        layer_id not in layers
        or not layers[layer_id].published
        or layers[layer_id].removed_at is not None
        for layer_id in layer_ids
    ):
        raise PublicShareError(404, "Published layer not found")
    now = _now()
    for layer_id in layer_ids:
        layers[layer_id].removed_at = now
    db.session.commit()
    return share


def get_share_storage_usage(user):
    """Return committed and reserved public-share storage for one owner."""
    config = _config()
    quota_limit = (
        user.public_share_quota_bytes
        if user.public_share_quota_bytes is not None
        else config["MAX_RESERVED_BYTES_PER_USER"]
    )
    owned_shares = PublicShare.query.filter_by(user_id=user.id)
    retained_shares = owned_shares.filter(PublicShare.cleanup_completed_at.is_(None))
    reserved_bytes = (
        db.session.query(
            db.func.coalesce(db.func.sum(PublicShare.quota_reserved_bytes), 0)
        )
        .filter(
            PublicShare.user_id == user.id,
            PublicShare.cleanup_completed_at.is_(None),
        )
        .scalar()
    )
    uploaded_bytes = (
        db.session.query(
            db.func.coalesce(db.func.sum(PublicShareLayer.expected_size_bytes), 0)
        )
        .join(PublicShare, PublicShareLayer.share_id == PublicShare.id)
        .filter(
            PublicShare.user_id == user.id,
            PublicShare.cleanup_completed_at.is_(None),
            PublicShareLayer.upload_completed.is_(True),
        )
        .scalar()
    )
    now = _now()
    draft_cutoff = now - timedelta(seconds=config["DRAFT_TTL_SECONDS"])
    cleanup_pending_bytes = (
        db.session.query(
            db.func.coalesce(db.func.sum(PublicShare.quota_reserved_bytes), 0)
        )
        .filter(
            PublicShare.user_id == user.id,
            PublicShare.cleanup_completed_at.is_(None),
            db.or_(
                PublicShare.state.in_(("revoked", "expired")),
                db.and_(
                    PublicShare.state == "ready",
                    PublicShare.expires_at.isnot(None),
                    PublicShare.expires_at <= now,
                ),
                db.and_(
                    PublicShare.state.in_(ACTIVE_STATES),
                    PublicShare.ready_at.is_(None),
                    PublicShare.created_at <= draft_cutoff,
                ),
            ),
        )
        .scalar()
    )
    removed_pending_bytes = (
        db.session.query(
            db.func.coalesce(db.func.sum(PublicShareLayer.expected_size_bytes), 0)
        )
        .join(PublicShare, PublicShareLayer.share_id == PublicShare.id)
        .filter(
            PublicShare.user_id == user.id,
            PublicShare.cleanup_completed_at.is_(None),
            PublicShare.state == "ready",
            PublicShare.expires_at > now,
            PublicShareLayer.removed_at.isnot(None),
        )
        .scalar()
    )
    reserved_bytes = int(reserved_bytes or 0)
    uploaded_bytes = int(uploaded_bytes or 0)
    cleanup_pending_bytes = int(cleanup_pending_bytes or 0) + int(
        removed_pending_bytes or 0
    )
    return {
        "quota_limit_bytes": int(quota_limit),
        "quota_override_bytes": user.public_share_quota_bytes,
        "reserved_bytes": reserved_bytes,
        "uploaded_bytes": uploaded_bytes,
        "cleanup_pending_bytes": cleanup_pending_bytes,
        "available_bytes": max(0, quota_limit - reserved_bytes),
        "over_quota": reserved_bytes > quota_limit,
        "retained_share_count": retained_shares.count(),
    }


def create_or_refresh_upload(share_id, layer_id, user_id):
    """Create a private multipart upload and bounded, short-lived part URLs."""
    ensure_public_shares_enabled()
    share = _share_query(share_id, user_id, lock=True)
    is_additive_update = share.state == "ready" and share.update_state in (
        "uploading",
        "failed",
    )
    if not is_additive_update and share.state not in ("draft", "uploading", "failed"):
        raise PublicShareError(409, "Share is not accepting uploads")
    layer = next((item for item in share.layers if str(item.id) == str(layer_id)), None)
    if layer is None or layer.removed_at is not None:
        raise PublicShareError(404, "Layer not found")
    if is_additive_update and layer.published:
        raise PublicShareError(409, "Published layers cannot be replaced")

    config = _config()
    client = _s3_client()
    part_size = config["MULTIPART_PART_BYTES"]
    if part_size < 5 * 1024**2:
        raise PublicShareError(503, "Multipart part size must be at least 5 MiB")
    part_count = math.ceil(layer.expected_size_bytes / part_size)
    if part_count > 10_000:
        raise PublicShareError(400, "Layer requires too many multipart parts")
    if layer.upload_completed:
        if not is_additive_update and share.state != "failed":
            raise PublicShareError(409, "Layer upload is already complete")
        _delete_s3_object_versions(client, config["S3_BUCKET"], layer.object_key)
        if layer.kind == "vector":
            staging_key, renderer_key = _vector_object_keys(layer)
            for key in {staging_key, renderer_key} - {layer.object_key}:
                _delete_s3_object_versions(client, config["S3_BUCKET"], key)
            layer.object_key = staging_key
        layer.upload_completed = False
        layer.upload_id = None
        layer.object_version_id = None
        layer.checksum_sha256 = None
        layer.bounds = None
        layer.crs = None
        layer.nodata = None
        layer.raster_band_count = None
        layer.validation_error = None
    upload_id = layer.upload_id
    if not upload_id:
        result = client.create_multipart_upload(
            Bucket=config["S3_BUCKET"],
            Key=layer.object_key,
            ContentType=(
                "image/tiff" if layer.kind == "raster" else "application/vnd.pmtiles"
            ),
            ServerSideEncryption="AES256",
            Metadata={"share-id": str(share.id), "layer-id": str(layer.id)},
        )
        upload_id = result["UploadId"]
        layer.upload_id = upload_id
    layer.upload_part_size = part_size
    layer.upload_part_count = part_count
    if not is_additive_update:
        share.state = "uploading"
    else:
        share.update_state = "uploading"
    db.session.commit()

    parts = []
    for part_number in range(1, part_count + 1):
        content_length = min(
            part_size,
            layer.expected_size_bytes - (part_number - 1) * part_size,
        )
        url = client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": config["S3_BUCKET"],
                "Key": layer.object_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
                "ContentLength": content_length,
            },
            ExpiresIn=config["UPLOAD_URL_TTL_SECONDS"],
        )
        parts.append(
            {"part_number": part_number, "size_bytes": content_length, "url": url}
        )
    return {"layer_id": str(layer.id), "parts": parts}


def complete_share_uploads(share_id, user_id, body):
    """Finish declared S3 multipart objects, then enqueue bounded validation."""
    ensure_public_shares_enabled()
    if not isinstance(body, dict) or set(body) != {"layers"}:
        raise PublicShareError(400, "Request must contain only a layers array")
    declared_parts = body["layers"]
    if not isinstance(declared_parts, list):
        raise PublicShareError(400, "layers must be an array")
    share = _share_query(share_id, user_id, lock=True)
    is_additive_update = share.state == "ready"
    if is_additive_update:
        if share.update_state == "validating":
            return share, True
        if share.update_state not in ("uploading", "failed"):
            raise PublicShareError(409, "Share is not accepting update completion")
    elif share.state == "validating" and all(
        layer.upload_completed for layer in share.layers
    ):
        return share, True
    elif share.state not in ("uploading", "failed"):
        raise PublicShareError(409, "Share is not ready for upload completion")
    pending_layers = (
        [
            layer
            for layer in share.layers
            if not layer.published and layer.removed_at is None
        ]
        if is_additive_update
        else share.layers
    )
    if not pending_layers or all(layer.upload_completed for layer in pending_layers):
        if not is_additive_update and share.state == "ready":
            return share, False
        if not is_additive_update and share.state == "validating":
            return share, True
    part_map = {}
    for entry in declared_parts:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"layer_id", "parts"}
            or not isinstance(entry["layer_id"], str)
            or entry["layer_id"] in part_map
            or not isinstance(entry["parts"], list)
        ):
            raise PublicShareError(400, "Invalid layer completion manifest")
        part_map[entry["layer_id"]] = entry["parts"]
    if set(part_map) != {str(layer.id) for layer in pending_layers}:
        raise PublicShareError(
            400, "Completion manifest must include every pending layer"
        )

    client = _s3_client()
    config = _config()
    for layer in pending_layers:
        if layer.upload_completed:
            continue
        if not layer.upload_id or not layer.upload_part_count:
            raise PublicShareError(409, "A layer has no active multipart upload")
        parts = part_map[str(layer.id)]
        if len(parts) != layer.upload_part_count:
            raise PublicShareError(
                400, "Multipart part count does not match declaration"
            )
        normalized_parts = []
        for expected_number, part in enumerate(parts, start=1):
            if (
                not isinstance(part, dict)
                or set(part) != {"part_number", "etag"}
                or type(part["part_number"]) is not int
                or part["part_number"] != expected_number
                or not isinstance(part["etag"], str)
                or not part["etag"]
                or len(part["etag"]) > 256
            ):
                raise PublicShareError(400, "Multipart parts are invalid or unordered")
            normalized_parts.append(
                {"PartNumber": expected_number, "ETag": part["etag"]}
            )
        try:
            client.complete_multipart_upload(
                Bucket=config["S3_BUCKET"],
                Key=layer.object_key,
                UploadId=layer.upload_id,
                MultipartUpload={"Parts": normalized_parts},
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in (
                "NoSuchUpload",
                "NoSuchKey",
                "404",
            ):
                raise
        head = client.head_object(Bucket=config["S3_BUCKET"], Key=layer.object_key)
        if head.get("ContentLength") != layer.expected_size_bytes:
            raise PublicShareError(
                400, "Uploaded layer size does not match declaration"
            )
        layer.object_version_id = head.get("VersionId")
        layer.upload_completed = True
        db.session.commit()

    if is_additive_update:
        share.update_state = "validating"
    else:
        share.state = "validating"
    share.cleanup_error = None
    db.session.commit()
    return share, True


def _download_and_validate(layer, bucket):
    client = _s3_client()
    with tempfile.TemporaryDirectory(prefix="te-share-") as temp_dir:
        path = Path(temp_dir) / (
            "snapshot.tif" if layer.kind == "raster" else "snapshot.pmtiles"
        )
        params = {"Bucket": bucket, "Key": layer.object_key}
        if layer.object_version_id:
            params["VersionId"] = layer.object_version_id
        source_key = layer.object_key
        try:
            response = client.get_object(**params)
        except botocore.exceptions.ClientError as exc:
            if layer.kind != "vector" or exc.response.get("Error", {}).get(
                "Code"
            ) not in ("NoSuchKey", "NoSuchVersion", "404"):
                raise
            # Recover a promotion whose S3 move succeeded before the DB commit.
            _, renderer_key = _vector_object_keys(layer)
            if source_key == renderer_key:
                raise
            source_key = renderer_key
            response = client.get_object(Bucket=bucket, Key=source_key)
        digest = hashlib.sha256()
        downloaded = 0
        with closing(response["Body"]) as body, path.open("wb") as output:
            if response.get("ContentLength") != layer.expected_size_bytes:
                raise ValueError("Uploaded layer size does not match declaration")
            while block := body.read(
                min(1024 * 1024, layer.expected_size_bytes - downloaded + 1)
            ):
                downloaded += len(block)
                if downloaded > layer.expected_size_bytes:
                    raise ValueError("Uploaded layer exceeds declared size")
                output.write(block)
                digest.update(block)
        if downloaded != layer.expected_size_bytes:
            raise ValueError("Uploaded layer is truncated")
        checksum = digest.hexdigest()
        if not hmac.compare_digest(checksum, layer.expected_sha256):
            raise ValueError("SHA-256 checksum does not match the declaration")

        if layer.kind == "vector":
            metadata = _validate_pmtiles_file(path, layer, temp_dir)
            staging_key, renderer_key = _vector_object_keys(layer)
            version_id = response.get("VersionId")
            if source_key != renderer_key:
                _delete_s3_object_versions(client, bucket, renderer_key)
                copy_source = {"Bucket": bucket, "Key": source_key}
                if version_id:
                    copy_source["VersionId"] = version_id
                copied = client.copy_object(
                    Bucket=bucket,
                    Key=renderer_key,
                    CopySource=copy_source,
                    ServerSideEncryption="AES256",
                    ContentType="application/vnd.pmtiles",
                    MetadataDirective="REPLACE",
                )
                version_id = copied.get("VersionId")
                _delete_s3_object_versions(client, bucket, staging_key)
            return {
                "checksum": checksum,
                "bounds": metadata.pop("bounds"),
                "crs": "EPSG:3857",
                "nodata": None,
                "band_count": None,
                "vector_metadata": metadata,
                "object_key": renderer_key,
                "object_version_id": version_id,
            }

        gdalinfo_json = Path(temp_dir) / "gdalinfo.json"
        gdal_env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": temp_dir,
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "GDAL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "GDAL_CACHEMAX": "64",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": "",
        }
        preexec_fn = None
        if os.name == "posix":
            import resource

            def apply_gdal_limits():
                memory_limit = 1024**3
                metadata_limit = 16 * 1024**2
                resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
                resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
                resource.setrlimit(
                    resource.RLIMIT_FSIZE, (metadata_limit, metadata_limit)
                )

            preexec_fn = apply_gdal_limits
        gdalinfo = shutil.which("gdalinfo")
        if gdalinfo is None:
            raise FileNotFoundError("gdalinfo executable is required")
        with gdalinfo_json.open("wb") as output:
            subprocess.run(  # noqa: S603
                [
                    gdalinfo,
                    "-json",
                    "-mdd",
                    "IMAGE_STRUCTURE",
                    str(path),
                ],
                check=True,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=90,
                env=gdal_env,
                preexec_fn=preexec_fn,
            )
        if gdalinfo_json.stat().st_size > 16 * 1024**2:
            raise ValueError("Raster metadata exceeds the configured limit")
        info = json.loads(gdalinfo_json.read_text(encoding="utf-8"))
        if info.get("driverShortName") != "GTiff":
            raise ValueError("Only GeoTIFF/COG uploads are supported")
        metadata = info.get("metadata") or {}
        image_structure = metadata.get("IMAGE_STRUCTURE") or {}
        if image_structure.get("LAYOUT") != "COG":
            raise ValueError("Raster is not a cloud-optimized GeoTIFF")
        bands = info.get("bands") or []
        size = info.get("size") or []
        config = _config()
        if (
            len(size) != 2
            or any(
                type(dimension) is not int
                or dimension < 1
                or dimension > config["MAX_RASTER_DIMENSION"]
                for dimension in size
            )
            or size[0] * size[1] > config["MAX_RASTER_PIXELS"]
        ):
            raise ValueError("Raster dimensions exceed the configured limits")
        if (
            not bands
            or len(bands) > config["MAX_RASTER_BANDS"]
            or layer.band > len(bands)
        ):
            raise ValueError("Declared raster band does not exist")
        extent = (info.get("wgs84Extent") or {}).get("coordinates")
        coordinates = []
        pending = [extent]
        while pending:
            item = pending.pop()
            if (
                isinstance(item, list)
                and len(item) == 2
                and all(isinstance(value, (int, float)) for value in item)
            ):
                coordinates.append(item)
            elif isinstance(item, list):
                pending.extend(item)
        if not coordinates:
            raise ValueError("Raster has no usable geographic bounds")
        bounds = [
            min(point[0] for point in coordinates),
            min(point[1] for point in coordinates),
            max(point[0] for point in coordinates),
            max(point[1] for point in coordinates),
        ]
        if not all(math.isfinite(value) for value in bounds):
            raise ValueError("Raster bounds are invalid")
        crs_info = info.get("coordinateSystem") or {}
        crs = crs_info.get("wkt")
        if not isinstance(crs, str) or not crs.strip():
            raise ValueError("Raster must declare a coordinate reference system")
        return {
            "checksum": checksum,
            "bounds": bounds,
            "crs": crs,
            "nodata": bands[layer.band - 1].get("noDataValue"),
            "band_count": len(bands),
        }


def _validate_pmtiles_file(path, layer, temp_dir):
    if os.name != "posix":
        raise OSError("PMTiles validation requires POSIX process resource limits")
    validator = Path(__file__).with_name("pmtiles_validation.py")
    output_path = Path(temp_dir) / "pmtiles.json"
    with output_path.open("wb") as output:
        completed = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-I",
                str(validator),
                str(path),
                layer.vector_metadata["source_layer"],
                str(_config()["MAX_TILE_ZOOM"]),
            ],
            check=False,
            stdout=output,
            stderr=subprocess.DEVNULL,
            timeout=90,
            env={"PATH": os.defpath, "HOME": temp_dir},
        )
    if completed.returncode:
        raise ValueError("PMTiles failed bounded archive/MVT format validation")
    if output_path.stat().st_size > 1024**2:
        raise ValueError("PMTiles validation output exceeds its limit")
    metadata = json.loads(output_path.read_text(encoding="utf-8"))
    category = layer.style.get("category")
    if category and category["field"] not in metadata["fields"]:
        raise ValueError("Vector style category field is absent from PMTiles")
    return metadata


def _vector_object_keys(layer):
    """Derive both private keys from the persisted server-owned storage root."""
    root = layer.object_key.rsplit("/", 2)[0]
    return (
        f"{root}/{layer.share_id}/{layer.id}.pmtiles",
        f"{root}/vector/{layer.id}.pmtiles",
    )


def validate_share(share_id):
    """Validate all private objects and make a fully valid share publishable."""
    ensure_public_shares_enabled()
    share = PublicShare.query.filter_by(id=share_id).with_for_update().first()
    if share is None or (
        share.state != "validating" and share.update_state != "validating"
    ):
        return
    is_additive_update = share.state == "ready"
    bucket = _config()["S3_BUCKET"]
    failures = []
    pending_layers = (
        [
            layer
            for layer in share.layers
            if not layer.published and layer.removed_at is None
        ]
        if is_additive_update
        else share.layers
    )
    for layer in pending_layers:
        try:
            result = _download_and_validate(layer, bucket)
            layer.checksum_sha256 = result["checksum"]
            layer.bounds = result["bounds"]
            layer.crs = result["crs"]
            layer.nodata = result["nodata"]
            layer.raster_band_count = result["band_count"]
            if layer.kind == "vector":
                layer.vector_metadata = result["vector_metadata"]
                layer.object_key = result["object_key"]
                layer.object_version_id = result["object_version_id"]
            layer.validation_error = None
        except (
            subprocess.SubprocessError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            layer.validation_error = str(exc)[:500]
            failures.append(layer)
    if failures:
        if is_additive_update:
            share.update_state = "failed"
        else:
            share.state = "failed"
    else:
        if is_additive_update:
            for layer in pending_layers:
                layer.published = True
            share.update_state = "idle"
        else:
            for layer in pending_layers:
                layer.published = True
            share.state = "ready"
    db.session.commit()


def mint_public_link(share, *, rotate=False):
    """Mint a path token and activate or rotate the public link."""
    ensure_public_shares_enabled()
    now = _now()
    if share.state != "ready":
        raise PublicShareError(409, "Share has not passed validation")
    if rotate:
        if not share.is_accessible(now):
            raise PublicShareError(409, "Only active shares can rotate their link")
    elif share.ready_at is not None or share.token_hash is not None:
        raise PublicShareError(
            409, "Share is already published; rotate to get a new link"
        )
    for layer in share.layers:
        if layer.kind == "vector" and layer.removed_at is None:
            _ensure_martin_source_ready(layer)
    secret = secrets.token_urlsafe(SHARE_SECRET_BYTES)
    share.token_hash = hashlib.sha256(secret.encode("ascii")).hexdigest()
    share.link_ciphertext = _link_fernet().encrypt(secret.encode("ascii")).decode()
    share.session_generation += 1
    if not rotate:
        share.ready_at = now
        share.expires_at = now + timedelta(days=share.retention_days)
    db.session.commit()
    if rotate:
        invalidate_public_tiles(share)
    return _viewer_link(share, secret)


def _viewer_link(share, secret):
    viewer_url = _config()["VIEWER_BASE_URL"].rstrip("/")
    return f"{viewer_url}/s/{quote(secret, safe='')}"


def _link_fernet():
    """Derive the link-encryption key from the configured session secret."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"trends.earth-public-share-link-v1",
        info=b"fernet-key",
    )
    key = hkdf.derive(_config()["SESSION_SECRET"].encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def get_public_link(share):
    """Return the active link to its owner; access remains hash-verified."""
    ensure_public_shares_enabled()
    if not share.is_accessible(_now()):
        raise PublicShareError(409, "Only active shares have a viewable link")
    if not share.link_ciphertext:
        raise PublicShareError(
            409, "This link was not stored; rotate the link to view it again"
        )
    try:
        secret = _link_fernet().decrypt(share.link_ciphertext.encode()).decode()
    except (InvalidToken, UnicodeDecodeError, ValueError):
        raise PublicShareError(
            409, "This link can no longer be shown; rotate the link"
        ) from None
    if not verify_public_secret(share, secret):
        raise PublicShareError(409, "This link can no longer be shown; rotate the link")
    return _viewer_link(share, secret)


def verify_public_secret(share, supplied_secret):
    """Compare a capability without exposing its plaintext in storage."""
    if not isinstance(supplied_secret, str) or not 32 <= len(supplied_secret) <= 128:
        return False
    expected = share.token_hash
    if not expected:
        return False
    actual = hashlib.sha256(supplied_secret.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, actual)


def load_public_token(token):
    """Resolve current and legacy capabilities using their indexed hash."""
    if not isinstance(token, str) or not PUBLIC_TOKEN_RE.fullmatch(token):
        return None
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    share = PublicShare.query.filter_by(token_hash=digest).first()
    return share if share is not None and share.is_accessible() else None


def invalidate_public_tiles(share):
    """Purge all token generations for this share; a retry uses the same prefix."""
    distribution = _config().get("CDN_DISTRIBUTION_ID")
    if not distribution:
        return
    try:
        boto3.client("cloudfront").create_invalidation(
            DistributionId=distribution,
            InvalidationBatch={
                "CallerReference": str(uuid.uuid4()),
                "Paths": {
                    "Quantity": 1,
                    "Items": [f"/api/v1/public-shares/tiles/{share.id}/*"],
                },
            },
        )
    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as exc:
        logger.error("Could not invalidate cached tiles for share %s", share.id)
        raise PublicShareError(
            503, "Link updated, but CDN invalidation failed; retry this operation"
        ) from exc


def create_viewer_session(share):
    """Sign a rolling short-lived cookie with a fixed absolute lifetime."""
    maximum_lifetime = _config()["MAX_VIEWER_SESSION_LIFETIME_SECONDS"]
    if type(maximum_lifetime) is not int or maximum_lifetime < 1:
        raise PublicShareError(503, "Viewer session lifetime is not configured safely")
    absolute_expires_at = int(datetime.now(UTC).timestamp()) + maximum_lifetime
    serializer = URLSafeTimedSerializer(
        _config()["SESSION_SECRET"], salt="public-share-session-v1"
    )
    return serializer.dumps(
        {
            "share_id": str(share.id),
            "generation": share.session_generation,
            "absolute_expires_at": absolute_expires_at,
        }
    )


def _load_viewer_session_claims(cookie_value):
    config = _config()
    serializer = URLSafeTimedSerializer(
        config["SESSION_SECRET"], salt="public-share-session-v1"
    )
    try:
        claims = serializer.loads(
            cookie_value, max_age=config["VIEWER_SESSION_TTL_SECONDS"]
        )
    except (BadSignature, SignatureExpired):
        return None
    absolute_expires_at = claims.get("absolute_expires_at")
    if (
        type(absolute_expires_at) is not int
        or int(datetime.now(UTC).timestamp()) >= absolute_expires_at
    ):
        return None
    return claims


def load_viewer_session(share_id, cookie_value):
    """Validate a viewer cookie and re-check current share state."""
    try:
        uuid.UUID(str(share_id))
    except (ValueError, TypeError, AttributeError):
        return None
    claims = _load_viewer_session_claims(cookie_value)
    if claims is None or claims.get("share_id") != str(share_id):
        return None
    share = PublicShare.query.filter_by(id=share_id).first()
    if (
        share is None
        or claims.get("generation") != share.session_generation
        or not share.is_accessible()
    ):
        return None
    return share


def refresh_viewer_session(share, cookie_value):
    """Refresh a cookie without extending its absolute lifetime."""
    claims = _load_viewer_session_claims(cookie_value)
    if (
        claims is None
        or claims.get("share_id") != str(share.id)
        or claims.get("generation") != share.session_generation
        or not share.is_accessible()
    ):
        return None
    serializer = URLSafeTimedSerializer(
        _config()["SESSION_SECRET"], salt="public-share-session-v1"
    )
    remaining = claims["absolute_expires_at"] - int(datetime.now(UTC).timestamp())
    return serializer.dumps(claims), remaining


class _NoTileRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_tile_service(url, timeout=32):
    """Do not forward trusted internal requests to an upstream redirect target."""
    return build_opener(_NoTileRedirects()).open(url, timeout=timeout)


def tile_intersects_layer(layer, z, x, y):
    """Bounds are validated EPSG:4326, while tile coordinates are Web Mercator."""
    if not layer.bounds:
        return True
    count = 2**z
    west, east = x / count * 360 - 180, (x + 1) / count * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / count))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / count))))
    left, bottom, right, top = layer.bounds
    return west <= right and east >= left and south <= top and north >= bottom


def _ensure_martin_source_ready(layer):
    """Fail publication visibly while Martin's private prefix discovery catches up."""
    try:
        with open_tile_service(f"{_martin_base_url()}/{layer.id}") as response:
            data = response.read(1024**2 + 1)
            if (
                response.status != 200
                or response.headers.get_content_type() != "application/json"
                or len(data) > 1024**2
            ):
                raise PublicShareError(503, "Vector tile source is not available yet")
            metadata = json.loads(data)
            if (
                not isinstance(metadata, dict)
                or not isinstance(metadata.get("vector_layers"), list)
                or [
                    item.get("id")
                    for item in metadata["vector_layers"]
                    if isinstance(item, dict)
                ]
                != [layer.vector_metadata["source_layer"]]
            ):
                raise PublicShareError(503, "Vector tile source metadata is invalid")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise PublicShareError(
            503, "Vector tile source is not available yet; retry publication"
        ) from exc


def _raster_tile_request(layer, z, x, y):
    """Use the same server-owned raster request for HTTP and Lambda backends."""
    config = _config()
    path = f"/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png"
    query = {
        "url": f"s3://{config['S3_BUCKET']}/{layer.object_key}",
        "bidx": layer.band,
        "resampling": "nearest" if layer.style["kind"] == "categorical" else "bilinear",
    }
    if layer.style["kind"] == "continuous":
        query["rescale"] = f"{layer.style['min']},{layer.style['max']}"
        if "palette" in layer.style:
            query["colormap_name"] = layer.style["palette"]
        else:
            stops = layer.style["stops"]
            colormap = {}
            for index in range(256):
                position = layer.style["min"] + (
                    index * (layer.style["max"] - layer.style["min"]) / 255
                )
                right_index = next(
                    (
                        stop_index
                        for stop_index, stop in enumerate(stops[1:], start=1)
                        if position <= stop["value"]
                    ),
                    len(stops) - 1,
                )
                left, right = stops[right_index - 1], stops[right_index]
                proportion = (position - left["value"]) / (
                    right["value"] - left["value"]
                )
                left_rgb = tuple(
                    int(left["color"][offset : offset + 2], 16) for offset in (1, 3, 5)
                )
                right_rgb = tuple(
                    int(right["color"][offset : offset + 2], 16) for offset in (1, 3, 5)
                )
                color = "".join(
                    f"{round(start + (end - start) * proportion):02x}"
                    for start, end in zip(left_rgb, right_rgb, strict=True)
                )
                colormap[str(index)] = f"#{color}"
            query["colormap"] = json.dumps(colormap, separators=(",", ":"))
    elif layer.style.get("classification") == "discrete":
        # Match QGIS discrete classes: each color covers (previous, value].
        breaks = sorted(layer.style["values"].items(), key=lambda item: int(item[0]))
        intervals = []
        lower = -COLORMAP_LIMIT
        for index, (value, color) in enumerate(breaks):
            upper = (
                COLORMAP_LIMIT
                if index == len(breaks) - 1
                else math.nextafter(int(value), math.inf)
            )
            intervals.append([[lower, upper], color])
            lower = upper
        query["colormap"] = json.dumps(intervals, separators=(",", ":"))
    else:
        query["colormap"] = json.dumps(layer.style["values"], separators=(",", ":"))
    return path, urlencode(query)


def get_tile_url(share, layer, z, x, y):
    """Build fixed renderer URLs for the existing HTTP backend and Martin."""
    if layer.kind == "vector":
        return f"{_martin_base_url()}/{layer.id}/{z}/{x}/{y}"
    path, query = _raster_tile_request(layer, z, x, y)
    return _config()["TITILER_INTERNAL_URL"].rstrip("/") + path + "?" + query


class _LambdaTileResponse(BytesIO):
    def __init__(self, tile, content_type, status=200):
        super().__init__(tile)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type


def invoke_raster_tile(share, layer, z, x, y):
    """Invoke the private renderer without forwarding viewer credentials."""
    arn = _config()["TITILER_LAMBDA_ARN"]
    match = LAMBDA_ARN_RE.fullmatch(arn)
    if not match:
        raise PublicShareError(503, "Public web-map Lambda is not configured safely")
    path, query = _raster_tile_request(layer, z, x, y)
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query,
        "headers": {"host": "private-renderer"},
        "requestContext": {
            "http": {
                "method": "GET",
                "path": path,
                "sourceIp": "127.0.0.1",
                "protocol": "HTTP/1.1",
            }
        },
        "isBase64Encoded": False,
    }
    try:
        client = boto3.client(
            "lambda",
            region_name=match.group(2),
            config=botocore.config.Config(
                connect_timeout=1,
                read_timeout=32,
                retries={"total_max_attempts": 1},
            ),
        )
        for attempt in range(3):
            try:
                result = client.invoke(
                    FunctionName=arn,
                    InvocationType="RequestResponse",
                    LogType="None",
                    Payload=json.dumps(event).encode("utf-8"),
                )
                break
            except botocore.exceptions.ClientError as exc:
                if (
                    exc.response.get("Error", {}).get("Code")
                    != "TooManyRequestsException"
                    or attempt == 2
                ):
                    raise
                logger.warning("Private renderer throttled; retrying invocation")
                time.sleep((0.1 * 2**attempt) * (0.5 + secrets.randbelow(501) / 1000))
        with closing(result["Payload"]) as payload:
            raw = payload.read(4 * 1024**2 + 1)
        if result.get("FunctionError"):
            failure = json.loads(raw)
            error_type = failure.get("errorType") if isinstance(failure, dict) else None
            status = 504 if error_type in ("Sandbox.Timedout", "TimeoutError") else 502
            logger.warning("Private renderer failed (%s)", status)
            raise PublicShareError(status, "Tile service could not render this tile")
        if result.get("StatusCode") != 200:
            raise ValueError("Lambda invocation failed")
        if len(raw) > 4 * 1024**2:
            raise ValueError("Lambda response exceeds its limit")
        response = json.loads(raw)
        if not isinstance(response, dict) or not isinstance(
            response.get("headers"), dict
        ):
            raise TypeError("Invalid Lambda response envelope")
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in response["headers"].items()
        ):
            raise ValueError("Invalid Lambda response headers")
        headers = {key.lower(): value for key, value in response["headers"].items()}
        status = response.get("statusCode")
        if status == 204 and not response.get("body"):
            return _LambdaTileResponse(b"", "image/png", status=204)
        if status in (429, 502, 503, 504):
            logger.warning("Private renderer returned status %s", status)
            raise PublicShareError(
                503 if status == 429 else status,
                "Tile service is temporarily unavailable",
            )
        if (
            response.get("statusCode") != 200
            or response.get("isBase64Encoded") is not True
            or headers.get("content-type", "").split(";")[0] != "image/png"
            or "content-encoding" in headers
            or not isinstance(response.get("body"), str)
        ):
            raise ValueError("Invalid Lambda tile response")
        tile = base64.b64decode(response["body"], validate=True)
        if len(tile) > 2 * 1024**2 or not tile.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Lambda tile exceeds its limit")
        return _LambdaTileResponse(tile, "image/png")
    except botocore.exceptions.ReadTimeoutError as exc:
        logger.warning("Private renderer invocation timed out")
        raise PublicShareError(504, "Tile service timed out") from exc
    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as exc:
        logger.warning(
            "Private renderer invocation failed for share %s layer %s (%s)",
            share.id,
            layer.id,
            type(exc).__name__,
        )
        raise PublicShareError(503, "Tile service is temporarily unavailable") from exc
    except (ValueError, TypeError, KeyError) as exc:
        logger.error(
            "Private renderer returned an invalid tile for share %s layer %s",
            share.id,
            layer.id,
        )
        raise PublicShareError(
            502, "Tile service returned an invalid response"
        ) from exc


def mark_share_revoked(share):
    """Deny access immediately while preserving the record for async cleanup."""
    if share.state == "revoked":
        invalidate_public_tiles(share)
        return
    now = _now()
    share.state = "revoked"
    share.revoked_at = share.revoked_at or now
    share.token_hash = None
    share.link_ciphertext = None
    share.session_generation += 1
    share.cleanup_error = None
    db.session.commit()
    invalidate_public_tiles(share)


def _delete_s3_object_versions(client, bucket, key):
    """Delete every version so bucket versioning cannot retain expired data."""
    paginator = client.get_paginator("list_object_versions")
    pending = []
    found_versions = False

    def delete_batch(objects):
        response = client.delete_objects(
            Bucket=bucket, Delete={"Objects": objects, "Quiet": True}
        )
        errors = response.get("Errors") or []
        if errors:
            raise botocore.exceptions.ClientError(
                {
                    "Error": errors[0],
                    "ResponseMetadata": response.get("ResponseMetadata", {}),
                },
                "DeleteObjects",
            )

    for page in paginator.paginate(Bucket=bucket, Prefix=key):
        for collection in ("Versions", "DeleteMarkers"):
            for entry in page.get(collection, []):
                if entry["Key"] != key:
                    continue
                found_versions = True
                pending.append({"Key": key, "VersionId": entry["VersionId"]})
                if len(pending) == 1000:
                    delete_batch(pending)
                    pending = []
    if pending:
        delete_batch(pending)
    if not found_versions:
        client.delete_object(Bucket=bucket, Key=key)


def _delete_layer_objects(client, bucket, layer):
    if layer.upload_id and not layer.upload_completed:
        try:
            client.abort_multipart_upload(
                Bucket=bucket, Key=layer.object_key, UploadId=layer.upload_id
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in (
                "NoSuchUpload",
                "NoSuchKey",
                "404",
            ):
                raise
    _delete_s3_object_versions(client, bucket, layer.object_key)
    if layer.kind == "vector":
        for key in set(_vector_object_keys(layer)) - {layer.object_key}:
            _delete_s3_object_versions(client, bucket, key)


def cleanup_share_objects(share_id):
    """Idempotently remove private objects and abort incomplete uploads."""
    config = _config()
    try:
        uuid.UUID(str(share_id))
    except (ValueError, TypeError, AttributeError):
        return
    share = (
        PublicShare.query.filter_by(id=share_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if share is None or share.cleanup_completed_at is not None:
        return
    now = _now()
    draft_cutoff = now - timedelta(seconds=config["DRAFT_TTL_SECONDS"])
    if (
        share.state in ACTIVE_STATES
        and share.ready_at is None
        and share.created_at <= draft_cutoff
    ):
        share.state = "expired"
        share.token_hash = None
        share.link_ciphertext = None
        share.session_generation += 1
        db.session.commit()
    if share.state == "ready" and share.expires_at and share.expires_at <= now:
        share.state = "expired"
        share.token_hash = None
        share.link_ciphertext = None
        share.session_generation += 1
        db.session.commit()
    removed_layers = [layer for layer in share.layers if layer.removed_at is not None]
    if share.state not in ("revoked", "expired") and not removed_layers:
        return

    try:
        client = _s3_client()
        if (share.state == "revoked" and share.ready_at is not None) or removed_layers:
            invalidate_public_tiles(share)
        if share.state not in ("revoked", "expired"):
            for layer in removed_layers:
                _delete_layer_objects(client, config["S3_BUCKET"], layer)
                share.quota_reserved_bytes -= layer.expected_size_bytes
                db.session.delete(layer)
            share.cleanup_error = None
            db.session.commit()
            return
        for layer in share.layers:
            _delete_layer_objects(client, config["S3_BUCKET"], layer)
        share.quota_reserved_bytes = 0
        share.mask = None
        share.cleanup_error = None
        share.cleanup_completed_at = _now()
        if share.user_id is None:
            db.session.delete(share)
        db.session.commit()
    except (
        PublicShareError,
        botocore.exceptions.BotoCoreError,
        botocore.exceptions.ClientError,
    ) as exc:
        db.session.rollback()
        failed_share = PublicShare.query.filter_by(id=share_id).first()
        if failed_share is not None:
            failed_share.cleanup_error = str(exc)[:500]
            db.session.commit()
        raise


def public_origin_matches(origin_header):
    """Require browser-origin equality before exchanging a public capability."""
    return origin_header == _config().get("PUBLIC_ORIGIN")
