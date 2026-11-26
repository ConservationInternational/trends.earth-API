"""Tests for expiring public web-map sharing."""

import base64
from datetime import UTC, datetime, timedelta
from email.message import Message
import gzip
import hashlib
import importlib.util
import io
import json
import logging
from pathlib import Path
import shutil
import struct
import subprocess
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import parse_qs
import uuid

import botocore
from flask_jwt_extended import create_access_token
from itsdangerous import URLSafeTimedSerializer
import pytest

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import PublicShare, PublicShareLayer, User
from gefapi.services.public_share_service import (
    PublicShareError,
    _delete_s3_object_versions,
    _raster_tile_request,
    _validate_mask,
    _validate_style,
    _validate_vector_style,
    create_share,
    create_viewer_session,
    load_viewer_session,
    mark_share_revoked,
    mint_public_link,
)


@pytest.fixture
def public_share_config(monkeypatch, app):
    configured = {
        "ENABLED": True,
        "S3_BUCKET": "private-webmap-shares",
        "VIEWER_BASE_URL": "https://viewer.example/share",
        "PUBLIC_ORIGIN": "https://viewer.example",
        "TITILER_INTERNAL_URL": "http://titiler:8000/",
        "TITILER_LAMBDA_ARN": None,
        "CDN_DISTRIBUTION_ID": None,
        "MARTIN_INTERNAL_URL": "http://martin:3000/",
        "SESSION_SECRET": "share-session-test-secret-" * 2,
        "MAX_VIEWER_SESSION_LIFETIME_SECONDS": 86400,
    }
    for key, value in configured.items():
        monkeypatch.setitem(SETTINGS["PUBLIC_SHARES"], key, value)
    app.config["JWT_SECRET_KEY"] = "public-share-tests-jwt-secret-32"
    return configured


def _payload():
    return {
        "title": "Local analysis",
        "retention_days": 7,
        "layers": [
            {
                "name": "Land cover",
                "band": 1,
                "size_bytes": 1024,
                "sha256": hashlib.sha256(b"raster").hexdigest(),
                "style": {
                    "kind": "categorical",
                    "values": {"1": "#00ff00", "2": "#ff0000"},
                },
                "attribution": "Trends.Earth",
            }
        ],
    }


def _headers(user):
    return {
        "Authorization": f"Bearer {create_access_token(identity=user.id)}",
        "Content-Type": "application/json",
    }


def test_remove_layers_hides_tiles_and_retries_cleanup(
    app, client, regular_user, admin_user, public_share_config, monkeypatch
):
    from gefapi.services import public_share_service as service
    from gefapi.tasks.public_shares import cleanup_public_shares_task

    payload = _payload()
    payload["layers"].append({**payload["layers"][0], "name": "Second"})
    share = create_share(regular_user, payload)
    share.state = "ready"
    first, second = share.layers
    for layer in share.layers:
        layer.published = True
        layer.upload_completed = True
        layer.checksum_sha256 = layer.expected_sha256
    second.kind = "vector"
    second.object_key = f"public-shares/vector/{second.id}.pmtiles"
    share_id, first_id, second_id = str(share.id), str(first.id), str(second.id)
    first_key = first.object_key
    vector_keys = set(service._vector_object_keys(second))
    db.session.commit()
    monkeypatch.setattr(service, "_ensure_martin_source_ready", Mock())
    link = mint_public_link(share)
    token = link.rsplit("/", 1)[1]
    manifest_url = f"/api/v1/public-shares/s/{token}/manifest"
    url = f"/api/v1/shares/{share_id}/layers"
    monkeypatch.setattr(cleanup_public_shares_task, "delay", lambda: None)

    assert (
        client.delete(
            url, json={"layer_ids": [first_id]}, headers=_headers(admin_user)
        ).status_code
        == 404
    )
    assert (
        client.delete(
            url,
            json={"layer_ids": [first_id, first_id]},
            headers=_headers(regular_user),
        ).status_code
        == 400
    )
    assert (
        client.delete(
            url,
            json={"layer_ids": [first_id, str(uuid.uuid4())]},
            headers=_headers(regular_user),
        ).status_code
        == 404
    )
    assert len(client.get(manifest_url).json["data"]["layers"]) == 2
    share.update_state = "validating"
    db.session.commit()
    assert (
        client.delete(
            url, json={"layer_ids": [first_id]}, headers=_headers(regular_user)
        ).status_code
        == 409
    )
    share.update_state = "idle"
    db.session.commit()

    response = client.delete(
        url, json={"layer_ids": [first_id, second_id]}, headers=_headers(regular_user)
    )
    assert response.status_code == 202
    assert response.json["data"]["cleanup_scheduled"] is True
    assert response.json["data"]["share"]["layers"] == []
    assert client.get(manifest_url).json["data"]["layers"] == []
    tile = f"/api/v1/public-shares/tiles/{share_id}/{token}/{first_id}/0/0/0.png"
    assert client.get(tile).status_code == 404
    assert service.get_public_link(share) == link
    usage = client.get("/api/v1/shares/storage", headers=_headers(regular_user))
    assert usage.json["data"]["reserved_bytes"] == 2048
    assert usage.json["data"]["cleanup_pending_bytes"] == 2048

    invalidate = Mock(side_effect=PublicShareError(503, "CDN unavailable"))
    delete = Mock()
    monkeypatch.setattr(service, "invalidate_public_tiles", invalidate)
    monkeypatch.setattr(service, "_s3_client", Mock(return_value=Mock()))
    monkeypatch.setattr(service, "_delete_s3_object_versions", delete)
    assert cleanup_public_shares_task.run() == {"cleaned": 0, "failures": 1}
    assert delete.call_count == 0
    assert PublicShare.query.filter_by(id=share_id).one().quota_reserved_bytes == 2048

    invalidate.side_effect = None
    delete.side_effect = [
        None,
        botocore.exceptions.ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "retry"}},
            "DeleteObjects",
        ),
    ]
    assert cleanup_public_shares_task.run() == {"cleaned": 0, "failures": 1}
    db.session.expire_all()
    assert PublicShare.query.filter_by(id=share_id).one().quota_reserved_bytes == 2048
    assert client.get(manifest_url).json["data"]["layers"] == []

    delete.side_effect = None
    assert cleanup_public_shares_task.run() == {"cleaned": 1, "failures": 0}
    db.session.expire_all()
    cleaned = PublicShare.query.filter_by(id=share_id).one()
    assert cleaned.quota_reserved_bytes == 0
    assert cleaned.layers == []
    assert {call.args[2] for call in delete.call_args_list} == {first_key, *vector_keys}
    assert client.get(manifest_url).json["data"]["layers"] == []
    assert service.get_public_link(cleaned) == link
    added = client.post(
        url, json={"layers": [payload["layers"][0]]}, headers=_headers(regular_user)
    )
    assert added.status_code == 201
    assert added.json["data"]["layers"][0]["name"] == payload["layers"][0]["name"]


def test_removed_layer_migration_blocks_unsafe_downgrade(monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import sqlalchemy as sa

    path = (
        Path(__file__).parents[1]
        / "migrations/versions/9c4f7a3d8b20_remove_public_share_layers.py"
    )
    spec = importlib.util.spec_from_file_location("removed_layer_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE public_share_layer (id TEXT)"))
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO public_share_layer (id, removed_at) "
                "VALUES ('removed', '2026-01-01')"
            )
        )
        with pytest.raises(RuntimeError, match="cleanup"):
            migration.downgrade()
        connection.execute(sa.text("UPDATE public_share_layer SET removed_at = NULL"))
        migration.downgrade()
        assert [
            column["name"]
            for column in sa.inspect(connection).get_columns("public_share_layer")
        ] == ["id"]


def test_remove_one_layer_preserves_the_other_when_queue_is_unavailable(
    app, client, regular_user, public_share_config, monkeypatch
):
    from kombu.exceptions import OperationalError

    from gefapi.tasks.public_shares import cleanup_public_shares_task

    payload = _payload()
    payload["layers"].append({**payload["layers"][0], "name": "Second"})
    share = create_share(regular_user, payload)
    share.state = "ready"
    first, second = share.layers
    for layer in share.layers:
        layer.published = True
        layer.checksum_sha256 = layer.expected_sha256
    db.session.commit()
    token = mint_public_link(share).rsplit("/", 1)[1]
    monkeypatch.setattr(
        cleanup_public_shares_task, "delay", Mock(side_effect=OperationalError("down"))
    )
    response = client.delete(
        f"/api/v1/shares/{share.id}/layers",
        json={"layer_ids": [str(first.id)]},
        headers=_headers(regular_user),
    )
    assert response.status_code == 202
    assert response.json["data"]["cleanup_scheduled"] is False
    assert [layer["name"] for layer in response.json["data"]["share"]["layers"]] == [
        "Second"
    ]
    manifest = client.get(f"/api/v1/public-shares/s/{token}/manifest")
    assert [layer["id"] for layer in manifest.json["data"]["layers"]] == [
        str(second.id)
    ]


