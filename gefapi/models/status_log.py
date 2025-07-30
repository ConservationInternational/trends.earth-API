"""STATUS LOG MODEL"""

import datetime

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


class StatusLog(db.Model):
    """StatusLog Model"""

    __tablename__ = "status_log"
    id = db.Column(db.Integer(), primary_key=True)
    timestamp = db.Column(db.DateTime(), default=datetime.datetime.utcnow)

    # Execution counts
    executions_active = db.Column(db.Integer(), default=0)
    executions_ready = db.Column(db.Integer(), default=0)
    executions_running = db.Column(db.Integer(), default=0)
    executions_finished = db.Column(db.Integer(), default=0)
    executions_failed = db.Column(db.Integer(), default=0)
    executions_count = db.Column(db.Integer(), default=0)

    # Other counts
    users_count = db.Column(db.Integer(), default=0)
    scripts_count = db.Column(db.Integer(), default=0)

    def __init__(
        self,
        executions_active=0,
        executions_ready=0,
        executions_running=0,
        executions_finished=0,
        executions_failed=0,
        executions_count=0,
        users_count=0,
        scripts_count=0,
    ):
        self.executions_active = executions_active
        self.executions_ready = executions_ready
        self.executions_running = executions_running
        self.executions_finished = executions_finished
        self.executions_failed = executions_failed
        self.executions_count = executions_count
        self.users_count = users_count
        self.scripts_count = scripts_count

    def __repr__(self):
        return f"<StatusLog {self.id!r}>"

    def serialize(self):
        """Return object data in easily serializeable format"""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "executions_active": self.executions_active,
            "executions_ready": self.executions_ready,
            "executions_running": self.executions_running,
            "executions_finished": self.executions_finished,
            "executions_failed": self.executions_failed,
            "executions_count": self.executions_count,
            "users_count": self.users_count,
            "scripts_count": self.scripts_count,
            "memory_available_percent": self.memory_available_percent,
            "cpu_usage_percent": self.cpu_usage_percent,
        }
