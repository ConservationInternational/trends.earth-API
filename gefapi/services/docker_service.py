"""DOCKER SERVICE"""

import gzip
import json
import logging
import os
from pathlib import Path
from shutil import copy
import socket
import tarfile
import tempfile
from urllib.parse import urlparse

import docker
from docker import errors as docker_errors
from docker import types as docker_types
import rollbar

from gefapi import celery as celery_app  # Rename to avoid mypy confusion
from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import Execution, Script, ScriptLog
from gefapi.s3 import get_script_from_s3, push_params_to_s3

REGISTRY_URL = SETTINGS.get("REGISTRY_URL")
DOCKER_HOST = SETTINGS.get("DOCKER_HOST")

logger = logging.getLogger()


def _candidate_docker_hosts() -> list[str]:
    """Return candidate base_urls to try for connecting to the Docker daemon."""
    candidates: list[str] = []

    if DOCKER_HOST:
        candidates.append(DOCKER_HOST)

    # Common defaults inside containers
    candidates.extend(
        [
            "unix://var/run/docker.sock",  # standard socket path
            "unix://tmp/docker.sock",  # fallback if mounted to /tmp
        ]
    )

    # Deduplicate while preserving order
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)
    return unique_candidates


# Initialize docker client with error handling and fallbacks
docker_client = None
_active_docker_host: str | None = None
try:
    for base_url in _candidate_docker_hosts():
        try:
            temp_client = docker.DockerClient(base_url=base_url)
            temp_client.ping()
            docker_client = temp_client
            _active_docker_host = base_url
            logger.info(f"Connected to Docker daemon via {base_url}")
            break
        except Exception as conn_err:
            logger.debug(f"Docker connection failed for {base_url}: {conn_err}")
    if docker_client is None:
        logger.warning(
            "Docker daemon not reachable. Ensure the Docker socket is mounted and\n"
            "DOCKER_HOST is set correctly (e.g., unix://var/run/docker.sock)."
        )
except Exception as e:
    logger.warning(
        f"Failed to initialize Docker client: {e}. "
        "Docker functionality will be disabled"
    )
    docker_client = None


def get_docker_client():
    """Get docker client with lazy initialization and Docker 28.x compatibility.

    Tries multiple base_urls for robustness when running in Swarm with a mounted
    Unix socket.
    """
    global docker_client, _active_docker_host
    if docker_client is None:
        last_error: Exception | None = None
        for base_url in _candidate_docker_hosts():
            try:
                # Configure Docker client with Docker Engine 28.x compatibility fix
                client_kwargs = {
                    "base_url": base_url,
                    "timeout": 300,  # Standard timeout
                }

                # Check Docker version for compatibility workarounds
                try:
                    temp_client = docker.DockerClient(base_url=base_url)
                    version_info = temp_client.version()
                    engine_version = version_info.get("Version", "")
                    temp_client.close()

                    # Docker Engine 28+ has known registry push issues
                    version_major = (
                        int(engine_version.split(".")[0])
                        if engine_version.split(".")[0].isdigit()
                        else 0
                    )
                    if version_major >= 28:
                        logger.info(
                            f"Detected Docker Engine {engine_version} "
                            f"(v{version_major}+) - applying compatibility fixes"
                        )
                        # Force HTTP/1.1 for registry communication
                        client_kwargs.update(
                            {
                                "user_agent": f"docker/{engine_version} "
                                "(registry-compat)",
                            }
                        )
                except Exception as version_check_error:
                    logger.debug(
                        "Could not check Docker version on "
                        f"{base_url}: {version_check_error}"
                    )

                candidate = docker.DockerClient(**client_kwargs)
                candidate.ping()
                docker_client = candidate
                _active_docker_host = base_url
                logger.info(f"Connected to Docker daemon via {base_url}")
                break
            except Exception as e:
                last_error = e
                logger.debug(f"Docker connection failed for {base_url}: {e}")

        if docker_client is None:
            logger.error(f"Failed to connect to Docker: {last_error}")
            # In development environment, we might not always have Docker available
            if SETTINGS.get("ENVIRONMENT") == "dev":
                logger.warning(
                    "Running in development mode - Docker functionality disabled"
                )
                return None
            raise last_error if last_error else Exception("Docker not available")
    return docker_client


