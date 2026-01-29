#!/bin/bash
#
# CodeDeploy Agent Installation Script
#
# This script installs the AWS CodeDeploy agent on an EC2 instance.
# It supports Ubuntu/Debian and Amazon Linux/RHEL.
#
# Usage:
#   chmod +x install-codedeploy-agent.sh
#   sudo ./install-codedeploy-agent.sh [REGION]
#
# Example:
#   sudo ./install-codedeploy-agent.sh us-east-1
#

set -e

REGION="${1:-us-east-1}"

echo "========================================"
echo "CodeDeploy Agent Installation Script"
echo "========================================"
echo "Region: $REGION"
echo ""

# Detect OS
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VERSION=$VERSION_ID
    elif [ -f /etc/redhat-release ]; then
        OS="rhel"
    elif [ -f /etc/debian_version ]; then
        OS="debian"
    else
        echo "❌ Unsupported operating system"
        exit 1
    fi
    echo "📋 Detected OS: $OS $VERSION"
}

# Install on Ubuntu/Debian
install_debian() {
    echo "📋 Installing CodeDeploy agent for Ubuntu/Debian..."
    
    # Install dependencies
    apt-get update -qq
    apt-get install -y -qq ruby-full wget > /dev/null
    
    # Download and install agent
    cd /tmp
    wget -q "https://aws-codedeploy-${REGION}.s3.${REGION}.amazonaws.com/latest/install"
    chmod +x ./install
    ./install auto > /dev/null
    
    # Clean up
    rm -f /tmp/install
}

# Install on Amazon Linux/RHEL/CentOS
install_rhel() {
    echo "📋 Installing CodeDeploy agent for Amazon Linux/RHEL..."
    
    # Install dependencies
    yum install -y -q ruby wget
    
    # Download and install agent
    cd /tmp
    wget -q "https://aws-codedeploy-${REGION}.s3.${REGION}.amazonaws.com/latest/install"
    chmod +x ./install
    ./install auto > /dev/null
    
    # Clean up
    rm -f /tmp/install
}

# Check if agent is already installed
check_existing() {
    if systemctl is-active --quiet codedeploy-agent 2>/dev/null; then
        echo "ℹ️  CodeDeploy agent is already installed and running"
        codedeploy-agent --version 2>/dev/null || true
        return 0
    fi
    return 1
}

# Start and enable the agent
start_agent() {
    echo "📋 Starting CodeDeploy agent..."
    
    systemctl enable codedeploy-agent
    systemctl start codedeploy-agent
    
    # Wait for agent to start
    sleep 3
    
    if systemctl is-active --quiet codedeploy-agent; then
        echo "✅ CodeDeploy agent is running"
    else
        echo "❌ Failed to start CodeDeploy agent"
        systemctl status codedeploy-agent
        exit 1
    fi
}

# Show agent status
show_status() {
    echo ""
    echo "📋 Agent Status:"
    systemctl status codedeploy-agent --no-pager
    echo ""
    echo "📋 Agent Version:"
    codedeploy-agent --version 2>/dev/null || echo "Unknown"
}

# Main
main() {
    # Check if running as root
    if [ "$EUID" -ne 0 ]; then
        echo "❌ Please run as root (use sudo)"
        exit 1
    fi
    
    # Check if already installed
    if check_existing; then
        echo "ℹ️  Use 'sudo systemctl restart codedeploy-agent' to restart if needed"
        exit 0
    fi
    
    # Detect OS and install
    detect_os
    
    case $OS in
        ubuntu|debian)
            install_debian
            ;;
        amzn|amazon|rhel|centos|fedora)
            install_rhel
            ;;
        *)
            echo "❌ Unsupported OS: $OS"
            echo "   Supported: Ubuntu, Debian, Amazon Linux, RHEL, CentOS"
            exit 1
            ;;
    esac
    
    # Start the agent
    start_agent
    
    # Show status
    show_status
    
    echo ""
    echo "========================================"
    echo "✅ CodeDeploy Agent Installation Complete!"
    echo "========================================"
    echo ""
    echo "📋 Next Steps:"
    echo "1. Ensure the EC2 instance has the correct IAM role attached"
    echo "2. Tag the instance for the deployment group:"
    echo "   - Production: CodeDeploy-TrendsEarth-Production=true"
    echo "   - Staging: CodeDeploy-TrendsEarth-Staging=true"
    echo ""
    echo "📋 Useful Commands:"
    echo "   View logs: tail -f /var/log/aws/codedeploy-agent/codedeploy-agent.log"
    echo "   Check status: systemctl status codedeploy-agent"
    echo "   Restart agent: sudo systemctl restart codedeploy-agent"
    echo ""
}

main
