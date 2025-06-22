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

    # Other counts
    users_count = db.Column(db.Integer(), default=0)
    scripts_count = db.Column(db.Integer(), default=0)  # System metrics
    memory_available_percent = db.Column(db.Float(), default=0.0)
    cpu_usage_percent = db.Column(db.Float(), default=0.0)

    def __init__(
        self,
        executions_active=0,
        executions_ready=0,
        executions_running=0,
        executions_finished=0,
        users_count=0,
        scripts_count=0,
        memory_available_percent=0.0,
        cpu_usage_percent=0.0,
    ):
        self.executions_active = executions_active
        self.executions_ready = executions_ready
        self.executions_running = executions_running
        self.executions_finished = executions_finished
        self.users_count = users_count
        self.scripts_count = scripts_count
        self.memory_available_percent = memory_available_percent
        self.cpu_usage_percent = cpu_usage_percent

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
            "users_count": self.users_count,
            "scripts_count": self.scripts_count,
            "memory_available_percent": self.memory_available_percent,
            "cpu_usage_percent": self.cpu_usage_percent,
        }
