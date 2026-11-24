"""Owner and bearer-authorized APIs for expiring public web-map shares."""

import logging
from urllib.error import HTTPError, URLError
import uuid

import botocore
from flask import Response, current_app, jsonify, request
from flask_jwt_extended import current_user

from gefapi import limiter
from gefapi.models import PublicShare
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.public_share_service import (
    PublicShareError,
    add_layers_to_share,
    complete_share_uploads,
    create_or_refresh_upload,
    create_share,
    create_viewer_session,
    ensure_public_shares_enabled,
    get_public_link,
    get_share_storage_usage,
    get_tile_url,
    invoke_raster_tile,
    load_viewer_session,
    mark_share_revoked,
    mint_public_link,
    open_tile_service,
    public_origin_matches,
    refresh_viewer_session,
    verify_public_secret,
)
from gefapi.utils.bearer_auth import bearer_required
from gefapi.utils.scopes import require_scope

logger = logging.getLogger(__name__)
PUBLIC_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "X-Content-Type-Options": "nosniff",
}


def _expected_error(exc):
    return error(status=exc.status, detail=exc.detail)


def _public_json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers.update(PUBLIC_RESPONSE_HEADERS)
    return response


def _public_error(exc):
    return _public_json({"status": exc.status, "detail": exc.detail}, exc.status)


def _public_share(share_id):
    ensure_public_shares_enabled()
    cookie = request.cookies.get("te_share_session")
    share = load_viewer_session(share_id, cookie) if cookie else None
    if share is None:
        return None
    return share


@endpoints.route("/shares", methods=["POST"])
@bearer_required()
@require_scope("webmap:write")
def create_public_share():
    """Create an owner-only draft and reserve storage quota."""
    try:
        share = create_share(current_user, request.get_json(silent=True))
    except PublicShareError as exc:
        return _expected_error(exc)
    return jsonify(data=share.serialize()), 201


@endpoints.route("/shares", methods=["GET"])
@bearer_required()
@require_scope("webmap:read")
def list_public_shares():
    """List only the authenticated owner's shares; secrets are never returned."""
    try:
        ensure_public_shares_enabled()
    except PublicShareError as exc:
        return _expected_error(exc)
    shares = (
        current_user.public_shares.order_by(PublicShare.created_at.desc())
        .limit(100)
        .all()
    )
    return jsonify(data=[share.serialize() for share in shares])


@endpoints.route("/shares/storage", methods=["GET"])
@bearer_required()
@require_scope("webmap:read")
def get_public_share_storage():
    """Report the owner's storage quota, uploaded bytes, and reservations."""
    try:
        ensure_public_shares_enabled()
    except PublicShareError as exc:
        return _expected_error(exc)
    response = jsonify(data=get_share_storage_usage(current_user))
    response.headers["Cache-Control"] = "no-store"
    return response


@endpoints.route("/shares/<share_id>", methods=["GET"])
@bearer_required()
@require_scope("webmap:read")
def get_public_share(share_id):
    """Return one owner's share status without capability or storage secrets."""
    from gefapi.services.public_share_service import _share_query

    try:
        ensure_public_shares_enabled()
        share = _share_query(share_id, current_user.id)
    except PublicShareError as exc:
        return _expected_error(exc)
    return jsonify(data=share.serialize())


