"""DOCKER SERVICE"""

import datetime
import gzip
import json
import logging
import os
from pathlib import Path
from shutil import copy
import tarfile
import tempfile

import docker
import rollbar

from gefapi import celery as celery_app  # Rename to avoid mypy confusion
from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import Execution, Script, ScriptLog
from gefapi.s3 import get_script_from_s3, push_params_to_s3

REGISTRY_URL = SETTINGS.get("REGISTRY_URL")
DOCKER_HOST = SETTINGS.get("DOCKER_HOST")

logger = logging.getLogger()

# Initialize docker client with error handling
docker_client = None
try:
    if DOCKER_HOST:
        docker_client = docker.DockerClient(base_url=DOCKER_HOST)
        # Test the connection
        docker_client.ping()
    else:
        logger.warning(
            "DOCKER_HOST not configured, Docker functionality will be disabled"
        )
except Exception as e:
    logger.warning(
        f"Failed to connect to Docker: {e}. Docker functionality will be disabled"
    )
    docker_client = None


def get_docker_client():
    """Get docker client with lazy initialization and error handling"""
    global docker_client
    if docker_client is None:
        try:
            if DOCKER_HOST:
                docker_client = docker.DockerClient(base_url=DOCKER_HOST)
                docker_client.ping()
            else:
                raise Exception("DOCKER_HOST not configured")
        except Exception as e:
            logger.error(f"Failed to connect to Docker: {e}")
            # In development environment, we might not always have Docker available
            if SETTINGS.get("ENVIRONMENT") == "dev":
                logger.warning(
                    "Running in development mode - Docker functionality disabled"
                )
                return None
            raise
    return docker_client


@celery_app.task()
def docker_build(script_id):
    """Creates the execution for a script in the database"""
    logger.info(f"Building Docker image for script with id {script_id}")

    # Check if Docker is available
    try:
        client = get_docker_client()
        if client is None:
            logger.warning(
                f"Docker not available, skipping build for script {script_id}"
            )
            return
    except Exception as e:
        logger.error(f"Docker not available for build: {e}")
        return

    logger.debug(f"Obtaining script with id {script_id}")
    script = Script.query.get(script_id)
    script_file = script.slug + ".tar.gz"

    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info("[THREAD] Getting %s from S3", script_file)
        temp_file_path = os.path.join(temp_dir, script.slug + ".tar.gz")
        get_script_from_s3(script_file, temp_file_path)
        extract_path = temp_dir + "/" + script.slug
        with tarfile.open(name=temp_file_path, mode="r:gz") as tar:
            tar.extractall(path=extract_path)

        logger.info("[THREAD] Running build")
        script.status = "BUILDING"
        db.session.add(script)
        db.session.commit()
        logger.debug("Building...")
        correct, log = DockerService.build(
            script_id=script_id,
            path=extract_path,
            tag_image=script.slug,
            environment=script.environment,
            environment_version=script.environment_version,
        )
        logger.debug("Changing status")
        script = Script.query.get(script_id)
        if correct:
            logger.debug("Build successful")
            script.status = "SUCCESS"
        else:
            logger.debug("Build failed")
            script.status = "FAIL"
        db.session.add(script)
        db.session.commit()


@celery_app.task()
def docker_run(execution_id, image, environment, params):
    logger.info(f"[THREAD] Running script with image {image}")

    # Check if Docker is available
    try:
        client = get_docker_client()
        if client is None:
            logger.warning(
                f"Docker not available, skipping run for execution {execution_id}"
            )
            return
    except Exception as e:
        logger.error(f"Docker not available for run: {e}")
        return

    logger.debug(f"Obtaining execution with id {execution_id}")
    execution = Execution.query.get(execution_id)
    execution.status = "READY"
    db.session.add(execution)
    db.session.commit()

    logger.debug("Dumping parameters to json file...")
    with tempfile.TemporaryDirectory() as temp_dir:
        params_gz_file = Path(temp_dir) / (str(execution_id) + ".json.gz")
        json_str = json.dumps(params)
        json_bytes = json_str.encode("utf-8")
        with gzip.open(params_gz_file, "w") as fout:
            fout.write(json_bytes)
        push_params_to_s3(params_gz_file, params_gz_file.name)

    logger.debug("Running...")
    correct, error = DockerService.run(
        execution_id=execution_id, image=image, environment=environment
    )
    logger.debug("Execution run - changing status")
    execution = Execution.query.get(execution_id)

    if not correct:
        logger.debug("Execution failed")
        execution.status = "FAILED"
    else:
        logger.debug("Execution ongoing")
    db.session.add(execution)
    db.session.commit()


