"""USER MODEL"""

from __future__ import absolute_import, division, print_function

import datetime
import uuid

from werkzeug.security import check_password_hash, generate_password_hash

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


class User(db.Model):
    """User Model"""

    id = db.Column(db.GUID(), default=uuid.uuid4, primary_key=True, autoincrement=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    country = db.Column(db.String(120))
    institution = db.Column(db.String(120))
    password = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    role = db.Column(db.String(10))
    scripts = db.relationship(
        "Script",
        backref=db.backref("user"),
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    executions = db.relationship(
        "Execution",
        backref=db.backref("user"),
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def __init__(self, email, password, name, country, institution, role="USER"):
        self.email = email
        self.password = self.set_password(password=password)
        self.role = role
        self.name = name
        self.country = country
        self.institution = institution

    def __repr__(self):
        return "<User %r>" % self.email

    def serialize(self, include=None):
        """Return object data in easily serializeable format"""
        include = include if include else []
        user = {
            "id": self.id,
            "email": self.email,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "role": self.role,
            "name": self.name,
            "country": self.country,
            "institution": self.institution,
        }
        if "scripts" in include:
            user["scripts"] = self.serialize_scripts
        return user

    @property
    def serialize_scripts(self):
        """Serialize Scripts"""
        return [item.serialize() for item in self.scripts]

    def set_password(self, password):
        return generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)
