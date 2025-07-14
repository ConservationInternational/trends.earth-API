#!/bin/bash
# Docker Swarm Setup Script for Trends.Earth API Deployment
# This script sets up Docker Swarm and configures secrets for production/staging deployment

set -e

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

# Function to check if running as root
check_root() {
    if [[ $EUID -eq 0 ]]; then
        print_error "This script should not be run as root for security reasons."
        print_error "Please run as a regular user with sudo privileges."
        exit 1
    fi
}

# Function to check if Docker is installed
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install Docker first."
        print_error "Visit: https://docs.docker.com/engine/install/"
        exit 1
    fi
    
    if ! docker info &> /dev/null; then
        print_error "Docker daemon is not running or user doesn't have permission."
        print_error "Make sure Docker is running and user is in docker group:"
        print_error "sudo usermod -aG docker \$USER"
        exit 1
    fi
    
    print_success "Docker is installed and accessible"
}

# Function to initialize Docker Swarm
init_swarm() {
    print_status "Checking Docker Swarm status..."
    
    if docker info | grep -q "Swarm: active"; then
        print_success "Docker Swarm is already initialized"
        return 0
    fi
    
    print_status "Initializing Docker Swarm..."
    
    # Get the primary IP address
    LOCAL_IP=$(ip route get 8.8.8.8 | awk -F"src " 'NR==1{split($2,a," ");print a[1]}')
    
    if [[ -z "$LOCAL_IP" ]]; then
        print_warning "Could not detect IP address automatically"
        read -p "Enter the IP address for Swarm initialization: " LOCAL_IP
    fi
    
    docker swarm init --advertise-addr "$LOCAL_IP"
    print_success "Docker Swarm initialized with IP: $LOCAL_IP"
}

# Function to create Docker secrets
create_docker_secrets() {
    local env_type=$1
    local env_file=$2
    
    print_status "Creating Docker secrets for $env_type environment..."
    
    if [[ ! -f "$env_file" ]]; then
        print_error "Environment file $env_file not found!"
        print_error "Please create the environment file first."
        return 1
    fi
    
    # Create secrets from environment file
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        if [[ $key =~ ^#.*$ ]] || [[ -z "$key" ]]; then
            continue
        fi
        
        # Remove quotes from value if present
        value=$(echo "$value" | sed 's/^"//;s/"$//')
        
        secret_name="${env_type}_${key,,}" # Convert to lowercase
        
        # Check if secret already exists
        if docker secret inspect "$secret_name" &> /dev/null; then
            print_warning "Secret $secret_name already exists, skipping..."
            continue
        fi
        
        # Create the secret
        echo "$value" | docker secret create "$secret_name" -
        print_success "Created secret: $secret_name"
        
    done < "$env_file"
}

# Function to setup registry authentication
setup_registry_auth() {
    print_status "Setting up Docker registry authentication..."
    
    read -p "Enter Docker registry URL (e.g., your-registry.company.com:5000): " REGISTRY_URL
    read -p "Enter Docker registry username: " REGISTRY_USER
    read -s -p "Enter Docker registry password: " REGISTRY_PASS
    echo
    
    # Login to registry
    echo "$REGISTRY_PASS" | docker login "$REGISTRY_URL" -u "$REGISTRY_USER" --password-stdin
    print_success "Successfully logged into Docker registry"
    
    # Create registry auth secrets
    echo "$REGISTRY_URL" | docker secret create registry_url -
    echo "$REGISTRY_USER" | docker secret create registry_username -
    echo "$REGISTRY_PASS" | docker secret create registry_password -
    
    print_success "Registry authentication secrets created"
}

# Function to create application directories
setup_app_directories() {
    local env_type=$1
    local app_path="/opt/trends-earth-api"
    
    if [[ "$env_type" == "staging" ]]; then
        app_path="/opt/trends-earth-api-staging"
    fi
    
    print_status "Setting up application directory: $app_path"
    
    sudo mkdir -p "$app_path"
    sudo chown "$USER:$USER" "$app_path"
    
    # Clone repository if it doesn't exist
    if [[ ! -d "$app_path/.git" ]]; then
        print_status "Cloning repository..."
        git clone https://github.com/ConservationInternational/trends.earth-API.git "$app_path"
    fi
    
    print_success "Application directory setup complete: $app_path"
}

# Function to create systemd service for automatic deployment
create_deployment_service() {
    local env_type=$1
    
    print_status "Creating systemd service for $env_type deployment monitoring..."
    
    cat << EOF | sudo tee "/etc/systemd/system/trends-earth-${env_type}-deploy.service" > /dev/null
[Unit]
Description=Trends.Earth API ${env_type} Deployment Monitor
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=$USER
WorkingDirectory=/opt/trends-earth-api$([ "$env_type" = "staging" ] && echo "-staging" || echo "")
ExecStart=/bin/bash -c 'docker stack deploy -c docker-compose.${env_type}.yml --with-registry-auth trends-earth-${env_type}'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    
    sudo systemctl daemon-reload
    print_success "Systemd service created: trends-earth-${env_type}-deploy.service"
}

# Main execution
main() {
    print_status "Starting Docker Swarm setup for Trends.Earth API deployment"
    print_status "================================================================"
    
    check_root
    check_docker
    init_swarm
    
    # Setup production environment
    read -p "Setup production environment? (y/n): " setup_prod
    if [[ "$setup_prod" =~ ^[Yy]$ ]]; then
        setup_app_directories "production"
        
        if [[ -f "../../prod.env" ]]; then
            create_docker_secrets "prod" "../../prod.env"
        else
            print_warning "prod.env file not found. Please create it manually."
        fi
        
        create_deployment_service "prod"
    fi
    
    # Setup staging environment
    read -p "Setup staging environment? (y/n): " setup_staging
    if [[ "$setup_staging" =~ ^[Yy]$ ]]; then
        setup_app_directories "staging"
        
        if [[ -f "../../staging.env" ]]; then
            create_docker_secrets "staging" "../../staging.env"
        else
            print_warning "staging.env file not found. Please create it manually."
        fi
        
        create_deployment_service "staging"
    fi
    
    # Setup registry authentication
    read -p "Setup Docker registry authentication? (y/n): " setup_registry
    if [[ "$setup_registry" =~ ^[Yy]$ ]]; then
        setup_registry_auth
    fi
    
    print_success "Docker Swarm setup completed!"
    print_status "================================================================"
    print_status "Next steps:"
    print_status "1. Configure GitHub secrets (see docs/deployment/github-secrets.md)"
    print_status "2. Test deployment with: docker stack deploy -c docker-compose.prod.yml trends-earth-prod"
    print_status "3. Monitor services with: docker service ls"
    print_status "4. Check logs with: docker service logs trends-earth-prod_manager"
}

# Run main function
main "$@"
