"""EXECUTION MODEL"""

import datetime
import uuid

from sqlalchemy.dialects.postgresql import JSONB

from gefapi import db
from gefapi.models import GUID
from gefapi.utils.permissions import is_admin_or_higher

db.GUID = GUID


class Execution(db.Model):
    """Execution Model"""

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    start_date = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    end_date = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    status = db.Column(db.String(10), default="PENDING")
    progress = db.Column(db.Integer(), default=0)
    params = db.Column(JSONB, default={})
    results = db.Column(JSONB, default={})
    logs = db.relationship(
        "ExecutionLog",
        backref=db.backref("execution"),
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    script_id = db.Column(db.GUID(), db.ForeignKey("script.id"))
    user_id = db.Column(db.GUID(), db.ForeignKey("user.id"))

    def __init__(self, script_id, params, user_id):
        self.script_id = script_id
        self.params = params
        self.user_id = user_id

    def __repr__(self):
        return f"<Execution {self.id!r}>"

    def serialize(self, include=None, exclude=None, user=None):
        """Return object data in easily serializeable format"""
        include = include if include else []
        exclude = exclude if exclude else []
        end_date_formatted = None
        if self.end_date:
            end_date_formatted = self.end_date.isoformat()
        execution = {
            "id": self.id,
            "script_id": self.script_id,
            "user_id": self.user_id,
            "start_date": self.start_date.isoformat(),
            "end_date": end_date_formatted,
            "status": self.status,
            "progress": self.progress,
            "params": self.params,
            "results": self.results,
        }
        if "duration" in include:
            execution["duration"] = self.calculate_duration()
        if "logs" in include:
            execution["logs"] = self.serialize_logs
        if "user" in include:
            execution["user"] = self.user.serialize()
        # user_name/user_email: only for admin users, silently skip for non-admins
        if "user_name" in include and (not user or is_admin_or_higher(user)):
            execution["user_name"] = getattr(self.user, "name", None)
        if "user_email" in include and (not user or is_admin_or_higher(user)):
            execution["user_email"] = getattr(self.user, "email", None)
        if "script" in include:
            execution["script"] = self.script.serialize(user=user)
        if "script_name" in include:
            execution["script_name"] = getattr(self.script, "name", None)
        if "params" in exclude:
            del execution["params"]
        if "results" in exclude:
            del execution["results"]
        return execution

    def calculate_duration(self):
        """Calculate the duration of the execution in seconds"""
        if self.end_date:
            # Task is finished, calculate actual duration
            duration = self.end_date - self.start_date
        else:
            # Task is still running, calculate current duration
            duration = datetime.datetime.utcnow() - self.start_date

        return duration.total_seconds()

    @property
    def serialize_logs(self):
        """Serialize Logs"""
        return [item.serialize() for item in self.logs]
