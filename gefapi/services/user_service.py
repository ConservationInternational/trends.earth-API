"""SCRIPT SERVICE"""

import datetime
import logging
import re
import secrets
import string
from uuid import UUID

import rollbar
from sqlalchemy import func

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import (
    AuthError,
    EmailError,
    PasswordValidationError,
    UserDuplicated,
    UserNotFound,
)
from gefapi.models import User
from gefapi.services import EmailService
from gefapi.utils.security_events import (
    log_authentication_event,
    log_password_event,
)

ROLES = SETTINGS.get("ROLES")


logger = logging.getLogger()

MIN_PASSWORD_LENGTH = 12
SPECIAL_CHARACTERS = "!@#$%^&*()-_=+[]{}|;:,.<>?/"


def _generate_secure_password(length: int = 16) -> str:
    """Generate a password that satisfies the minimum complexity rules."""

    chars_upper = string.ascii_uppercase
    chars_lower = string.ascii_lowercase
    chars_digits = string.digits
    chars_special = SPECIAL_CHARACTERS

    if length < 12:
        length = 12

    password_chars = [
        secrets.choice(chars_upper),
        secrets.choice(chars_lower),
        secrets.choice(chars_digits),
        secrets.choice(chars_special),
    ]

    all_chars = chars_upper + chars_lower + chars_digits + chars_special
    password_chars.extend(secrets.choice(all_chars) for _ in range(length - 4))
    secrets.SystemRandom().shuffle(password_chars)
    return "".join(password_chars)


def _validate_password_strength(password: str) -> None:
    """Ensure passwords meet basic complexity requirements."""

    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long"
        )

    if not re.search(r"[A-Z]", password):
        raise PasswordValidationError("Password must include an uppercase letter")

    if not re.search(r"[a-z]", password):
        raise PasswordValidationError("Password must include a lowercase letter")

    if not re.search(r"\d", password):
        raise PasswordValidationError("Password must include a number")

    if not re.search(f"[{re.escape(SPECIAL_CHARACTERS)}]", password):
        raise PasswordValidationError("Password must include a special character")