def test_share_mask_creation_updates_and_manifest(
    app, client, regular_user, admin_user, public_share_config
):
    mask = {
        "type": "Polygon",
        "coordinates": [[[-10, -5], [10, -5], [10, 5], [-10, 5], [-10, -5]]],
    }
    payload = _payload()
    payload["mask"] = mask
    share = create_share(regular_user, payload)
    assert share.serialize()["has_mask"] is True
    assert share.mask_version == 0
    share.state = "ready"
    share.layers[0].published = True
    db.session.commit()
    token = mint_public_link(share).rsplit("/", 1)[1]
    manifest_url = f"/api/v1/public-shares/s/{token}/manifest"
    assert client.get(manifest_url).get_json()["data"]["mask"]["type"] == "Polygon"

    url = f"/api/v1/shares/{share.id}/mask"
    assert (
        client.put(
            url, json={"mask": None, "mask_version": 0}, headers=_headers(admin_user)
        ).status_code
        == 404
    )
    response = client.put(
        url, json={"mask": None, "mask_version": 0}, headers=_headers(regular_user)
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["mask_version"] == 1
    assert client.get(manifest_url).get_json()["data"]["mask"] is None
    assert (
        client.put(
            url, json={"mask": mask, "mask_version": 0}, headers=_headers(regular_user)
        ).status_code
        == 409
    )
    assert (
        client.put(
            url, json={"mask": mask, "mask_version": 1}, headers=_headers(regular_user)
        ).status_code
        == 200
    )
    share.state = "revoked"
    db.session.commit()
    assert (
        client.put(
            url, json={"mask": None, "mask_version": 2}, headers=_headers(regular_user)
        ).status_code
        == 409
    )


def test_mask_validation_splits_antimeridian_and_rejects_bad_geometry():
    mask = {
        "type": "Polygon",
        "coordinates": [[[170, -10], [-170, -10], [-170, 10], [170, 10], [170, -10]]],
    }
    result = _validate_mask(mask)
    assert result["type"] == "MultiPolygon"
    assert len(result["coordinates"]) == 2
    with pytest.raises(PublicShareError):
        _validate_mask({"type": "Point", "coordinates": [0, 0]})
    with pytest.raises(PublicShareError):
        _validate_mask(
            {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]],
            }
        )
    with pytest.raises(PublicShareError, match="too many"):
        _validate_mask(
            {
                "type": "Polygon",
                "coordinates": [[[0, 0]] * 30001],
            }
        )


def test_share_cleanup_removes_mask(
    app, regular_user, public_share_config, monkeypatch
):
    from gefapi.services import public_share_service as service

    payload = _payload()
    payload["mask"] = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
    }
    share = create_share(regular_user, payload)
    share.state = "revoked"
    db.session.commit()
    monkeypatch.setattr(service, "_s3_client", Mock())
    monkeypatch.setattr(service, "_delete_s3_object_versions", Mock())
    service.cleanup_share_objects(share.id)
    assert share.cleanup_completed_at is not None
    assert share.mask is None


def test_create_share_reserves_quota_and_returns_no_storage_key(
    app, client, regular_user, public_share_config
):
    response = client.post(
        "/api/v1/shares", json=_payload(), headers=_headers(regular_user)
    )

    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["quota_reserved_bytes"] == 1024
    assert data["retention_days"] == 7
    assert "object_key" not in response.get_data(as_text=True)
    assert "token_hash" not in response.get_data(as_text=True)
    layer = PublicShareLayer.query.filter_by(share_id=data["id"]).one()
    assert layer.object_key.startswith(f"public-shares/{data['id']}/")
    assert layer.object_key.endswith(f"{layer.id}.tif")


def test_empty_share_is_publishable_without_uploads(
    app, client, regular_user, public_share_config
):
    payload = {**_payload(), "layers": []}
    response = client.post(
        "/api/v1/shares", json=payload, headers=_headers(regular_user)
    )
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["state"] == "ready"
    assert data["layers"] == []
    assert data["quota_reserved_bytes"] == 0

    published = client.post(
        f"/api/v1/shares/{data['id']}/publish", headers=_headers(regular_user)
    )
    assert published.status_code == 200
    token = published.get_json()["data"]["link"].rsplit("/", 1)[1]
    manifest = client.get(f"/api/v1/public-shares/s/{token}/manifest")
    assert manifest.status_code == 200
    assert manifest.get_json()["data"]["layers"] == []

    too_many = {**_payload(), "layers": [_payload()["layers"][0]] * 11}
    assert (
        client.post(
            "/api/v1/shares", json=too_many, headers=_headers(regular_user)
        ).status_code
        == 400
    )


def test_share_creation_rejects_unknown_file_path_and_invalid_retention(
    app, client, regular_user, public_share_config
):
    with_path = _payload()
    with_path["layers"][0]["path"] = "/home/user/private.tif"
    response = client.post(
        "/api/v1/shares", json=with_path, headers=_headers(regular_user)
    )
    assert response.status_code == 400

    invalid_retention = _payload()
    invalid_retention["retention_days"] = 14
    response = client.post(
        "/api/v1/shares", json=invalid_retention, headers=_headers(regular_user)
    )
    assert response.status_code == 400


def test_custom_continuous_style_creates_bounded_tile_colormap(
    public_share_config,
):
    from types import SimpleNamespace

    style = _validate_style(
        {
            "kind": "continuous",
            "min": -1,
            "max": 1,
            "stops": [
                {"value": -1, "color": "#0000ff"},
                {"value": 0, "color": "#ffffff"},
                {"value": 1, "color": "#ff0000"},
            ],
        }
    )
    layer = SimpleNamespace(
        object_key="public-shares/share/layer.tif",
        band=1,
        style=style,
    )

    _, query = _raster_tile_request(layer, 0, 0, 0)
    parsed = parse_qs(query)
    colors = json.loads(parsed["colormap"][0])
    assert parsed["rescale"] == ["-1.0,1.0"]
    assert len(colors) == 256
    assert colors["0"] == "#0000ff"
    assert colors["255"] == "#ff0000"
    assert colors["128"].startswith("#")
    with pytest.raises(PublicShareError, match="stops"):
        _validate_style(
            {
                "kind": "continuous",
                "min": 0,
                "max": 1,
                "stops": [
                    {"value": 0.5, "color": "#000000"},
                    {"value": 1, "color": "#ffffff"},
                ],
            }
        )


def test_discrete_labelled_style_renders_qgis_value_ranges(public_share_config):
    from types import SimpleNamespace

    style = _validate_style(
        {
            "kind": "categorical",
            "values": {"9": "#ffffe0", "-10": "#9b2779", "100": "#006500"},
            "labels": {"-10": "Degradation", "9": "Stable", "100": "Improvement"},
            "classification": "discrete",
        }
    )
    assert style["labels"]["9"] == "Stable"
    layer = SimpleNamespace(object_key="key.tif", band=1, style=style)

    _, query = _raster_tile_request(layer, 0, 0, 0)
    parsed = parse_qs(query)
    intervals = json.loads(parsed["colormap"][0])

    def color(value):
        return next(c for (low, high), c in intervals if low <= value < high)

    assert parsed["resampling"] == ["nearest"]
    assert [color(v) for v in (-50, -10)] == ["#9b2779"] * 2
    assert [color(v) for v in (-9, 0, 9)] == ["#ffffe0"] * 3
    assert [color(v) for v in (10, 100, 150)] == ["#006500"] * 3
    for labels in ({"2": "Unknown"}, {"9": ""}, {"9": "Bad\nlabel"}):
        with pytest.raises(PublicShareError, match="labels"):
            _validate_style({**style, "labels": labels})
    with pytest.raises(PublicShareError):
        _validate_style({**style, "classification": "interpolated"})


def test_vector_legend_text_comes_from_style():
    style = {
        "kind": "vector",
        "stroke": "#333333",
        "fill": "#CCCCCC",
        "width": 1,
        "fill_opacity": 0.5,
        "radius": 4,
        "category": {
            "field": "status",
            "values": {"a": "#FF0000"},
            "labels": {"a": "Dégradé"},
            "other_label": "Autres valeurs",
        },
    }
    assert _validate_vector_style(style)["category"]["other_label"] == "Autres valeurs"
    style["category"]["other_label"] = "Bad\nlabel"
    with pytest.raises(PublicShareError, match="labels"):
        _validate_vector_style(style)


def test_share_status_is_owner_only(app, client, regular_user, public_share_config):
    share = create_share(regular_user, _payload())
    other_user = User(
        email="other-share-owner@test.com",
        password="OtherPass123!",
        name="Other User",
        role="USER",
        country="Test Country",
        institution="Test Institution",
    )
    db.session.add(other_user)
    db.session.commit()

    response = client.get(f"/api/v1/shares/{share.id}", headers=_headers(other_user))

    assert response.status_code == 404