def _parse_registry_host_port(registry: str) -> tuple[str, int | None]:
    """Parse REGISTRY_URL into (host, port).

    Accepts forms like:
    - 172.40.1.52:5000
    - http://172.40.1.52:5000
    - https://registry.local:443
    - registry.local (no port -> None)
    """
    if not registry:
        return "", None

    # If scheme missing, urlparse treats it as path; prepend dummy scheme
    parsed = urlparse(registry if "://" in registry else f"dummy://{registry}")
    host = parsed.hostname or ""
    port = parsed.port
    return host, port


def _registry_preflight(registry: str) -> tuple[bool, str]:
    """Quick TCP + HTTP GET /v2/ connectivity probe for the registry.

    Returns (ok, message). Any non-fatal but informative message is returned
    for logging, especially useful when the daemon->registry path is broken.
    """
    import http.client as _http_client

    host, port = _parse_registry_host_port(registry)
    if not host:
        return False, "REGISTRY_URL has no host"

    # Default port for HTTP(S) if not provided; many internal registries use 5000
    port = port or 5000

    # TCP reachability
    try:
        with socket.create_connection((host, port), timeout=5):
            pass
    except Exception as e:
        return (
            False,
            f"TCP connect to {host}:{port} failed: {type(e).__name__}: {e}",
        )

    # HTTP /v2/ reachability (no auth expected; 200 or 401 both indicate a registry)
    try:
        conn = _http_client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/v2/")
        resp = conn.getresponse()
        status = resp.status
        resp.read()  # drain
        conn.close()
        if status in (200, 401):
            return True, f"Registry responded to /v2/ with {status}"
        return False, f"Registry /v2/ unexpected status: {status}"
    except Exception as e:
        return (
            False,
            f"HTTP probe to http://{host}:{port}/v2/ failed: {type(e).__name__}: {e}",
        )


