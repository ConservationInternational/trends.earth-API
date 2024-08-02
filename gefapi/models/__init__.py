"""GEFAPI MODELS MODULE"""

from __future__ import absolute_import, division, print_function

import uuid

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import CHAR, TypeDecorator


class GUID(TypeDecorator):
    """Platform-independent GUID type.

    Uses PostgreSQL's UUID type, otherwise uses
    CHAR(32), storing as stringified hex values.

    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID())
        else:
            return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return str(value)
        else:
            if not isinstance(value, uuid.UUID):
                return "%.32x" % uuid.UUID(value).int
            else:
                # hexstring
                return "%.32x" % value.int

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            return uuid.UUID(value)


from gefapi.models.execution import Execution  # noqa: E402
from gefapi.models.execution_log import ExecutionLog  # noqa: E402
from gefapi.models.script import Script  # noqa: E402
from gefapi.models.script_log import ScriptLog  # noqa: E402
from gefapi.models.user import User  # noqa: E402

__all__ = ["Execution", "ExecutionLog", "Script", "ScriptLog", "User"]
