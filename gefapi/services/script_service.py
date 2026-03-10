"""SCRIPT SERVICE"""

import datetime
import json
import logging
import os
import tarfile
from uuid import UUID

import rollbar
from slugify import slugify
from sqlalchemy import String, cast
from werkzeug.utils import secure_filename

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import InvalidFile, NotAllowed, ScriptDuplicated, ScriptNotFound
from gefapi.models import Script, ScriptLog, User
from gefapi.s3 import push_script_to_s3
from gefapi.services import docker_build
from gefapi.utils.permissions import is_admin_or_higher

# Security: Explicitly allowed fields for filter and sort operations
# to prevent unauthorized access to sensitive model fields
SCRIPT_ALLOWED_FILTER_FIELDS = {
    "id",
    "name",
    "slug",
    "status",
    "public",
    "restricted",
    "created_at",
    "updated_at",
    "environment",
    "environment_version",
}
# Fields that require admin privileges to filter/sort
SCRIPT_ADMIN_ONLY_FIELDS = {"user_name", "user_email"}
SCRIPT_ALLOWED_SORT_FIELDS = SCRIPT_ALLOWED_FILTER_FIELDS | SCRIPT_ADMIN_ONLY_FIELDS

ROLES = SETTINGS.get("ROLES")

logger = logging.getLogger()


def allowed_file(filename):
    if len(filename.rsplit(".")) > 2:
        return filename.rsplit(".")[1] + "." + filename.rsplit(".")[
            2
        ].lower() in SETTINGS.get("ALLOWED_EXTENSIONS")
    return "." in filename and filename.rsplit(".", 1)[1].lower() in SETTINGS.get(
        "ALLOWED_EXTENSIONS"
    )


