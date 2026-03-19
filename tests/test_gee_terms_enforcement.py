"""Tests for GEE terms enforcement on script execution."""

from unittest.mock import patch

from gefapi.models import Script


class TestGeeTermsEnforcement:
    """Test that users must accept GEE terms to run GEE scripts."""

    def _enable_enforcement(self, app):
        from gefapi.config import SETTINGS

        SETTINGS["GEE_TERMS_ENFORCEMENT_ENABLED"] = True

    def _disable_enforcement(self, app):
        from gefapi.config import SETTINGS

        SETTINGS["GEE_TERMS_ENFORCEMENT_ENABLED"] = False

    def test_gee_script_blocked_without_terms(
        self, client, app, auth_headers_user, regular_user, sample_script, db_session
    ):
        """User without GEE terms cannot run a GEE script when enforcement is on."""
        self._enable_enforcement(app)
        try:
            regular_user = db_session.merge(regular_user)
            regular_user.gee_license_acknowledged = False
            db_session.add(regular_user)

            sample_script = db_session.merge(sample_script)
            sample_script.uses_gee = True
            db_session.add(sample_script)
            db_session.commit()

            with patch("gefapi.services.execution_service._dispatch_execution"):
                response = client.post(
                    f"/api/v1/script/{sample_script.id}/run",
                    json={"test": "param"},
                    headers=auth_headers_user,
                )
            assert response.status_code == 403
            data = response.get_json()
            assert data["error_code"] == "gee_terms_required"
        finally:
            self._disable_enforcement(app)

    def test_gee_script_allowed_with_terms(
        self, client, app, auth_headers_user, regular_user, sample_script, db_session
    ):
        """User with GEE terms accepted can run a GEE script."""
        self._enable_enforcement(app)
        try:
            regular_user = db_session.merge(regular_user)
            regular_user.gee_license_acknowledged = True
            db_session.add(regular_user)

            sample_script = db_session.merge(sample_script)
            sample_script.uses_gee = True
            db_session.add(sample_script)
            db_session.commit()

            with patch("gefapi.services.execution_service._dispatch_execution"):
                response = client.post(
                    f"/api/v1/script/{sample_script.id}/run",
                    json={"test": "param"},
                    headers=auth_headers_user,
                )
            assert response.status_code == 200
        finally:
            self._disable_enforcement(app)

    def test_non_gee_script_allowed_without_terms(
        self, client, app, auth_headers_user, regular_user, sample_script, db_session
    ):
        """User without GEE terms can run a non-GEE script."""
        self._enable_enforcement(app)
        try:
            regular_user = db_session.merge(regular_user)
            regular_user.gee_license_acknowledged = False
            db_session.add(regular_user)

            sample_script = db_session.merge(sample_script)
            sample_script.uses_gee = False
            db_session.add(sample_script)
            db_session.commit()

            with patch("gefapi.services.execution_service._dispatch_execution"):
                response = client.post(
                    f"/api/v1/script/{sample_script.id}/run",
                    json={"test": "param"},
                    headers=auth_headers_user,
                )
            assert response.status_code == 200
        finally:
            self._disable_enforcement(app)

    def test_admin_exempt_from_gee_terms(
        self, client, app, auth_headers_admin, admin_user, sample_script, db_session
    ):
        """Admins are exempt from GEE terms requirement."""
        self._enable_enforcement(app)
        try:
            admin_user = db_session.merge(admin_user)
            admin_user.gee_license_acknowledged = False
            db_session.add(admin_user)

            sample_script = db_session.merge(sample_script)
            sample_script.uses_gee = True
            db_session.add(sample_script)
            db_session.commit()

            with patch("gefapi.services.execution_service._dispatch_execution"):
                response = client.post(
                    f"/api/v1/script/{sample_script.id}/run",
                    json={"test": "param"},
                    headers=auth_headers_admin,
                )
            assert response.status_code == 200
        finally:
            self._disable_enforcement(app)

    def test_enforcement_disabled_allows_all(
        self, client, app, auth_headers_user, regular_user, sample_script, db_session
    ):
        """When enforcement is off, users without terms can run GEE scripts."""
        self._disable_enforcement(app)
        regular_user = db_session.merge(regular_user)
        regular_user.gee_license_acknowledged = False
        db_session.add(regular_user)

        sample_script = db_session.merge(sample_script)
        sample_script.uses_gee = True
        db_session.add(sample_script)
        db_session.commit()

        with patch("gefapi.services.execution_service._dispatch_execution"):
            response = client.post(
                f"/api/v1/script/{sample_script.id}/run",
                json={"test": "param"},
                headers=auth_headers_user,
            )
        assert response.status_code == 200