def test_storage_usage_reports_uploads_and_retains_quota_until_cleanup(
    app, client, regular_user, public_share_config, monkeypatch
):
    share = create_share(regular_user, _payload())
    layer = share.layers[0]
    layer.upload_completed = True
    share.state = "ready"
    share.ready_at = datetime.now(UTC).replace(tzinfo=None)
    share.expires_at = share.ready_at + timedelta(days=7)
    db.session.commit()

    before_delete = client.get("/api/v1/shares/storage", headers=_headers(regular_user))
    assert before_delete.status_code == 200
    assert before_delete.headers["Cache-Control"] == "no-store"
    usage = before_delete.get_json()["data"]
    assert usage["uploaded_bytes"] == 1024
    assert usage["reserved_bytes"] == 1024
    assert usage["cleanup_pending_bytes"] == 0

    from gefapi.tasks.public_shares import cleanup_public_shares_task

    monkeypatch.setattr(cleanup_public_shares_task, "delay", lambda: None)
    deleted = client.delete(
        f"/api/v1/shares/{share.id}", headers=_headers(regular_user)
    )
    assert deleted.status_code == 202

    pending = client.get("/api/v1/shares/storage", headers=_headers(regular_user))
    usage = pending.get_json()["data"]
    assert usage["uploaded_bytes"] == 1024
    assert usage["reserved_bytes"] == 1024
    assert usage["cleanup_pending_bytes"] == 1024

    share.quota_reserved_bytes = 0
    share.cleanup_completed_at = datetime.now(UTC).replace(tzinfo=None)
    db.session.commit()
    cleaned = client.get("/api/v1/shares/storage", headers=_headers(regular_user))
    usage = cleaned.get_json()["data"]
    assert usage["uploaded_bytes"] == 0
    assert usage["reserved_bytes"] == 0


def test_superadmin_quota_override_is_used_and_reset_to_default(
    app, client, regular_user, superadmin_user, public_share_config
):
    url = f"/api/v1/admin/users/{regular_user.id}/public-share-quota"
    denied = client.put(url, json={"quota_bytes": 2048}, headers=_headers(regular_user))
    assert denied.status_code == 403

    updated = client.put(
        url, json={"quota_bytes": 2048}, headers=_headers(superadmin_user)
    )
    assert updated.status_code == 200
    assert updated.get_json()["data"]["quota_limit_bytes"] == 2048

    usage = client.get("/api/v1/shares/storage", headers=_headers(regular_user))
    assert usage.get_json()["data"]["quota_override_bytes"] == 2048

    create_share(regular_user, _payload())
    create_share(regular_user, _payload())
    with pytest.raises(PublicShareError) as exc_info:
        create_share(regular_user, _payload())
    assert exc_info.value.status == 429

    reset = client.put(
        url, json={"quota_bytes": None}, headers=_headers(superadmin_user)
    )
    assert reset.status_code == 200
    usage = client.get("/api/v1/shares/storage", headers=_headers(regular_user))
    assert (
        usage.get_json()["data"]["quota_limit_bytes"]
        == SETTINGS["PUBLIC_SHARES"]["MAX_RESERVED_BYTES_PER_USER"]
    )


def test_publish_expiry_rotation_and_revocation_invalidate_sessions(
    app, regular_user, public_share_config
):
    share = PublicShare(
        id=str(uuid.uuid4()),
        user_id=regular_user.id,
        title="Validated share",
        retention_days=7,
        state="ready",
        quota_reserved_bytes=0,
    )
    db.session.add(share)
    db.session.commit()

    link = mint_public_link(share)
    secret = link.rsplit("/", 1)[1]
    cookie = create_viewer_session(share)
    assert link.startswith("https://viewer.example/share/s/") and "#" not in link
    assert share.expires_at - share.ready_at == timedelta(days=7)
    assert share.token_hash != secret
    assert load_viewer_session(share.id, cookie) is not None

    rotated_link = mint_public_link(share, rotate=True)
    assert load_viewer_session(share.id, cookie) is None
    assert rotated_link.rsplit("/", 1)[1] != secret
    assert share.expires_at - share.ready_at == timedelta(days=7)

    share.state = "revoked"
    db.session.commit()
    assert load_viewer_session(share.id, create_viewer_session(share)) is None


def test_owner_can_view_current_link_until_rotation_or_revocation(
    app, client, regular_user, admin_user, public_share_config
):
    share = create_share(regular_user, _payload())
    share.state = "ready"
    db.session.commit()
    url = f"/api/v1/shares/{share.id}/link"

    assert client.get(url, headers=_headers(regular_user)).status_code == 409
    link = mint_public_link(share)
    assert share.link_ciphertext and link.rsplit("/", 1)[1] not in share.link_ciphertext

    response = client.get(url, headers=_headers(regular_user))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["data"]["link"] == link
    assert "link_ciphertext" not in client.get(
        f"/api/v1/shares/{share.id}", headers=_headers(regular_user)
    ).get_data(as_text=True)
    assert client.get(url, headers=_headers(admin_user)).status_code == 404

    rotated = mint_public_link(share, rotate=True)
    response = client.get(url, headers=_headers(regular_user))
    assert response.get_json()["data"]["link"] == rotated

    share.link_ciphertext = None
    db.session.commit()
    assert client.get(url, headers=_headers(regular_user)).status_code == 409

    mint_public_link(share, rotate=True)
    mark_share_revoked(share)
    assert share.link_ciphertext is None
    assert client.get(url, headers=_headers(regular_user)).status_code == 409


def test_fragment_exchange_sets_scoped_cookie_and_manifest_is_safe(
    app, client, regular_user, public_share_config
):
    share = create_share(regular_user, _payload())
    share.state = "ready"
    layer = share.layers[0]
    layer.checksum_sha256 = layer.expected_sha256
    layer.published = True
    layer.bounds = [-10.0, -5.0, 10.0, 5.0]
    layer.crs = "EPSG:4326"
    db.session.commit()
    link = mint_public_link(share)
    secret = link.rsplit("/", 1)[1]

    exchange = client.post(
        f"/api/v1/public-shares/{share.id}/session",
        json={"secret": secret},
        headers={"Origin": "https://viewer.example"},
    )
    assert exchange.status_code == 200
    assert exchange.headers["Cache-Control"] == "no-store"
    cookie = client.get_cookie(
        "te_share_session", path=f"/api/v1/public-shares/{share.id}"
    )
    assert cookie is not None
    assert cookie.http_only is True
    assert cookie.secure is True
    assert cookie.path == f"/api/v1/public-shares/{share.id}"

    manifest = client.get(f"/api/v1/public-shares/{share.id}/manifest")
    assert manifest.status_code == 200
    assert manifest.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    manifest_text = manifest.get_data(as_text=True)
    assert "object_key" not in manifest_text
    assert "token_hash" not in manifest_text
    assert layer.name in manifest_text

    added = client.post(
        f"/api/v1/shares/{share.id}/layers",
        json={
            "layers": [
                {
                    "name": "Pending layer",
                    "band": 1,
                    "size_bytes": 64,
                    "sha256": hashlib.sha256(b"pending").hexdigest(),
                    "style": {
                        "kind": "categorical",
                        "values": {"1": "#ff0000"},
                    },
                }
            ]
        },
        headers=_headers(regular_user),
    )
    assert added.status_code == 201
    updated = added.get_json()["data"]
    assert updated["state"] == "ready"
    assert updated["update_state"] == "uploading"
    assert updated["layers"][0]["published"] is True
    assert updated["layers"][1]["published"] is False
    unchanged_manifest = client.get(f"/api/v1/public-shares/{share.id}/manifest")
    assert unchanged_manifest.status_code == 200
    manifest_layers = unchanged_manifest.get_json()["data"]["layers"]
    assert [item["name"] for item in manifest_layers] == [layer.name]

    mark_share_revoked(share)
    manifest = client.get(f"/api/v1/public-shares/{share.id}/manifest")
    assert manifest.status_code == 404


def test_path_token_needs_no_cookie_and_rotation_revocation_expiry_deny_access(
    app, client, regular_user, public_share_config, monkeypatch
):
    from gefapi.routes.api.v1 import public_shares as routes
    from gefapi.services.public_share_service import get_public_link

    share = create_share(regular_user, _payload())
    share.state = "ready"
    layer = share.layers[0]
    layer.checksum_sha256 = layer.expected_sha256
    layer.published = True
    db.session.commit()
    link = mint_public_link(share)
    token = link.rsplit("/", 1)[1]
    manifest_url = f"/api/v1/public-shares/s/{token}/manifest"
    manifest = client.get(manifest_url)
    assert manifest.status_code == 200 and "Set-Cookie" not in manifest.headers
    assert manifest.headers["Cache-Control"] == "no-store"
    tile_url = manifest.json["data"]["layers"][0]["tiles"].format(z=0, x=0, y=0)
    upstream = Mock(return_value=_upstream(b"png", "image/png"))
    monkeypatch.setattr(routes, "open_tile_service", upstream)
    assert client.get(tile_url).status_code == 200
    assert (
        client.get(tile_url.replace(str(share.id), str(uuid.uuid4()))).status_code
        == 404
    )
    assert client.get(manifest_url.replace(token, "x" * 43)).status_code == 404
    assert client.get(f"/api/v1/public-shares/{share.id}/manifest").status_code == 404
    assert get_public_link(share) == link

    new_token = mint_public_link(share, rotate=True).rsplit("/", 1)[1]
    assert client.get(manifest_url).status_code == 404
    assert client.get(tile_url).status_code == 404
    manifest_url = manifest_url.replace(token, new_token)
    tile_url = tile_url.replace(token, new_token)
    assert client.get(manifest_url).status_code == 200
    share.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    db.session.commit()
    assert client.get(manifest_url).status_code == 404
    assert client.get(tile_url).status_code == 404
    share.expires_at += timedelta(days=1)
    db.session.commit()
    mark_share_revoked(share)
    assert client.get(manifest_url).status_code == 404
    assert client.get(tile_url).status_code == 404


