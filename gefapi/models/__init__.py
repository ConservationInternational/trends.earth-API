"""GEFAPI MODELS MODULE"""

from operator import attrgetter
import uuid

from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import CHAR, TypeDecorator


# Below is from https://docs.sqlalchemy.org/en/20/core/custom_types.html
# #backend-agnostic-guid-type
class GUID(TypeDecorator):
    """Platform-independent GUID type.

    Uses PostgreSQL's UUID type or MSSQL's UNIQUEIDENTIFIER,
    otherwise uses CHAR(32), storing as stringified hex values.

    """

    impl = CHAR
    cache_ok = True

    _default_type = CHAR(32)
    _uuid_as_str = attrgetter("hex")

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID())
        if dialect.name == "mssql":
            return dialect.type_descriptor(UNIQUEIDENTIFIER())
        return dialect.type_descriptor(self._default_type)

    def process_bind_param(self, value, dialect):
        if value is None or dialect.name in ("postgresql", "mssql"):
            return value
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(value)
        return self._uuid_as_str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(value)
        return value


from gefapi.models.boundary import (  # noqa: E402
    AdminBoundary0Metadata,
    AdminBoundary1Metadata,
    AdminBoundary1Unit,
)
from gefapi.models.execution import Execution  # noqa: E402
from gefapi.models.execution_log import ExecutionLog  # noqa: E402
from gefapi.models.password_reset_token import PasswordResetToken  # noqa: E402
from gefapi.models.rate_limit_event import RateLimitEvent  # noqa: E402
from gefapi.models.refresh_token import RefreshToken  # noqa: E402
from gefapi.models.script import Script  # noqa: E402
from gefapi.models.script_log import ScriptLog  # noqa: E402
from gefapi.models.status_log import StatusLog  # noqa: E402
from gefapi.models.user import User  # noqa: E402

__all__ = [
    "AdminBoundary0Metadata",
    "AdminBoundary1Metadata",
    "AdminBoundary1Unit",
    "Execution",
    "ExecutionLog",
    "PasswordResetToken",
    "RateLimitEvent",
    "RefreshToken",
    "Script",
    "ScriptLog",
    "StatusLog",
    "User",
]
