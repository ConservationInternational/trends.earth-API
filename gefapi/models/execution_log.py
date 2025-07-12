"""EXECUTION LOG MODEL"""

import datetime

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


class ExecutionLog(db.Model):
    """ExecutionLog Model"""

    __tablename__ = "execution_log"
    id = db.Column(db.Integer(), primary_key=True)
    text = db.Column(db.Text())
    level = db.Column(db.String(80), nullable=False, default="DEBUG")
    register_date = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    execution_id = db.Column(db.GUID(), db.ForeignKey("execution.id"))

    def __init__(self, text, level, execution_id):
        self.text = text
        self.level = level
        self.execution_id = execution_id

    def __repr__(self):
        return f"<ExecutionLog {self.id!r}>"

    def serialize(self):
        """Return object data in easily serializeable format"""
        return {
            "id": self.id,
            "text": self.text,
            "level": self.level,
            "register_date": self.register_date.isoformat(),
            "execution_id": self.execution_id,
        }
