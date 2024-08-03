"""SCRIPT MODEL"""

from __future__ import absolute_import, division, print_function

import datetime
import uuid

from gefapi import db
from gefapi.models import GUID

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

    def __repr__(self):
        return "<Script %r>" % self.name

    def serialize(self, include=None):
        """Return object data in easily serializeable format"""
        include = include if include else []
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
            "cpu_reservation": self.cpu_reservation,
            "cpu_limit": self.cpu_limit,
            "memory_reservation": self.memory_reservation,
            "memory_limit": self.memory_limit,
        }
        if "logs" in include:
            script["logs"] = self.serialize_logs
        if "user" in include:
            script["user"] = self.user.serialize()
        if "executions" in include:
            script["executions"] = self.serialize_executions
        if "environment" in include:
            script["environment"] = self.environment
            script["environment_version"] = self.environment_version
        return script

    @property
    def serialize_logs(self):
        """Serialize Logs"""
        return [item.serialize() for item in self.logs]

    @property
    def serialize_executions(self):
        """Serialize Logs"""
        return [item.serialize() for item in self.executions]
