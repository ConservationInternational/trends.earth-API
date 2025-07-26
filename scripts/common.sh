#!/bin/bash

# Common utilities for deployment scripts
# This file should be sourced by other scripts

# Configuration - Update these values for your setup
GITHUB_OWNER="ConservationInternational"
GITHUB_REPO="trends.earth-API"

#
# SECRET STRATEGY DOCUMENTATION
# =============================
# This project uses a hybrid approach for GitHub secrets:
#
# REPOSITORY-LEVEL SECRETS (shared across all environments):
# - AWS_ACCESS_KEY_ID
# - AWS_SECRET_ACCESS_KEY  
# - AWS_REGION
# - DOCKER_REGISTRY
# - DOCKER_HTTP_SECRET
#
# ENVIRONMENT-LEVEL SECRETS (staging/production specific):
# - STAGING_* secrets → staging environment
# - PROD_* secrets → production environment
# - Includes: SSH keys, hosts, usernames, ports, app paths, security group IDs
# - Database: STAGING_DB_*, PROD_DB_* 
# - Test users: TEST_*_EMAIL, TEST_*_PASSWORD (staging environment only)
#
# This ensures shared infrastructure secrets are reused while maintaining
# environment isolation for deployment-specific configurations.
#

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Function to check if GitHub CLI is installed and authenticated
check_github_cli() {
    if ! command -v gh &> /dev/null; then
        print_error "GitHub CLI is not installed. Please install it first."
        exit 1
    fi

    if ! gh auth status &> /dev/null; then
        print_error "Not authenticated with GitHub CLI. Please run 'gh auth login' first."
        exit 1
    fi
}

# Function to check if AWS CLI is installed and authenticated
check_aws_cli() {
    if ! command -v aws &> /dev/null; then
        print_error "AWS CLI is not installed. Please install it first."
        exit 1
    fi

    if ! aws sts get-caller-identity &> /dev/null; then
        print_error "Not authenticated with AWS. Please run 'aws configure' first."
        exit 1
    fi
}
