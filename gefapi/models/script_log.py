"""SCRIPT LOG MODEL"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime

from gefapi.models import GUID
from gefapi import db

db.GUID = GUID


class ScriptLog(db.Model):
    """ScriptLog Model"""

    __tablename__ = "script_log"
    id = db.Column(db.Integer(), primary_key=True)
    text = db.Column(db.Text())
    register_date = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    script_id = db.Column(db.GUID(), db.ForeignKey("script.id"))

    def __init__(self, text, script_id):
        self.text = text
        self.script_id = script_id

    def __repr__(self):
        return "<ScriptLog %r>" % self.id

    def serialize(self):
        """Return object data in easily serializeable format"""
        return {
            "id": self.id,
            "text": self.text,
            "register_date": self.register_date.isoformat(),
            "script_id": self.script_id,
        }