@endpoints.route("/shares/<share_id>/link", methods=["GET"])
@bearer_required()
@require_scope("webmap:write")
def get_public_share_link(share_id):
    """Return the owner's current unlisted link for an active share."""
    from gefapi.services.public_share_service import _share_query

    try:
        ensure_public_shares_enabled()
        share = _share_query(share_id, current_user.id)
        link = get_public_link(share)
    except PublicShareError as exc:
        return _expected_error(exc)
    response = jsonify(
        data={
            "id": str(share.id),
            "link": link,
            "expires_at": share.serialize()["expires_at"],
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@endpoints.route("/shares/<share_id>/layers", methods=["POST"])
@bearer_required()
@require_scope("webmap:write")
def add_public_share_layers(share_id):
    """Append validated layer declarations to an active published share."""
    try:
        share = add_layers_to_share(
            share_id, current_user.id, request.get_json(silent=True)
        )
    except PublicShareError as exc:
        return _expected_error(exc)
    return jsonify(data=share.serialize()), 201


@endpoints.route("/shares/<share_id>/uploads", methods=["POST"])
@bearer_required()
@require_scope("webmap:write")
def create_public_share_upload(share_id):
    """Create or refresh fixed-size, short-lived multipart part URLs."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"layer_id"}:
        return error(400, "Request must contain only layer_id")
    try:
        result = create_or_refresh_upload(share_id, body["layer_id"], current_user.id)
    except PublicShareError as exc:
        return _expected_error(exc)
    except botocore.exceptions.BotoCoreError:
        logger.exception("Could not create public share upload for %s", share_id)
        return error(502, "Storage service is temporarily unavailable")
    response = jsonify(data=result)
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@endpoints.route("/shares/<share_id>/complete", methods=["POST"])
@bearer_required()
@require_scope("webmap:write")
def complete_public_share_upload(share_id):
    """Complete every declared upload and enqueue private GIS validation."""
    try:
        share, enqueue_validation = complete_share_uploads(
            share_id, current_user.id, request.get_json(silent=True)
        )
    except PublicShareError as exc:
        return _expected_error(exc)
    except botocore.exceptions.BotoCoreError:
        logger.exception("Could not complete public share upload for %s", share_id)
        return error(502, "Storage service could not complete the upload")
    if enqueue_validation:
        from kombu.exceptions import OperationalError

        from gefapi.tasks.public_shares import validate_public_share_task

        try:
            validate_public_share_task.delay(str(share.id))
        except OperationalError:
            logger.exception(
                "Could not enqueue validation for public share %s", share_id
            )
            return error(
                503, "Validation queue is temporarily unavailable; retry completion"
            )
    return jsonify(data=share.serialize()), 202


@endpoints.route("/shares/<share_id>/publish", methods=["POST"])
@bearer_required()
@require_scope("webmap:write")
def publish_public_share(share_id):
    """Atomically set the retention window and return the public URL once."""
    from gefapi.services.public_share_service import _share_query

    try:
        ensure_public_shares_enabled()
        share = _share_query(share_id, current_user.id, lock=True)
        link = mint_public_link(share)
    except PublicShareError as exc:
        return _expected_error(exc)
    response = jsonify(data={"share": share.serialize(), "link": link})
    response.headers["Cache-Control"] = "no-store"
    return response, 200


@endpoints.route("/shares/<share_id>/rotate", methods=["POST"])
@bearer_required()
@require_scope("webmap:write")
def rotate_public_share_link(share_id):
    """Rotate a link without extending its original expiry."""
    from gefapi.services.public_share_service import _share_query

    try:
        ensure_public_shares_enabled()
        share = _share_query(share_id, current_user.id, lock=True)
        link = mint_public_link(share, rotate=True)
    except PublicShareError as exc:
        return _expected_error(exc)
    response = jsonify(data={"share": share.serialize(), "link": link})
    response.headers["Cache-Control"] = "no-store"
    return response, 200


@endpoints.route("/shares/<share_id>", methods=["DELETE"])
@bearer_required()
@require_scope("webmap:write")
def revoke_public_share(share_id):
    """Revoke immediately; asynchronous cleanup removes objects and uploads."""
    from gefapi.services.public_share_service import _share_query

    try:
        ensure_public_shares_enabled()
        share = _share_query(share_id, current_user.id, lock=True)
        mark_share_revoked(share)
    except PublicShareError as exc:
        return _expected_error(exc)
    cleanup_scheduled = True
    from kombu.exceptions import OperationalError

    from gefapi.tasks.public_shares import cleanup_public_shares_task

    try:
        cleanup_public_shares_task.delay()
    except OperationalError:
        cleanup_scheduled = False
        logger.exception("Revoked public share cleanup awaits periodic recovery")
    response = jsonify(
        data={"share": share.serialize(), "cleanup_scheduled": cleanup_scheduled}
    )
    response.status_code = 202
    return response


@endpoints.route("/public-shares/<share_id>/session", methods=["POST"])
@limiter.limit("10 per minute")
def exchange_public_share_secret(share_id):
    """Exchange a fragment-delivered bearer secret for an HttpOnly cookie."""
    try:
        ensure_public_shares_enabled()
    except PublicShareError as exc:
        return _public_error(exc)
    if not public_origin_matches(request.headers.get("Origin")):
        return _public_json({"status": 403, "detail": "Origin is not allowed"}, 403)
    try:
        uuid.UUID(str(share_id))
    except (ValueError, TypeError, AttributeError):
        return _public_json({"status": 404, "detail": "Share is unavailable"}, 404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"secret"}:
        return _public_json({"status": 400, "detail": "Secret is required"}, 400)
    share = PublicShare.query.filter_by(id=share_id).first()
    if (
        share is None
        or not share.is_accessible()
        or not verify_public_secret(share, body["secret"])
    ):
        return _public_json({"status": 404, "detail": "Share is unavailable"}, 404)
    cookie_value = create_viewer_session(share)
    response = _public_json(
        {
            "data": {
                "expires_in": current_app.config["PUBLIC_SHARES"][
                    "VIEWER_SESSION_TTL_SECONDS"
                ]
            }
        }
    )
    response.set_cookie(
        "te_share_session",
        cookie_value,
        max_age=current_app.config["PUBLIC_SHARES"]["VIEWER_SESSION_TTL_SECONDS"],
        secure=True,
        httponly=True,
        samesite="Strict",
        path=f"/api/v1/public-shares/{share.id}",
    )
    return response


@endpoints.route("/public-shares/<share_id>/session/refresh", methods=["POST"])
def refresh_public_share_session(share_id):
    """Refresh a short viewer session only from the same trusted origin."""
    try:
        ensure_public_shares_enabled()
    except PublicShareError as exc:
        return _public_error(exc)
    if not public_origin_matches(request.headers.get("Origin")):
        return _public_json({"status": 403, "detail": "Origin is not allowed"}, 403)
    share = _public_share(share_id)
    if share is None:
        return _public_json({"status": 404, "detail": "Share is unavailable"}, 404)
    refreshed_session = refresh_viewer_session(
        share, request.cookies.get("te_share_session")
    )
    if refreshed_session is None:
        return _public_json({"status": 404, "detail": "Share is unavailable"}, 404)
    refreshed_cookie, absolute_lifetime_remaining = refreshed_session
    cookie_lifetime = min(
        current_app.config["PUBLIC_SHARES"]["VIEWER_SESSION_TTL_SECONDS"],
        absolute_lifetime_remaining,
    )
    response = _public_json({"data": {"expires_in": cookie_lifetime}})
    response.set_cookie(
        "te_share_session",
        refreshed_cookie,
        max_age=cookie_lifetime,
        secure=True,
        httponly=True,
        samesite="Strict",
        path=f"/api/v1/public-shares/{share.id}",
    )
    return response


@endpoints.route("/public-shares/<share_id>/manifest", methods=["GET"])
def get_public_share_manifest(share_id):
    """Return only the validated presentation metadata for an authorized share."""
    try:
        share = _public_share(share_id)
    except PublicShareError as exc:
        return _public_error(exc)
    if share is None:
        return _public_json({"status": 404, "detail": "Share is unavailable"}, 404)
    return _public_json(
        {
            "data": {
                "id": str(share.id),
                "title": share.title,
                "expires_at": share.expires_at.isoformat() + "Z",
                "layers": [
                    {
                        "id": str(layer.id),
                        "name": layer.name,
                        "kind": layer.kind,
                        "band": layer.band,
                        "style": layer.style,
                        "attribution": layer.attribution,
                        "bounds": layer.bounds,
                        "crs": layer.crs,
                        "nodata": layer.nodata,
                        "vector_metadata": layer.vector_metadata,
                        "tile_format": "pbf" if layer.kind == "vector" else "png",
                        "tiles": (
                            f"/api/v1/public-shares/{share.id}/tiles/{layer.id}/"
                            "{z}/{x}/{y}."
                            + ("pbf" if layer.kind == "vector" else "png")
                        ),
                    }
                    for layer in share.layers
                    if layer.published
                ],
            }
        }
    )


@endpoints.route(
    "/public-shares/<share_id>/tiles/<layer_id>/<int:z>/<int:x>/<int:y>.png",
    methods=["GET"],
)
@endpoints.route(
    "/public-shares/<share_id>/tiles/<layer_id>/<int:z>/<int:x>/<int:y>.pbf",
    methods=["GET"],
    defaults={"tile_format": "pbf"},
)
def get_public_share_tile(share_id, layer_id, z, x, y, tile_format="png"):
    """Authorize each tile before a bounded request to TiTiler or Martin."""
    try:
        share = _public_share(share_id)
    except PublicShareError as exc:
        return _public_error(exc)
    if share is None:
        return _public_json({"status": 404, "detail": "Share is unavailable"}, 404)
    maximum_zoom = current_app.config["PUBLIC_SHARES"]["MAX_TILE_ZOOM"]
    if z < 0 or z > maximum_zoom or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
        return _public_json({"status": 400, "detail": "Invalid tile coordinates"}, 400)
    layer = next((item for item in share.layers if str(item.id) == layer_id), None)
    if (
        layer is None
        or not layer.published
        or not layer.checksum_sha256
        or tile_format != ("pbf" if layer.kind == "vector" else "png")
    ):
        return _public_json({"status": 404, "detail": "Layer is unavailable"}, 404)
    if layer.kind == "vector" and not (
        layer.vector_metadata["minzoom"] <= z <= layer.vector_metadata["maxzoom"]
    ):
        response = Response(status=204)
        response.headers.update(PUBLIC_RESPONSE_HEADERS)
        response.headers["Vary"] = "Cookie"
        return response
    try:
        tile_service = (
            invoke_raster_tile(share, layer, z, x, y)
            if layer.kind == "raster"
            and current_app.config["PUBLIC_SHARES"].get("TITILER_LAMBDA_ARN")
            else open_tile_service(get_tile_url(share, layer, z, x, y), timeout=10)
        )
        with tile_service as tile_response:
            content_type = tile_response.headers.get_content_type()
            encoding = tile_response.headers.get("Content-Encoding")
            status = tile_response.status
            tile = tile_response.read(2 * 1024 * 1024 + 1)
            allowed_types = (
                {"application/x-protobuf", "application/vnd.mapbox-vector-tile"}
                if layer.kind == "vector"
                else {"image/png"}
            )
            if (
                status not in (200, 204)
                or (status == 204 and (layer.kind != "vector" or tile))
                or (status == 200 and content_type not in allowed_types)
                or len(tile) > 2 * 1024 * 1024
                or encoding not in (None, "gzip")
            ):
                logger.error(
                    "Renderer returned an invalid tile for share %s layer %s",
                    share_id,
                    layer_id,
                )
                return _public_json(
                    {
                        "status": 502,
                        "detail": "Tile service returned an invalid response",
                    },
                    502,
                )
    except PublicShareError as exc:
        return _public_error(exc)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.warning(
            "Authorized renderer request failed for share %s layer %s (%s)",
            share_id,
            layer_id,
            type(exc).__name__,
        )
        status_code = (
            503
            if layer.kind == "vector" and isinstance(exc, HTTPError) and exc.code == 404
            else 502
        )
        return _public_json(
            {
                "status": status_code,
                "detail": "Tile service is temporarily unavailable",
            },
            status_code,
        )
    response = Response(
        tile,
        status=status,
        mimetype=(
            "application/vnd.mapbox-vector-tile"
            if layer.kind == "vector"
            else "image/png"
        ),
    )
    if encoding and status == 200:
        response.headers["Content-Encoding"] = encoding
    response.headers.update(PUBLIC_RESPONSE_HEADERS)
    response.headers["Vary"] = "Cookie"
    return response