def test_public_token_paths_are_excluded_from_access_logs_and_redacted_in_events():
    from gefapi.utils import redact_public_share_url

    spec = importlib.util.spec_from_file_location(
        "public_share_gunicorn", Path(__file__).parents[1] / "gunicorn.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = "a" * 43
    for path in [
        f"/api/v1/public-shares/s/{token}/manifest",
        f"/api/v1/public-shares/tiles/share-id/{token}/layer/0/0/0.png",
    ]:
        assert token not in redact_public_share_url(f"https://viewer.example{path}")
        record = logging.LogRecord(
            "access", logging.INFO, "", 1, "%(U)s", ({"U": path},), None
        )
        assert module.PublicShareAccessFilter().filter(record) is False
    record.args = {"U": "/health"}
    assert module.PublicShareAccessFilter().filter(record) is True


def test_cdn_cache_lifetime_is_four_days_or_remaining_expiry_and_purges_all_tokens(
    app, client, regular_user, public_share_config, monkeypatch
):
    from gefapi.routes.api.v1 import public_shares as routes
    from gefapi.services import public_share_service as service

    share = create_share(regular_user, _payload())
    _publish_vector(share, monkeypatch, client)
    token = service.get_public_link(share).rsplit("/", 1)[1]
    url = (
        f"/api/v1/public-shares/tiles/{share.id}/{token}/{share.layers[0].id}/0/0/0.png"
    )
    upstream = Mock(return_value=_upstream(b"png", "image/png"))
    monkeypatch.setattr(routes, "open_tile_service", upstream)
    monkeypatch.setitem(SETTINGS["PUBLIC_SHARES"], "CDN_DISTRIBUTION_ID", "E123")
    response = client.get(url)
    assert response.status_code == 200
    assert response.cache_control.s_maxage == 4 * 86400
    assert response.cache_control.max_age == 0
    assert "Vary" not in response.headers or "Cookie" not in response.headers["Vary"]
    share.expires_at = service._now() + timedelta(seconds=100)
    db.session.commit()
    response = client.get(url)
    assert 95 <= response.cache_control.s_maxage < 100

    monkeypatch.setattr(routes, "open_tile_service", Mock(side_effect=TimeoutError()))
    response = client.get(url)
    assert response.status_code == 504
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Retry-After"] == "1"

    sdk = Mock()
    monkeypatch.setattr(service.boto3, "client", Mock(return_value=sdk))
    mint_public_link(share, rotate=True)
    mark_share_revoked(share)
    assert sdk.create_invalidation.call_count == 2
    options = sdk.create_invalidation.call_args.kwargs
    assert options["DistributionId"] == "E123"
    assert options["InvalidationBatch"]["Paths"]["Items"] == [
        f"/api/v1/public-shares/tiles/{share.id}/*"
    ]
    sdk.create_invalidation.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "CreateInvalidation"
    )
    with pytest.raises(PublicShareError, match="CDN invalidation failed"):
        mark_share_revoked(share)
    assert share.state == "revoked"
    sdk.create_invalidation.side_effect = None
    mark_share_revoked(share)
    assert sdk.create_invalidation.call_count == 4


@pytest.mark.parametrize("kind", ["raster", "vector"])
def test_tiles_outside_layer_bounds_are_empty_without_invoking_renderer(
    app, client, regular_user, public_share_config, monkeypatch, kind
):
    from gefapi.routes.api.v1 import public_shares as routes

    share = create_share(
        regular_user, _payload() if kind == "raster" else _vector_payload()
    )
    _publish_vector(share, monkeypatch, client)
    layer = share.layers[0]
    layer.bounds = [10, 10, 20, 20]
    db.session.commit()
    upstream = Mock(
        return_value=_upstream(
            b"tile", "image/png" if kind == "raster" else "application/x-protobuf"
        )
    )
    monkeypatch.setattr(routes, "open_tile_service", upstream)
    extension = "png" if kind == "raster" else "pbf"
    base = f"/api/v1/public-shares/{share.id}/tiles/{layer.id}"
    assert client.get(f"{base}/2/0/0.{extension}").status_code == 204
    upstream.assert_not_called()
    assert client.get(f"{base}/0/0/0.{extension}").status_code == 200


def test_cleanup_retries_account_deletion_cache_invalidation_before_removing_share(
    app, client, regular_user, public_share_config, monkeypatch
):
    from gefapi.services import public_share_service as service
    from gefapi.tasks.public_shares import cleanup_public_shares_task

    share = create_share(regular_user, _payload())
    _publish_vector(share, monkeypatch, client)
    share_id = share.id
    share.user_id = None
    share.state = "revoked"
    share.token_hash = None
    db.session.commit()
    monkeypatch.setitem(SETTINGS["PUBLIC_SHARES"], "CDN_DISTRIBUTION_ID", "E123")
    cdn = Mock()
    cdn.create_invalidation.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ServiceUnavailable", "Message": "retry"}},
        "CreateInvalidation",
    )
    storage = Mock()
    storage.get_paginator.return_value.paginate.return_value = []
    monkeypatch.setattr(service, "_s3_client", lambda: storage)
    monkeypatch.setattr(service.boto3, "client", Mock(return_value=cdn))
    assert cleanup_public_shares_task.run() == {"cleaned": 0, "failures": 1}
    storage.delete_object.assert_not_called()
    assert PublicShare.query.filter_by(id=share_id).one().cleanup_error
    cdn.create_invalidation.side_effect = None
    assert cleanup_public_shares_task.run() == {"cleaned": 1, "failures": 0}
    assert PublicShare.query.filter_by(id=share_id).first() is None
    storage.delete_object.assert_called_once()


