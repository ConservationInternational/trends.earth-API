"""USER DELETION AUDIT MODEL

Tracks when users are deleted from the system for compliance and analytics.

This table stores anonymized records of user deletions while ensuring:
1. GDPR/EUDR compliance - no PII retained after deletion request
2. Audit trail for security and compliance reporting
3. Analytics about user churn and cleanup operations

IMPORTANT PRIVACY NOTES:
- email_hash: SHA-256 hash of email for de-duplication detection only
- No names, actual emails, or other PII is retained
- For user-requested deletions, even email_hash is cleared after 30 days
"""

import datetime
import hashlib
import logging
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID

logger = logging.getLogger(__name__)


class DeletionReason:
    """Constants for tracking why a user was deleted."""

    # User-initiated deletions (GDPR right to erasure)
    USER_REQUEST = "user_request"  # User deleted their own account
    ADMIN_REQUEST = "admin_request"  # Admin deleted user account

    # Automated cleanup deletions
    UNVERIFIED_EMAIL = "unverified_email"  # Never verified email address
    NEVER_LOGGED_IN = "never_logged_in"  # Created account but never used it
    INACTIVE = "inactive"  # Long period of inactivity

    # Other
    POLICY_VIOLATION = "policy_violation"  # Terms of service violation
    DUPLICATE_ACCOUNT = "duplicate_account"  # Duplicate/spam account
    OTHER = "other"  # Catch-all for other reasons

    @classmethod
    def is_gdpr_erasure_request(cls, reason: str) -> bool:
        """Check if this deletion triggers GDPR erasure requirements."""
        return reason in (cls.USER_REQUEST,)


