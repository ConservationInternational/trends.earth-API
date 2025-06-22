"""SCRIPT SERVICE"""

import datetime
import json
import logging
import os
import tarfile
from uuid import UUID

import rollbar
from slugify import slugify
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import InvalidFile, NotAllowed, ScriptDuplicated, ScriptNotFound
from gefapi.models import Script, ScriptLog
from gefapi.s3 import push_script_to_s3
from gefapi.services import docker_build

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
        except Exception as error:
            rollbar.report_exc_info()
            raise error

        if script is None:
            # Creating new entity
            name = script_name
            slug = slugify(script_name)
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
        # TO DB
        try:
            logger.info("[DB]: ADD")
            db.session.add(script)
            try:
                push_script_to_s3(sent_file_path, script.slug + ".tar.gz")
            except Exception:
                rollbar.report_exc_info()
                logger.error(f"Error pushing {script.slug} to S3")
            db.session.commit()

            _ = docker_build.delay(script.id)
        except Exception as error:
            logger.error(error)
            rollbar.report_exc_info()

        return script

    @staticmethod
    def get_scripts(user):
        logger.info("[SERVICE]: Getting scripts")
        logger.info("[DB]: QUERY")
        if user.role == "ADMIN":
            scripts = Script.query.all()
            return scripts
        scripts = db.session.query(Script).filter(
            or_(Script.user_id == user.id, Script.public)
        )
        return scripts

    @staticmethod
    def get_script(script_id, user="fromservice"):
        logger.info("[SERVICE]: Getting script: " + script_id)
        logger.info("[DB]: QUERY")
        if user == "fromservice" or user.role == "ADMIN":
            logger.info(
                f"[SERVICE]: trying to get script {script_id} for service or admin"
            )
            try:
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
                UUID(script_id, version=4)
                script = (
                    db.session.query(Script)
                    .filter(Script.id == script_id)
                    .filter(or_(Script.user_id == user.id, Script.public))
                    .first()
                )
            except ValueError:
                logger.info("[SERVICE]: valueerror")
                script = (
                    db.session.query(Script)
                    .filter(Script.slug == script_id)
                    .filter(or_(Script.user_id == user.id, Script.public))
                    .first()
                )
            except Exception as error:
                rollbar.report_exc_info()
                raise error
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
            UUID(script_id, version=4)
            script = Script.query.filter_by(id=script_id).first()
        except ValueError:
            script = Script.query.filter_by(slug=script_id).first()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        if not script:
            raise ScriptNotFound(
                message="Script with id " + script_id + " does not exist"
            )

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
        if (
            user.role == "ADMIN"
            or user.email == "gef@gef.com"
            or user.id == script.user_id
        ):
            return ScriptService.create_script(sent_file, user, script)
        raise NotAllowed(message="Operation not allowed to this user")

    @staticmethod
    def delete_script(script_id, user):
        logger.info("[SERVICE]: Deleting script" + script_id)
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
        logger.info("[SERVICE]: Publishing script: " + script_id)
        if user.role == "ADMIN":
            try:
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
        logger.info("[SERVICE]: Unpublishing script: " + script_id)
        if user.role == "ADMIN":
            try:
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