def test_token_index_migration_preserves_existing_capabilities(monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import sqlalchemy as sa

    path = (
        Path(__file__).parents[1]
        / "migrations/versions/ce4a8f2d109b_index_public_share_tokens.py"
    )
    spec = importlib.util.spec_from_file_location("token_index_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE public_share (token_hash VARCHAR(64))")
        )
        connection.execute(
            sa.text("INSERT INTO public_share VALUES ('existing-hash'), (NULL)")
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        assert sa.inspect(connection).get_indexes("public_share")[0][
            "column_names"
        ] == ["token_hash"]
        migration.downgrade()
        assert not sa.inspect(connection).get_indexes("public_share")
        assert connection.execute(
            sa.text("SELECT token_hash FROM public_share")
        ).scalars().all() == ["existing-hash", None]
    engine.dispose()


def test_refresh_preserves_absolute_session_lifetime_and_rejects_expired_cookie(
    app, client, regular_user, public_share_config
):
    share = create_share(regular_user, _payload())
    share.state = "ready"
    share.layers[0].checksum_sha256 = share.layers[0].expected_sha256
    share.layers[0].published = True
    share.layers[0].bounds = [-10.0, -5.0, 10.0, 5.0]
    share.layers[0].crs = "EPSG:4326"
    db.session.commit()
    link = mint_public_link(share)
    exchanged = client.post(
        f"/api/v1/public-shares/{share.id}/session",
        json={"secret": link.rsplit("/", 1)[1]},
        headers={"Origin": "https://viewer.example"},
    )
    assert exchanged.status_code == 200
    cookie = client.get_cookie(
        "te_share_session", path=f"/api/v1/public-shares/{share.id}"
    )
    serializer = URLSafeTimedSerializer(
        SETTINGS["PUBLIC_SHARES"]["SESSION_SECRET"],
        salt="public-share-session-v1",
    )
    original_claims = serializer.loads(cookie.value, max_age=900)
    assert original_claims["absolute_expires_at"] > int(datetime.now(UTC).timestamp())

    refreshed = client.post(
        f"/api/v1/public-shares/{share.id}/session/refresh",
        headers={"Origin": "https://viewer.example"},
    )
    assert refreshed.status_code == 200
    refreshed_cookie = client.get_cookie(
        "te_share_session", path=f"/api/v1/public-shares/{share.id}"
    )
    refreshed_claims = serializer.loads(refreshed_cookie.value, max_age=900)
    assert (
        refreshed_claims["absolute_expires_at"]
        == original_claims["absolute_expires_at"]
    )

    expired_cookie = serializer.dumps(
        {
            "share_id": str(share.id),
            "generation": share.session_generation,
            "absolute_expires_at": int(datetime.now(UTC).timestamp()) - 1,
        }
    )
    client.set_cookie(
        "te_share_session",
        expired_cookie,
        path=f"/api/v1/public-shares/{share.id}",
    )
    denied_refresh = client.post(
        f"/api/v1/public-shares/{share.id}/session/refresh",
        headers={"Origin": "https://viewer.example"},
    )
    assert denied_refresh.status_code == 404
    assert client.get(f"/api/v1/public-shares/{share.id}/manifest").status_code == 404


def test_invalid_share_id_does_not_raise_database_conversion_error(
    app, regular_user, public_share_config
):
    with pytest.raises(PublicShareError) as exc_info:
        from gefapi.services.public_share_service import _share_query

        _share_query("not-a-uuid", regular_user.id)
    assert exc_info.value.status == 404


def test_validation_task_failure_marks_share_as_failed(
    app, regular_user, public_share_config
):
    share = create_share(regular_user, _payload())
    share.state = "validating"
    db.session.commit()

    from gefapi.tasks.public_shares import validate_public_share_task

    validate_public_share_task.on_failure(
        RuntimeError("validation worker failed"),
        "task-id",
        [str(share.id)],
        {},
        None,
    )
    db.session.refresh(share)

    assert share.state == "failed"
    assert share.layers[0].validation_error is not None


@pytest.mark.parametrize("key", ["shares/layer.tif", "shares/layer.pmtiles"])
def test_cleanup_removes_all_object_versions(key):
    client = Mock()
    client.delete_objects.return_value = {"Errors": []}
    client.get_paginator.return_value.paginate.return_value = [
        {
            "Versions": [
                {"Key": key, "VersionId": "v1"},
                {"Key": key, "VersionId": "v2"},
            ],
            "DeleteMarkers": [{"Key": key, "VersionId": "delete-marker"}],
        }
    ]

    _delete_s3_object_versions(client, "private-bucket", key)

    client.delete_objects.assert_called_once_with(
        Bucket="private-bucket",
        Delete={
            "Objects": [
                {"Key": key, "VersionId": "v1"},
                {"Key": key, "VersionId": "v2"},
                {"Key": key, "VersionId": "delete-marker"},
            ],
            "Quiet": True,
        },
    )
    client.delete_object.assert_not_called()


def _vector_payload(data=b"vector"):
    payload = _payload()
    payload["layers"] = [
        {
            "kind": "vector",
            "name": "Counterbalancing",
            "source_layer": "analysis",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "style": {
                "kind": "vector",
                "stroke": "#ff0000",
                "fill": "#00ff00",
                "width": 1,
                "fill_opacity": 0.5,
                "radius": 4,
                "category": {
                    "field": "status",
                    "values": {"1": "#ff0000", "2": "#00ff00"},
                },
            },
        }
    ]
    return payload


def _pmtiles_fixture(*, directory=None, tile=None, metadata=None):
    # A single z0 point feature with status=1 in the "analysis" layer.
    feature = b"\x12\x02\x00\x00\x18\x01\x22\x03\x09\x00\x00"
    layer = (
        b"\x0a\x08analysis\x12"
        + bytes([len(feature)])
        + feature
        + b"\x1a\x06status\x22\x02\x28\x01\x28\x80\x20\x78\x02"
    )
    tile = tile if tile is not None else b"\x1a" + bytes([len(layer)]) + layer
    directory = (
        directory
        if directory is not None
        else (b"\x01\x00\x01" + bytes([len(tile)]) + b"\x01")
    )
    metadata = json.dumps(
        metadata
        if metadata is not None
        else {"vector_layers": [{"id": "analysis", "fields": {"status": "Number"}}]}
    ).encode()
    header = bytearray(127)
    header[:8] = b"PMTiles\x03"
    metadata_offset = 127 + len(directory)
    tile_offset = metadata_offset + len(metadata)
    struct.pack_into(
        "<11Q",
        header,
        8,
        127,
        len(directory),
        metadata_offset,
        len(metadata),
        tile_offset,
        0,
        tile_offset,
        len(tile),
        1,
        1,
        1,
    )
    header[96:102] = bytes([1, 1, 1, 1, 0, 0])
    struct.pack_into("<4i", header, 102, 0, 0, 10_000_000, 10_000_000)
    return bytes(header) + directory + metadata + tile


def _upstream(data, content_type="application/x-protobuf", status=200, encoding=None):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status = status
    response.headers = Message()
    response.headers["Content-Type"] = content_type
    if encoding:
        response.headers["Content-Encoding"] = encoding
    response.read.side_effect = lambda limit: data[:limit]
    return response


def _publish_vector(share, monkeypatch, client):
    from gefapi.services import public_share_service as service

    share.state = "ready"
    for layer in share.layers:
        layer.checksum_sha256 = layer.expected_sha256
        layer.published = True
        if layer.kind == "vector":
            layer.vector_metadata = {
                "source_layer": "analysis",
                "minzoom": 0,
                "maxzoom": 2,
                "fields": {"status": "number"},
            }
    db.session.commit()
    metadata = json.dumps({"vector_layers": [{"id": "analysis"}]}).encode()
    monkeypatch.setattr(
        service,
        "open_tile_service",
        lambda *a, **kw: _upstream(metadata, "application/json"),
    )
    link = mint_public_link(share)
    response = client.post(
        f"/api/v1/public-shares/{share.id}/session",
        json={"secret": link.rsplit("/", 1)[1]},
        headers={"Origin": "https://viewer.example"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("continuous", [False, True])
def test_authorized_raster_lambda_invocation(
    app, client, regular_user, public_share_config, monkeypatch, continuous
):
    from gefapi.services import public_share_service as service

    arn = "arn:aws:lambda:us-east-1:123456789012:function:share-tiler:live"
    monkeypatch.setitem(SETTINGS["PUBLIC_SHARES"], "TITILER_LAMBDA_ARN", arn)
    monkeypatch.setitem(SETTINGS["PUBLIC_SHARES"], "TITILER_INTERNAL_URL", None)
    payload = _payload()
    if continuous:
        payload["layers"][0]["style"] = {
            "kind": "continuous",
            "min": -1,
            "max": 1,
            "palette": "viridis",
        }
    share = create_share(regular_user, payload)
    _publish_vector(share, monkeypatch, client)
    layer = share.layers[0]
    tile = b"\x89PNG\r\n\x1a\nraster"
    sdk = Mock()
    sdk.invoke.return_value = {
        "StatusCode": 200,
        "Payload": io.BytesIO(
            json.dumps(
                {
                    "statusCode": 200,
                    "headers": {"content-type": "image/png"},
                    "isBase64Encoded": True,
                    "body": base64.b64encode(tile).decode(),
                }
            ).encode()
        ),
    }
    sdk_factory = Mock(return_value=sdk)
    monkeypatch.setattr(service.boto3, "client", sdk_factory)
    url = f"/api/v1/public-shares/{share.id}/tiles/{layer.id}/0/0/0.png"
    response = client.get(url)
    assert response.status_code == 200 and response.data == tile
    assert response.headers["Cache-Control"] == "no-store"
    kwargs = sdk.invoke.call_args.kwargs
    assert kwargs["FunctionName"] == arn
    assert kwargs["InvocationType"] == "RequestResponse"
    assert kwargs["LogType"] == "None"
    event = json.loads(kwargs["Payload"])
    assert event["rawPath"] == "/cog/tiles/WebMercatorQuad/0/0/0.png"
    assert event["headers"] == {"host": "private-renderer"}
    assert "body" not in event
    query = parse_qs(event["rawQueryString"])
    assert query["url"] == [f"s3://private-webmap-shares/{layer.object_key}"]
    assert query["bidx"] == ["1"]
    assert query["resampling"] == ["bilinear" if continuous else "nearest"]
    if continuous:
        assert query["rescale"] == ["-1.0,1.0"]
        assert query["colormap_name"] == ["viridis"]
    else:
        assert json.loads(query["colormap"][0]) == {"1": "#00ff00", "2": "#ff0000"}
    sdk_factory.assert_called_once()
    assert sdk_factory.call_args.kwargs["region_name"] == "us-east-1"
    config = sdk_factory.call_args.kwargs["config"]
    assert config.retries["total_max_attempts"] == 1
    assert config.read_timeout == 32
    sdk.invoke.reset_mock()
    assert client.get(url.replace("/0/0/0.", "/0/1/0.")).status_code == 400
    assert client.get(url.replace(".png", ".pbf")).status_code == 404
    assert client.get(url.replace(str(layer.id), str(uuid.uuid4()))).status_code == 404
    share.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    db.session.commit()
    assert client.get(url).status_code == 404
    sdk.invoke.assert_not_called()


@pytest.mark.parametrize(
    "problem",
    [
        "function_error",
        "bad_json",
        "bad_headers",
        "bad_type",
        "bad_base64",
        "unencoded",
        "oversized",
        "throttled",
    ],
)
def test_lambda_tile_failures_are_explicit_and_do_not_fall_back_to_http(
    app, client, regular_user, public_share_config, monkeypatch, problem
):
    from gefapi.routes.api.v1 import public_shares as routes
    from gefapi.services import public_share_service as service

    monkeypatch.setitem(
        SETTINGS["PUBLIC_SHARES"],
        "TITILER_LAMBDA_ARN",
        "arn:aws:lambda:us-east-1:123456789012:function:share-tiler:live",
    )
    share = create_share(regular_user, _payload())
    _publish_vector(share, monkeypatch, client)
    envelope = {
        "statusCode": 200,
        "headers": {"content-type": "image/png"},
        "isBase64Encoded": True,
        "body": base64.b64encode(b"png").decode(),
    }
    if problem == "bad_headers":
        envelope["headers"] = []
    elif problem == "bad_type":
        envelope["headers"]["content-type"] = "text/html"
    elif problem == "bad_base64":
        envelope["body"] = "%%%"
    elif problem == "unencoded":
        envelope["isBase64Encoded"] = False
    elif problem == "oversized":
        envelope["body"] = base64.b64encode(b"x" * (2 * 1024**2 + 1)).decode()
    sdk = Mock()
    sdk.invoke.return_value = {
        "StatusCode": 200,
        "Payload": io.BytesIO(
            b"not json" if problem == "bad_json" else json.dumps(envelope).encode()
        ),
    }
    if problem == "function_error":
        sdk.invoke.return_value["FunctionError"] = "Unhandled"
    if problem == "throttled":
        sdk.invoke.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "TooManyRequestsException", "Message": "throttled"}},
            "Invoke",
        )
    monkeypatch.setattr(service.boto3, "client", Mock(return_value=sdk))
    http = Mock()
    monkeypatch.setattr(routes, "open_tile_service", http)
    layer = share.layers[0]
    response = client.get(
        f"/api/v1/public-shares/{share.id}/tiles/{layer.id}/0/0/0.png"
    )
    assert response.status_code == (503 if problem == "throttled" else 502)
    assert response.headers["Cache-Control"] == "no-store"
    if problem == "throttled":
        assert sdk.invoke.call_count == 3
        assert response.headers["Retry-After"] == "1"
    http.assert_not_called()


