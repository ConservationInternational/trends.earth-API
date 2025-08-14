"""DOCKER SERVICE"""

import gzip
import json
import logging
import os
from pathlib import Path
from shutil import copy
import tarfile
import tempfile

import docker
from docker import errors as docker_errors
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
    if not script:
        logger.error(f"Script with id {script_id} not found.")
        return
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
        if not script:
            logger.error(f"Script with id {script_id} not found after build.")
            return
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
    if not execution:
        logger.error(f"Execution with id {execution_id} not found.")
        return
    try:
        execution.status = "READY"
    except Exception:
        logger.warning(
            "Could not set status on execution "
            f"{execution_id} (may be missing attribute)"
        )
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
    if not execution:
        logger.error(f"Execution with id {execution_id} not found after run.")
        return
    if not correct:
        logger.debug("Execution failed")
        try:
            execution.status = "FAILED"
        except Exception:
            logger.warning(
                "Could not set status on execution "
                f"{execution_id} (may be missing attribute)"
            )
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
        """
        Push image to private docker registry with retry on connection errors.

        Logs are saved after push completes.
        """
        import http.client
        import time

        import urllib3

        logger.debug(f"Pushing image with tag {tag_image}")
        max_retries = 4
        base_delay = 2  # seconds
        attempt = 0
        last_error = None
        output_lines = []

        if not REGISTRY_URL:
            logger.error("REGISTRY_URL is not configured.")
            return False, Exception("REGISTRY_URL is not configured.")
        while attempt < max_retries:
            pushed = False
            output_lines.clear()
            blob_unknown_error = False
            try:
                client = get_docker_client()
                if client is None:
                    raise Exception("Docker client is not available.")
                push_tag = f"{REGISTRY_URL}/{tag_image}"
                push_stream = client.images.push(push_tag, stream=True, decode=True)
                result_aux = None
                for line in push_stream:
                    # Only process if line is a dict
                    if not isinstance(line, dict):
                        continue
                    output_lines.append(line)
                    # Detect 'blob unknown' errors in push logs
                    error_val = (
                        str(line.get("error", "")).lower() if line.get("error") else ""
                    )
                    error_detail_val = (
                        str(line.get("errorDetail", "")).lower()
                        if line.get("errorDetail")
                        else ""
                    )
                    if (
                        "blob unknown" in error_val
                        or "blob unknown" in error_detail_val
                    ):
                        blob_unknown_error = True
                    if line.get("aux") and pushed:
                        result_aux = line["aux"]
                    if line.get("status") == "Pushed":
                        pushed = True

                # Save all logs after push attempt
                for log_line in output_lines:
                    DockerService.save_build_log(script_id=script_id, line=log_line)

                if pushed and result_aux and not blob_unknown_error:
                    return True, result_aux

                # If we get here, the build failed
                if blob_unknown_error:
                    raise RuntimeError("Blob unknown error detected during push.")
                raise Exception("Image push did not complete successfully.")

            except (
                docker_errors.APIError,
                urllib3.exceptions.ProtocolError,
                http.client.IncompleteRead,
                RuntimeError,  # Retry on blob unknown
            ) as error:
                last_error = error
                logger.warning(f"Push attempt {attempt + 1} failed: {error}")
                # Save logs for this failed attempt
                for log_line in output_lines:
                    DockerService.save_build_log(script_id=script_id, line=log_line)
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(
                        f"Retrying push in {delay} seconds "
                        f"(attempt {attempt + 2}/{max_retries})..."
                    )
                    time.sleep(delay)
                attempt += 1
            except Exception as error:
                # Save logs for this failed attempt
                for log_line in output_lines:
                    DockerService.save_build_log(script_id=script_id, line=log_line)
                logger.error(error)
                rollbar.report_exc_info()
                return False, error

        logger.error(f"Image push failed after {max_retries} attempts: {last_error}")
        # Save logs for the final failed attempt
        for log_line in output_lines:
            DockerService.save_build_log(script_id=script_id, line=log_line)
        rollbar.report_exc_info()
        return False, last_error

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
        if not REGISTRY_URL:
            logger.error("REGISTRY_URL is not configured.")
            return False, Exception("REGISTRY_URL is not configured.")
        try:
            logger.debug("[SERVICE]: Copying dockerfile")
            dockerfile = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "run/Dockerfile",
            )
            copy(dockerfile, os.path.join(path, "Dockerfile"))

            tag_full = f"{REGISTRY_URL}/{tag_image}"
            logger.debug(f"[SERVICE]: tag is {tag_full}")
            client = get_docker_client()
            if client is None:
                logger.error("Docker client is not available.")
                return False, Exception("Docker client is not available.")
            image, logs = client.images.build(
                path=path,
                rm=True,
                tag=tag_full,
                forcerm=True,
                pull=True,
                nocache=True,
                buildargs={
                    "ENVIRONMENT": environment,
                    "ENVIRONMENT_VERSION": environment_version,
                },
            )

            for line in logs:
                # Only process if line is a dict
                if not isinstance(line, dict):
                    continue
                if "errorDetail" in line and line["errorDetail"]:
                    return False, line["errorDetail"]
                DockerService.save_build_log(script_id=script_id, line=line)

            # Push the image
            push_result = DockerService.push(script_id=script_id, tag_image=tag_image)

            # Remove the image from the local Docker daemon after push
            try:
                client.images.remove(image=tag_full, force=True)
                logger.info(f"Removed local image {tag_full} after push.")
            except Exception as remove_error:
                logger.warning(
                    f"Failed to remove local image {tag_full}: {remove_error}"
                )

            return push_result

        except docker_errors.APIError as error:
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

                # Create network spec to connect to execution network for API access
                networks = []
                try:
                    # Autodetect execution network by matching environment
                    execution_network = None
                    current_env = os.getenv("ENVIRONMENT", "prod")

                    # Find the execution network for this environment
                    all_networks = client.networks.list()
                    for network in all_networks:
                        network_name = network.name

                        # For Docker Compose (only in development environment)
                        if current_env == "dev" and network_name == "execution":
                            execution_network = network
                            logger.info(
                                f"Found Docker Compose execution network: "
                                f"{network_name}"
                            )
                            break

                        # For Docker Swarm (production/staging/any non-dev environment)
                        # Match networks ending with "-{env}_execution"
                        if network_name.endswith(f"-{current_env}_execution"):
                            execution_network = network
                            logger.info(
                                f"Found environment-matched execution network: "
                                f"{network_name} for environment: {current_env}"
                            )
                            break

                    if execution_network:
                        networks = [execution_network.id]
                        logger.info(
                            f"Connecting execution-{execution_id} to execution "
                            f"network {execution_network.name}"
                        )
                    else:
                        raise docker.errors.NotFound(
                            f"No execution network found for environment: {current_env}"
                        )

                except docker.errors.NotFound:
                    logger.warning(
                        f"Execution network not found for environment "
                        f"{os.getenv('ENVIRONMENT', 'prod')}, execution "
                        "will use external API access"
                    )
                except Exception as e:
                    logger.warning(f"Failed to autodetect execution network: {e}")

                client.services.create(
                    image=f"{REGISTRY_URL}/{image}",
                    command="./entrypoint.sh",
                    env=env,
                    name="execution-" + str(execution_id),
                    labels={
                        "execution.id": str(execution_id),
                        "execution.script_id": str(script.id),
                        "service.type": "execution",
                        "managed.by": "trends.earth-api",
                    },
                    networks=networks,
                    resources=docker.types.Resources(
                        cpu_reservation=script.cpu_reservation,
                        cpu_limit=script.cpu_limit,
                        mem_reservation=script.memory_reservation,
                        mem_limit=script.memory_limit,
                    ),
                    restart_policy=docker.types.RestartPolicy(
                        condition="on-failure", delay=60, max_attempts=2, window=7200
                    ),
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
        except docker_errors.ImageNotFound as error:
            logger.error("Image not found", error)

            return False, error
        except Exception as error:
            logger.error(error)
            rollbar.report_exc_info()
            return False, error

        return True, None

    @staticmethod
    def get_service_logs(execution_id):
        """Get docker service logs for an execution by dispatching a celery task"""
        logger.info(
            f"Dispatching celery task to get docker logs for execution {execution_id}"
        )
        try:
            result = celery_app.send_task(
                "docker.get_service_logs", args=[execution_id]
            )
            # Wait for the task to complete with a timeout
            logs = result.get(timeout=120)  # Wait for 2 minutes
            return logs
        except Exception as e:
            logger.error(
                f"Error dispatching or getting result for get_docker_logs_task "
                f"for execution {execution_id}: {e}"
            )
            rollbar.report_exc_info()
            raise e


@celery_app.task(name="docker.get_service_logs")
def get_docker_logs_task(execution_id):
    """Celery task to get docker service logs for an execution"""
    logger.info(f"Celery task: Getting docker logs for execution {execution_id}")
    try:
        client = get_docker_client()
        if not client:
            raise Exception("Docker client not available")

        service_name = f"execution-{execution_id}"
        services = client.services.list(filters={"name": service_name})

        if not services:
            logger.warning(f"Service {service_name} not found")
            return None

        service = services[0]
        logs = service.logs(stdout=True, stderr=True, tail=1000, timestamps=True)

        formatted_logs = []
        for i, line in enumerate(logs):
            log_entry = line.decode("utf-8").strip()
            parts = log_entry.split(" ", 1)
            timestamp_str = parts[0]
            text = parts[1] if len(parts) > 1 else ""
            formatted_logs.append({"id": i, "created_at": timestamp_str, "text": text})
        return formatted_logs

    except docker_errors.NotFound as e:
        logger.warning(f"Could not find service for execution {execution_id}: {e}")
        return None
    except Exception as e:
        logger.error(
            f"Error getting docker logs for execution {execution_id} "
            f"in celery task: {e}"
        )
        rollbar.report_exc_info()
        # Re-raise the exception to mark the task as failed
        raise e


@celery_app.task(name="docker.cancel_execution")
def cancel_execution_task(execution_id):
    """
    Celery task to cancel a Docker execution.
    This runs on the build queue where Docker access is available.
    """
    logger.info(f"Celery task: Canceling Docker resources for execution {execution_id}")

    cancellation_results = {
        "docker_service_stopped": False,
        "docker_container_stopped": False,
        "errors": [],
    }

    try:
        client = get_docker_client()
        if not client:
            error_msg = "Docker client not available"
            logger.warning(f"[DOCKER_CANCEL]: {error_msg}")
            cancellation_results["errors"].append(error_msg)
            return cancellation_results

        docker_service_name = f"execution-{execution_id}"

        # Try to stop Docker service first (for swarm mode)
        try:
            services = client.services.list(filters={"name": docker_service_name})
            for service in services:
                logger.info(f"[DOCKER_CANCEL]: Stopping Docker service {service.name}")
                service.remove()
                cancellation_results["docker_service_stopped"] = True
                break
        except Exception as docker_error:
            error_msg = f"Docker service stop failed: {str(docker_error)}"
            logger.warning(f"[DOCKER_CANCEL]: {error_msg}")
            cancellation_results["errors"].append(error_msg)

        # Try to stop Docker container (for standalone mode)
        try:
            containers = client.containers.list(
                filters={"name": docker_service_name}, all=True
            )
            for container in containers:
                logger.info(
                    f"[DOCKER_CANCEL]: Stopping Docker container {container.name}"
                )
                if container.status == "running":
                    container.stop(timeout=10)
                container.remove(force=True)
                cancellation_results["docker_container_stopped"] = True
                break
        except Exception as docker_error:
            error_msg = f"Docker container stop failed: {str(docker_error)}"
            logger.warning(f"[DOCKER_CANCEL]: {error_msg}")
            cancellation_results["errors"].append(error_msg)

        logger.info(
            f"[DOCKER_CANCEL]: Completed cancellation for execution {execution_id}"
        )
        return cancellation_results

    except Exception as error:
        error_msg = f"Docker cancellation error: {str(error)}"
        logger.error(f"[DOCKER_CANCEL]: {error_msg}")
        rollbar.report_exc_info()
        cancellation_results["errors"].append(error_msg)
        return cancellation_results
