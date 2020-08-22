"""SCRIPT SERVICE"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import random
import datetime
import string
import logging
from uuid import UUID

from gefapi import db
from gefapi.models import User
from gefapi.errors import UserNotFound, UserDuplicated, AuthError, EmailError
from gefapi.services import EmailService
from gefapi.config import SETTINGS

ROLES = SETTINGS.get('ROLES')


class UserService(object):
    """User Class"""

    @staticmethod
    def create_user(user):
        logging.info('[SERVICE]: Creating user')
        email = user.get('email', None)
        password = user.get('password', None)
        
        password = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6)) if password is None else password
        logging.info(password)
        role = user.get('role', 'USER')
        name = user.get('name', 'notset')
        country = user.get('country', None)
        institution = user.get('institution', None)
        if role not in ROLES:
            role = 'USER'
        if email is None or password is None:
            raise Exception
        current_user = User.query.filter_by(email=user.get('email')).first()
        if current_user:
            raise UserDuplicated(message='User with email '+email+' already exists')
        user = User(email=email, password=password, role=role, name=name, country=country, institution=institution)
        
        try:
            logging.info('[DB]: ADD')
            db.session.add(user)
            db.session.commit()
            try:
                email = EmailService.send_html_email(
                    recipients=[user.email],
                    html='<p>User: ' + user.email + '</p><p>Password: ' + password + '</p>',
                    subject='[trends.earth] User created'
                )
                print(email)
                
            except EmailError as error:
                raise error
        except Exception as error:
            raise error
        return user

    @staticmethod
    def get_users():
        logging.info('[SERVICE]: Getting users')
        logging.info('[DB]: QUERY')
        users = User.query.all()
        return users

    @staticmethod
    def get_user(user_id):
        logging.info('[SERVICE]: Getting user '+user_id)
        logging.info('[DB]: QUERY')
        try:
            val = UUID(user_id, version=4)
            user = User.query.get(user_id)
        except ValueError:
            user = User.query.filter_by(email=user_id).first()
        except Exception as error:
            raise error
        if not user:
            raise UserNotFound(message='User with id '+user_id+' does not exist')
        return user

    @staticmethod
    def recover_password(user_id):
        logging.info('[SERVICE]: Recovering password'+user_id)
        logging.info('[DB]: QUERY')
        user = UserService.get_user(user_id=user_id)
        if not user:
            raise UserNotFound(message='User with id '+user_id+' does not exist')
        password = ''.join(random.choices(string.ascii_uppercase + string.digits, k=20))
        logging.info(password)
        user.password = user.set_password(password=password)
        try:
            logging.info('[DB]: ADD')
            db.session.add(user)
            db.session.commit()
            try:
                email = EmailService.send_html_email(
                    recipients=[user.email],
                    html='<p>User: ' + user.email + '</p><p>Password: ' + password + '</p>',
                    subject='[trends.earth] Recover password'
                )
            except EmailError as error:
                raise error
        except Exception as error:
            raise error
        return user

    @staticmethod
    def update_profile_password(user, current_user):
        logging.info('[SERVICE]: Updating user password')
        password = user.get('password')
        current_user.password = current_user.set_password(password=password)
        try:
            logging.info('[DB]: ADD')
            db.session.add(current_user)
            db.session.commit()
        except Exception as error:
            raise error
        return current_user

    @staticmethod
    def update_user(user, user_id):
        logging.info('[SERVICE]: Updating user')
        current_user = UserService.get_user(user_id=user_id)
        if not current_user:
            raise UserNotFound(message='User with id '+user_id+' does not exist')
        if 'role' in user:
            role = user.get('role') if user.get('role') in ROLES else current_user.role
            current_user.role = role
        current_user.name = user.get('name', current_user.name)
        current_user.country = user.get('country', current_user.country)
        current_user.institution = user.get('institution', current_user.institution)
        current_user.updated_at = ''
        try:
            logging.info('[DB]: ADD')
            db.session.add(current_user)
            db.session.commit()
        except Exception as error:
            raise error
        return current_user

    @staticmethod
    def delete_user(user_id):
        logging.info('[SERVICE]: Deleting user'+user_id)
        user = UserService.get_user(user_id=user_id)
        if not user:
            raise UserNotFound(message='User with email '+user_id+' does not exist')
        try:
            logging.info('[DB]: DELETE')
            db.session.delete(user)
            db.session.commit()
        except Exception as error:
            raise error
        return user

    @staticmethod
    def authenticate_user(user_id, password):
        logging.info('[SERVICE]: Authenticate user '+user_id)
        user = UserService.get_user(user_id=user_id)
        if not user:
            raise UserNotFound(message='User with email '+user_id+' does not exist')
        if not user.check_password(password):
            raise AuthError(message='User or password not valid')
        #  to serialize id with jwt
        user.id = user.id.hex
        return user