def test_malformed_lambda_configuration_fails_closed(
    app, public_share_config, monkeypatch
):
    from gefapi.services import public_share_service as service

    monkeypatch.setitem(
        SETTINGS["PUBLIC_SHARES"], "TITILER_LAMBDA_ARN", "https://public.example"
    )
    with pytest.raises(PublicShareError, match="Lambda is not configured safely"):
        service.ensure_public_shares_enabled()


@pytest.mark.parametrize("status", [204, 503, 504])
def test_lambda_empty_and_transient_statuses_are_preserved(
    app, client, regular_user, public_share_config, monkeypatch, status
):
    from gefapi.services import public_share_service as service

    share = create_share(regular_user, _payload())
    _publish_vector(share, monkeypatch, client)
    monkeypatch.setitem(
        SETTINGS["PUBLIC_SHARES"],
        "TITILER_LAMBDA_ARN",
        "arn:aws:lambda:us-east-1:123456789012:function:share-tiler:live",
    )
    sdk = Mock()
    sdk.invoke.return_value = {
        "StatusCode": 200,
        "Payload": io.BytesIO(
            json.dumps(
                {
                    "statusCode": status,
                    "headers": {"content-type": "application/json"},
                    "body": "" if status == 204 else '{"detail":"renderer error"}',
                }
            ).encode()
        ),
    }
    monkeypatch.setattr(service.boto3, "client", Mock(return_value=sdk))
    response = client.get(
        f"/api/v1/public-shares/{share.id}/tiles/{share.layers[0].id}/0/0/0.png"
    )
    assert response.status_code == status
    if status == 204:
        assert not response.data
    else:
        assert response.headers["Retry-After"] == "1"


def test_lambda_throttle_retry_can_recover_and_read_timeout_is_504(
    app, client, regular_user, public_share_config, monkeypatch
):
    from gefapi.services import public_share_service as service

    share = create_share(regular_user, _payload())
    _publish_vector(share, monkeypatch, client)
    monkeypatch.setitem(
        SETTINGS["PUBLIC_SHARES"],
        "TITILER_LAMBDA_ARN",
        "arn:aws:lambda:us-east-1:123456789012:function:share-tiler:live",
    )
    sdk = Mock()
    sdk.invoke.side_effect = [
        botocore.exceptions.ClientError(
            {"Error": {"Code": "TooManyRequestsException"}}, "Invoke"
        ),
        {
            "StatusCode": 200,
            "Payload": io.BytesIO(
                json.dumps(
                    {
                        "statusCode": 204,
                        "headers": {},
                        "body": "",
                    }
                ).encode()
            ),
        },
    ]
    monkeypatch.setattr(service.boto3, "client", Mock(return_value=sdk))
    sleep = Mock()
    monkeypatch.setattr(service.time, "sleep", sleep)
    url = f"/api/v1/public-shares/{share.id}/tiles/{share.layers[0].id}/0/0/0.png"
    assert client.get(url).status_code == 204
    assert sdk.invoke.call_count == 2
    assert 0.05 <= sleep.call_args.args[0] <= 0.1
    sdk.invoke.side_effect = botocore.exceptions.ReadTimeoutError(
        endpoint_url="private"
    )
    response = client.get(url)
    assert response.status_code == 504
    assert response.headers["Retry-After"] == "1"


@pytest.mark.parametrize("size,expected", [(2 * 1024**2, 200), (2 * 1024**2 + 1, 502)])
def test_lambda_tile_byte_limit(
    app, client, regular_user, public_share_config, monkeypatch, size, expected
):
    from gefapi.services import public_share_service as service

    monkeypatch.setitem(
        SETTINGS["PUBLIC_SHARES"],
        "TITILER_LAMBDA_ARN",
        "arn:aws:lambda:us-east-1:123456789012:function:share-tiler:live",
    )
    share = create_share(regular_user, _payload())
    _publish_vector(share, monkeypatch, client)
    tile = b"\x89PNG\r\n\x1a\n" + b"x" * (size - 8)
    sdk = Mock()
    sdk.invoke.return_value = {
        "StatusCode": 200,
        "Payload": io.BytesIO(
            json.dumps(
                {
                    "statusCode": 200,
                    "headers": {"content-type": "image/png"},
                    "isBase64Encoded": True,
                    "body": base64.b64encode(tile).decode(),
                }
            ).encode()
        ),
    }
    monkeypatch.setattr(service.boto3, "client", Mock(return_value=sdk))
    response = client.get(
        f"/api/v1/public-shares/{share.id}/tiles/{share.layers[0].id}/0/0/0.png"
    )
    assert response.status_code == expected


def test_mixed_share_contract_quota_upload_and_manifest(
    app, client, regular_user, public_share_config, monkeypatch
):
    from gefapi.services import public_share_service as service

    payload = _payload()
    payload["layers"] += _vector_payload()["layers"]
    share = create_share(regular_user, payload)
    raster, vector = share.layers
    assert raster.kind == "raster" and raster.band == 1
    assert vector.kind == "vector" and vector.band is None
    assert vector.object_key == f"public-shares/{share.id}/{vector.id}.pmtiles"
    assert share.quota_reserved_bytes == 1030
    storage = client.get("/api/v1/shares/storage", headers=_headers(regular_user))
    assert storage.get_json()["data"]["reserved_bytes"] == 1030
    owner = db.session.get(User, regular_user.id)
    owner.public_share_quota_bytes = 1030
    db.session.commit()
    with pytest.raises(PublicShareError, match="quota"):
        create_share(regular_user, _vector_payload())
    s3 = Mock()
    s3.create_multipart_upload.return_value = {"UploadId": "upload"}
    s3.generate_presigned_url.return_value = "https://s3.example/fixed-part"
    monkeypatch.setattr(service, "_s3_client", lambda: s3)
    result = service.create_or_refresh_upload(share.id, vector.id, regular_user.id)
    assert result["parts"][0]["size_bytes"] == 6
    assert (
        s3.create_multipart_upload.call_args.kwargs["ContentType"]
        == "application/vnd.pmtiles"
    )
    _publish_vector(share, monkeypatch, client)
    manifest = client.get(f"/api/v1/public-shares/{share.id}/manifest").get_json()[
        "data"
    ]
    assert manifest["layers"][1]["tile_format"] == "pbf"
    assert manifest["layers"][1]["tiles"].endswith("/{z}/{x}/{y}.pbf")
    assert "object_key" not in json.dumps(manifest)