class UserDeletionAudit(db.Model):
    """Audit record for user deletions.

    Stores anonymized information about deleted users for:
    - Compliance reporting (GDPR/EUDR audit trail)
    - Analytics on user churn
    - Detection of abuse patterns (rapid re-registration)

    Privacy by Design:
    - No PII is stored that isn't necessary
    - email_hash allows de-duplication without storing email
    - For GDPR erasure requests, email_hash is cleared after retention period
    - Country is kept as aggregate geographic data for reporting
    """

    __tablename__ = "user_deletion_audit"

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )

    # When the deletion occurred
    deleted_at = db.Column(
        db.DateTime(),
        default=datetime.datetime.utcnow,
        nullable=False,
        index=True,
    )

    # Why the user was deleted (see DeletionReason constants)
    deletion_reason = db.Column(db.String(50), nullable=False, index=True)

    # Email hash for de-duplication detection (SHA-256)
    # Cleared after retention period for GDPR erasure requests
    email_hash = db.Column(db.String(64), nullable=True, index=True)

    # Non-PII metadata retained for analytics
    country = db.Column(db.String(120), nullable=True)

    # Account age and activity metrics (anonymized statistics)
    account_created_at = db.Column(db.DateTime(), nullable=True)
    account_age_days = db.Column(db.Integer(), nullable=True)
    last_login_at = db.Column(db.DateTime(), nullable=True)
    days_since_last_login = db.Column(db.Integer(), nullable=True)
    last_activity_at = db.Column(db.DateTime(), nullable=True)
    days_since_last_activity = db.Column(db.Integer(), nullable=True)

    # Usage statistics (non-identifying aggregate data)
    total_executions = db.Column(db.Integer(), default=0)
    total_scripts = db.Column(db.Integer(), default=0)
    completed_executions = db.Column(db.Integer(), default=0)
    failed_executions = db.Column(db.Integer(), default=0)

    # Was email verified before deletion?
    email_verified = db.Column(db.Boolean(), nullable=True)

    # User role at time of deletion (USER, ADMIN, SUPERADMIN)
    role = db.Column(db.String(10), nullable=True)

    # Actor information (who performed the deletion)
    # For user_request: same as deleted user (but anonymized)
    # For admin_request: admin user id (kept for audit trail)
    deleted_by_admin_id = db.Column(db.GUID(), nullable=True)

    # Additional context (JSON-serialized, no PII)
    # Example: {"cleanup_task": "cleanup_unverified_users", "threshold_days": 60}
    context = db.Column(db.Text(), nullable=True)

    # For GDPR compliance: when to clear email_hash
    # Set to deleted_at + 30 days for user-requested deletions
    email_hash_expires_at = db.Column(db.DateTime(), nullable=True)

    def __init__(
        self,
        deletion_reason: str,
        email: str | None = None,
        country: str | None = None,
        account_created_at: datetime.datetime | None = None,
        last_login_at: datetime.datetime | None = None,
        last_activity_at: datetime.datetime | None = None,
        total_executions: int = 0,
        total_scripts: int = 0,
        completed_executions: int = 0,
        failed_executions: int = 0,
        email_verified: bool | None = None,
        role: str | None = None,
        deleted_by_admin_id: str | None = None,
        context: str | None = None,
    ):
        self.deletion_reason = deletion_reason
        self.deleted_at = datetime.datetime.utcnow()

        # Hash email for de-duplication (not storing the actual email)
        if email:
            self.email_hash = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()
        else:
            self.email_hash = None

        # For GDPR erasure requests, set expiration for email hash
        if DeletionReason.is_gdpr_erasure_request(deletion_reason):
            self.email_hash_expires_at = self.deleted_at + datetime.timedelta(days=30)

        self.country = country
        self.account_created_at = account_created_at
        self.last_login_at = last_login_at
        self.last_activity_at = last_activity_at
        self.total_executions = total_executions
        self.total_scripts = total_scripts
        self.completed_executions = completed_executions
        self.failed_executions = failed_executions
        self.email_verified = email_verified
        self.role = role
        self.deleted_by_admin_id = deleted_by_admin_id
        self.context = context

        # Calculate derived fields
        if account_created_at:
            self.account_age_days = (self.deleted_at - account_created_at).days

        if last_login_at:
            self.days_since_last_login = (self.deleted_at - last_login_at).days

        if last_activity_at:
            self.days_since_last_activity = (self.deleted_at - last_activity_at).days

    def __repr__(self):
        return f"<UserDeletionAudit {self.id} reason={self.deletion_reason}>"

    def serialize(self):
        """Return object data in easily serializable format."""
        return {
            "id": str(self.id) if self.id else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "deletion_reason": self.deletion_reason,
            "country": self.country,
            "account_age_days": self.account_age_days,
            "days_since_last_login": self.days_since_last_login,
            "days_since_last_activity": self.days_since_last_activity,
            "total_executions": self.total_executions,
            "total_scripts": self.total_scripts,
            "completed_executions": self.completed_executions,
            "failed_executions": self.failed_executions,
            "email_verified": self.email_verified,
            "role": self.role,
        }

    @classmethod
    def create_from_user(
        cls,
        user,
        deletion_reason: str,
        deleted_by_admin_id: str | None = None,
        context: str | None = None,
    ) -> "UserDeletionAudit":
        """Create an audit record from a User object before deletion.

        Args:
            user: The User model instance being deleted
            deletion_reason: One of DeletionReason constants
            deleted_by_admin_id: ID of admin performing deletion (if applicable)
            context: Additional JSON context (no PII)

        Returns:
            UserDeletionAudit instance (not yet committed to database)
        """
        # Count executions by status
        from gefapi.models import Execution

        total_executions = user.executions.count()
        completed_executions = user.executions.filter(
            Execution.status == "FINISHED"
        ).count()
        failed_executions = user.executions.filter(Execution.status == "FAILED").count()
        total_scripts = user.scripts.count()

        return cls(
            deletion_reason=deletion_reason,
            email=user.email,
            country=user.country,
            account_created_at=user.created_at,
            last_login_at=user.last_login_at,
            last_activity_at=user.last_activity_at,
            total_executions=total_executions,
            total_scripts=total_scripts,
            completed_executions=completed_executions,
            failed_executions=failed_executions,
            email_verified=user.email_verified,
            role=user.role,
            deleted_by_admin_id=deleted_by_admin_id,
            context=context,
        )

    @classmethod
    def cleanup_expired_hashes(cls) -> int:
        """Remove email hashes that have passed their retention period.

        This should be called periodically (e.g., daily) to ensure
        GDPR compliance for user-requested deletions.

        Returns:
            Number of records updated
        """
        now = datetime.datetime.utcnow()
        result = cls.query.filter(
            cls.email_hash.isnot(None),
            cls.email_hash_expires_at.isnot(None),
            cls.email_hash_expires_at < now,
        ).update({cls.email_hash: None}, synchronize_session=False)
        db.session.commit()
        logger.info(
            f"[AUDIT]: Cleared {result} expired email hashes from deletion audit"
        )
        return result
