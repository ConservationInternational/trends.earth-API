"""GEFAPI VALIDATORS"""

import logging
import re

from gefapi.routes.api.v1 import error
from gefapi.config import SETTINGS

from functools import wraps
from flask import request, jsonify


ROLES = SETTINGS.get('ROLES')
EMAIL_REGEX = re.compile(r'^[A-Za-z0-9\.\+_-]+@[A-Za-z0-9\._-]+\.[a-zA-Z]*$')


def validate_user_creation(func):
    """User Creation Validation"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()
        if 'email' not in json_data:
            return error(status=400, detail='Email is required')
        else:
            if not EMAIL_REGEX.match(json_data.get('email')):
                return error(status=400, detail='Email not valid')
        if 'name' not in json_data:
            return error(status=400, detail='Name is required')
        if 'role' in json_data:
            role = json_data.get('role')
            if role not in ROLES:
                return error(status=400, detail='role not valid')
        return func(*args, **kwargs)
    return wrapper


def validate_user_update(func):
    """User Update Validation"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()
        if 'role' in json_data:
            role = json_data.get('role')
            if role not in ROLES:
                return error(status=400, detail='role not valid')
        return func(*args, **kwargs)
    return wrapper


def validate_profile_update(func):
    """User Update Validation"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()
        if 'password' not in json_data or 'repeatPassword' not in json_data:
            return error(status=400, detail='not updated')
        password = json_data.get('password')
        repeat_password = json_data.get('repeatPassword')
        if password != repeat_password:
            return error(status=400, detail='not updated')
        return func(*args, **kwargs)
    return wrapper


def validate_file(func):
    """Script File Validation"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if 'file' not in request.files:
            return error(status=400, detail='File Required')
        if request.files.get('file', None) is None:
            return error(status=400, detail='File Required')
        return func(*args, **kwargs)
    return wrapper


def validate_execution_update(func):
    """Execution Update Validation"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()
        if 'status' not in json_data and 'progress' not in json_data and 'results' not in json_data:
            return error(status=400, detail='Status, progress or results are required')
        return func(*args, **kwargs)
    return wrapper


def validate_execution_log_creation(func):
    """Execution Log Creation Validation"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()
        if 'text' not in json_data or 'level' not in json_data:
            return error(status=400, detail='Text and level are required')
        return func(*args, **kwargs)
    return wrapper