@pytest.mark.parametrize(
    "change",
    [
        {"band": 1},
        {"source_layer": "../secret"},
        {"kind": "gpkg"},
        {"style": {"kind": "vector", "url": "https://external.example"}},
    ],
)
def test_vector_declarations_reject_unsafe_or_unsupported_fields(
    app, regular_user, public_share_config, change
):
    payload = _vector_payload()
    payload["layers"][0].update(change)
    with pytest.raises(PublicShareError) as exc:
        create_share(regular_user, payload)
    assert exc.value.status == 400


def test_vector_validation_and_cleanup_share_lifecycle(
    app, regular_user, public_share_config, monkeypatch
):
    from gefapi.services import public_share_service as service

    data = _pmtiles_fixture()
    share = create_share(regular_user, _vector_payload(data))
    layer = share.layers[0]
    share.state = "validating"
    layer.upload_completed = True
    layer.object_version_id = "immutable-version"
    upload_key = layer.object_key
    db.session.commit()
    s3 = Mock()
    s3.get_object.return_value = {
        "ContentLength": len(data),
        "Body": io.BytesIO(data),
        "VersionId": "immutable-version",
    }
    s3.copy_object.return_value = {"VersionId": "render-version"}
    s3.get_paginator.return_value.paginate.return_value = []
    monkeypatch.setattr(service, "_s3_client", lambda: s3)
    service.validate_share(share.id)
    assert share.state == "ready"
    assert layer.vector_metadata["fields"] == {"status": "number"}
    assert layer.bounds == [0, 0, 1, 1]
    assert layer.crs == "EPSG:3857"
    assert layer.object_key == f"public-shares/vector/{layer.id}.pmtiles"
    assert layer.object_version_id == "render-version"
    s3.copy_object.assert_called_once_with(
        Bucket="private-webmap-shares",
        Key=layer.object_key,
        CopySource={
            "Bucket": "private-webmap-shares",
            "Key": upload_key,
            "VersionId": "immutable-version",
        },
        ServerSideEncryption="AES256",
        ContentType="application/vnd.pmtiles",
        MetadataDirective="REPLACE",
    )
    s3.get_object.assert_called_once_with(
        Bucket="private-webmap-shares",
        Key=upload_key,
        VersionId="immutable-version",
    )
    removed = []
    monkeypatch.setattr(
        service, "_delete_s3_object_versions", lambda c, b, k: removed.append(k)
    )
    mark_share_revoked(share)
    assert share.quota_reserved_bytes == len(data)
    service.cleanup_share_objects(share.id)
    assert set(removed) == {layer.object_key, upload_key}
    assert share.quota_reserved_bytes == 0


@pytest.mark.parametrize(
    "data",
    [
        b"not pmtiles",
        _pmtiles_fixture(tile=b"\xff"),
        _pmtiles_fixture(directory=b"\x01\x00\x01\x7f\x01"),
        _pmtiles_fixture(metadata={"vector_layers": [{"id": "secret", "fields": {}}]}),
    ],
    ids=["not_pmtiles", "bad_tile", "bad_directory", "undeclared_layer"],
)
def test_invalid_archives_fail_closed(
    app, regular_user, public_share_config, monkeypatch, data
):
    from gefapi.services import public_share_service as service

    share = create_share(regular_user, _vector_payload(data))
    share.state = "validating"
    db.session.commit()
    s3 = Mock()
    s3.get_object.return_value = {"ContentLength": len(data), "Body": io.BytesIO(data)}
    monkeypatch.setattr(service, "_s3_client", lambda: s3)
    service.validate_share(share.id)
    assert share.state == "failed"
    assert share.layers[0].validation_error
    assert share.layers[0].checksum_sha256 is None
    s3.copy_object.assert_not_called()
    with pytest.raises(PublicShareError, match="validation"):
        mint_public_link(share)


@pytest.mark.skipif(
    not all(shutil.which(t) for t in ("ogr2ogr", "gdal_create", "gdal_translate")),
    reason="GDAL command-line tools are not installed",
)
def test_gdal_generated_pmtiles_and_cog_pass_worker_validation(
    app, regular_user, public_share_config, monkeypatch, tmp_path
):
    from gefapi.services import public_share_service as service

    source = tmp_path / "source.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"status": 1, "label": "Boundary"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                        },
                    }
                ],
            }
        )
    )
    pmtiles = tmp_path / "snapshot.pmtiles"
    subprocess.run(  # noqa: S603
        [
            shutil.which("ogr2ogr"),
            "-f",
            "PMTiles",
            str(pmtiles),
            str(source),
            "-nln",
            "analysis",
            "-dsco",
            "MAXZOOM=2",
        ],
        check=True,
    )
    raster = tmp_path / "source.tif"
    cog = tmp_path / "snapshot.tif"
    subprocess.run(  # noqa: S603
        [
            shutil.which("gdal_create"),
            "-outsize",
            "16",
            "16",
            "-a_srs",
            "EPSG:4326",
            "-a_ullr",
            "0",
            "1",
            "1",
            "0",
            str(raster),
        ],
        check=True,
    )
    subprocess.run(  # noqa: S603
        [shutil.which("gdal_translate"), "-q", "-of", "COG", str(raster), str(cog)],
        check=True,
    )
    payload = _vector_payload(pmtiles.read_bytes())
    declaration = _payload()["layers"][0]
    declaration.update(
        size_bytes=cog.stat().st_size,
        sha256=hashlib.sha256(cog.read_bytes()).hexdigest(),
    )
    payload["layers"].append(declaration)
    share = create_share(regular_user, payload)
    share.state = "validating"
    db.session.commit()
    objects = {
        share.layers[0].object_key: pmtiles.read_bytes(),
        share.layers[1].object_key: cog.read_bytes(),
    }
    s3 = Mock()
    s3.get_object.side_effect = lambda **kw: {
        "ContentLength": len(objects[kw["Key"]]),
        "Body": io.BytesIO(objects[kw["Key"]]),
    }
    s3.copy_object.return_value = {}
    s3.get_paginator.return_value.paginate.return_value = []
    monkeypatch.setattr(service, "_s3_client", lambda: s3)
    service.validate_share(share.id)
    assert share.state == "ready", share.serialize()["validation_errors"]
    assert share.layers[0].vector_metadata["maxzoom"] == 2
    assert share.layers[0].vector_metadata["fields"]["label"] == "string"
    assert share.layers[1].raster_band_count == 1


def test_martin_discovery_delay_does_not_publish_a_link(
    app, regular_user, public_share_config, monkeypatch
):
    from gefapi.services import public_share_service as service

    share = create_share(regular_user, _vector_payload())
    share.state = "ready"
    db.session.commit()
    upstream = Mock(side_effect=HTTPError("http://martin", 404, "not found", {}, None))
    monkeypatch.setattr(service, "open_tile_service", upstream)
    with pytest.raises(PublicShareError) as exc:
        mint_public_link(share)
    assert exc.value.status == 503
    assert share.ready_at is None and share.token_hash is None
    assert upstream.call_args.args[0] == f"http://martin:3000/{share.layers[0].id}"


@pytest.mark.parametrize(
    "data,content_type,status,encoding,expected",
    [
        (b"mvt", "application/x-protobuf", 200, None, 200),
        (b"gzip-mvt", "application/vnd.mapbox-vector-tile", 200, "gzip", 200),
        (b"", "application/x-protobuf", 204, None, 204),
        (b"html", "text/html", 200, None, 502),
        (b"x" * (2 * 1024**2 + 1), "application/x-protobuf", 200, None, 502),
        (b"x" * (2 * 1024**2), "application/x-protobuf", 200, None, 200),
        (b"mvt", "application/x-protobuf", 200, "br", 502),
    ],
    # Explicit ids keep multi-MB payloads out of verbose test names; CI log
    # ingestion stalls on very long lines.
    ids=["mvt", "gzip", "empty", "html", "oversized", "max_size", "brotli"],
)
def test_authorized_vector_tile_proxy(
    app,
    client,
    regular_user,
    public_share_config,
    monkeypatch,
    data,
    content_type,
    status,
    encoding,
    expected,
):
    from gefapi.routes.api.v1 import public_shares as routes

    share = create_share(regular_user, _vector_payload())
    _publish_vector(share, monkeypatch, client)
    upstream = Mock(return_value=_upstream(data, content_type, status, encoding))
    monkeypatch.setattr(routes, "open_tile_service", upstream)
    layer = share.layers[0]
    url = f"/api/v1/public-shares/{share.id}/tiles/{layer.id}/0/0/0.pbf"
    response = client.get(url)
    assert response.status_code == expected
    assert response.headers["Cache-Control"] == "no-store"
    assert upstream.call_args.args[0] == f"http://martin:3000/{layer.id}/0/0/0"
    if expected == 200:
        assert response.data == data
        assert response.headers.get("Content-Encoding") == encoding
    upstream.reset_mock()
    assert client.get(url.replace(".pbf", ".png")).status_code == 404
    assert client.get(url.replace("/0/0/0.", "/0/1/0.")).status_code == 400
    assert client.get(url.replace(str(layer.id), str(uuid.uuid4()))).status_code == 404
    assert client.get(url.replace("/0/0/0.", "/3/0/0.")).status_code == 204
    upstream.assert_not_called()
    share.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    db.session.commit()
    assert client.get(url).status_code == 404
    upstream.assert_not_called()


