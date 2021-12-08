"""USER MODEL"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime
import uuid

from werkzeug.security import generate_password_hash, \
    check_password_hash

from gefapi.models import GUID
from gefapi.models.model import db
db.GUID = GUID


class User(db.Model):
    """User Model"""
    id = db.Column(db.GUID(), default=uuid.uuid4,
                   primary_key=True, autoincrement=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    first_name = db.Column(db.String(120), nullable=True, default="")
    last_name = db.Column(db.String(120), nullable=True, default="")
    name = db.Column(db.String(120), nullable=False)
    country = db.Column(db.String(120))
    region = db.Column(db.String(120), default="")
    institution = db.Column(db.String(120))
    password = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    role = db.Column(db.String(10))
    is_plugin_user = db.Column(db.Boolean(), default=True)
    is_in_mailing_list = db.Column(db.Boolean(), default=False)
    scripts = db.relationship('Script',
                              backref=db.backref('user'),
                              cascade='all, delete-orphan',
                              lazy='dynamic')
    executions = db.relationship('Execution',
                                 backref=db.backref('user'),
                                 cascade='all, delete-orphan',
                                 lazy='dynamic')
    deleted = db.Column(db.Boolean(), default=False)

    def __init__(self, email, password, name, country, institution, role='USER', first_name="", last_name="",
                 is_plugin_user=True, is_in_mailing_list=False):
        self.email = email
        self.password = self.set_password(password=password)
        self.role = role
        self.name = name
        self.country = country
        self.institution = institution
        self.first_name = first_name
        self.last_name = last_name
        self.is_plugin_user = is_plugin_user
        self.is_in_mailing_list = is_in_mailing_list

    def __repr__(self):
        return '<User %r>' % self.email

    def serialize(self, include=None):
        """Return object data in easily serializeable format"""
        include = include if include else []
        user = {
            'id': self.id,
            'email': self.email,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'role': self.role,
            'name': self.name,
            'first_name': self.first_name,
            'last_name': self.first_name,
            'country': self.country,
            'institution': self.institution,
            'is_plugin_user': self.is_plugin_user,
            'is_in_mailing_list': self.is_in_mailing_list
        }
        if 'scripts' in include:
            user['scripts'] = self.serialize_scripts
        return user

    @property
    def serialize_scripts(self):
        """Serialize Scripts"""
        return [item.serialize() for item in self.scripts]

    def set_password(self, password):
        return generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)