class TestScriptUsesGeeField:
    """Test the uses_gee field on Script model and API."""

    def test_script_serialize_includes_uses_gee(self, app, sample_script, db_session):
        """Script serialization includes uses_gee field."""
        sample_script = db_session.merge(sample_script)
        data = sample_script.serialize()
        assert "uses_gee" in data
        assert data["uses_gee"] is True

    def test_script_uses_gee_defaults_true(self, app, regular_user, db_session):
        """New scripts default to uses_gee=True."""
        regular_user = db_session.merge(regular_user)
        script = Script(
            name="Test Default",
            slug="test-default-gee",
            user_id=regular_user.id,
        )
        db_session.add(script)
        db_session.commit()
        assert script.uses_gee is True

    def test_script_uses_gee_can_be_false(self, app, regular_user, db_session):
        """Scripts can be created with uses_gee=False."""
        regular_user = db_session.merge(regular_user)
        script = Script(
            name="Test No GEE",
            slug="test-no-gee",
            user_id=regular_user.id,
            uses_gee=False,
        )
        db_session.add(script)
        db_session.commit()
        assert script.uses_gee is False


class TestScriptConfigPatch:
    """Test the PATCH /script/<id>/config metadata endpoint."""

    def test_admin_can_update_uses_gee(
        self, client, auth_headers_admin, admin_user, sample_script, db_session
    ):
        """Admin can toggle uses_gee via config PATCH."""
        sample_script = db_session.merge(sample_script)
        response = client.patch(
            f"/api/v1/script/{sample_script.id}/config",
            json={"uses_gee": False},
            headers=auth_headers_admin,
        )
        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["uses_gee"] is False

    def test_admin_can_update_name_and_description(
        self, client, auth_headers_admin, admin_user, sample_script, db_session
    ):
        """Admin can update name and description via config PATCH."""
        sample_script = db_session.merge(sample_script)
        response = client.patch(
            f"/api/v1/script/{sample_script.id}/config",
            json={"name": "Updated Name", "description": "Updated desc"},
            headers=auth_headers_admin,
        )
        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["name"] == "Updated Name"
        assert data["description"] == "Updated desc"

    def test_regular_user_cannot_update_other_script(
        self, client, auth_headers_user, regular_user, admin_user, db_session
    ):
        """Regular user cannot update a script they don't own."""
        admin_user = db_session.merge(admin_user)
        script = Script(
            name="Admin Script",
            slug="admin-only-script-cfg",
            user_id=admin_user.id,
        )
        script.status = "SUCCESS"
        script.public = True
        db_session.add(script)
        db_session.commit()

        response = client.patch(
            f"/api/v1/script/{script.id}/config",
            json={"uses_gee": False},
            headers=auth_headers_user,
        )
        assert response.status_code == 403

    def test_no_json_body_returns_400(
        self, client, auth_headers_admin, sample_script, db_session
    ):
        """Missing JSON body returns 400."""
        sample_script = db_session.merge(sample_script)
        response = client.patch(
            f"/api/v1/script/{sample_script.id}/config",
            headers=auth_headers_admin,
        )
        assert response.status_code == 400

    def test_empty_fields_returns_400(
        self, client, auth_headers_admin, sample_script, db_session
    ):
        """Empty update payload returns 400."""
        sample_script = db_session.merge(sample_script)
        response = client.patch(
            f"/api/v1/script/{sample_script.id}/config",
            json={"unknown_field": "value"},
            headers=auth_headers_admin,
        )
        assert response.status_code == 400