def test_pmtiles_promotion_recovers_after_s3_move_before_database_commit(
    app, regular_user, public_share_config, monkeypatch
):
    import botocore

    from gefapi.services import public_share_service as service

    data = _pmtiles_fixture()
    share = create_share(regular_user, _vector_payload(data))
    share.state = "validating"
    db.session.commit()
    layer = share.layers[0]
    original_key = layer.object_key
    s3 = Mock()
    s3.get_object.side_effect = [
        botocore.exceptions.ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject"),
        {
            "ContentLength": len(data),
            "Body": io.BytesIO(data),
            "VersionId": "promoted-version",
        },
    ]
    monkeypatch.setattr(service, "_s3_client", lambda: s3)
    service.validate_share(share.id)
    assert share.state == "ready"
    assert layer.object_key != original_key
    assert layer.object_version_id == "promoted-version"
    assert layer.checksum_sha256 == hashlib.sha256(data).hexdigest()
    s3.copy_object.assert_not_called()


@pytest.mark.parametrize(
    "mutate", ["version", "compression", "tile_type", "zoom", "overlap", "truncated"]
)
def test_pmtiles_header_and_gzip_limits_reject_malicious_archives(tmp_path, mutate):
    from gefapi.services.pmtiles_validation import _unpack, validate_pmtiles

    data = bytearray(_pmtiles_fixture())
    if mutate == "version":
        data[7] = 2
    elif mutate == "compression":
        data[97] = 4
    elif mutate == "tile_type":
        data[99] = 2
    elif mutate == "zoom":
        data[101] = 23
    elif mutate == "overlap":
        struct.pack_into("<Q", data, 24, 127)
    else:
        data = data[:-1]
    path = tmp_path / "bad.pmtiles"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        validate_pmtiles(path, "analysis")
    if mutate == "compression":
        with pytest.raises(ValueError, match="limit"):
            _unpack(gzip.compress(b"x" * (1024**2 + 1)), 2, 1024**2)


def test_viewer_cookie_cannot_read_other_share_vector_tiles(
    app, client, regular_user, public_share_config, monkeypatch
):
    from gefapi.routes.api.v1 import public_shares as routes

    share = create_share(regular_user, _vector_payload())
    other = create_share(regular_user, _vector_payload())
    _publish_vector(share, monkeypatch, client)
    upstream = Mock()
    monkeypatch.setattr(routes, "open_tile_service", upstream)
    layer = other.layers[0]
    url = f"/api/v1/public-shares/{other.id}/tiles/{layer.id}/0/0/0.pbf"
    assert client.get(url).status_code == 404
    upstream.assert_not_called()


def test_vector_migration_preserves_legacy_rasters_and_blocks_unsafe_downgrade(
    app, regular_user, public_share_config
):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import sqlalchemy as sa

    share = create_share(regular_user, _payload())
    layer_id = share.layers[0].id
    db.session.execute(sa.text("ALTER TABLE public_share_layer DROP COLUMN kind"))
    db.session.execute(
        sa.text("ALTER TABLE public_share_layer DROP COLUMN vector_metadata")
    )
    db.session.execute(
        sa.text("ALTER TABLE public_share_layer ALTER COLUMN band SET NOT NULL")
    )
    db.session.commit()
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/4f68e9d12c3a_add_public_share_vector_layers.py"
    )
    spec = importlib.util.spec_from_file_location("vector_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with (
        db.engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        migration.upgrade()
    db.session.expire_all()
    layer = db.session.get(PublicShareLayer, layer_id)
    assert layer.kind == "raster" and layer.band == 1
    vector_share = create_share(regular_user, _vector_payload())
    assert vector_share.layers[0].band is None
    with (
        pytest.raises(RuntimeError, match="Remove vector shares"),
        db.engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        migration.downgrade()
    assert db.session.get(PublicShareLayer, layer_id).kind == "raster"


@pytest.mark.parametrize("problem", ["checksum", "oversized_stream", "category_field"])
def test_vector_validation_rejects_upload_and_style_mismatches(
    app, regular_user, public_share_config, monkeypatch, problem
):
    from gefapi.services import public_share_service as service

    data = _pmtiles_fixture()
    payload = _vector_payload(data)
    if problem == "category_field":
        payload["layers"][0]["style"]["category"]["field"] = "missing"
    share = create_share(regular_user, payload)
    share.state = "validating"
    db.session.commit()
    stream = data + b"extra" if problem == "oversized_stream" else data
    if problem == "checksum":
        stream = b"x" + data[1:]
    s3 = Mock()
    s3.get_object.return_value = {
        "ContentLength": len(data),
        "Body": io.BytesIO(stream),
    }
    monkeypatch.setattr(service, "_s3_client", lambda: s3)
    service.validate_share(share.id)
    assert share.state == "failed"
    assert share.layers[0].validation_error
    s3.copy_object.assert_not_called()


def test_missing_martin_tile_source_is_retryable_and_revocation_denies_requests(
    app, client, regular_user, public_share_config, monkeypatch
):
    from gefapi.routes.api.v1 import public_shares as routes

    share = create_share(regular_user, _vector_payload())
    _publish_vector(share, monkeypatch, client)
    upstream = Mock(side_effect=HTTPError("http://martin", 404, "not found", {}, None))
    monkeypatch.setattr(routes, "open_tile_service", upstream)
    layer = share.layers[0]
    url = f"/api/v1/public-shares/{share.id}/tiles/{layer.id}/0/0/0.pbf"
    assert client.get(url).status_code == 503
    upstream.reset_mock()
    mark_share_revoked(share)
    assert client.get(url).status_code == 404
    upstream.assert_not_called()


def test_pmtiles_leaf_directory_and_mvt_geometry_validation(tmp_path):
    from gefapi.services.pmtiles_validation import validate_pmtiles

    data = bytearray(_pmtiles_fixture())
    tile_offset = struct.unpack_from("<Q", data, 56)[0]
    leaf = bytes(data[127:132])
    data[127:132] = bytes([1, 0, 0, len(leaf), 1])
    data[tile_offset:tile_offset] = leaf
    struct.pack_into("<Q", data, 40, tile_offset)
    struct.pack_into("<Q", data, 48, len(leaf))
    struct.pack_into("<Q", data, 56, tile_offset + len(leaf))
    path = tmp_path / "leaves.pmtiles"
    path.write_bytes(data)
    assert validate_pmtiles(path, "analysis")["source_layer"] == "analysis"
    # Change the point into an unclosed polygon with only its MoveTo command.
    data = data.replace(b"\x18\x01\x22", b"\x18\x03\x22")
    path.write_bytes(data)
    with pytest.raises(ValueError, match="Unclosed MVT polygon"):
        validate_pmtiles(path, "analysis")


@pytest.mark.parametrize("state", ["validating", "ready"])
def test_unpublished_vector_objects_expire_even_after_interrupted_validation(
    app, regular_user, public_share_config, monkeypatch, state
):
    from gefapi.services import public_share_service as service
    from gefapi.tasks.public_shares import cleanup_public_shares_task

    share = create_share(regular_user, _vector_payload())
    share.state = state
    share.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    share.layers[0].upload_completed = True
    db.session.commit()
    assert service.get_share_storage_usage(regular_user)["cleanup_pending_bytes"] == 6
    s3 = Mock()
    removed = []
    monkeypatch.setattr(service, "_s3_client", lambda: s3)
    monkeypatch.setattr(
        service, "_delete_s3_object_versions", lambda c, b, k: removed.append(k)
    )
    assert cleanup_public_shares_task.run() == {"cleaned": 1, "failures": 0}
    db.session.refresh(share)
    assert share.state == "expired"
    assert len(removed) == 2
    assert share.quota_reserved_bytes == 0


def test_cleanup_rechecks_publication_after_loading_an_old_draft(
    app, regular_user, public_share_config, monkeypatch
):
    import sqlalchemy as sa

    from gefapi.services import public_share_service as service

    share = create_share(regular_user, _vector_payload())
    share.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    db.session.commit()
    assert share.ready_at is None
    now = datetime.now(UTC).replace(tzinfo=None)
    with db.engine.begin() as connection:
        connection.execute(
            sa.update(PublicShare.__table__)
            .where(PublicShare.id == share.id)
            .values(state="ready", ready_at=now, expires_at=now + timedelta(days=7))
        )
    assert share.ready_at is None
    s3 = Mock()
    monkeypatch.setattr(service, "_s3_client", lambda: s3)
    service.cleanup_share_objects(share.id)
    assert share.is_accessible()
    s3.get_paginator.assert_not_called()
