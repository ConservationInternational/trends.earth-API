"""DOCKER SERVICE"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging
import os
from shutil import copy
import docker

import time

from gefapi import db, celery
from gefapi.models import Script, ScriptLog, Execution
from gefapi.config import SETTINGS


REGISTRY_URL = SETTINGS.get('REGISTRY_URL')
DOCKER_URL = SETTINGS.get('DOCKER_URL')

api_client = docker.APIClient(base_url=DOCKER_URL)
docker_client = docker.DockerClient(base_url=DOCKER_URL)


@celery.task()
def docker_build(script_id, path, tag_image):
    logging.info('[THREAD] Running build')
    logging.debug('Obtaining script with id %s' % (script_id));
    script = Script.query.get(script_id)
    script.status = 'BUILDING'
    db.session.add(script)
    db.session.commit()
    logging.debug('Building...')
    correct, log = DockerService.build(script_id=script_id, path=path, tag_image=tag_image)
    try:
        docker_client.remove(image=tag_image)
    except Exception:
        logging.info('Error removing the image')
    logging.debug('Changing status')
    script = Script.query.get(script_id)
    if correct:
        logging.debug('Correct build')
        script.status = 'SUCCESS'
    else:
        logging.debug('Fail build')
        script.status = 'FAIL'
    db.session.add(script)
    db.session.commit()


@celery.task()
def docker_run(execution_id, image, environment, params):
    logging.info('[THREAD] Running script with params %s'%(params))
    logging.debug('Obtaining execution with id %s' % (execution_id));
    execution = Execution.query.get(execution_id)
    execution.status = 'READY'
    db.session.add(execution)
    db.session.commit()
    logging.debug('Running...')
    correct, log = DockerService.run(execution_id=execution_id, image=image, environment=environment, params=params)
    logging.debug('Execution done')
    logging.debug('Changing status')
    execution = Execution.query.get(execution_id)
    if not correct:
        logging.debug('Failed')
        execution.status = 'FAILED'
    db.session.add(execution)
    db.session.commit()

class DockerService(object):
    """Docker Service"""

    @staticmethod
    def save_build_log(script_id, line):
        """Save docker logs"""
        text = None
        if 'stream' in line:
            text = 'Build: ' + line['stream']
        elif 'status' in line:
            text = line['status']
            if 'id' in line:
                text += ' ' + line['id']

        logging.debug(text)
        if text != None:
            script_log = ScriptLog(text=text, script_id=script_id)
            db.session.add(script_log)
            db.session.commit()

    @staticmethod
    def push(script_id, tag_image):
        """Push image to private docker registry"""
        logging.debug('Pushing image with tag %s' % (tag_image))
        pushed = False
        try:
            for line in api_client.push(REGISTRY_URL+'/'+tag_image, stream=True, decode=True):
                DockerService.save_build_log(script_id=script_id, line=line)
                if 'aux' in line and pushed:
                    return True, line['aux']
                elif 'status' in line and line['status'] == 'Pushed':
                    pushed = True
        except Exception as error:
            logging.error(error)
            return False, error

    @staticmethod
    def build(script_id, path, tag_image):
        """Build image and push to private docker registry"""

        logging.info('Building new image in path %s with tag %s' % (path, tag_image))
        try:
            logging.debug('[SERVICE]: Copying dockerfile')
            dockerfile = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'run/Dockerfile')
            copy(dockerfile, os.path.join(path, 'Dockerfile'))
            for line in api_client.build(path=path, rm=True, decode=True, tag=REGISTRY_URL+'/'+tag_image, forcerm=True, pull=False, nocache=True):
                if 'errorDetail' in line:
                    return False, line['errorDetail']
                else:
                    DockerService.save_build_log(script_id=script_id, line=line)
            return DockerService.push(script_id=script_id, tag_image=tag_image)
        except docker.errors.APIError as error:
            logging.error(error)
            return False, error
        except Exception as error:
            logging.error(error)
            return False, error

    @staticmethod
    def run(execution_id, image, environment, params):
        """Run image with environment"""
        logging.info('Running %s image with params %s' % (image, params))
        container = None
        try:
            environment['ENV'] = 'prod'
            command = './entrypoint.sh ' + params
            if os.getenv('ENVIRONMENT') != 'dev':
                env = [k+'='+v for k,v in environment.items()]
                logging.info(env)
                container = docker_client.services.create(image=REGISTRY_URL+'/'+image, command=command, env=env, name='execution-'+str(execution_id), restart_policy=docker.types.RestartPolicy(condition='on-failure', delay=10, max_attempts=2, window=0))
            else:
                container = docker_client.containers.run(image=REGISTRY_URL+'/'+image, command=params, environment=environment, detach=True, name='execution-'+str(execution_id))
        except docker.errors.ImageNotFound as error:
            logging.error('Image not found', error)
            return False, error
        except Exception as error:
            logging.error(error)
            return False, error
        return True, None
