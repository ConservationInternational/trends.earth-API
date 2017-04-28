"""SCRIPT SERVICE"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import logging
import tarfile
import json
import shutil
from uuid import UUID

from werkzeug.utils import secure_filename
from slugify import slugify

from gefapi.services import docker_build
from gefapi import db
from gefapi.models import Script, ScriptLog
from gefapi.config import SETTINGS
from gefapi.errors import InvalidFile, ScriptNotFound, ScriptDuplicated, NotAllowed


def allowed_file(filename):
    if len(filename.rsplit('.')) > 2:
        return filename.rsplit('.')[1]+'.'+filename.rsplit('.')[2].lower() in SETTINGS.get('ALLOWED_EXTENSIONS')
    else:
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in SETTINGS.get('ALLOWED_EXTENSIONS')


class ScriptService(object):
    """Script Class"""

    @staticmethod
    def create_script(sent_file, user, script=None):
        logging.info('[SERVICE]: Creating script')
        if sent_file and allowed_file(sent_file.filename):
            logging.info('[SERVICE]: Allowed format')
            filename = secure_filename(sent_file.filename)
            sent_file_path = os.path.join(SETTINGS.get('UPLOAD_FOLDER'), filename)
            logging.info('[SERVICE]: Saving file')
            try:
                if not os.path.exists(SETTINGS.get('UPLOAD_FOLDER')):
                    os.makedirs(SETTINGS.get('UPLOAD_FOLDER'))
                sent_file.save(sent_file_path)
            except Exception as e:
                logging.error(e)
                raise e
            logging.info('[SERVICE]: File saved')
        else:
            raise InvalidFile(message='Invalid File')

        try:
            with tarfile.open(name=sent_file_path, mode='r:gz') as tar:
                if 'configuration.json' not in tar.getnames():
                    raise InvalidFile(message='Invalid File')
                config_file = tar.extractfile(member='configuration.json')
                logging.info('[SERVICE]: Config file extracted')
                config_content = config_file.read()
                logging.info('[SERVICE]: Config file opened')
                config = json.loads(config_content)
                script_name = config.get('name', None)
        except Exception as error:
            raise error

        if script is None:
            # Creating new entity
            name = script_name
            slug = slugify(script_name)
            currentScript = Script.query.filter_by(slug=slug).first()
            if currentScript:
                raise ScriptDuplicated(message='Script with name '+name+' generates an existing script slug')
            script = Script(name=name, slug=slug, user_id=user.id)
        else:
            # Updating existing entity
            logging.debug(script_name)
            script.name = script_name

        # TO DB
        try:
            logging.info('[DB]: ADD')
            db.session.add(script)
            
        
            shutil.move(sent_file_path, os.path.join(SETTINGS.get('SCRIPTS_FS'), script.slug+'.tar.gz'))
            sent_file_path = os.path.join(SETTINGS.get('SCRIPTS_FS'), script.slug+'.tar.gz')
            with tarfile.open(name=sent_file_path, mode='r:gz') as tar:
                tar.extractall(path=SETTINGS.get('SCRIPTS_FS') + '/'+script.slug)
            
            db.session.commit()
            result = docker_build.delay(script.id, path=SETTINGS.get('SCRIPTS_FS') + '/'+script.slug, tag_image=script.slug)

        except Exception as error:
            logging.error(error)
            raise error
        return script

    @staticmethod
    def get_scripts(user):
        logging.info('[SERVICE]: Getting scripts')
        logging.info('[DB]: QUERY')
        if user.role == 'ADMIN':
            scripts = Script.query.all()
            return scripts
        else:
            scripts = Script.query.filter_by(user_id=user.id)
            return scripts

    @staticmethod
    def get_script(script_id, user):
        logging.info('[SERVICE]: Getting script: '+script_id)
        logging.info('[DB]: QUERY')
        if user.role == 'ADMIN':
            try:
                val = UUID(script_id, version=4)
                script = Script.query.get(script_id)
            except ValueError:
                script = Script.query.filter_by(slug=script_id).first()
            except Exception as error:
                raise error
        else:
            try:
                val = UUID(script_id, version=4)
                script = Script.query.filter_by(id=script_id, user_id=user.id).first()
            except ValueError:
                script = Script.query.filter_by(slug=script_id, user_id=user.id).first()
            except Exception as error:
                raise error
        if not script:
            raise ScriptNotFound(message='Script with id '+script_id+' does not exist')
        return script

    @staticmethod
    def get_script_logs(script_id, start_date, last_id):
        logging.info('[SERVICE]: Getting script logs of script %s: ' % (script_id))
        logging.info('[DB]: QUERY')
        try:
            script = Script.query.get(script_id)
        except ValueError:
            script = Script.query.filter_by(slug=script_id).first()
        except Exception as error:
            raise error
        if not script:
            raise ScriptNotFound(message='Script with id '+script_id+' does not exist')

        if start_date:
            logging.debug(start_date)
            return ScriptLog.query.filter(ScriptLog.script_id == script.id, ScriptLog.register_date > start_date).order_by(ScriptLog.register_date).all()
        elif last_id:
            return ScriptLog.query.filter(ScriptLog.script_id == script.id, ScriptLog.id > last_id).order_by(ScriptLog.register_date).all()
        else:
            return script.logs

    def update_script(script_id, sent_file, user):
        logging.info('[SERVICE]: Updating script')
        script = ScriptService.get_script(script_id, user)
        if not script:
            raise ScriptNotFound(message='Script with id '+script_id+' does not exist')
        if user.id != script.user_id and user.role != 'ADMIN':
            raise NotAllowed(message='Operation not allowed to this user')
        return ScriptService.create_script(sent_file, user, script)

    @staticmethod
    def delete_script(script_id):
        logging.info('[SERVICE]: Deleting script'+script_id)
        script = ScriptService.get_script(script_id=script_id)
        if not script:
            raise ScriptNotFound(message='Script with script_id '+script_id+' does not exist')
        try:
            logging.info('[DB]: DELETE')
            db.session.delete(script)
            db.session.commit()
        except Exception as error:
            raise error
        return script