def _registry_manifest_exists(
    registry: str, repo: str, reference: str
) -> tuple[bool, str]:
    """HEAD the manifest to confirm it exists in the registry.

    Returns (exists, message).
    """
    import http.client as _http_client

    host, port = _parse_registry_host_port(registry)
    if not host:
        return False, "REGISTRY_URL has no host"
    port = port or 5000

    path = f"/v2/{repo}/manifests/{reference}"
    try:
        conn = _http_client.HTTPConnection(host, port, timeout=10)
        headers = {
            "Accept": (
                "application/vnd.docker.distribution.manifest.v2+json, "
                "application/vnd.docker.distribution.manifest.v1+json, "
                "application/vnd.oci.image.manifest.v1+json"
            )
        }
        conn.request("HEAD", path, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        conn.close()
        if status == 200:
            return True, f"Manifest {repo}:{reference} exists"
        return False, f"Manifest HEAD returned {status} for {repo}:{reference}"
    except Exception as e:
        return False, f"Manifest HEAD failed: {type(e).__name__}: {e}"


def _registry_get_manifest_digest(
    registry: str, repo: str, reference: str
) -> tuple[bool, str | None, str | None, str]:
    """HEAD the manifest and return (exists, digest, last_modified, message).

    - exists: True if HTTP 200
    - digest: Value of Docker-Content-Digest header when present
    - last_modified: Value of Last-Modified header when present
    - message: Human-readable status message

    Uses HEAD to avoid transferring the full manifest body; falls back to
    returning headers only. This function is tolerant to HTTP/registry quirks
    and never raises; errors are reported via (False, None, None, msg).
    """
    import http.client as _http_client

    host, port = _parse_registry_host_port(registry)
    if not host:
        return False, None, None, "REGISTRY_URL has no host"
    port = port or 5000

    path = f"/v2/{repo}/manifests/{reference}"
    try:
        conn = _http_client.HTTPConnection(host, port, timeout=10)
        headers = {
            "Accept": (
                "application/vnd.docker.distribution.manifest.v2+json, "
                "application/vnd.docker.distribution.manifest.v1+json, "
                "application/vnd.oci.image.manifest.v1+json"
            )
        }
        conn.request("HEAD", path, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        # Read & close to free the socket
        resp.read()
        # http.client doesn't expose headers as a dict directly in type hints,
        # but mapping access via .get works in practice
        digest = resp.getheader("Docker-Content-Digest")
        last_mod = resp.getheader("Last-Modified")
        conn.close()
        if status == 200:
            msg = (
                f"Manifest {repo}:{reference} exists (digest={digest}, "
                f"last_modified={last_mod})"
            )
            return True, digest, last_mod, msg
        return (
            False,
            None,
            None,
            f"Manifest HEAD returned {status} for {repo}:{reference}",
        )
    except Exception as e:
        return False, None, None, f"Manifest HEAD failed: {type(e).__name__}: {e}"


def _split_repo_ref(tag_image: str) -> tuple[str, str]:
    """Split a name[:tag] into (repo, reference). Defaults to latest."""
    if ":" in tag_image:
        repo, ref = tag_image.rsplit(":", 1)
        return repo, ref or "latest"
    return tag_image, "latest"


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
            return None
    except Exception as e:
        logger.error(f"Docker not available for build: {e}")
        return None

    logger.debug(f"Obtaining script with id {script_id}")
    script = Script.query.get(script_id)
    if not script:
        logger.error(f"Script with id {script_id} not found.")
        ScriptLog(text="Build failed: script not found.", script_id=script_id)
        return {"success": False, "error": "Script not found"}
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
        logger.info(f"[STATUS] Script {script_id} status set to BUILDING")
        ScriptLog(text="Build started.", script_id=script_id)
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
            ScriptLog(
                text="Build failed: script not found after build.", script_id=script_id
            )
            db.session.commit()
            return {"success": False, "error": "Script not found after build"}
        if correct:
            logger.info(f"[STATUS] Script {script_id} build SUCCESS")
            script.status = "SUCCESS"
            ScriptLog(text="Build successful.", script_id=script_id)
            db.session.add(script)
            db.session.commit()
            return {"success": True}
        logger.error(f"[STATUS] Script {script_id} build FAIL: {log}")
        script.status = "FAIL"
        # Save error reason to ScriptLog and script object
        error_msg = str(log)
        script.build_error = error_msg if hasattr(script, "build_error") else None
        ScriptLog(text=f"Build failed: {error_msg}", script_id=script_id)
        db.session.add(script)
        db.session.commit()
        return {"success": False, "error": error_msg}


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
        Push image to private docker registry
        """
        import http.client
        import time

        import urllib3

        # Log node information for debugging
        hostname = socket.gethostname()
        logger.debug(f"Pushing image with tag {tag_image} from node {hostname}")

        # Check Docker Swarm node info if available
        try:
            client = get_docker_client()
            if client:
                swarm_info = client.info().get("Swarm", {})
                node_id = swarm_info.get("NodeID", "unknown")
                is_manager = swarm_info.get("ControlAvailable", False)
                logger.info(
                    f"Docker Swarm node info - ID: {node_id}, Manager: {is_manager}, "
                    f"Hostname: {hostname}"
                )
        except Exception as e:
            logger.warning(f"Could not get Docker Swarm info: {e}")

        max_retries = 3
        base_delay = 2
        attempt = 0
        last_error = None
        output_lines = []

        if not REGISTRY_URL:
            logger.error("REGISTRY_URL is not configured.")
            return False, Exception("REGISTRY_URL is not configured.")

        # Fast preflight to provide immediate diagnostics before attempting push
        ok, msg = _registry_preflight(REGISTRY_URL)
        if not ok:
            logger.warning(
                (
                    "Registry preflight failed for %s: %s. "
                    "Ensure this node can reach the registry and that it is "
                    "configured as insecure if using HTTP."
                ),
                REGISTRY_URL,
                msg,
            )
        else:
            logger.info("Registry preflight: %s", msg)

        # Capture pre-push manifest state to detect tag updates even if the
        # streaming push logs are ambiguous or truncated.
        repo_name, reference = _split_repo_ref(tag_image)
        pre_exists, pre_digest, pre_last_mod, pre_msg = _registry_get_manifest_digest(
            REGISTRY_URL, repo_name, reference
        )
        if pre_exists:
            # pre_msg already includes digest/last_modified details
            logger.info("Pre-push manifest state: %s", pre_msg)
        else:
            logger.info("Pre-push manifest state: %s", pre_msg)

        while attempt < max_retries:
            pushed = False
            output_lines.clear()
            blob_unknown_error = False
            try:
                client = get_docker_client()
                if client is None:
                    raise Exception("Docker client is not available.")

                push_tag = f"{REGISTRY_URL}/{tag_image}"
                logger.debug(
                    f"Attempting push {attempt + 1}/{max_retries} for {push_tag} "
                    f"from node {hostname}"
                )

                # Docker Engine 28+ compatibility fix for IncompleteRead errors
                try:
                    version_info = client.version()
                    engine_version = version_info.get("Version", "")

                    version_major = (
                        int(engine_version.split(".")[0])
                        if engine_version.split(".")[0].isdigit()
                        else 0
                    )
                    if version_major >= 28:
                        logger.debug(
                            f"Applying Docker Engine {engine_version} "
                            f"(v{version_major}+) registry compatibility fix"
                        )

                        # Force HTTP/1.1 for registry communication
                        try:
                            session = getattr(client.api, "_session", None)
                            if session:
                                session.headers.update(
                                    {
                                        "Connection": "close",
                                        "HTTP2-Settings": "",  # Disable HTTP/2
                                        "Upgrade": "",  # Disable protocol upgrade
                                    }
                                )
                                logger.debug(
                                    f"Applied Docker {version_major}+ "
                                    "HTTP/1.1 compatibility headers"
                                )
                        except Exception as http_fix_error:
                            logger.debug(
                                f"Could not apply HTTP fixes: {http_fix_error}"
                            )

                except Exception as version_check_error:
                    logger.debug(
                        f"Could not check Docker version: {version_check_error}"
                    )

                push_stream = client.images.push(push_tag, stream=True, decode=True)

                result_aux = None
                saw_digest = False
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
                    if line.get("aux"):
                        result_aux = line["aux"]
                        try:
                            digest_val = result_aux.get("Digest") or result_aux.get(
                                "digest"
                            )
                            if digest_val:
                                saw_digest = True
                        except Exception as aux_err:
                            logger.debug(
                                "Failed to parse aux digest during push: %s", aux_err
                            )

                    status_text = str(line.get("status", ""))
                    if status_text == "Pushed":
                        pushed = True
                    # Detect digest in textual status lines
                    st_lower = status_text.lower()
                    if "digest:" in st_lower:
                        saw_digest = True

                # Save all logs after push attempt
                for log_line in output_lines:
                    DockerService.save_build_log(script_id=script_id, line=log_line)

                if (pushed or saw_digest) and not blob_unknown_error:
                    logger.info(
                        f"Successfully pushed {push_tag} on attempt {attempt + 1}"
                    )
                    return True, result_aux or {"result": "ok"}

                # If we get here, the push failed
                if blob_unknown_error:
                    raise RuntimeError("Blob unknown error detected during push.")
                # Before raising, perform a post-push manifest verification in
                # case the daemon finished the push even though the stream was
                # truncated or lacked explicit success lines.
                post_exists, post_digest, post_last_mod, post_msg = (
                    _registry_get_manifest_digest(REGISTRY_URL, repo_name, reference)
                )
                if post_exists and (not pre_exists or post_digest != pre_digest):
                    if not pre_exists:
                        logger.info(
                            (
                                "Registry verification: %s; treating push as success "
                                "(created new manifest: %s)"
                            ),
                            post_msg,
                            post_digest,
                        )
                    else:
                        logger.info(
                            (
                                "Registry verification: %s; treating push as success "
                                "(updated digest: %s -> %s, last_modified=%s)"
                            ),
                            post_msg,
                            pre_digest,
                            post_digest,
                            post_last_mod,
                        )
                    # Save any collected logs for observability
                    for log_line in output_lines:
                        DockerService.save_build_log(script_id=script_id, line=log_line)
                    return True, {"digest": post_digest, "verified": True}
                if post_exists and post_digest == pre_digest:
                    logger.warning(
                        (
                            "Registry verification: %s; manifest digest unchanged "
                            "(%s). Treating push as failure per policy."
                        ),
                        post_msg,
                        post_digest,
                    )
                else:
                    # Post-push verification did not find a manifest; log details
                    logger.info(
                        (
                            "Registry verification: %s; manifest not present after "
                            "push attempt (pre_exists=%s, pre_digest=%s)"
                        ),
                        post_msg,
                        pre_exists,
                        pre_digest,
                    )
                raise Exception("Image push did not complete successfully.")

            except (
                docker_errors.APIError,
                urllib3.exceptions.ProtocolError,
                urllib3.exceptions.ReadTimeoutError,
                urllib3.exceptions.ConnectionError,
                http.client.IncompleteRead,
                http.client.HTTPException,
                ConnectionError,
                RuntimeError,  # Retry on blob unknown
            ) as error:
                last_error = error
                error_type = type(error).__name__
                logger.warning(
                    f"Push attempt {attempt + 1} failed with {error_type}: {error}"
                )

                # Provide actionable guidance for common registry/daemon issues
                import http.client as _http_client

                from urllib3.exceptions import (
                    ProtocolError as _Urllib3ProtocolError,  # type: ignore
                )

                if isinstance(
                    error, (_Urllib3ProtocolError, _http_client.IncompleteRead)
                ):
                    # Note: docker-py talks to the local daemon; the daemon then
                    # pushes to the registry. Client-side HTTP tweaks don't affect
                    # daemon->registry traffic.
                    logger.info(
                        (
                            "Hint: IncompleteRead/ProtocolError: daemon->registry link "
                            "likely closed early (proxy/HTTP2/insecure-registry). "
                            "Ensure: 1) daemon.json has insecure-registries ['%s'] and "
                            "Docker restarted; 2) any proxy uses HTTP/1.1 with "
                            "generous timeouts; 3) registry reachable (no "
                            "firewall/NAT idle-close)."
                        ),
                        REGISTRY_URL,
                    )

                # Save logs for this failed attempt
                for log_line in output_lines:
                    DockerService.save_build_log(script_id=script_id, line=log_line)

                # If this was a transport-level interruption, check the
                # registry to see if the manifest was actually created/updated.
                import http.client as _http_client

                from urllib3.exceptions import (
                    ProtocolError as _Urllib3ProtocolError,  # type: ignore
                )

                if isinstance(
                    error, (_Urllib3ProtocolError, _http_client.IncompleteRead)
                ):
                    post_exists, post_digest, post_last_mod, post_msg = (
                        _registry_get_manifest_digest(
                            REGISTRY_URL, repo_name, reference
                        )
                    )
                    if post_exists and (not pre_exists or post_digest != pre_digest):
                        if not pre_exists:
                            logger.info(
                                (
                                    "Registry verification after error: %s; treating "
                                    "push as success (created manifest: %s)"
                                ),
                                post_msg,
                                post_digest,
                            )
                        else:
                            logger.info(
                                (
                                    "Registry verification after error: %s; treating "
                                    "push as success (updated digest: %s -> %s, "
                                    "last_modified=%s)"
                                ),
                                post_msg,
                                pre_digest,
                                post_digest,
                                post_last_mod,
                            )
                        return True, {"digest": post_digest, "verified": True}
                    if post_exists and post_digest == pre_digest:
                        logger.warning(
                            (
                                "Registry verification after error: %s; manifest "
                                "digest unchanged (%s). Treating push as failure per "
                                "policy."
                            ),
                            post_msg,
                            post_digest,
                        )
                    else:
                        logger.info(
                            (
                                "Registry verification after error: %s; manifest not "
                                "present (pre_exists=%s, pre_digest=%s). Will retry if "
                                "attempts remain."
                            ),
                            post_msg,
                            pre_exists,
                            pre_digest,
                        )

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
                logger.error(f"Unexpected error during push: {error}")
                rollbar.report_exc_info()
                return False, error

        # Final verification before giving up: check if registry has the new
        # manifest digest compared to pre-push state.
        try:
            post_exists, post_digest, post_last_mod, post_msg = (
                _registry_get_manifest_digest(REGISTRY_URL, repo_name, reference)
            )
            if post_exists and (not pre_exists or post_digest != pre_digest):
                if not pre_exists:
                    logger.info(
                        (
                            "Final registry verification: %s; treating push as "
                            "success despite client errors (created manifest: %s)"
                        ),
                        post_msg,
                        post_digest,
                    )
                else:
                    logger.info(
                        (
                            "Final registry verification: %s; treating push as "
                            "success despite client errors (updated digest: %s -> %s, "
                            "last_modified=%s)"
                        ),
                        post_msg,
                        pre_digest,
                        post_digest,
                        post_last_mod,
                    )
                return True, {"digest": post_digest, "verified": True}
            if post_exists and post_digest == pre_digest:
                logger.warning(
                    (
                        "Final registry verification: %s; manifest digest unchanged "
                        "(%s). Treating push as failure per policy."
                    ),
                    post_msg,
                    post_digest,
                )
            else:
                logger.info(
                    (
                        "Final registry verification: %s; manifest not found. "
                        "Failing push."
                    ),
                    post_msg,
                )
        except Exception as _ver_err:
            logger.debug(f"Final registry verification failed: {_ver_err}")

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

            # Push the image (defaults to ':latest' if no tag provided)
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

                # Resolve execution and script safely
                exec_obj = Execution.query.get(execution_id)
                script = Script.query.get(exec_obj.script_id) if exec_obj else None

                client = get_docker_client()

                # Create network spec to connect to execution network for API access
                networks = []
                try:
                    # Autodetect execution network by matching environment
                    execution_network = None
                    current_env = os.getenv("ENVIRONMENT", "prod")

                    # Ensure client is available
                    if client is None:
                        raise Exception("Docker client is not available")

                    # Find the execution network for this environment
                    all_networks = client.networks.list()
                    for network in all_networks:
                        network_name = str(network.name)

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
                        raise docker_errors.NotFound(
                            f"No execution network found for environment: {current_env}"
                        )

                except docker_errors.NotFound:
                    logger.warning(
                        f"Execution network not found for environment "
                        f"{os.getenv('ENVIRONMENT', 'prod')}, execution "
                        "will use external API access"
                    )
                except Exception as e:
                    logger.warning(f"Failed to autodetect execution network: {e}")

                # Ensure Docker client is available (helps static analysis)
                if client is None:
                    raise Exception("Docker client is not available")

                create_kwargs = {
                    "image": f"{REGISTRY_URL}/{image}",
                    "command": "./entrypoint.sh",
                    "env": env,
                    "name": "execution-" + str(execution_id),
                    "labels": {
                        "execution.id": str(execution_id),
                        "service.type": "execution",
                        "managed.by": "trends.earth-api",
                    },
                    "networks": networks,
                    "restart_policy": docker_types.RestartPolicy(
                        condition="on-failure", delay=60, max_attempts=2, window=7200
                    ),
                }

                # Include script-specific labels/resources if available
                if script and getattr(script, "id", None):
                    create_kwargs["labels"]["execution.script_id"] = str(script.id)
                if script and all(
                    getattr(script, attr, None) is not None
                    for attr in (
                        "cpu_reservation",
                        "cpu_limit",
                        "memory_reservation",
                        "memory_limit",
                    )
                ):
                    create_kwargs["resources"] = docker_types.Resources(
                        cpu_reservation=script.cpu_reservation,
                        cpu_limit=script.cpu_limit,
                        mem_reservation=script.memory_reservation,
                        mem_limit=script.memory_limit,
                    )

                client.services.create(**create_kwargs)
            else:
                logger.info(
                    "Creating container (running in "
                    f"{os.getenv('ENVIRONMENT')} environment, with image "
                    f"{REGISTRY_URL}/{image}, as execution "
                    f"execution-{str(execution_id)})",
                )
                client = get_docker_client()
                if client is None:
                    raise Exception("Docker client is not available")
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
