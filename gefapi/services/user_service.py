"""SCRIPT SERVICE"""

import datetime
import logging
import random
import string
from uuid import UUID

import rollbar

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import AuthError, EmailError, UserDuplicated, UserNotFound
from gefapi.models import User
from gefapi.services import EmailService

ROLES = SETTINGS.get("ROLES")


logger = logging.getLogger()


class UserService:
    """User Class"""

    @staticmethod
    def create_user(user):
        logger.info("[SERVICE]: Creating user")
        email = user.get("email", None)
        password = user.get("password", None)
        password = (
            "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if password is None
            else password
        )
        role = user.get("role", "USER")
        name = user.get("name", "notset")
        country = user.get("country", None)
        institution = user.get("institution", None)
        if role not in ROLES:
            role = "USER"
        if email is None or password is None:
            raise Exception
        current_user = User.query.filter_by(email=user.get("email")).first()
        if current_user:
            raise UserDuplicated(message="User with email " + email + " already exists")
        user = User(
            email=email,
            password=password,
            role=role,
            name=name,
            country=country,
            institution=institution,
        )
        try:
            logger.info("[DB]: ADD")
            db.session.add(user)
            db.session.commit()
            try:
                email = EmailService.send_html_email(
                    recipients=[user.email],
                    html="<p>User: "
                    + user.email
                    + "</p><p>Password: "
                    + password
                    + "</p>",
                    subject="[trends.earth] User created",
                )
            except EmailError as error:
                rollbar.report_exc_info()
                raise error
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return user

    @staticmethod
    def get_users(
        filter_param=None,
        sort=None,
        page=1,
        per_page=2000,
        paginate=False,
    ):
        logger.info("[SERVICE]: Getting users")
        logger.info("[DB]: QUERY")

        if paginate:
            if page < 1:
                raise Exception("Page must be greater than 0")
            if per_page < 1:
                raise Exception("Per page must be greater than 0")

        query = db.session.query(User)

        # SQL-style filter_param
        if filter_param:
            import re

            from sqlalchemy import and_

            filter_clauses = []
            for expr in filter_param.split(","):
                expr = expr.strip()
                m = re.match(
                    r"(\w+)\s*(=|!=|>=|<=|>|<| like )\s*(.+)", expr, re.IGNORECASE
                )
                if m:
                    field, op, value = m.groups()
                    field = field.strip()
                    op = op.strip().lower()
                    value = value.strip().strip("'\"")
                    col = getattr(User, field, None)
                    if col is not None:
                        if op == "=":
                            filter_clauses.append(col == value)
                        elif op == "!=":
                            filter_clauses.append(col != value)
                        elif op == ">":
                            filter_clauses.append(col > value)
                        elif op == "<":
                            filter_clauses.append(col < value)
                        elif op == ">=":
                            filter_clauses.append(col >= value)
                        elif op == "<=":
                            filter_clauses.append(col <= value)
                        elif op == "like":
                            filter_clauses.append(col.like(value))
            if filter_clauses:
                query = query.filter(and_(*filter_clauses))

        # SQL-style sorting
        if sort:
            from sqlalchemy import asc, desc

            for sort_expr in sort.split(","):
                sort_expr = sort_expr.strip()
                if not sort_expr:
                    continue
                parts = sort_expr.split()
                field = parts[0]
                direction = parts[1].lower() if len(parts) > 1 else "asc"
                col = getattr(User, field, None)
                if col is not None:
                    if direction == "desc":
                        query = query.order_by(desc(col))
                    else:
                        query = query.order_by(asc(col))
        else:
            query = query.order_by(User.created_at.desc())

        if paginate:
            total = query.count()
            users = query.offset((page - 1) * per_page).limit(per_page).all()
        else:
            users = query.all()
            total = len(users)

        return users, total

    @staticmethod
    def get_user(user_id):
        logger.info(f"[SERVICE]: Getting user {user_id}")
        logger.info("[DB]: QUERY")
        try:
            # If user_id is already a UUID object, use it directly
            if isinstance(user_id, UUID):
                user = User.query.get(user_id)
            else:
                UUID(user_id, version=4)
                user = User.query.get(user_id)
        except ValueError:
            user = User.query.filter_by(email=user_id).first()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        if not user:
            raise UserNotFound(message=f"User with id {user_id} does not exist")
        return user

    @staticmethod
    def change_password(user, old_password, new_password):
        """Change user password"""
        logger.info(f"[SERVICE]: Changing password for user {user.email}")
        if not user.check_password(old_password):
            raise AuthError("Invalid current password")
        user.password = user.set_password(new_password)
        try:
            db.session.add(user)
            db.session.commit()
            logger.info(
                f"[SERVICE]: Password for user {user.email} changed successfully"
            )
        except Exception as e:
            db.session.rollback()
            logger.error(f"[SERVICE]: Error changing password for {user.email}: {e}")
            rollbar.report_exc_info()
            raise
        return user

    @staticmethod
    def admin_change_password(user, new_password):
        """Admin change user password (no old password verification required)"""
        logger.info(f"[SERVICE]: Admin changing password for user {user.email}")
        user.password = user.set_password(new_password)
        try:
            db.session.add(user)
            db.session.commit()
            logger.info(
                f"[SERVICE]: Password for user {user.email} changed successfully "
                f"by admin"
            )
        except Exception as e:
            db.session.rollback()
            logger.error(f"[SERVICE]: Error changing password for {user.email}: {e}")
            rollbar.report_exc_info()
            raise
        return user

    @staticmethod
    def recover_password(user_id):
        logger.info("[SERVICE]: Recovering password" + user_id)
        logger.info("[DB]: QUERY")
        user = UserService.get_user(user_id=user_id)
        if not user:
            raise UserNotFound(message="User with id " + user_id + " does not exist")
        password = "".join(random.choices(string.ascii_uppercase + string.digits, k=20))
        user.password = user.set_password(password=password)
        try:
            logger.info("[DB]: ADD")
            db.session.add(user)
            db.session.commit()
            try:
                EmailService.send_html_email(
                    recipients=[user.email],
                    html="<p>User: "
                    + user.email
                    + "</p><p>Password: "
                    + password
                    + "</p>",
                    subject="[trends.earth] Recover password",
                )
            except EmailError as error:
                rollbar.report_exc_info()
                raise error
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return user

    @staticmethod
    def update_profile_password(user, current_user):
        logger.info("[SERVICE]: Updating user password")
        password = user.get("password")
        current_user.password = current_user.set_password(password=password)
        try:
            logger.info("[DB]: ADD")
            db.session.add(current_user)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return current_user

    @staticmethod
    def update_user(user, user_id):
        logger.info("[SERVICE]: Updating user")
        current_user = UserService.get_user(user_id=user_id)
        if not current_user:
            raise UserNotFound(message="User with id " + user_id + " does not exist")
        if "role" in user:
            role = user.get("role") if user.get("role") in ROLES else current_user.role
            current_user.role = role
        current_user.name = user.get("name", current_user.name)
        current_user.country = user.get("country", current_user.country)
        current_user.institution = user.get("institution", current_user.institution)
        current_user.updated_at = datetime.datetime.utcnow()
        try:
            logger.info("[DB]: ADD")
            db.session.add(current_user)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return current_user

    @staticmethod
    def delete_user(user_id):
        logger.info("[SERVICE]: Deleting user " + str(user_id))
        user = UserService.get_user(user_id=user_id)
        if not user:
            raise UserNotFound(
                message="User with ID " + str(user_id) + " does not exist"
            )
        try:
            logger.info("[DB]: DELETE")
            db.session.delete(user)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return user

    @staticmethod
    def authenticate_user(email, password):
        logger.info("[SERVICE]: Authenticate user " + email)
        user = User.query.filter_by(email=email).first()
        if not user:
            logger.debug(f"[SERVICE]: User with email {email} not found")
            return None
        if not user.check_password(password):
            logger.debug(f"[SERVICE]: Invalid password for user {email}")
            return None
        #  to serialize id with jwt
        user.id = user.id.hex
        return user
