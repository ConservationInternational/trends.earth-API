#!/bin/bash

# Script Access Control Management Tool for Admin Container
# This script provides an easy way to manage script access controls from the admin container

set -e

# Change to the gefapi directory where the Flask app is located
cd /opt/gef-api

# Set the Flask app environment variable
export FLASK_APP=main.py

# Run the script access management tool with all passed arguments
python scripts/manage_script_access.py "$@"