class UserService:
    """User Class"""

    @staticmethod
    def create_user(user, legacy=True):
        """Create a new user account.

        Args:
            user: Dictionary with user data (email, password, name, etc.)
            legacy: If True (default), emails the password directly for
                backwards compatibility with existing QGIS plugin.
                If False, sends a password reset link instead.

        Returns:
            User object

        When legacy=True (default):
            - Generates password if not provided
            - Emails the plain-text password to the user
            - Maintains backwards compatibility with QGIS plugin

        When legacy=False (secure mode):
            - Creates user with a temporary locked password
            - Sends a password reset email with a secure token link
            - User must click the link to set their own password
            - Token expires after 1 hour
        """
        logger.info("[SERVICE]: Creating user")
        email_addr = user.get("email", None)
        password = user.get("password", None)
        role = user.get("role", "USER")
        name = user.get("name", "notset")
        country = user.get("country", None)
        institution = user.get("institution", None)

        if role not in ROLES:
            role = "USER"
        if email_addr is None:
            raise PasswordValidationError("Email is required")

        # Check for existing user
        current_user = User.query.filter_by(email=email_addr).first()
        if current_user:
            raise UserDuplicated(
                message="User with email " + email_addr + " already exists"
            )

        if legacy:
            return UserService._create_user_legacy(
                email_addr=email_addr,
                password=password,
                role=role,
                name=name,
                country=country,
                institution=institution,
            )
        return UserService._create_user_secure(
            email_addr=email_addr,
            password=password,
            role=role,
            name=name,
            country=country,
            institution=institution,
        )

    @staticmethod
    def _create_user_legacy(email_addr, password, role, name, country, institution):
        """Legacy user creation - emails plain-text password.

        This maintains backwards compatibility with the QGIS plugin
        which expects the password to be included in the welcome email.
        """
        if password is None:
            password = _generate_secure_password()
        else:
            _validate_password_strength(password)

        user = User(
            email=email_addr,
            password=password,
            role=role,
            name=name,
            country=country,
            institution=institution,
        )
        try:
            logger.info("[DB]: ADD")
            db.session.add(user)
            db.session.commit()
            try:
                EmailService.send_html_email(
                    recipients=[user.email],
                    html="<p>User: "
                    + user.email
                    + "</p><p>Password: "
                    + password
                    + "</p>",
                    subject="[trends.earth] User created",
                )
            except EmailError as error:
                rollbar.report_exc_info()
                raise error
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return user

    @staticmethod
    def _create_user_secure(email_addr, password, role, name, country, institution):
        """Secure user creation - sends password reset link.

        Instead of emailing the password directly, this method:
        1. Creates the user with a temporary secure password
        2. Generates a password reset token
        3. Sends an email with a link to set their password
        4. The token expires after 1 hour

        If a password is provided, it is validated but the user must still
        use the reset link to set it (the provided password is ignored for
        security - we don't want passwords transmitted via the API).
        """
        from gefapi.models import PasswordResetToken

        # Validate password if provided (but don't use it)
        if password is not None:
            _validate_password_strength(password)
            logger.info(
                "[SERVICE]: Password provided but using secure flow - "
                "user will set password via email link"
            )

        # Create user with a temporary password (user can't use this directly)
        temp_password = _generate_secure_password(length=32)
        user = User(
            email=email_addr,
            password=temp_password,
            role=role,
            name=name,
            country=country,
            institution=institution,
        )

        try:
            logger.info("[DB]: ADD user")
            db.session.add(user)
            db.session.commit()

            # Create password reset token
            reset_token = PasswordResetToken(user_id=user.id)
            logger.info("[DB]: ADD password reset token")
            db.session.add(reset_token)
            db.session.commit()

            # Build the reset URL (points to UI, which routes to /reset-password)
            api_url = SETTINGS.get("API_URL", "https://api.trends.earth")
            reset_url = f"{api_url}/reset-password?token={reset_token.token}"

            # Send welcome email with reset link
            email_html = f"""
            <p>Hello {user.name},</p>

            <p>Welcome to Trends.Earth! Your account has been created.</p>

            <p>To complete your registration, please set your password by
            clicking the link below. This link will expire in 1 hour.</p>

            <p><a href="{reset_url}">Set Your Password</a></p>

            <p>If you cannot click the link, copy and paste this URL into your
            browser:</p>
            <p>{reset_url}</p>

            <p>If you did not create this account, please ignore this email.</p>

            <p>Best regards,<br>The Trends.Earth Team</p>
            """

            try:
                EmailService.send_html_email(
                    recipients=[user.email],
                    html=email_html,
                    subject="[trends.earth] Welcome - Set Your Password",
                )
                logger.info(
                    f"[SERVICE]: Secure registration email sent to {user.email}"
                )
            except EmailError as error:
                rollbar.report_exc_info()
                raise error

        except Exception as error:
            rollbar.report_exc_info()
            raise error

        return user

    @staticmethod
    def get_users(
        filter_param=None,
        sort=None,
        page=1,
        per_page=2000,
        paginate=False,
    ):
        logger.info("[SERVICE]: Getting users")
        logger.info("[DB]: QUERY")

        if paginate:
            if page < 1:
                raise Exception("Page must be greater than 0")
            if per_page < 1:
                raise Exception("Per page must be greater than 0")

        query = db.session.query(User)

        # SQL-style filter_param
        if filter_param:
            import re

            from sqlalchemy import and_

            filter_clauses = []
            for expr in filter_param.split(","):
                expr = expr.strip()
                m = re.match(
                    r"(\w+)\s*(=|!=|>=|<=|>|<| like )\s*(.+)", expr, re.IGNORECASE
                )
                if m:
                    field, op, value = m.groups()
                    field = field.strip().lower()
                    op = op.strip().lower()
                    value = value.strip().strip("'\"")
                    col = getattr(User, field, None)
                    if col is not None:
                        # Check if this is a string column for case-insensitive compare
                        is_string_col = (
                            hasattr(col.type, "python_type")
                            and isinstance(col.type.python_type, type)
                            and issubclass(col.type.python_type, str)
                        ) or str(col.type).upper().startswith(
                            ("VARCHAR", "TEXT", "STRING")
                        )

                        if op == "=":
                            if is_string_col:
                                filter_clauses.append(func.lower(col) == value.lower())
                            else:
                                filter_clauses.append(col == value)
                        elif op == "!=":
                            if is_string_col:
                                filter_clauses.append(func.lower(col) != value.lower())
                            else:
                                filter_clauses.append(col != value)
                        elif op == ">":
                            filter_clauses.append(col > value)
                        elif op == "<":
                            filter_clauses.append(col < value)
                        elif op == ">=":
                            filter_clauses.append(col >= value)
                        elif op == "<=":
                            filter_clauses.append(col <= value)
                        elif op == "like":
                            filter_clauses.append(col.ilike(value))
            if filter_clauses:
                query = query.filter(and_(*filter_clauses))

        # SQL-style sorting
        if sort:
            from sqlalchemy import asc, desc

            for sort_expr in sort.split(","):
                sort_expr = sort_expr.strip()
                if not sort_expr:
                    continue
                parts = sort_expr.split()
                field = parts[0].lower()
                direction = parts[1].lower() if len(parts) > 1 else "asc"
                col = getattr(User, field, None)
                if col is not None:
                    if direction == "desc":
                        query = query.order_by(desc(col))
                    else:
                        query = query.order_by(asc(col))
        else:
            query = query.order_by(User.created_at.desc())

        if paginate:
            total = query.count()
            users = query.offset((page - 1) * per_page).limit(per_page).all()
        else:
            users = query.all()
            total = len(users)

        return users, total

    @staticmethod
    def get_user(user_id):
        logger.info(f"[SERVICE]: Getting user {user_id}")
        logger.info("[DB]: QUERY")
        try:
            # If user_id is already a UUID object, use it directly
            if isinstance(user_id, UUID):
                user = User.query.get(user_id)
            else:
                UUID(user_id, version=4)
                user = User.query.get(user_id)
        except ValueError:
            user = User.query.filter_by(email=user_id).first()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        if not user:
            raise UserNotFound(message=f"User with id {user_id} does not exist")
        return user

    @staticmethod
    def change_password(user, old_password, new_password):
        """Change user password"""
        logger.info(f"[SERVICE]: Changing password for user {user.email}")
        if not user.check_password(old_password):
            raise AuthError("Invalid current password")
        if old_password == new_password:
            raise PasswordValidationError(
                "New password must be different from current password"
            )
        _validate_password_strength(new_password)
        user.password = user.set_password(new_password)
        try:
            db.session.add(user)
            db.session.commit()
            logger.info(
                f"[SERVICE]: Password for user {user.email} changed successfully"
            )
            # Log security event
            log_password_event(
                "PASSWORD_CHANGE", str(user.id), user.email, admin_action=False
            )

            # Invalidate all other sessions for security after password change
            from gefapi.services.refresh_token_service import RefreshTokenService

            try:
                revoked_count = RefreshTokenService.invalidate_user_sessions(user.id)
                logger.info(
                    f"[SERVICE]: Invalidated {revoked_count} sessions after "
                    f"password change for {user.email}"
                )
            except Exception as e:
                logger.warning(
                    f"[SERVICE]: Failed to invalidate sessions after "
                    f"password change: {e}"
                )

        except Exception as e:
            db.session.rollback()
            logger.error(f"[SERVICE]: Error changing password for {user.email}: {e}")
            rollbar.report_exc_info()
            raise
        return user

    @staticmethod
    def admin_change_password(user, new_password):
        """Admin change user password (no old password verification required)"""
        logger.info(f"[SERVICE]: Admin changing password for user {user.email}")
        _validate_password_strength(new_password)
        user.password = user.set_password(new_password)
        try:
            db.session.add(user)
            db.session.commit()
            logger.info(
                f"[SERVICE]: Password for user {user.email} changed successfully "
                f"by admin"
            )
            # Log security event
            log_password_event(
                "PASSWORD_CHANGE", str(user.id), user.email, admin_action=True
            )

            # Invalidate all user sessions for security
            from gefapi.services.refresh_token_service import RefreshTokenService

            try:
                revoked_count = RefreshTokenService.invalidate_user_sessions(user.id)
                logger.info(
                    f"[SERVICE]: Invalidated {revoked_count} sessions for {user.email} "
                    f"after admin password change"
                )
            except Exception as session_error:
                logger.warning(
                    f"[SERVICE]: Failed to invalidate sessions for "
                    f"{user.email}: {session_error}"
                )
                # Don't fail the password change if session invalidation fails

        except Exception as e:
            db.session.rollback()
            logger.error(f"[SERVICE]: Error changing password for {user.email}: {e}")
            rollbar.report_exc_info()
            raise
        return user

    @staticmethod
    def recover_password(user_id, legacy=True):
        """Initiate password recovery for a user account.

        Supports two modes:
        - Legacy mode (default): Generates a new password and emails it directly.
          This maintains backwards compatibility with existing QGIS plugin
          installations.
        - Secure mode (legacy=False): Sends a secure reset link that expires
          after 1 hour. More secure but requires client support.

        Args:
            user_id: User identifier (email or ID)
            legacy: If True (default), use legacy password-email flow for
                backwards compatibility. If False, use secure token-based flow.

        Returns:
            User object

        Raises:
            UserNotFound: If user doesn't exist
            EmailError: If email delivery fails
        """
        logger.info(f"[SERVICE]: Initiating password recovery for {user_id}")
        logger.info("[DB]: QUERY")
        user = UserService.get_user(user_id=user_id)
        if not user:
            raise UserNotFound(message="User with id " + user_id + " does not exist")

        if legacy:
            # Legacy mode: Generate password and email directly
            # DEPRECATED: This method is less secure as passwords are sent via email
            logger.warning(
                f"[SERVICE]: Using LEGACY password recovery for {user_id}. "
                "Consider migrating to token-based recovery (legacy=false)."
            )
            return UserService._recover_password_legacy(user)
        # Secure mode: Send reset token link
        return UserService._recover_password_secure(user)

    @staticmethod
    def _recover_password_legacy(user):
        """Legacy password recovery - generates and emails password directly.

        DEPRECATED: This method is maintained for backwards compatibility with
        older QGIS plugin versions. New integrations should use token-based
        recovery (legacy=False).

        Security concerns:
        - Password is transmitted via email (can be intercepted)
        - Password is stored in email history
        - No verification that requester controls the email
        """
        password = _generate_secure_password()
        user.password = user.set_password(password=password)
        try:
            logger.info("[DB]: ADD")
            db.session.add(user)
            db.session.commit()
            try:
                EmailService.send_html_email(
                    recipients=[user.email],
                    html="<p>User: "
                    + user.email
                    + "</p><p>Password: "
                    + password
                    + "</p>",
                    subject="[trends.earth] Recover password",
                )
            except EmailError as error:
                rollbar.report_exc_info()
                raise error
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return user

    @staticmethod
    def _recover_password_secure(user):
        """Secure password recovery using time-limited reset tokens.

        This is the recommended method for password recovery:
        1. Generates a cryptographically secure reset token
        2. Invalidates any existing tokens for the user
        3. Sends an email with a secure reset link
        4. The link expires after 1 hour
        """
        from gefapi.models import PasswordResetToken

        try:
            # Invalidate any existing tokens for this user
            PasswordResetToken.invalidate_user_tokens(user.id)

            # Create new reset token
            reset_token = PasswordResetToken(user_id=user.id)
            logger.info("[DB]: ADD password reset token")
            db.session.add(reset_token)
            db.session.commit()

            # Build the reset URL (points to UI, which routes to /reset-password)
            api_url = SETTINGS.get("API_URL", "https://api.trends.earth")
            reset_url = f"{api_url}/reset-password?token={reset_token.token}"

            # Send email with reset link (not the password itself)
            email_html = f"""
            <p>Hello {user.name},</p>

            <p>A password reset was requested for your Trends.Earth account.</p>

            <p>Click the link below to reset your password. This link will expire
            in 1 hour.</p>

            <p><a href="{reset_url}">Reset Your Password</a></p>

            <p>If you cannot click the link, copy and paste this URL into your
            browser:</p>
            <p>{reset_url}</p>

            <p>If you did not request this password reset, please ignore this email.
            Your password will remain unchanged.</p>

            <p>For security, do not share this link with anyone.</p>

            <p>Best regards,<br>The Trends.Earth Team</p>
            """

            try:
                EmailService.send_html_email(
                    recipients=[user.email],
                    html=email_html,
                    subject="[trends.earth] Password Reset Request",
                )
                logger.info(f"[SERVICE]: Password reset email sent to {user.email}")
            except EmailError as error:
                rollbar.report_exc_info()
                raise error

        except Exception as error:
            rollbar.report_exc_info()
            raise error

        return user

    @staticmethod
    def reset_password_with_token(token_string, new_password):
        """Reset a user's password using a valid reset token.

        Args:
            token_string: The password reset token from the email link
            new_password: The new password to set

        Returns:
            User object if successful

        Raises:
            UserNotFound: If token is invalid, expired, or already used
            PasswordValidationError: If new password doesn't meet requirements
        """
        from gefapi.models import PasswordResetToken

        logger.info("[SERVICE]: Attempting password reset with token")

        # Find and validate token
        reset_token = PasswordResetToken.get_valid_token(token_string)
        if not reset_token:
            logger.warning("[SERVICE]: Invalid or expired password reset token used")
            raise UserNotFound(message="Invalid or expired password reset token")

        # Validate new password strength
        _validate_password_strength(new_password)

        # Get the user
        user = User.query.get(reset_token.user_id)
        if not user:
            raise UserNotFound(message="User not found")

        try:
            # Set new password
            user.password = user.set_password(password=new_password)

            # Mark token as used
            reset_token.mark_used()

            logger.info("[DB]: ADD")
            db.session.add(user)
            db.session.add(reset_token)
            db.session.commit()

            logger.info(f"[SERVICE]: Password reset successful for {user.email}")

            # Log security event
            log_password_event(
                "PASSWORD_RESET", str(user.id), user.email, admin_action=False
            )

            return user

        except Exception as error:
            rollbar.report_exc_info()
            raise error

    @staticmethod
    def update_profile_password(user, current_user):
        logger.info("[SERVICE]: Updating user password")
        password = user.get("password")
        _validate_password_strength(password)
        current_user.password = current_user.set_password(password=password)
        try:
            logger.info("[DB]: ADD")
            db.session.add(current_user)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return current_user

    @staticmethod
    def update_user(user, user_id):
        """Update user profile information including notification preferences

        Updates various user fields including basic profile information and
        preferences like email notification settings.

        Args:
            user (dict): Dictionary containing fields to update:
                - name (str, optional): User's display name
                - country (str, optional): User's country
                - institution (str, optional): User's institution/organization
                - role (str, optional): User's role (filtered by permissions)
                - email_notifications_enabled (bool, optional): Email notification
                  preference
            user_id (str): UUID of the user to update

        Returns:
            User: Updated user object

        Raises:
            UserNotFound: If user with given ID doesn't exist

        Notes:
            - Role updates are filtered based on user permissions
            - email_notifications_enabled must be a boolean value
            - Automatically updates the updated_at timestamp
        """
        logger.info("[SERVICE]: Updating user")
        current_user = UserService.get_user(user_id=user_id)
        if not current_user:
            raise UserNotFound(message="User with id " + user_id + " does not exist")
        if "role" in user:
            role = user.get("role") if user.get("role") in ROLES else current_user.role
            current_user.role = role
        current_user.name = user.get("name", current_user.name)
        current_user.country = user.get("country", current_user.country)
        current_user.institution = user.get("institution", current_user.institution)

        # Update email notification preferences if provided
        if "email_notifications_enabled" in user:
            email_notifications_enabled = user.get("email_notifications_enabled")
            if isinstance(email_notifications_enabled, bool):
                current_user.email_notifications_enabled = email_notifications_enabled

        current_user.updated_at = datetime.datetime.utcnow()
        try:
            logger.info("[DB]: ADD")
            db.session.add(current_user)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return current_user

    @staticmethod
    def delete_user(user_id):
        logger.info("[SERVICE]: Deleting user " + str(user_id))
        user = UserService.get_user(user_id=user_id)
        if not user:
            raise UserNotFound(
                message="User with ID " + str(user_id) + " does not exist"
            )
        try:
            # Import models for explicit deletion
            from sqlalchemy import String, cast

            from gefapi.models import Execution, ExecutionLog, Script, StatusLog
            from gefapi.models.password_reset_token import PasswordResetToken
            from gefapi.models.refresh_token import RefreshToken
            from gefapi.models.script_log import ScriptLog

            user_uuid = user.id

            # Get execution IDs for this user (needed for related table deletions)
            execution_ids_uuid = db.session.query(Execution.id).filter(
                Execution.user_id == user_uuid
            )
            # Cast to string for StatusLog which uses String(36) for execution_id
            execution_ids_str = db.session.query(cast(Execution.id, String)).filter(
                Execution.user_id == user_uuid
            )

            # Get script IDs for this user (needed for script log deletions)
            script_ids_uuid = db.session.query(Script.id).filter(
                Script.user_id == user_uuid
            )

            # Batch delete related records for performance
            # Delete status logs for user's executions first
            # StatusLog.execution_id is String(36), so use string-cast query
            logger.info("[DB]: Deleting status logs for user's executions")
            StatusLog.query.filter(
                StatusLog.execution_id.in_(execution_ids_str)
            ).delete(synchronize_session=False)

            # Delete execution logs (uses UUID foreign key)
            logger.info("[DB]: Deleting execution logs for user's executions")
            ExecutionLog.query.filter(
                ExecutionLog.execution_id.in_(execution_ids_uuid)
            ).delete(synchronize_session=False)

            # Delete executions
            logger.info("[DB]: Deleting executions")
            Execution.query.filter(Execution.user_id == user_uuid).delete(
                synchronize_session=False
            )

            # Delete script logs for user's scripts
            logger.info("[DB]: Deleting script logs for user's scripts")
            ScriptLog.query.filter(ScriptLog.script_id.in_(script_ids_uuid)).delete(
                synchronize_session=False
            )

            # Delete scripts
            logger.info("[DB]: Deleting scripts")
            Script.query.filter(Script.user_id == user_uuid).delete(
                synchronize_session=False
            )

            # Delete password reset tokens
            logger.info("[DB]: Deleting password reset tokens")
            PasswordResetToken.query.filter(
                PasswordResetToken.user_id == user_uuid
            ).delete(synchronize_session=False)

            # Delete refresh tokens
            logger.info("[DB]: Deleting refresh tokens")
            RefreshToken.query.filter(RefreshToken.user_id == user_uuid).delete(
                synchronize_session=False
            )

            # Now delete the user
            logger.info("[DB]: DELETE user")
            db.session.delete(user)
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            rollbar.report_exc_info()
            raise error
        return user

    @staticmethod
    def authenticate_user(email, password):
        logger.info(f"[AUTH]: Authentication attempt for {email}")
        user = User.query.filter_by(email=email).first()

        if not user:
            logger.warning(f"[AUTH]: Failed login - user not found: {email}")
            log_authentication_event(False, email, "user_not_found")
            return None

        if not user.check_password(password):
            logger.warning(f"[AUTH]: Failed login - invalid password: {email}")
            log_authentication_event(False, email, "invalid_password")
            return None

        # Successful authentication
        # logger.info(f"[AUTH]: Successful login for user {email}")
        # log_authentication_event(True, email)

        #  to serialize id with jwt
        user.id = user.id.hex
        return user
