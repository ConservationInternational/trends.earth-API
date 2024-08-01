"""DOCKER SERVICE"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import gzip
import json
import logging
import os
import tarfile
import tempfile
from pathlib import Path
from shutil import copy

import docker
import rollbar
from gefapi import celery
from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import Execution
from gefapi.models import Script
from gefapi.models import ScriptLog
from gefapi.s3 import get_script_from_s3
from gefapi.s3 import push_params_to_s3

rollbar.init(os.getenv("ROLLBAR_SERVER_TOKEN"), os.getenv("ENV"))

REGISTRY_URL = SETTINGS.get("REGISTRY_URL")
DOCKER_HOST = SETTINGS.get("DOCKER_HOST")

docker_client = docker.DockerClient(base_url=DOCKER_HOST)


@celery.task()
def docker_build(script_id):
    logging.debug("Obtaining script with id %s" % (script_id))
    script = Script.query.get(script_id)
    script_file = script.slug + ".tar.gz"

    with tempfile.TemporaryDirectory() as temp_dir:
        logging.info("[THREAD] Getting %s from S3", script_file)
        temp_file_path = os.path.join(temp_dir, script.slug + ".tar.gz")
        get_script_from_s3(script_file, temp_file_path)
        extract_path = temp_dir + "/" + script.slug
        with tarfile.open(name=temp_file_path, mode="r:gz") as tar:
            tar.extractall(path=extract_path)

        logging.info("[THREAD] Running build")
        script.status = "BUILDING"
        db.session.add(script)
        db.session.commit()
        logging.debug("Building...")
        correct, log = DockerService.build(
            script_id=script_id,
            path=extract_path,
            tag_image=script.slug,
            environment=script.environment,
            environment_version=script.environment_version,
        )
        logging.debug("Changing status")
        script = Script.query.get(script_id)
        if correct:
            logging.debug("Build successful")
            script.status = "SUCCESS"
        else:
            logging.debug("Build failed")
            script.status = "FAIL"
        db.session.add(script)
        db.session.commit()


@celery.task()
def docker_run(execution_id, image, environment, params):
    logging.info("[THREAD] Running script with image %s" % (image))
    logging.debug("Obtaining execution with id %s" % (execution_id))
    execution = Execution.query.get(execution_id)
    execution.status = "READY"
    db.session.add(execution)
    db.session.commit()

    logging.debug("Dumping parameters to json file...")
    with tempfile.TemporaryDirectory() as temp_dir:
        params_gz_file = Path(temp_dir) / (str(execution_id) + ".json.gz")
        json_str = json.dumps(params)
        json_bytes = json_str.encode("utf-8")
        with gzip.open(params_gz_file, "w") as fout:
            fout.write(json_bytes)
        push_params_to_s3(params_gz_file, params_gz_file.name)

    logging.debug("Running...")
    correct, error = DockerService.run(
        execution_id=execution_id, image=image, environment=environment
    )
    logging.debug("Execution run - changing status")
    execution = Execution.query.get(execution_id)

    if not correct:
        logging.debug("Execution failed")
        execution.status = "FAILED"
    else:
        logging.debug("Execution ongoing")
    db.session.add(execution)
    db.session.commit()


class DockerService(object):
    """Docker Service"""

    @staticmethod
    def save_build_log(script_id, line):
        """Save docker logs"""
        text = None

        if "stream" in line:
            text = "Build: " + line["stream"]
        elif "status" in line:
            text = line["status"]

            if "id" in line:
                text += " " + line["id"]

        logging.debug(text)

        if text is not None:
            script_log = ScriptLog(text=text, script_id=script_id)
            db.session.add(script_log)
            db.session.commit()

    @staticmethod
    def push(script_id, tag_image):
        """Push image to private docker registry"""
        logging.debug("Pushing image with tag %s" % (tag_image))
        pushed = False
        try:
            for line in docker_client.images.push(
                REGISTRY_URL + "/" + tag_image, stream=True, decode=True
            ):
                DockerService.save_build_log(script_id=script_id, line=line)
                if "aux" in line and pushed:
                    return True, line["aux"]
                elif "status" in line and line["status"] == "Pushed":
                    pushed = True

            # If we get here, the build failed
            raise Exception

        except Exception as error:
            logging.error(error)
            rollbar.report_exc_info()
            return False, error

    @staticmethod
    def build(
        script_id,
        path,
        tag_image,
        environment,
        environment_version,
    ):
        """Build image and push to private docker registry"""

        logging.info("Building new image in path %s with tag %s" % (path, tag_image))
        try:
            logging.debug("[SERVICE]: Copying dockerfile")
            dockerfile = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "run/Dockerfile",
            )
            copy(dockerfile, os.path.join(path, "Dockerfile"))

            logging.debug(f"[SERVICE]: tag is {REGISTRY_URL + '/' + tag_image}")
            image, logs = docker_client.images.build(
                path=path,
                rm=True,
                tag=REGISTRY_URL + "/" + tag_image,
                forcerm=True,
                pull=True,
                nocache=True,
                buildargs={
                    "ENVIRONMENT": environment,
                    "ENVIRONMENT_VERSION": environment_version,
                },
            )

            for line in logs:
                if "errorDetail" in line:
                    return False, line["errorDetail"]
                else:
                    DockerService.save_build_log(script_id=script_id, line=line)

            return DockerService.push(script_id=script_id, tag_image=tag_image)

        except docker.errors.APIError as error:
            logging.error(error)

            return False, error

        except Exception as error:
            logging.error(error)
            rollbar.report_exc_info()
            return False, error

    @staticmethod
    def run(execution_id, image, environment):
        """Run image with environment"""
        logging.info("Running %s image" % (image))
        try:
            environment["ENV"] = "prod"

            if os.getenv("ENVIRONMENT") not in ["dev"]:
                logging.info(
                    "Creating service (running in "
                    f'{os.getenv("ENVIRONMENT")} environment, with image '
                    f"{REGISTRY_URL}/{image}, as execution "
                    f"execution-{str(execution_id)})",
                )

                # env = [k + "=" + v for str(k), str(v) in environment.items()]
                env = []
                for item in environment.items():
                    env.append(f"{item[0]}={item[1]}")

                script = Script.query.get(Execution.query.get(execution_id).script_id)

                docker_client.services.create(
                    image=f"{REGISTRY_URL}/{image}",
                    command="./entrypoint.sh",
                    env=env,
                    name="execution-" + str(execution_id),
                    resources=docker.types.Resources(
                        cpu_reservation=script.cpu_reservation,
                        cpu_limit=script.cpu_limit,
                        mem_reservation=script.memory_reservation,
                        mem_limit=script.memory_limit,
                    ),
                    restart_policy=docker.types.RestartPolicy(
                        condition="on-failure", delay=10, max_attempts=2, window=0
                    ),
                )
            else:
                logging.info(
                    "Creating container (running in "
                    f'{os.getenv("ENVIRONMENT")} environment, with image '
                    f"{REGISTRY_URL}/{image}, as execution "
                    f"execution-{str(execution_id)})",
                )
                docker_client.containers.run(
                    image=f"{REGISTRY_URL}/{image}",
                    command="./entrypoint.sh",
                    environment=environment,
                    detach=True,
                    name="execution-" + str(execution_id),
                    remove=True,
                )
        except docker.errors.ImageNotFound as error:
            logging.error("Image not found", error)

            return False, error
        except Exception as error:
            logging.error(error)
            rollbar.report_exc_info()
            return False, error

        return True, None
