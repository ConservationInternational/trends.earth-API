"""EXECUTION MODEL"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime
import uuid

from gefapi import db
from gefapi.models import GUID
from sqlalchemy.dialects.postgresql import JSONB
db.GUID = GUID


class Execution(db.Model):
    """Execution Model"""
    id = db.Column(db.GUID(), default=uuid.uuid4, primary_key=True, autoincrement=False)
    start_date = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    end_date = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    status = db.Column(db.String(10), default='PENDING')
    progress = db.Column(db.Integer(), default=0)
    params = db.Column(JSONB, default={})
    results = db.Column(JSONB, default={})
    logs = db.relationship('ExecutionLog',
                           backref=db.backref('execution'),
                           cascade='all, delete-orphan',
                           lazy='dynamic')
    script_id = db.Column(db.GUID(), db.ForeignKey('script.id'))
    user_id = db.Column(db.GUID(), db.ForeignKey('user.id'))

    def __init__(self, script_id, params, user_id):
        self.script_id = script_id
        self.params = params
        self.user_id = user_id

    def __repr__(self):
        return '<Execution %r>' % self.id

    def serialize(self, include=None, exclude=None):
        """Return object data in easily serializeable format"""
        include = include if include else []
        exclude = exclude if exclude else []
        end_date_formatted = None
        if self.end_date:
            end_date_formatted = self.end_date.isoformat()
        execution = {
            'id': self.id,
            'script_id': self.script_id,
            'user_id': self.user_id,
            'start_date': self.start_date.isoformat(),
            'end_date': end_date_formatted,
            'status': self.status,
            'progress': self.progress,
            'params': self.params,
            'results': self.results,
        }
        if 'logs' in include:
            execution['logs'] = self.serialize_logs
        if 'user' in include:
            execution['user'] = self.user.serialize()
        if 'script' in include:
            execution['script'] = self.script.serialize()
        if 'params' in exclude:
            del execution['params']
        if 'results' in exclude:
            del execution['results']
        return execution

    @property
    def serialize_logs(self):
        """Serialize Logs"""
        return [item.serialize() for item in self.logs]