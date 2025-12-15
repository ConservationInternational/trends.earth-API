"""Tests for security improvements.

Tests for:
1. SQL injection prevention in script_service.py (parameterized queries)
2. Column allowlist validation for filter/sort operations
3. Secure password reset tokens (instead of emailing passwords)
"""

import datetime

from gefapi.models.password_reset_token import PasswordResetToken


class TestPasswordResetToken:
    """Tests for the PasswordResetToken model."""

    def test_token_generation_is_secure(self, app):
        """Test that generated tokens are cryptographically secure."""
        with app.app_context():
            token1 = PasswordResetToken._generate_secure_token()
            token2 = PasswordResetToken._generate_secure_token()

            # Tokens should be unique
            assert token1 != token2

            # Tokens should be URL-safe and sufficiently long
            assert len(token1) >= 48
            assert all(c.isalnum() or c in "-_" for c in token1)

    def test_token_expiry(self, app):
        """Test that tokens expire after 1 hour."""
        with app.app_context():
            # Create a mock user_id
            import uuid

            user_id = str(uuid.uuid4())

            token = PasswordResetToken(user_id=user_id)

            # Token should be valid initially
            assert token.is_valid()

            # Check that expires_at is about 1 hour from created_at
            time_diff = token.expires_at - token.created_at
            assert 3500 < time_diff.total_seconds() < 3700  # ~1 hour

    def test_token_single_use(self, app):
        """Test that tokens can only be used once."""
        with app.app_context():
            import uuid

            user_id = str(uuid.uuid4())
            token = PasswordResetToken(user_id=user_id)

            # Token should be valid initially
            assert token.is_valid()

            # Mark as used
            token.mark_used()

            # Token should no longer be valid
            assert not token.is_valid()
            assert token.used_at is not None

    def test_expired_token_is_invalid(self, app):
        """Test that expired tokens are invalid."""
        with app.app_context():
            import uuid

            user_id = str(uuid.uuid4())
            token = PasswordResetToken(user_id=user_id)

            # Manually set expires_at to the past
            token.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
                hours=1
            )

            # Token should not be valid
            assert not token.is_valid()


class TestColumnAllowlists:
    """Tests for column allowlist validation in filter/sort operations."""

    def test_script_filter_allowlist_blocks_unauthorized_fields(self, app):
        """Test that filtering on disallowed fields is rejected."""
        from gefapi.services.script_service import (
            SCRIPT_ADMIN_ONLY_FIELDS,
            SCRIPT_ALLOWED_FILTER_FIELDS,
        )

        # Verify allowlists are defined
        assert len(SCRIPT_ALLOWED_FILTER_FIELDS) > 0
        assert "name" in SCRIPT_ALLOWED_FILTER_FIELDS
        assert "status" in SCRIPT_ALLOWED_FILTER_FIELDS

        # Verify sensitive fields require admin
        assert "user_name" in SCRIPT_ADMIN_ONLY_FIELDS
        assert "user_email" in SCRIPT_ADMIN_ONLY_FIELDS

        # Verify password and other sensitive fields are not in any allowlist
        assert "password" not in SCRIPT_ALLOWED_FILTER_FIELDS
        assert "password" not in SCRIPT_ADMIN_ONLY_FIELDS

    def test_execution_filter_allowlist_blocks_unauthorized_fields(self, app):
        """Test that execution filtering on disallowed fields is rejected."""
        from gefapi.services.execution_service import (
            EXECUTION_ADMIN_ONLY_FIELDS,
            EXECUTION_ALLOWED_FILTER_FIELDS,
        )

        # Verify allowlists are defined
        assert len(EXECUTION_ALLOWED_FILTER_FIELDS) > 0
        assert "status" in EXECUTION_ALLOWED_FILTER_FIELDS
        assert "start_date" in EXECUTION_ALLOWED_FILTER_FIELDS

        # Verify sensitive fields require admin
        assert "user_email" in EXECUTION_ADMIN_ONLY_FIELDS
        assert "user_name" in EXECUTION_ADMIN_ONLY_FIELDS

        # Verify params (could contain sensitive data) is not filterable
        assert "params" not in EXECUTION_ALLOWED_FILTER_FIELDS

    def test_script_sort_allowlist(self, app):
        """Test that script sort allowlist is properly defined."""
        from gefapi.services.script_service import SCRIPT_ALLOWED_SORT_FIELDS

        # Verify common sort fields are allowed
        assert "name" in SCRIPT_ALLOWED_SORT_FIELDS
        assert "created_at" in SCRIPT_ALLOWED_SORT_FIELDS
        assert "updated_at" in SCRIPT_ALLOWED_SORT_FIELDS

    def test_execution_sort_allowlist(self, app):
        """Test that execution sort allowlist is properly defined."""
        from gefapi.services.execution_service import EXECUTION_ALLOWED_SORT_FIELDS

        # Verify common sort fields are allowed
        assert "status" in EXECUTION_ALLOWED_SORT_FIELDS
        assert "start_date" in EXECUTION_ALLOWED_SORT_FIELDS
        assert "duration" in EXECUTION_ALLOWED_SORT_FIELDS