class ScriptService:
    """Script Class"""

    @staticmethod
    def create_script(sent_file, user, script=None):
        logger.info("[SERVICE]: Creating script")
        if sent_file and allowed_file(sent_file.filename):
            logger.info("[SERVICE]: Allowed format")
            filename = secure_filename(sent_file.filename)
            sent_file_path = os.path.join(SETTINGS.get("UPLOAD_FOLDER"), filename)
            logger.info("[SERVICE]: Saving file")
            try:
                if not os.path.exists(SETTINGS.get("UPLOAD_FOLDER")):
                    os.makedirs(SETTINGS.get("UPLOAD_FOLDER"))
                sent_file.save(sent_file_path)
            except Exception as e:
                logger.error(e)
                rollbar.report_exc_info()
                raise e
            logger.info("[SERVICE]: File saved")
        else:
            raise InvalidFile(message="Invalid File")

        try:
            with tarfile.open(name=sent_file_path, mode="r:gz") as tar:
                if "configuration.json" not in tar.getnames():
                    raise InvalidFile(message="Invalid File")
                config_file = tar.extractfile(member="configuration.json")
                logger.info("[SERVICE]: Config file extracted")
                config_content = config_file.read()
                logger.info("[SERVICE]: Config file opened")
                config = json.loads(config_content)
                script_name = config.get("name", None)
                cpu_reservation = config.get("cpu_reservation", None)
                cpu_limit = config.get("cpu_limit", None)
                memory_limit = config.get("memory_limit", None)
                memory_reservation = config.get("memory_reservation", None)
                environment = config.get("environment", None)
                environment_version = config.get("environment_version", None)
                compute_type = config.get("compute_type", None)
                batch_job_definition = config.get("batch_job_definition", None)
                batch_job_queue = config.get("batch_job_queue", None)
                batch_image = config.get("batch_image", None)
        except Exception as error:
            rollbar.report_exc_info()
            raise error

        if script is None:
            # Creating new entity
            name = script_name
            if not name:
                raise InvalidFile(message="Script configuration must include a 'name'")
            logger.info("[SERVICE]: Creating slug for script name: " + name)
            slug = slugify(name)
            if not slug:
                raise InvalidFile(message="Cannot generate valid slug from script name")
            current_script = Script.query.filter_by(slug=slug).first()
            if current_script:
                raise ScriptDuplicated(
                    message="Script with name "
                    + name
                    + " generates an existing script slug"
                )
            script = Script(
                name=name,
                slug=slug,
                user_id=user.id,
                cpu_reservation=cpu_reservation,
                cpu_limit=cpu_limit,
                memory_reservation=memory_reservation,
                memory_limit=memory_limit,
                environment=environment,
                environment_version=environment_version,
                compute_type=compute_type,
                batch_job_definition=batch_job_definition,
                batch_job_queue=batch_job_queue,
                batch_image=batch_image,
            )
        else:
            # Updating existing entity
            logger.debug(script_name)
            script.name = script_name
            script.updated_at = datetime.datetime.utcnow()
            if cpu_reservation:
                script.cpu_reservation = cpu_reservation
            if cpu_limit:
                script.cpu_limit = cpu_limit
            if memory_reservation:
                script.memory_reservation = memory_reservation
            if memory_limit:
                script.memory_limit = memory_limit
            if environment:
                script.environment = environment
            if environment_version:
                script.environment_version = environment_version
            if compute_type:
                script.compute_type = compute_type
            if batch_job_definition is not None:
                script.batch_job_definition = batch_job_definition
            if batch_job_queue is not None:
                script.batch_job_queue = batch_job_queue
            if batch_image is not None:
                script.batch_image = batch_image
        # TO DB
        try:
            logger.info("[DB]: ADD")
            db.session.add(script)
            # Commit first to get the script ID
            db.session.commit()

            try:
                logger.debug(f"Script slug: {script.slug}")
                logger.debug(f"Script name: {script.name}")
                logger.debug(f"Script id: {script.id}")
                if not script.slug:
                    raise InvalidFile(message="Script slug is missing")
                if not script.id:
                    raise InvalidFile(message="Script id is missing")
                push_script_to_s3(sent_file_path, script.slug + ".tar.gz")
                docker_build.delay(script.id)
            except Exception as e:
                logger.error("Exception type: %s", type(e).__name__)
                logger.error("Exception message: %s", str(e))
                rollbar.report_exc_info()
                script.status = "FAILED"
                db.session.commit()
                raise e

        except Exception as error:
            logger.error(error)
            rollbar.report_exc_info()
            raise error

        return script

    @staticmethod
    def get_scripts(
        user,
        filter_param=None,
        sort=None,
        page=1,
        per_page=2000,
        paginate=False,
    ):
        logger.info("[SERVICE]: Getting scripts")
        logger.info("[DB]: QUERY")

        if paginate:
            if page < 1:
                raise Exception("Page must be greater than 0")
            if per_page < 1:
                raise Exception("Per page must be greater than 0")

        query = db.session.query(Script)

        # User access control
        if is_admin_or_higher(user):
            # Admins can see all scripts
            pass
        else:
            # Regular users can see:
            # 1. Their own scripts
            # 2. Public scripts
            # 3. Scripts they have explicit access to (restricted scripts with
            #    role/user access)
            from sqlalchemy import and_, or_

            # Build access conditions
            access_conditions = [
                Script.user_id == user.id,  # Own scripts
                # Public non-restricted scripts
                and_(Script.public, ~Script.restricted),  # type: ignore
            ]

            # Add restricted script access based on role
            # Use parameterized queries to prevent SQL injection
            if user.role in ["USER", "ADMIN", "SUPERADMIN"]:
                role_pattern = f'%"{user.role}"%'
                access_conditions.append(
                    and_(
                        Script.restricted,
                        cast(Script.allowed_roles, String).like(role_pattern),
                    )
                )

            # Add restricted script access based on user ID
            # Use parameterized queries to prevent SQL injection
            user_id_pattern = f'%"{user.id}"%'
            access_conditions.append(
                and_(
                    Script.restricted,
                    cast(Script.allowed_users, String).like(user_id_pattern),
                )
            )

            query = query.filter(or_(*access_conditions))

        # SQL-style filter_param (supports OR groups)
        join_users = False
        filter_clauses = []
        if filter_param:
            from sqlalchemy import and_

            from gefapi.utils.query_filters import parse_filter_param

            allowed_fields = SCRIPT_ALLOWED_FILTER_FIELDS | SCRIPT_ADMIN_ONLY_FIELDS

            def _resolve_script_filter_column(field_name):
                nonlocal join_users
                if field_name == "user_name":
                    if not is_admin_or_higher(user):
                        raise Exception("Only admin users can filter by user_name")
                    join_users = True
                    return User.name
                if field_name == "user_email":
                    if not is_admin_or_higher(user):
                        raise Exception("Only admin users can filter by user_email")
                    join_users = True
                    return User.email
                return getattr(Script, field_name, None)

            filter_clauses = parse_filter_param(
                filter_param,
                allowed_fields=allowed_fields,
                resolve_column=_resolve_script_filter_column,
                string_field_names={"user_name", "user_email"},
            )

        # SQL-style sorting
        order_clauses = []
        if sort:
            from gefapi.utils.query_filters import parse_sort_param

            def _resolve_script_sort_column(field_name, direction):
                nonlocal join_users
                col = getattr(Script, field_name, None)
                if col is not None:
                    return col
                if field_name == "user_email":
                    if not is_admin_or_higher(user):
                        raise Exception("Only admin users can sort by user_email")
                    join_users = True
                    return User.email
                if field_name == "user_name":
                    if not is_admin_or_higher(user):
                        raise Exception("Only admin users can sort by user_name")
                    join_users = True
                    return User.name
                return None

            order_clauses = parse_sort_param(
                sort,
                allowed_fields=SCRIPT_ALLOWED_SORT_FIELDS,
                resolve_column=_resolve_script_sort_column,
            )

        # Apply JOINs once (after both filter and sort have been processed)
        # to avoid duplicate joins when both reference the same table.
        if join_users:
            query = query.join(User, Script.user_id == User.id)

        # Apply filter clauses
        if filter_clauses:
            from sqlalchemy import and_

            query = query.filter(and_(*filter_clauses))

        # Apply sort clauses
        if order_clauses:
            for clause in order_clauses:
                query = query.order_by(clause)
        else:
            query = query.order_by(Script.created_at.desc())

        if paginate:
            total = query.count()
            scripts = query.offset((page - 1) * per_page).limit(per_page).all()
        else:
            scripts = query.all()
            total = len(scripts)

        return scripts, total

    @staticmethod
    def get_script(script_id, user="fromservice"):
        logger.info(f"[SERVICE]: Getting script: {script_id}")
        logger.info("[DB]: QUERY")
        if user == "fromservice" or is_admin_or_higher(user):
            logger.info(
                f"[SERVICE]: trying to get script {script_id} for service or admin"
            )
            try:
                # If script_id is already a UUID object, use it directly
                if isinstance(script_id, UUID):
                    script = Script.query.filter_by(id=script_id).first()
                else:
                    UUID(script_id, version=4)
                    script = Script.query.filter_by(id=script_id).first()
            except ValueError:
                logger.info("[SERVICE]: valueerror")
                script = Script.query.filter_by(slug=script_id).first()
            except Exception as error:
                rollbar.report_exc_info()
                raise error
        else:
            try:
                logger.info(f"[SERVICE]: trying to get script {script_id}")
                # If script_id is already a UUID object, use it directly
                if isinstance(script_id, UUID):
                    script = Script.query.filter_by(id=script_id).first()
                else:
                    UUID(script_id, version=4)
                    script = Script.query.filter_by(id=script_id).first()
            except ValueError:
                logger.info("[SERVICE]: valueerror")
                script = Script.query.filter_by(slug=script_id).first()
            except Exception as error:
                rollbar.report_exc_info()
                raise error

            # Check access permissions after retrieving the script
            if script and not script.can_access(user):
                script = None
        if not script:
            raise ScriptNotFound(
                message="Script with id " + script_id + " does not exist"
            )
        return script

    @staticmethod
    def get_script_logs(script_id, start_date, last_id):
        logger.info(f"[SERVICE]: Getting script logs of script {script_id}: ")
        logger.info("[DB]: QUERY")
        try:
            # If script_id is already a UUID object, use it directly
            if isinstance(script_id, UUID):
                script = Script.query.filter_by(id=script_id).first()
            else:
                UUID(script_id, version=4)
                script = Script.query.filter_by(id=script_id).first()
        except ValueError:
            script = Script.query.filter_by(slug=script_id).first()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        if not script:
            raise ScriptNotFound(message=f"Script with id {script_id} does not exist")

        if start_date:
            logger.debug(start_date)
            return (
                ScriptLog.query.filter(
                    ScriptLog.script_id == script.id,
                    ScriptLog.register_date > start_date,
                )
                .order_by(ScriptLog.register_date)
                .all()
            )
        if last_id:
            return (
                ScriptLog.query.filter(
                    ScriptLog.script_id == script.id, ScriptLog.id > last_id
                )
                .order_by(ScriptLog.register_date)
                .all()
            )
        return script.logs

    @staticmethod
    def update_script(script_id, sent_file, user):
        logger.info("[SERVICE]: Updating script")
        script = ScriptService.get_script(script_id, user)
        if not script:
            raise ScriptNotFound(
                message="Script with id " + script_id + " does not exist"
            )
        if is_admin_or_higher(user) or user.id == script.user_id:
            return ScriptService.create_script(sent_file, user, script)
        raise NotAllowed(message="Operation not allowed to this user")

    @staticmethod
    def delete_script(script_id, user):
        logger.info(f"[SERVICE]: Deleting script {script_id}")
        try:
            script = ScriptService.get_script(script_id, user)
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        if not script:
            raise ScriptNotFound(
                message="Script with id " + script_id + " does not exist"
            )

        try:
            logger.info("[DB]: DELETE")
            db.session.delete(script)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return script

    @staticmethod
    def publish_script(script_id, user):
        logger.info(f"[SERVICE]: Publishing script: {script_id}")
        if is_admin_or_higher(user):
            try:
                # If script_id is already a UUID object, use it directly
                if isinstance(script_id, UUID):
                    script = Script.query.filter_by(id=script_id).first()
                else:
                    UUID(script_id, version=4)
                    script = Script.query.filter_by(id=script_id).first()
            except ValueError:
                script = Script.query.filter_by(slug=script_id).first()
            except Exception as error:
                rollbar.report_exc_info()
                raise error
        else:
            try:
                script = (
                    db.session.query(Script)
                    .filter(Script.id == script_id)
                    .filter(Script.user_id == user.id)
                    .first()
                )
            except ValueError:
                script = (
                    db.session.query(Script)
                    .filter(Script.slug == script_id)
                    .filter(Script.user_id == user.id)
                    .first()
                )
            except Exception as error:
                rollbar.report_exc_info()
                raise error
        if not script:
            raise ScriptNotFound(
                message="Script with id " + script_id + " does not exist"
            )
        script.public = True
        try:
            logger.info("[DB]: SAVE")
            db.session.add(script)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return script

    @staticmethod
    def unpublish_script(script_id, user):
        logger.info(f"[SERVICE]: Unpublishing script: {script_id}")
        if is_admin_or_higher(user):
            try:
                # If script_id is already a UUID object, use it directly
                if isinstance(script_id, UUID):
                    script = Script.query.filter_by(id=script_id).first()
                else:
                    UUID(script_id, version=4)
                    script = Script.query.filter_by(id=script_id).first()
            except ValueError:
                script = Script.query.filter_by(slug=script_id).first()
            except Exception as error:
                rollbar.report_exc_info()
                raise error
        else:
            try:
                script = (
                    db.session.query(Script)
                    .filter(Script.id == script_id)
                    .filter(Script.user_id == user.id)
                    .first()
                )
            except ValueError:
                script = (
                    db.session.query(Script)
                    .filter(Script.slug == script_id)
                    .filter(Script.user_id == user.id)
                    .first()
                )
        if not script:
            raise ScriptNotFound(
                message="Script with id " + script_id + " does not exist"
            )
        script.public = False
        try:
            logger.info("[DB]: SAVE")
            db.session.add(script)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return script
