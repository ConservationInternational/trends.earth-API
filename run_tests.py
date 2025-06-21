#!/usr/bin/env python3
"""
Local test runner for Trends.Earth API
This script helps developers run tests locally before pushing to GitHub
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run_command(cmd, description, ignore_errors=False):
    """Run a command and handle errors"""
    print(f"\n🔄 {description}...")
    print(f"Running: {cmd}")

    try:
        result = subprocess.run(
            cmd, shell=True, check=True, capture_output=True, text=True
        )
        print(f"✅ {description} completed successfully")
        if result.stdout:
            print("Output:", result.stdout[-500:])  # Show last 500 chars
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed")
        if e.stdout:
            print("Output:", e.stdout[-500:])
        if e.stderr:
            print("Error:", e.stderr[-500:])

        if not ignore_errors:
            return False
        else:
            print("⚠️  Continuing despite errors...")
            return True


def check_dependencies():
    """Check if required dependencies are installed"""
    print("🔍 Checking dependencies...")

    # Check if pytest is installed
    try:
        import pytest

        print(f"✅ pytest {pytest.__version__} found")
    except ImportError:
        print("❌ pytest not found. Please run: pip install -r requirements-dev.txt")
        return False

    # Check if flask is installed
    try:
        import flask

        print(f"✅ Flask {flask.__version__} found")
    except ImportError:
        print("❌ Flask not found. Please run: pip install -r requirements.txt")
        return False

    return True


def setup_environment():
    """Set up environment variables for testing"""
    print("🔧 Setting up test environment...")

    env_vars = {
        "DATABASE_URL": "sqlite:///test.db",
        "REDIS_URL": "redis://localhost:6379/1",
        "JWT_SECRET_KEY": "test-secret-key",
        "FLASK_ENV": "testing",
        "TESTING": "true",
    }

    for key, value in env_vars.items():
        os.environ[key] = value
        print(f"  {key} = {value}")

    print("✅ Environment setup complete")


def main():
    parser = argparse.ArgumentParser(description="Run Trends.Earth API tests locally")
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument(
        "--integration", action="store_true", help="Run integration tests only"
    )
    parser.add_argument(
        "--validation", action="store_true", help="Run API validation tests only"
    )
    parser.add_argument(
        "--performance", action="store_true", help="Run performance tests"
    )
    parser.add_argument("--lint", action="store_true", help="Run linting only")
    parser.add_argument(
        "--coverage", action="store_true", help="Generate coverage report"
    )
    parser.add_argument("--fast", action="store_true", help="Skip slow tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--install-deps", action="store_true", help="Install dependencies first"
    )

    args = parser.parse_args()

    # Change to project directory
    project_root = Path(__file__).parent
    os.chdir(project_root)

    print("🚀 Trends.Earth API Test Runner")
    print(f"📁 Working directory: {os.getcwd()}")

    # Install dependencies if requested
    if args.install_deps:
        if not run_command(
            "pip install -r requirements.txt", "Installing main dependencies"
        ):
            return 1
        if not run_command(
            "pip install -r requirements-dev.txt", "Installing dev dependencies"
        ):
            return 1

    # Check dependencies
    if not check_dependencies():
        print("\n💡 Try running with --install-deps to install missing dependencies")
        return 1

    # Setup environment
    setup_environment()

    success = True

    # Run linting if requested or if no specific tests requested
    if args.lint or not any(
        [args.unit, args.integration, args.validation, args.performance]
    ):
        print("\n" + "=" * 50)
        print("🔍 RUNNING LINTING")
        print("=" * 50)

        linting_commands = [
            ("black --check --diff gefapi/ tests/", "Black formatter check", True),
            ("isort --check-only --diff gefapi/ tests/", "Import sorting check", True),
            (
                "flake8 gefapi/ tests/ --max-line-length=88 --extend-ignore=E203,W503",
                "Flake8 linting",
                True,
            ),
        ]

        for cmd, description, ignore_errors in linting_commands:
            if not run_command(cmd, description, ignore_errors):
                success = False

    # Build pytest command
    pytest_args = []
    if args.verbose:
        pytest_args.append("-v")
    if args.coverage:
        pytest_args.extend(["--cov=gefapi", "--cov-report=html", "--cov-report=term"])

    # Run specific test categories
    if args.unit:
        print("\n" + "=" * 50)
        print("🧪 RUNNING UNIT TESTS")
        print("=" * 50)
        cmd_args = ["pytest", "tests/"] + pytest_args
        if args.fast:
            cmd_args.extend(["-m", "not slow and not integration"])
        cmd = " ".join(cmd_args)
        if not run_command(cmd, "Unit tests"):
            success = False

    if args.integration:
        print("\n" + "=" * 50)
        print("🔗 RUNNING INTEGRATION TESTS")
        print("=" * 50)
        cmd_args = ["pytest", "tests/test_integration.py"] + pytest_args
        cmd = " ".join(cmd_args)
        if not run_command(cmd, "Integration tests"):
            success = False

    if args.validation:
        print("\n" + "=" * 50)
        print("✅ RUNNING API VALIDATION TESTS")
        print("=" * 50)
        cmd_args = ["pytest", "tests/test_api_validation.py"] + pytest_args
        cmd = " ".join(cmd_args)
        if not run_command(cmd, "API validation tests"):
            success = False

    if args.performance:
        print("\n" + "=" * 50)
        print("⚡ RUNNING PERFORMANCE TESTS")
        print("=" * 50)
        cmd_args = ["pytest", "tests/test_performance.py"] + pytest_args
        if args.fast:
            cmd_args.extend(["-m", "not slow"])
        cmd = " ".join(cmd_args)
        if not run_command(cmd, "Performance tests", ignore_errors=True):
            print("⚠️  Performance tests completed with issues")

    # If no specific category was requested, run all tests
    if not any(
        [args.unit, args.integration, args.validation, args.performance, args.lint]
    ):
        print("\n" + "=" * 50)
        print("🧪 RUNNING ALL TESTS")
        print("=" * 50)

        cmd_args = ["pytest", "tests/"] + pytest_args
        if args.fast:
            cmd_args.extend(["-m", "not slow"])
        cmd = " ".join(cmd_args)
        if not run_command(cmd, "All tests"):
            success = False

    # Print summary
    print("\n" + "=" * 50)
    print("📊 TEST SUMMARY")
    print("=" * 50)

    if success:
        print("✅ All tests passed! Ready to push to GitHub.")
    else:
        print("❌ Some tests failed. Please fix the issues before pushing.")

    print(f"\n📋 Test artifacts:")
    if os.path.exists("htmlcov/index.html"):
        print(f"  Coverage report: {os.path.abspath('htmlcov/index.html')}")
    if os.path.exists("test-report.html"):
        print(f"  Test report: {os.path.abspath('test-report.html')}")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