class DockerService:
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

        logger.debug(text)

        if text is not None:
            script_log = ScriptLog(text=text, script_id=script_id)
            db.session.add(script_log)
            db.session.commit()

    @staticmethod
    def push(script_id, tag_image):
        """Push image to private docker registry"""
        logger.debug(f"Pushing image with tag {tag_image}")
        pushed = False
        try:
            client = get_docker_client()
            for line in client.images.push(
                REGISTRY_URL + "/" + tag_image, stream=True, decode=True
            ):
                DockerService.save_build_log(script_id=script_id, line=line)
                if "aux" in line and pushed:
                    return True, line["aux"]
                if "status" in line and line["status"] == "Pushed":
                    pushed = True

            # If we get here, the build failed
            raise Exception

        except Exception as error:
            logger.error(error)
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

        logger.info(f"Building new image in path {path} with tag {tag_image}")
        try:
            logger.debug("[SERVICE]: Copying dockerfile")
            dockerfile = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "run/Dockerfile",
            )
            copy(dockerfile, os.path.join(path, "Dockerfile"))

            logger.debug(f"[SERVICE]: tag is {REGISTRY_URL + '/' + tag_image}")
            client = get_docker_client()
            image, logs = client.images.build(
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
                DockerService.save_build_log(script_id=script_id, line=line)

            return DockerService.push(script_id=script_id, tag_image=tag_image)

        except docker.errors.APIError as error:
            logger.error(error)

            return False, error

        except Exception as error:
            logger.error(error)
            rollbar.report_exc_info()
            return False, error

    @staticmethod
    def run(execution_id, image, environment):
        """Run image with environment"""
        logger.info(f"Running {image} image")
        try:
            environment["ENV"] = "prod"

            if os.getenv("ENVIRONMENT") not in ["dev"]:
                logger.info(
                    "Creating service (running in "
                    f"{os.getenv('ENVIRONMENT')} environment, with image "
                    f"{REGISTRY_URL}/{image}, as execution "
                    f"execution-{str(execution_id)})",
                )

                # env = [k + "=" + v for str(k), str(v) in environment.items()]
                env = []
                for item in environment.items():
                    env.append(f"{item[0]}={item[1]}")

                script = Script.query.get(Execution.query.get(execution_id).script_id)

                client = get_docker_client()

                # Create service using Docker API with individual parameters
                response = client.api.create_service(
                    task_template={
                        "ContainerSpec": {
                            "Image": f"{REGISTRY_URL}/{image}",
                            "Command": ["./entrypoint.sh"],
                            "Env": env,
                            "Labels": {
                                "execution.id": str(execution_id),
                                "service.type": "execution",
                            },
                        },
                        "Resources": {
                            "Reservations": {
                                "NanoCPUs": (
                                    int(script.cpu_reservation * 1_000_000_000)
                                    if script.cpu_reservation
                                    else 100_000_000  # 0.1 CPU default (reduced)
                                ),
                                "MemoryBytes": (
                                    script.memory_reservation
                                    if script.memory_reservation
                                    else 200 * 1024 * 1024  # 200MB default (reduced)
                                ),
                            },
                            "Limits": {
                                "NanoCPUs": (
                                    int(script.cpu_limit * 1_000_000_000)
                                    if script.cpu_limit
                                    else 500_000_000  # 0.5 CPU default (reduced)
                                ),
                                "MemoryBytes": (
                                    script.memory_limit
                                    if script.memory_limit
                                    else 500 * 1024 * 1024  # 500MB default (reduced)
                                ),
                            },
                        },
                        "RestartPolicy": {
                            "Condition": "on-failure",
                            "Delay": 10_000_000_000,  # 10 seconds in nanoseconds
                            "MaxAttempts": 2,
                            "Window": 120_000_000_000,  # 2 minutes in nanoseconds
                        },
                        "Placement": {
                            "Constraints": [
                                # Prefer worker nodes for executions
                                "node.role != manager",
                                # Only schedule on active nodes
                                "node.availability == active",
                            ]
                        },
                    },
                    name=f"execution-{execution_id}",
                    labels={
                        "execution.id": str(execution_id),
                        "execution.script_id": str(script.id),
                        "service.type": "execution",
                        "managed.by": "trends.earth-api",
                        "created.at": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                    },
                    mode={"Replicated": {"Replicas": 1}},
                )
                service_id = response.get("ID", "unknown")
                logger.info(
                    f"Created Swarm service for execution {execution_id}: {service_id}"
                )
            else:
                logger.info(
                    "Creating container (running in "
                    f"{os.getenv('ENVIRONMENT')} environment, with image "
                    f"{REGISTRY_URL}/{image}, as execution "
                    f"execution-{str(execution_id)})",
                )
                client = get_docker_client()
                client.containers.run(
                    image=f"{REGISTRY_URL}/{image}",
                    command="./entrypoint.sh",
                    environment=environment,
                    detach=True,
                    name="execution-" + str(execution_id),
                    remove=True,
                )
        except docker.errors.ImageNotFound as error:
            logger.error("Image not found", error)

            return False, error
        except Exception as error:
            logger.error(error)
            rollbar.report_exc_info()
            return False, error

        return True, None
