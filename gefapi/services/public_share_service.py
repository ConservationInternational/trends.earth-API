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
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
import uuid

import boto3
import botocore
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import PublicShare, PublicShareLayer, User

SHARE_SECRET_BYTES = 32
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
VECTOR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,119}$")
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


def _validate_style(style):
    if not isinstance(style, dict):
        raise PublicShareError(400, "Each layer requires a supported style")
    kind = style.get("kind")
    if kind == "continuous":
        if style.keys() != {"kind", "min", "max", "palette"}:
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
            or style.get("palette") not in ALLOWED_PALETTES
        ):
            raise PublicShareError(400, "Continuous style values are invalid")
        return {
            "kind": kind,
            "min": float(minimum),
            "max": float(maximum),
            "palette": style["palette"],
        }
    if kind == "categorical":
        values = style.get("values")
        if style.keys() != {"kind", "values"} or not isinstance(values, dict):
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
        return {"kind": kind, "values": safe_values}
    raise PublicShareError(400, "Style kind must be continuous or categorical")


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
            or category.keys() != {"field", "values"}
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


def create_share(user, body):
    """Create a quota-reserved draft with server-generated layer object keys."""
    ensure_public_shares_enabled()
    if not isinstance(body, dict):
        raise PublicShareError(400, "JSON object required")
    if body.keys() - {"title", "retention_days", "layers"}:
        raise PublicShareError(400, "Unsupported share fields")
    title = body.get("title")
    retention_days = body.get("retention_days")
    layers = body.get("layers")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
        raise PublicShareError(400, "title must contain 1 to 120 characters")
    if type(retention_days) is not int or retention_days not in (7, 30, 90):
        raise PublicShareError(400, "retention_days must be 7, 30, or 90")
    config = _config()
    if not isinstance(layers, list) or not layers or len(layers) > config["MAX_LAYERS"]:
        raise PublicShareError(
            400, f"layers must contain 1 to {config['MAX_LAYERS']} items"
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
        retention_days=retention_days,
        state="draft",
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
    reserved_bytes = int(reserved_bytes or 0)
    uploaded_bytes = int(uploaded_bytes or 0)
    cleanup_pending_bytes = int(cleanup_pending_bytes or 0)
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
    if share.state not in ("draft", "uploading", "failed"):
        raise PublicShareError(409, "Share is not accepting uploads")
    layer = next((item for item in share.layers if str(item.id) == str(layer_id)), None)
    if layer is None:
        raise PublicShareError(404, "Layer not found")

    config = _config()
    client = _s3_client()
    part_size = config["MULTIPART_PART_BYTES"]
    if part_size < 5 * 1024**2:
        raise PublicShareError(503, "Multipart part size must be at least 5 MiB")
    part_count = math.ceil(layer.expected_size_bytes / part_size)
    if part_count > 10_000:
        raise PublicShareError(400, "Layer requires too many multipart parts")
    if layer.upload_completed:
        if share.state != "failed":
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
    share.state = "uploading"
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
    if share.state == "ready" and all(layer.upload_completed for layer in share.layers):
        return share, False
    if share.state == "validating" and all(
        layer.upload_completed for layer in share.layers
    ):
        return share, True
    if share.state not in ("uploading", "failed"):
        raise PublicShareError(409, "Share is not ready for upload completion")
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
    if set(part_map) != {str(layer.id) for layer in share.layers}:
        raise PublicShareError(400, "Completion manifest must include every layer")

    client = _s3_client()
    config = _config()
    for layer in share.layers:
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
    if share is None or share.state != "validating":
        return
    bucket = _config()["S3_BUCKET"]
    failures = []
    for layer in share.layers:
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
        share.state = "failed"
    else:
        share.state = "ready"
    db.session.commit()


def mint_public_link(share, *, rotate=False):
    """Mint a one-time bearer secret and activate or rotate the public link."""
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
        if layer.kind == "vector":
            _ensure_martin_source_ready(layer)
    secret = secrets.token_urlsafe(SHARE_SECRET_BYTES)
    share.token_hash = hashlib.sha256(secret.encode("ascii")).hexdigest()
    share.session_generation += 1
    if not rotate:
        share.ready_at = now
        share.expires_at = now + timedelta(days=share.retention_days)
    db.session.commit()
    viewer_url = _config()["VIEWER_BASE_URL"].rstrip("/")
    link = f"{viewer_url}/{quote(str(share.id), safe='')}#{secret}"
    return link


def verify_public_secret(share, supplied_secret):
    """Compare a fragment-delivered capability without exposing it in storage."""
    if not isinstance(supplied_secret, str) or not 32 <= len(supplied_secret) <= 128:
        return False
    expected = share.token_hash
    if not expected:
        return False
    actual = hashlib.sha256(supplied_secret.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, actual)


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


def open_tile_service(url, timeout=10):
    """Do not forward trusted internal requests to an upstream redirect target."""
    return build_opener(_NoTileRedirects()).open(url, timeout=timeout)


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
        query["colormap_name"] = layer.style["palette"]
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
    def __init__(self, tile, content_type):
        super().__init__(tile)
        self.status = 200
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
                connect_timeout=3,
                read_timeout=15,
                retries={"total_max_attempts": 1},
            ),
        )
        result = client.invoke(
            FunctionName=arn,
            InvocationType="RequestResponse",
            LogType="None",
            Payload=json.dumps(event).encode("utf-8"),
        )
        with closing(result["Payload"]) as payload:
            raw = payload.read(4 * 1024**2 + 1)
        if result.get("StatusCode") != 200 or result.get("FunctionError"):
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
        return
    now = _now()
    share.state = "revoked"
    share.revoked_at = share.revoked_at or now
    share.token_hash = None
    share.session_generation += 1
    share.cleanup_error = None
    db.session.commit()


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
        share.session_generation += 1
        db.session.commit()
    if share.state == "ready" and share.expires_at and share.expires_at <= now:
        share.state = "expired"
        share.token_hash = None
        share.session_generation += 1
        db.session.commit()
    if share.state not in ("revoked", "expired"):
        return

    client = _s3_client()
    try:
        for layer in share.layers:
            if layer.upload_id and not layer.upload_completed:
                try:
                    client.abort_multipart_upload(
                        Bucket=config["S3_BUCKET"],
                        Key=layer.object_key,
                        UploadId=layer.upload_id,
                    )
                except botocore.exceptions.ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code")
                    if code not in ("NoSuchUpload", "NoSuchKey", "404"):
                        raise
            _delete_s3_object_versions(client, config["S3_BUCKET"], layer.object_key)
            if layer.kind == "vector":
                for key in set(_vector_object_keys(layer)) - {layer.object_key}:
                    _delete_s3_object_versions(client, config["S3_BUCKET"], key)
        share.quota_reserved_bytes = 0
        share.cleanup_error = None
        share.cleanup_completed_at = _now()
        if share.user_id is None:
            db.session.delete(share)
        db.session.commit()
    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as exc:
        db.session.rollback()
        failed_share = PublicShare.query.filter_by(id=share_id).first()
        if failed_share is not None:
            failed_share.cleanup_error = str(exc)[:500]
            db.session.commit()
        raise


def public_origin_matches(origin_header):
    """Require browser-origin equality before exchanging a public capability."""
    return origin_header == _config().get("PUBLIC_ORIGIN")