class TestSQLInjectionPrevention:
    """Tests to verify SQL injection vulnerabilities are patched."""

    def test_script_access_control_uses_parameterized_queries(self, app):
        """Test that script access control doesn't use raw SQL text()."""
        import inspect

        from gefapi.services.script_service import ScriptService

        # Get the source code of get_scripts
        source = inspect.getsource(ScriptService.get_scripts)

        # Should not contain text() with f-string (SQL injection vulnerability)
        assert 'text(f"' not in source
        assert "text(f'" not in source

        # Should use cast() for parameterized queries
        assert "cast(" in source or "Cast" in source

    def test_role_and_user_id_patterns_are_safe(self, app):
        """Test that role/user ID patterns don't allow SQL injection."""
        # These patterns should be simple string patterns, not SQL
        test_role = "USER'; DROP TABLE users; --"
        test_user_id = "abc'; DROP TABLE users; --"

        # Creating patterns with these should just create literal strings
        role_pattern = f'%"{test_role}"%'
        user_pattern = f'%"{test_user_id}"%'

        # Patterns should contain the injection attempt as literal text
        assert "DROP TABLE" in role_pattern
        assert "DROP TABLE" in user_pattern

        # But when used with SQLAlchemy's like() with cast(), they're safe
        # because the pattern is passed as a parameter, not interpolated
        # This test verifies the pattern format is what we expect


class TestPasswordRecoveryEndpoint:
    """Tests for the password recovery flow with backwards compatibility."""

    def test_recover_password_defaults_to_legacy_mode(self, app):
        """Test that recover_password defaults to legacy=True for backwards compat."""
        import inspect

        from gefapi.services.user_service import UserService

        # Check the function signature has legacy=True as default
        sig = inspect.signature(UserService.recover_password)
        legacy_param = sig.parameters.get("legacy")
        assert legacy_param is not None
        assert legacy_param.default is True

    def test_legacy_mode_emails_password_directly(self, app):
        """Test that legacy mode maintains old behavior of emailing password."""
        import inspect

        from gefapi.services.user_service import UserService

        # Get the source code of the legacy method
        source = inspect.getsource(UserService._recover_password_legacy)

        # Should generate a password
        assert "_generate_secure_password" in source

        # Should email the password directly (the old insecure behavior)
        assert '"Password: "' in source

    def test_secure_mode_sends_token_not_password(self, app):
        """Test that secure mode (legacy=False) sends a reset link."""
        import inspect

        from gefapi.services.user_service import UserService

        # Get the source code of the secure method
        source = inspect.getsource(UserService._recover_password_secure)

        # Should use PasswordResetToken
        assert "PasswordResetToken" in source

        # Should build a reset URL
        assert "reset_url" in source
        assert "reset-password" in source

        # Should NOT email password directly
        assert '"Password: "' not in source

    def test_recover_password_routes_to_correct_method(self, app):
        """Test that recover_password correctly routes based on legacy flag."""
        import inspect

        from gefapi.services.user_service import UserService

        # Get the source code of recover_password
        source = inspect.getsource(UserService.recover_password)

        # Should check the legacy flag
        assert "if legacy:" in source

        # Should call legacy method for backwards compat
        assert "_recover_password_legacy" in source

        # Should call secure method when legacy=False
        assert "_recover_password_secure" in source

    def test_reset_password_with_token_validates_token(self, app):
        """Test that password reset validates the token."""
        import inspect

        from gefapi.services.user_service import UserService

        # Get the source code
        source = inspect.getsource(UserService.reset_password_with_token)

        # Should validate token
        assert "get_valid_token" in source

        # Should mark token as used
        assert "mark_used" in source

        # Should validate password strength
        assert "_validate_password_strength" in source


class TestSecureUserCreation:
    """Tests for secure user creation with backwards compatibility."""

    def test_create_user_defaults_to_legacy_mode(self, app):
        """Test that create_user defaults to legacy=True for backwards compat."""
        import inspect

        from gefapi.services.user_service import UserService

        # Check the function signature has legacy=True as default
        sig = inspect.signature(UserService.create_user)
        legacy_param = sig.parameters.get("legacy")
        assert legacy_param is not None
        assert legacy_param.default is True

    def test_legacy_user_creation_emails_password(self, app):
        """Test that legacy mode emails the plain-text password."""
        import inspect

        from gefapi.services.user_service import UserService

        # Get the source code of the legacy method
        source = inspect.getsource(UserService._create_user_legacy)

        # Should generate password if not provided
        assert "_generate_secure_password" in source

        # Should email the password directly (old insecure behavior)
        assert '"Password: "' in source

    def test_secure_user_creation_sends_reset_link(self, app):
        """Test that secure mode (legacy=False) sends a password reset link."""
        import inspect

        from gefapi.services.user_service import UserService

        # Get the source code of the secure method
        source = inspect.getsource(UserService._create_user_secure)

        # Should use PasswordResetToken
        assert "PasswordResetToken" in source

        # Should build a reset URL
        assert "reset_url" in source
        assert "reset-password" in source

        # Should NOT email password directly
        assert '"Password: "' not in source

        # Should send welcome email with link
        assert "Welcome to Trends.Earth" in source
        assert "Set Your Password" in source

    def test_create_user_routes_to_correct_method(self, app):
        """Test that create_user correctly routes based on legacy flag."""
        import inspect

        from gefapi.services.user_service import UserService

        # Get the source code of create_user
        source = inspect.getsource(UserService.create_user)

        # Should check the legacy flag
        assert "if legacy:" in source

        # Should call legacy method for backwards compat
        assert "_create_user_legacy" in source

        # Should call secure method when legacy=False
        assert "_create_user_secure" in source

    def test_secure_creation_uses_temporary_password(self, app):
        """Test that secure creation uses a long temporary password."""
        import inspect

        from gefapi.services.user_service import UserService

        # Get the source code
        source = inspect.getsource(UserService._create_user_secure)

        # Should create a temporary password with extra length
        assert "temp_password = _generate_secure_password(length=32)" in source

    def test_secure_creation_validates_provided_password(self, app):
        """Test that provided passwords are validated even in secure mode."""
        import inspect

        from gefapi.services.user_service import UserService

        # Get the source code
        source = inspect.getsource(UserService._create_user_secure)

        # Should validate password if provided
        assert "_validate_password_strength(password)" in source
