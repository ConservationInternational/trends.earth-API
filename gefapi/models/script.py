"""SCRIPT MODEL"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime
import uuid

from gefapi.models import GUID
from gefapi.models.model import db

db.GUID = GUID


class Script(db.Model):
    """Script Model"""
    id = db.Column(db.GUID(),
                   default=uuid.uuid4,
                   primary_key=True,
                   autoincrement=False)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.Text(), default='')
    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    user_id = db.Column(db.GUID(), db.ForeignKey('user.id'))
    status = db.Column(db.String(80), nullable=False, default='PENDING')
    logs = db.relationship('ScriptLog',
                           backref=db.backref('script'),
                           cascade='all, delete-orphan',
                           lazy='dynamic')
    executions = db.relationship('Execution',
                                 backref=db.backref('script'),
                                 cascade='all, delete-orphan',
                                 lazy='dynamic')
    public = db.Column(db.Boolean(), default=False, nullable=False)
    cpu_reservation = db.Column(db.Integer(),
                                default=1e8)  # 1e8 is 10% of a CPU
    cpu_limit = db.Column(db.Integer(), default=5e8, nullable=False)
    memory_reservation = db.Column(db.Integer(), default=1e8, nullable=False)
    memory_limit = db.Column(db.Integer(), default=2e9, nullable=False)

    def __init__(self, name, slug, user_id):
        self.name = name
        self.slug = slug
        self.user_id = user_id

    def __repr__(self):
        return '<Script %r>' % self.name

    def serialize(self, include=None):
        """Return object data in easily serializeable format"""
        include = include if include else []
        script = {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'user_id': self.user_id,
            'status': self.status,
            'public': self.public or False,
            'cpu_reservation': self.cpu_reservation,
            'cpu_limit': self.cpu_limit,
            'memory_reservation': self.memory_reservation,
            'memory_limit': self.memory_limit
        }

        if 'logs' in include:
            script['logs'] = self.serialize_logs

        if 'user' in include:
            script['user'] = self.user.serialize()

        if 'executions' in include:
            script['executions'] = self.serialize_executions

        return script

    @property
    def serialize_logs(self):
        """Serialize Logs"""

        return [item.serialize() for item in self.logs]

    @property
    def serialize_executions(self):
        """Serialize Logs"""

        return [item.serialize() for item in self.executions]
