#!/bin/bash

# Helper script to set up SSH keys for GitHub Actions deployment
# This script helps you add SSH private keys to GitHub secrets

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
GITHUB_OWNER="ConservationInternational"
GITHUB_REPO="trends.earth-API"

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

# Function to check prerequisites
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    # Check GitHub CLI
    if ! command -v gh &> /dev/null; then
        print_error "GitHub CLI is not installed. Please install it first."
        exit 1
    fi
    
    # Check if authenticated with GitHub
    if ! gh auth status &> /dev/null; then
        print_error "Not authenticated with GitHub CLI. Please run 'gh auth login' first."
        exit 1
    fi
    
    print_success "Prerequisites are met"
}

# Function to set SSH key from file
set_ssh_key_from_file() {
    local secret_name="$1"
    local description="$2"
    
    echo
    print_status "Setting up $description"
    echo -n "Enter path to SSH private key file: "
    read -r key_path
    
    if [[ ! -f "$key_path" ]]; then
        print_error "File not found: $key_path"
        return 1
    fi
    
    # Check if it looks like a private key
    if ! grep -q "BEGIN.*PRIVATE KEY" "$key_path"; then
        print_warning "File does not appear to be a private key. Continue anyway? (y/N): "
        read -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi
    
    # Set the secret
    gh secret set "$secret_name" < "$key_path" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    print_success "$description set successfully"
}

# Function to set SSH key from input
set_ssh_key_from_input() {
    local secret_name="$1"
    local description="$2"
    
    echo
    print_status "Setting up $description"
    echo "Please paste the SSH private key (press Ctrl+D when done):"
    
    # Read multi-line input
    key_content=$(cat)
    
    if [[ -z "$key_content" ]]; then
        print_error "No key content provided"
        return 1
    fi
    
    # Check if it looks like a private key
    if ! echo "$key_content" | grep -q "BEGIN.*PRIVATE KEY"; then
        print_warning "Content does not appear to be a private key. Continue anyway? (y/N): "
        read -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi
    
    # Set the secret
    echo "$key_content" | gh secret set "$secret_name" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    print_success "$description set successfully"
}

# Function to generate new SSH key pair
generate_ssh_key() {
    local env_name="$1"
    local host="$2"
    
    print_status "Generating new SSH key pair for $env_name"
    
    # Create keys directory
    mkdir -p ~/.ssh/github-actions-keys
    
    local key_path="$HOME/.ssh/github-actions-keys/github-actions-$env_name"
    
    # Generate key pair
    ssh-keygen -t ed25519 -f "$key_path" -N "" -C "github-actions-$env_name@trends.earth"
    
    print_success "SSH key pair generated:"
    echo "Private key: $key_path"
    echo "Public key: $key_path.pub"
    echo
    
    print_warning "IMPORTANT: You need to add the public key to the server!"
    echo "Public key content:"
    echo "----------------------------------------"
    cat "$key_path.pub"
    echo "----------------------------------------"
    echo
    echo "Add this key to ~/.ssh/authorized_keys on the server: $host"
    echo
    
    read -p "Press Enter after you've added the public key to the server..."
    
    # Set the private key as GitHub secret
    local secret_name="${env_name^^}_SSH_KEY"
    gh secret set "$secret_name" < "$key_path" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    print_success "Private key set as GitHub secret: $secret_name"
}

# Function to display main menu
show_menu() {
    echo
    echo -e "${BLUE}=== SSH Key Setup for GitHub Actions ===${NC}"
    echo "Choose an option:"
    echo "1. Set staging SSH key from file"
    echo "2. Set staging SSH key from input"
    echo "3. Set production SSH key from file"
    echo "4. Set production SSH key from input"
    echo "5. Generate new SSH key for staging"
    echo "6. Generate new SSH key for production"
    echo "7. Set all required SSH keys interactively"
    echo "8. Exit"
    echo
}

# Function to set all SSH keys interactively
set_all_keys() {
    print_status "Setting up all SSH keys..."
    
    # Get host information
    echo -n "Enter staging host IP/hostname: "
    read -r staging_host
    echo -n "Enter production host IP/hostname: "
    read -r prod_host
    
    echo
    echo "Choose how to set up SSH keys:"
    echo "1. Use existing SSH key files"
    echo "2. Generate new SSH key pairs"
    echo "3. Enter keys manually"
    echo -n "Choice (1-3): "
    read -r choice
    
    case $choice in
        1)
            set_ssh_key_from_file "STAGING_SSH_KEY" "staging SSH key"
            set_ssh_key_from_file "PROD_SSH_KEY" "production SSH key"
            ;;
        2)
            generate_ssh_key "staging" "$staging_host"
            generate_ssh_key "production" "$prod_host"
            ;;
        3)
            set_ssh_key_from_input "STAGING_SSH_KEY" "staging SSH key"
            set_ssh_key_from_input "PROD_SSH_KEY" "production SSH key"
            ;;
        *)
            print_error "Invalid choice"
            return 1
            ;;
    esac
    
    print_success "All SSH keys have been configured!"
}

# Main function
main() {
    check_prerequisites
    
    while true; do
        show_menu
        echo -n "Enter your choice (1-8): "
        read -r choice
        
        case $choice in
            1)
                set_ssh_key_from_file "STAGING_SSH_KEY" "staging SSH key"
                ;;
            2)
                set_ssh_key_from_input "STAGING_SSH_KEY" "staging SSH key"
                ;;
            3)
                set_ssh_key_from_file "PROD_SSH_KEY" "production SSH key"
                ;;
            4)
                set_ssh_key_from_input "PROD_SSH_KEY" "production SSH key"
                ;;
            5)
                echo -n "Enter staging host IP/hostname: "
                read -r staging_host
                generate_ssh_key "staging" "$staging_host"
                ;;
            6)
                echo -n "Enter production host IP/hostname: "
                read -r prod_host
                generate_ssh_key "production" "$prod_host"
                ;;
            7)
                set_all_keys
                ;;
            8)
                print_success "Goodbye!"
                exit 0
                ;;
            *)
                print_error "Invalid choice. Please try again."
                ;;
        esac
    done
}

# Run main function
main "$@"
