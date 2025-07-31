"""SCRIPT MODEL"""

import datetime
import uuid

from gefapi import db
from gefapi.models import GUID
from gefapi.utils.permissions import is_admin_or_higher

db.GUID = GUID


class Script(db.Model):
    """Script Model"""

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.Text(), default="")
    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    user_id = db.Column(db.GUID(), db.ForeignKey("user.id"))
    status = db.Column(db.String(80), nullable=False, default="PENDING")
    logs = db.relationship(
        "ScriptLog",
        backref=db.backref("script"),
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    executions = db.relationship(
        "Execution",
        backref=db.backref("script"),
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    public = db.Column(db.Boolean(), default=False, nullable=False)
    # Access control fields
    allowed_roles = db.Column(db.Text(), default=None)  # JSON array of allowed roles
    allowed_users = db.Column(db.Text(), default=None)  # JSON array of allowed user IDs
    # Flag for restricted access
    restricted = db.Column(db.Boolean(), default=False, nullable=False)
    # When setting cpu reservations, note that 1e8 is 10% of a CPU
    cpu_reservation = db.Column(db.BigInteger(), default=int(1e8))
    cpu_limit = db.Column(db.BigInteger(), default=int(5e8))
    # memory reservations are in bytes
    memory_reservation = db.Column(db.BigInteger(), default=int(1e8))
    memory_limit = db.Column(db.BigInteger(), default=int(1e9))
    environment = db.Column(db.Text(), default="trends.earth-environment")
    environment_version = db.Column(db.Text(), default="0.1.6")

    def __init__(
        self,
        name,
        slug,
        user_id,
        cpu_reservation=None,
        cpu_limit=None,
        memory_reservation=None,
        memory_limit=None,
        environment=None,
        environment_version=None,
        allowed_roles=None,
        allowed_users=None,
        restricted=False,
    ):
        self.name = name
        self.slug = slug
        self.user_id = user_id
        self.cpu_reservation = cpu_reservation
        self.cpu_limit = cpu_limit
        self.memory_reservation = memory_reservation
        self.memory_limit = memory_limit
        self.environment = environment
        self.environment_version = environment_version
        self.allowed_roles = allowed_roles
        self.allowed_users = allowed_users
        self.restricted = restricted

    def __repr__(self):
        return f"<Script {self.name!r}>"

    def can_access(self, user):
        """Check if a user can access this script"""
        import json

        from gefapi.utils.permissions import is_admin_or_higher

        # Admins and superadmins can always access
        if is_admin_or_higher(user):
            return True

        # Script owner can always access
        if user.id == self.user_id:
            return True

        # Public scripts can be accessed by anyone
        if self.public:
            return True

        # If script is not restricted, any authenticated user can access
        if not self.restricted:
            return True

        # Check role-based access
        if self.allowed_roles:
            try:
                allowed_roles = json.loads(self.allowed_roles)
                if user.role in allowed_roles:
                    return True
            except (json.JSONDecodeError, AttributeError):
                pass

        # Check user-based access
        if self.allowed_users:
            try:
                allowed_users = json.loads(self.allowed_users)
                if str(user.id) in allowed_users:
                    return True
            except (json.JSONDecodeError, AttributeError):
                pass

        return False

    def set_allowed_roles(self, roles):
        """Set allowed roles for this script"""
        import json

        if roles:
            self.allowed_roles = json.dumps(roles) if isinstance(roles, list) else roles
            self.restricted = True
        else:
            self.allowed_roles = None

    def set_allowed_users(self, user_ids):
        """Set allowed users for this script"""
        import json

        if user_ids:
            if isinstance(user_ids, list):
                self.allowed_users = json.dumps(user_ids)
            else:
                self.allowed_users = user_ids
            self.restricted = True
        else:
            self.allowed_users = None

    def get_allowed_roles(self):
        """Get list of allowed roles"""
        import json

        if self.allowed_roles:
            try:
                return json.loads(self.allowed_roles)
            except json.JSONDecodeError:
                return []
        return []

    def get_allowed_users(self):
        """Get list of allowed user IDs"""
        import json

        if self.allowed_users:
            try:
                return json.loads(self.allowed_users)
            except json.JSONDecodeError:
                return []
        return []

    def serialize(self, include=None, exclude=None, user=None):
        """Return object data in easily serializeable format"""
        include = include if include else []
        exclude = exclude if exclude else []
        script = {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "user_id": self.user_id,
            "status": self.status,
            "public": self.public or False,
            "restricted": self.restricted or False,
            "cpu_reservation": self.cpu_reservation,
            "cpu_limit": self.cpu_limit,
            "memory_reservation": self.memory_reservation,
            "memory_limit": self.memory_limit,
        }
        if "logs" in include:
            script["logs"] = self.serialize_logs
        if "user" in include:
            script["user"] = self.user.serialize()
        if "user_name" in include:
            if user and not is_admin_or_higher(user):
                raise Exception("Only admin or superadmin users can include user_name")
            script["user_name"] = getattr(self.user, "name", None)
        if "user_email" in include:
            if user and not is_admin_or_higher(user):
                raise Exception("Only admin or superadmin users can include user_email")
            script["user_email"] = getattr(self.user, "email", None)
        if "executions" in include:
            script["executions"] = self.serialize_executions
        if "environment" in include:
            script["environment"] = self.environment
            script["environment_version"] = self.environment_version
        if "access_control" in include:
            if user and is_admin_or_higher(user):
                script["allowed_roles"] = self.get_allowed_roles()
                script["allowed_users"] = self.get_allowed_users()
            elif user and user.id == self.user_id:
                # Script owners can see access control info for their own scripts
                script["allowed_roles"] = self.get_allowed_roles()
                script["allowed_users"] = self.get_allowed_users()

        # Remove excluded fields
        for field in exclude:
            script.pop(field, None)

        return script

    @property
    def serialize_logs(self):
        """Serialize Logs"""
        return [item.serialize() for item in self.logs]

    @property
    def serialize_executions(self):
        """Serialize Logs"""
        return [item.serialize() for item in self.executions]
