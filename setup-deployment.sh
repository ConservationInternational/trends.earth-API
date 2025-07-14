#!/bin/bash

# Quick setup script to make all scripts executable and run the main setup

set -e

# Color codes for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Trends.Earth GitHub Actions Setup ===${NC}"
echo

# Make scripts executable
chmod +x scripts/setup-github-deployment.sh
chmod +x scripts/setup-ssh-keys.sh

echo -e "${GREEN}✅ Made scripts executable${NC}"
echo

echo "Available setup scripts:"
echo "1. ./scripts/setup-github-deployment.sh - Main deployment setup (IAM + GitHub secrets)"
echo "2. ./scripts/setup-ssh-keys.sh - SSH key management"
echo

echo "For detailed instructions, see: scripts/README.md"
echo

read -p "Do you want to run the main deployment setup now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    ./scripts/setup-github-deployment.sh
else
    echo "You can run the setup scripts manually when ready."
    echo "Start with: ./scripts/setup-github-deployment.sh"
fi
