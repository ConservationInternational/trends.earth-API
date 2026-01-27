#!/usr/bin/env python3
"""
Deployment Utilities for Trends.Earth API

This module provides utility functions for deployment workflows to reduce
code duplication across GitHub Actions YAML files.

Security note: This file intentionally uses subprocess calls with shell commands
for deployment automation. S603 and S607 warnings are expected and acceptable.
"""
# ruff: noqa: S603, S607

import argparse
import json
import logging
from pathlib import Path
import subprocess
import sys
import time

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DeploymentUtils:
    """Utility class for deployment operations."""

    def __init__(self, registry: str, image_name: str, app_path: str = None):
        """Initialize deployment utilities.

        Args:
            registry: Docker registry URL (e.g., 'registry.example.com:5000')
            image_name: Docker image name (e.g., 'trendsearth-api')
            app_path: Application path on server (default: current directory)
        """
        self.registry = registry
        self.image_name = image_name
        self.app_path = Path(app_path) if app_path else Path.cwd()

    def clean_git_workspace(self, branch: str = None) -> bool:
        """Clean git workspace and optionally checkout a specific branch.

        Args:
            branch: Branch to checkout (optional)

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logger.info("🧹 Cleaning git workspace...")

            # Clean untracked files and directories
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=self.app_path,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("✅ Cleaned untracked files")

            # Reset to clean state
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                cwd=self.app_path,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("✅ Reset to clean state")

            if branch:
                # Fetch latest changes
                subprocess.run(
                    ["git", "fetch", "origin"],
                    cwd=self.app_path,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Checkout and reset to origin branch
                subprocess.run(
                    ["git", "checkout", branch],
                    cwd=self.app_path,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                subprocess.run(
                    ["git", "reset", "--hard", f"origin/{branch}"],
                    cwd=self.app_path,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                logger.info(f"✅ Checked out and reset to origin/{branch}")

            # Get current branch and commit
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.app_path,
                capture_output=True,
                text=True,
            )
            current_branch = branch_result.stdout.strip()

            commit_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.app_path,
                capture_output=True,
                text=True,
            )
            current_commit = commit_result.stdout.strip()[:7]

            logger.info(f"✅ Currently on branch: {current_branch}")
            logger.info(f"✅ Current commit: {current_commit}")
            logger.info("✅ Workspace cleaned and reset to latest commit")

            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Git workspace cleaning failed: {e}")
            logger.error(f"Command output: {e.stdout}")
            logger.error(f"Command error: {e.stderr}")
            return False

    def configure_docker_registry(self) -> bool:
        """Configure Docker daemon and client for insecure registry.

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logger.info("📋 Configuring insecure registry on server...")

            # Create Docker daemon configuration
            daemon_config = {"insecure-registries": [self.registry]}
            daemon_config_path = Path("/etc/docker/daemon.json")

            # Create /etc/docker directory if it doesn't exist
            subprocess.run(
                ["sudo", "mkdir", "-p", "/etc/docker"], check=True, capture_output=True
            )

            # Check if daemon.json exists and contains insecure-registries
            config_exists = False
            if daemon_config_path.exists():
                try:
                    result = subprocess.run(
                        ["grep", "-q", "insecure-registries", str(daemon_config_path)],
                        capture_output=True,
                    )
                    config_exists = result.returncode == 0
                except subprocess.CalledProcessError:
                    config_exists = False

            if not config_exists:
                # Write daemon configuration
                config_json = json.dumps(daemon_config)
                subprocess.run(
                    ["sudo", "tee", str(daemon_config_path)],
                    input=config_json,
                    text=True,
                    check=True,
                    capture_output=True,
                )

                # Restart Docker daemon
                subprocess.run(
                    ["sudo", "systemctl", "restart", "docker"],
                    check=True,
                    capture_output=True,
                )

                logger.info("⏳ Waiting for Docker daemon to restart...")
                time.sleep(10)

            # Configure Docker client
            docker_dir = Path.home() / ".docker"
            docker_dir.mkdir(exist_ok=True)

            client_config = {"insecure-registries": [self.registry]}
            client_config_path = docker_dir / "config.json"

            with open(client_config_path, "w") as f:
                json.dump(client_config, f, indent=2)

            logger.info("✅ Docker registry configuration completed")
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Docker registry configuration failed: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error in Docker registry configuration: {e}")
            return False

    def clean_docker_build_cache(self) -> bool:
        """Clean Docker build cache for fresh builds.

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logger.info("🧹 Cleaning Docker build cache...")

            subprocess.run(
                ["docker", "builder", "prune", "-f"], check=True, capture_output=True
            )

            logger.info("✅ Docker build cache cleaned")
            return True

        except subprocess.CalledProcessError as e:
            logger.warning(f"⚠️ Docker build cache cleaning failed (non-critical): {e}")
            return False  # Non-critical failure

    def build_and_push_image(
        self, tags: list[str], commit_sha: str = None, no_cache: bool = True
    ) -> bool:
        """Build and push Docker image with specified tags.

        Args:
            tags: List of image tags to build and push
            commit_sha: Git commit SHA for tagging (optional)
            no_cache: Whether to build without cache

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logger.info("🔨 Building Docker image...")

            if not tags:
                logger.error("❌ No tags specified for image build")
                return False

            # Clean build cache if requested
            if no_cache:
                self.clean_docker_build_cache()

            # Build with the first tag (primary tag)
            primary_tag = tags[0]
            full_primary_tag = f"{self.registry}/{self.image_name}:{primary_tag}"

            logger.info(f"Building with primary tag: {full_primary_tag}")

            build_args = ["docker", "build"]
            if no_cache:
                build_args.extend(["--no-cache", "--pull"])
            build_args.extend(["-t", full_primary_tag, "."])

            cache_mode = "--no-cache" if no_cache else "cached"
            logger.info(f"Using {cache_mode} build to ensure fresh migration files")

            subprocess.run(build_args, cwd=self.app_path, check=True)

            # Tag with additional tags
            full_tags = []
            for tag in tags:
                full_tag = f"{self.registry}/{self.image_name}:{tag}"
                full_tags.append(full_tag)

                if full_tag != full_primary_tag:
                    logger.info(f"Tagging with: {full_tag}")
                    subprocess.run(
                        ["docker", "tag", full_primary_tag, full_tag],
                        check=True,
                        capture_output=True,
                    )

            # Push all tags to registry
            logger.info("🚀 Pushing images to registry...")
            for full_tag in full_tags:
                logger.info(f"Pushing: {full_tag}")
                subprocess.run(
                    ["docker", "push", full_tag], check=True, capture_output=True
                )

            logger.info("✅ Image build and push completed!")
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Docker build/push failed: {e}")
            return False

    def check_service_health(
        self,
        port: int,
        path: str = "/api-health",
        max_attempts: int = 30,
        wait_seconds: int = 10,
    ) -> bool:
        """Check service health via HTTP endpoint.

        Args:
            port: Port to check
            path: Health check path
            max_attempts: Maximum number of attempts
            wait_seconds: Seconds to wait between attempts

        Returns:
            bool: True if healthy, False otherwise
        """
        logger.info("🏥 Performing health check...")

        for attempt in range(1, max_attempts + 1):
            logger.info(f"⏳ Health check attempt {attempt}/{max_attempts}...")

            try:
                # Check if port is listening using netcat
                nc_result = subprocess.run(
                    ["nc", "-z", "127.0.0.1", str(port)], capture_output=True
                )

                if nc_result.returncode == 0:
                    logger.info(f"✅ Port {port} is listening")
                else:
                    logger.info(f"⚠️ Port {port} is not listening yet")

                # Perform health check request
                url = f"http://127.0.0.1:{port}{path}"
                curl_result = subprocess.run(
                    ["curl", "-f", "-s", "-w", "HTTP_CODE:%{http_code}", url],
                    capture_output=True,
                    text=True,
                )

                if curl_result.returncode == 0:
                    logger.info("✅ Health check passed")
                    logger.info(f"Response: {curl_result.stdout}")
                    return True
                logger.info(
                    f"⏳ Health check failed with exit code {curl_result.returncode}"
                )
                logger.info(f"Response: {curl_result.stdout}")

            except Exception as e:
                logger.info(f"⏳ Health check attempt failed: {e}")

            if attempt < max_attempts:
                time.sleep(wait_seconds)

        logger.error(f"❌ Health check failed after {max_attempts} attempts")
        return False

    def wait_for_services_ready(self, stack_name: str, max_wait: int = 120) -> bool:
        """Wait for Docker Swarm services to be ready.

        This method checks that all services have converged (update completed)
        and have the correct number of running replicas. It properly handles:
        - One-shot services like 'migrate' that run and exit
        - Services with multiple replicas (e.g., api with 2/2)
        - Swarm rolling updates by checking UpdateStatus

        Args:
            stack_name: Docker stack name
            max_wait: Maximum wait time in seconds

        Returns:
            bool: True if services are ready, False otherwise
        """
        logger.info("📊 Waiting for Swarm services to converge...")

        # Services that run once and exit (one-shot tasks)
        one_shot_services = ["migrate"]

        wait_time = 0

        while wait_time < max_wait:
            try:
                # Get service list with replicas in JSON format for proper parsing
                result = subprocess.run(
                    [
                        "docker",
                        "service",
                        "ls",
                        "--filter",
                        f"name={stack_name}",
                        "--format",
                        "{{.Name}}\t{{.Replicas}}",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                lines = result.stdout.strip().split("\n")
                pending_services = []
                all_ready = True

                for line in lines:
                    if not line.strip():
                        continue

                    parts = line.split("\t")
                    if len(parts) != 2:
                        continue

                    service_name, replicas = parts
                    # Extract just the service suffix (e.g., "api" from "stack_api")
                    service_suffix = service_name.replace(f"{stack_name}_", "")

                    # Skip one-shot services - they run and exit
                    if service_suffix in one_shot_services:
                        continue

                    # Parse replicas (e.g., "2/2" -> running=2, desired=2)
                    try:
                        running, desired = replicas.split("/")
                        running = int(running)
                        desired = int(desired)

                        if running != desired:
                            pending_services.append(
                                f"{service_suffix}: {running}/{desired}"
                            )
                            all_ready = False
                    except ValueError:
                        pending_services.append(f"{service_suffix}: {replicas}")
                        all_ready = False

                # Also check Swarm update status to ensure rolling updates are complete
                update_status_ok = self._check_swarm_update_status(
                    stack_name, one_shot_services
                )

                if all_ready and update_status_ok:
                    # Verify migrate service completed successfully
                    migrate_ok = self._check_one_shot_service_status(
                        f"{stack_name}_migrate"
                    )
                    if migrate_ok:
                        logger.info("✅ All services converged and running")
                        return True
                    logger.warning("⚠️ Migrate service may have failed")
                    # Continue anyway - the health check will catch issues
                    return True

                logger.info(
                    f"⏳ Waiting for services to converge... "
                    f"({wait_time}/{max_wait} seconds)"
                )
                if pending_services:
                    logger.info(f"Pending: {', '.join(pending_services)}")
                if not update_status_ok:
                    logger.info("Update still in progress...")

                time.sleep(10)
                wait_time += 10

            except subprocess.CalledProcessError as e:
                logger.warning(f"⚠️ Failed to check service status: {e}")
                time.sleep(10)
                wait_time += 10

        logger.warning(
            f"⚠️ Some services may not be fully ready after {max_wait} seconds"
        )
        return False

    def _check_swarm_update_status(
        self, stack_name: str, exclude_services: list
    ) -> bool:
        """Check if all Swarm service updates have completed.

        Args:
            stack_name: Docker stack name
            exclude_services: Service names to exclude from check

        Returns:
            bool: True if all updates are complete, False if still updating
        """
        try:
            # Get all services in the stack
            result = subprocess.run(
                [
                    "docker",
                    "service",
                    "ls",
                    "--filter",
                    f"name={stack_name}",
                    "--format",
                    "{{.Name}}",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            services = [s.strip() for s in result.stdout.strip().split("\n") if s]

            for service in services:
                service_suffix = service.replace(f"{stack_name}_", "")
                if service_suffix in exclude_services:
                    continue

                # Inspect service to check UpdateStatus
                inspect_result = subprocess.run(
                    [
                        "docker",
                        "service",
                        "inspect",
                        service,
                        "--format",
                        "{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                update_state = inspect_result.stdout.strip()
                # Empty means no update in progress, "completed" means done
                if update_state and update_state not in ("completed", ""):
                    logger.debug(f"Service {service} update state: {update_state}")
                    return False

            return True

        except subprocess.CalledProcessError:
            # If we can't check, assume OK and let health check validate
            return True

    def _check_one_shot_service_status(self, service_name: str) -> bool:
        """Check if a one-shot service (like migrate) completed successfully.

        Args:
            service_name: Full service name (e.g., 'stack_migrate')

        Returns:
            bool: True if completed successfully or doesn't exist, False if failed
        """
        try:
            # Get tasks for this service
            result = subprocess.run(
                [
                    "docker",
                    "service",
                    "ps",
                    service_name,
                    "--format",
                    "{{.CurrentState}}",
                    "--filter",
                    "desired-state=shutdown",
                ],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                # Service might not exist, that's OK
                return True

            states = result.stdout.strip().split("\n")
            # Check if any task completed successfully
            for state in states:
                if "Complete" in state:
                    logger.info(f"✅ {service_name} completed successfully")
                    return True
                if "Failed" in state or "Rejected" in state:
                    logger.warning(f"⚠️ {service_name} task state: {state}")
                    return False

            # No completed tasks yet, but that's OK - it might still be running
            return True

        except subprocess.CalledProcessError:
            return True


def main():
    """Main function for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Deployment utilities for Trends.Earth API"
    )
    parser.add_argument("--registry", required=True, help="Docker registry URL")
    parser.add_argument(
        "--image-name", default="trendsearth-api", help="Docker image name"
    )
    parser.add_argument("--app-path", help="Application path on server")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Git cleanup command
    git_parser = subparsers.add_parser("clean-git", help="Clean git workspace")
    git_parser.add_argument("--branch", help="Branch to checkout after cleaning")

    # Docker registry configuration command
    subparsers.add_parser("configure-registry", help="Configure Docker registry")

    # Docker build command
    build_parser = subparsers.add_parser(
        "build-image", help="Build and push Docker image"
    )
    build_parser.add_argument(
        "--tags", required=True, nargs="+", help="Image tags to build"
    )
    build_parser.add_argument("--commit-sha", help="Git commit SHA")
    build_parser.add_argument(
        "--no-cache", action="store_true", default=True, help="Build without cache"
    )

    # Health check command
    health_parser = subparsers.add_parser("health-check", help="Check service health")
    health_parser.add_argument("--port", type=int, required=True, help="Port to check")
    health_parser.add_argument(
        "--path", default="/api-health", help="Health check path"
    )
    health_parser.add_argument(
        "--max-attempts", type=int, default=30, help="Maximum attempts"
    )

    # Service ready check command
    services_parser = subparsers.add_parser(
        "wait-services", help="Wait for services to be ready"
    )
    services_parser.add_argument(
        "--stack-name", required=True, help="Docker stack name"
    )
    services_parser.add_argument(
        "--max-wait", type=int, default=120, help="Maximum wait time"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Initialize deployment utils
    utils = DeploymentUtils(
        registry=args.registry, image_name=args.image_name, app_path=args.app_path
    )

    # Execute command
    success = False

    if args.command == "clean-git":
        success = utils.clean_git_workspace(branch=args.branch)

    elif args.command == "configure-registry":
        success = utils.configure_docker_registry()

    elif args.command == "build-image":
        success = utils.build_and_push_image(
            tags=args.tags, commit_sha=args.commit_sha, no_cache=args.no_cache
        )

    elif args.command == "health-check":
        success = utils.check_service_health(
            port=args.port, path=args.path, max_attempts=args.max_attempts
        )

    elif args.command == "wait-services":
        success = utils.wait_for_services_ready(
            stack_name=args.stack_name, max_wait=args.max_wait
        )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
