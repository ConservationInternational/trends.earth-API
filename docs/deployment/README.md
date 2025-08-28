# Deployment Setup Guide

This guide walks you through setting up automated deployment for the Trends.Earth API using GitHub Actions and Docker Swarm.

## Overview

The deployment system uses:
- **GitHub Actions** for CI/CD workflows
- **Docker Swarm** for container orchestration
- **EC2 instances** for hosting (production and staging)
- **Private Docker Registry** for image storage

## Architecture

```
GitHub Repository
    ├── Push to master → Production Deployment (port 3001)
    ├── Push to staging/develop → Staging Deployment (port 3002)
    └── Docker Images → Private Registry (registry.company.com:5000)

AWS Infrastructure
    ├── EC2 Security Groups (dynamic GitHub Actions runner access)
    ├── Automated security group rule management
    └── AWS credentials for deployment automation

EC2 Instances
    ├── Production Server
    │   ├── Docker Swarm Manager
    │   ├── Application: /opt/trends-earth-api
    │   └── Stack: trends-earth-prod
    └── Staging Server
        ├── Docker Swarm Manager  
        ├── Application: /opt/trends-earth-api-staging
        ├── Stack: trends-earth-staging
        └── Automated Database Setup (PostgreSQL + test data)
```

## Prerequisites

### Server Requirements
- Ubuntu 20.04+ or similar Linux distribution
- Docker 20.10+ installed
- Git installed
- Minimum 2GB RAM, 10GB disk space
- SSH access configured
- User with sudo privileges

### Development Requirements
- GitHub CLI (`gh`) installed
- SSH client
- Access to the Docker registry (registry.company.com:5000)
- AWS CLI configured (for security group management)
- Access to production database (for staging data import)

## Quick Start

### 1. Server Setup

Run this on each deployment server (production and staging):

```bash
# Clone the repository  
git clone https://github.com/ConservationInternational/trends.earth-API.git
cd trends.earth-API

# Make scripts executable
chmod +x scripts/*.sh
chmod +x scripts/deployment/*.sh

# Setup Docker and required dependencies
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Initialize Docker Swarm
docker swarm init

# Create application directories
sudo mkdir -p /opt/trends-earth-api
sudo mkdir -p /opt/trends-earth-api-staging
sudo chown -R $USER:$USER /opt/trends-earth-api*
```

### 2. GitHub Secrets Setup

Configure the required GitHub secrets manually in your repository settings:

```bash
# Go to: https://github.com/ConservationInternational/trends.earth-API/settings/secrets/actions

# Add all the secrets listed in the "GitHub Secrets" section above
# You can use the GitHub CLI to add secrets programmatically:

gh secret set AWS_ACCESS_KEY_ID --body "your-aws-key"
gh secret set AWS_SECRET_ACCESS_KEY --body "your-aws-secret"
gh secret set STAGING_HOST --body "your-staging-server-ip"
# ... add all other required secrets
```

### 3. Test Deployment

Push to the appropriate branch to trigger deployment:

```bash
# For staging deployment
git push origin staging

# For production deployment  
git push origin master
```

## Detailed Setup Instructions

### Server Preparation

1. **Update System**
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

2. **Install Docker**
   ```bash
   curl -fsSL https://get.docker.com -o get-docker.sh
   sudo sh get-docker.sh
   sudo usermod -aG docker $USER
   # Log out and back in for group changes to take effect
   ```

3. **Install Git**
   ```bash
   sudo apt install git -y
   ```

4. **Configure Firewall** (if using UFW)
   ```bash
   sudo ufw allow 22/tcp    # SSH
   sudo ufw allow 3001/tcp  # Production API
   sudo ufw allow 3002/tcp  # Staging API
   sudo ufw allow 2377/tcp  # Docker Swarm management
   sudo ufw allow 7946/tcp  # Docker Swarm communication
   sudo ufw allow 7946/udp  # Docker Swarm communication
   sudo ufw allow 4789/udp  # Docker overlay networks
   sudo ufw enable
   ```

### Docker Swarm Configuration

The setup script will:
1. Initialize Docker Swarm
2. Create Docker secrets from environment files
3. Setup registry authentication
4. Create application directories
5. Configure systemd services

### Environment Files

Create environment files in the repository root:

**prod.env** (for production):
```bash
DATABASE_URL=postgresql://user:pass@db-host:5432/trendsearth_prod
REDIS_URL=redis://redis-host:6379/0
JWT_SECRET_KEY=your-production-jwt-secret
ROLLBAR_SERVER_TOKEN=your-rollbar-token
# ... other production variables
```

**staging.env** (for staging):
```bash
DATABASE_URL=postgresql://user:pass@db-host:5432/trendsearth_staging
REDIS_URL=redis://redis-host:6379/1
JWT_SECRET_KEY=your-staging-jwt-secret
# ... other staging variables
```

### GitHub Secrets

The following secrets must be configured in your GitHub repository settings:

#### Repository Secrets (shared)
- `DOCKER_REGISTRY` - Docker registry URL (e.g., registry.company.com:5000)

#### AWS Infrastructure Secrets (required for both environments)
- `AWS_ACCESS_KEY_ID` - AWS access key for security group management
- `AWS_SECRET_ACCESS_KEY` - AWS secret key for security group management  
- `AWS_REGION` - AWS region (default: us-east-1)

#### Production Environment Secrets
- `PROD_HOST` - Production server IP/hostname
- `PROD_USERNAME` - SSH username for production server
- `PROD_SSH_KEY` - SSH private key for production server
- `PROD_SSH_PORT` - SSH port (default: 22)
- `PROD_APP_PATH` - Application directory path (default: /opt/trends-earth-api)
- `PROD_SECURITY_GROUP_ID` - AWS security group ID for production server

#### Staging Environment Secrets
- `STAGING_HOST` - Staging server IP/hostname
- `STAGING_USERNAME` - SSH username for staging server
- `STAGING_SSH_KEY` - SSH private key for staging server
- `STAGING_SSH_PORT` - SSH port (default: 22)
- `STAGING_APP_PATH` - Application directory path (default: /opt/trends-earth-api-staging)
- `STAGING_SECURITY_GROUP_ID` - AWS security group ID for staging server

#### Staging Database Secrets (for automated setup)
- `STAGING_DB_NAME` - Staging database name
- `STAGING_DB_USER` - Staging database user
- `STAGING_DB_PASSWORD` - Staging database password

#### Production Database Secrets (for staging data import)
- `PROD_DB_HOST` - Production database hostname
- `PROD_DB_PORT` - Production database port (default: 5432)
- `PROD_DB_NAME` - Production database name (default: trendsearth)
- `PROD_DB_USER` - Production database user
- `PROD_DB_PASSWORD` - Production database password

#### Test User Secrets (for staging environment)
- `TEST_SUPERADMIN_EMAIL` - Test superadmin email
- `TEST_SUPERADMIN_PASSWORD` - Test superadmin password
- `TEST_ADMIN_EMAIL` - Test admin email
- `TEST_ADMIN_PASSWORD` - Test admin password
- `TEST_USER_EMAIL` - Test user email
- `TEST_USER_PASSWORD` - Test user password

#### Monitoring Secrets (optional)
- `ROLLBAR_SERVER_ACCESS_TOKEN` - Rollbar access token for deployment notifications

## Deployment Workflows

### Production Deployment (`.github/workflows/deploy-production.yml`)

Triggers on:
- Push to `master` branch
- Manual workflow dispatch

Process:
1. AWS security group setup (dynamic runner IP access)
2. Build Docker image
3. Push to registry  
4. Deploy to production server via SSH
5. Run health checks with automatic rollback on failure
6. Notify Rollbar of deployment
7. Cleanup AWS security group rules

### Staging Deployment (`.github/workflows/deploy-staging.yml`)

Triggers on:
- Push to `staging` or `develop` branches
- Closed pull requests to `staging`
- Manual workflow dispatch

Process:
1. AWS security group setup (dynamic runner IP access)
2. Build Docker image with staging tags
3. Push to registry
4. Deploy to staging server via SSH with rolling updates
5. Wait for automated database migration and staging environment setup
6. Run comprehensive integration tests
7. Notify Rollbar of deployment
8. Cleanup AWS security group rules

### Production Rollback (`.github/workflows/rollback-production.yml`)

Triggers on:
- Manual workflow dispatch only

Required inputs:
- **Reason**: Explanation for the rollback (required)
- **Services**: Which services to rollback - `all` (default) or comma-separated list: `api,worker,beat,docker,redis`
- **Rollback to image**: Optional specific image tag to rollback to (uses service rollback history if not specified)

Process:
1. AWS security group setup (dynamic runner IP access)
2. Validate rollback parameters and service names
3. Connect to production server via SSH
4. Perform Docker service rollbacks for specified services
5. Wait for services to stabilize after rollback
6. Run health checks to verify rollback success
7. Run basic integration tests
8. Notify Rollbar of rollback event
9. Cleanup AWS security group rules

**Usage Examples:**
```bash
# Rollback all services to previous version
# Go to: Actions → Rollback Production Deployment → Run workflow
# Services: all
# Reason: "Health check failures after deployment"

# Rollback specific services only
# Services: api,worker
# Reason: "API performance issues"

# Rollback to specific image tag
# Services: all
# Rollback to image: master-abc1234
# Reason: "Revert to known good version"
```

## Monitoring and Troubleshooting

### Service Status
```bash
# List Docker services
docker service ls

# Check specific service logs
docker service logs trends-earth-prod_api
docker service logs trends-earth-staging_api

# Check service details
docker service inspect trends-earth-prod_api
```

### Health Checks
```bash
# Production health check (basic)
curl http://localhost:3001/api-health

# Staging health check (with version info for debugging)
curl http://localhost:3002/api-health

# Example response with commit SHA (staging):
# {
#   "status": "healthy",
#   "environment": "staging", 
#   "version": "a1b2c3d",
#   "timestamp": "2025-01-28T10:30:00Z"
# }

# Example response (production - minimal info):
# {
#   "status": "healthy",
#   "timestamp": "2025-01-28T10:30:00Z"
# }
```

### Update Services
```bash
# Force service update (production)
docker service update --force trends-earth-prod_api

# Force service update (staging)
docker service update --force trends-earth-staging_api
```

### Rollback

#### Automatic Rollback
The production deployment includes automatic rollback capabilities:
- **Health Check Failures**: If health checks fail after deployment, services automatically rollback to the previous version
- **Service Failures**: Docker Swarm monitors services and can automatically rollback on failure (configured with `failure_action: rollback`)
- **Update Monitoring**: Services are monitored for 60 seconds after updates with automatic rollback on failure

#### GitHub Actions Rollback (Recommended)

**Automated Rollback via GitHub Actions:**
The recommended way to perform production rollbacks is using the GitHub Actions workflow:

1. **Go to GitHub Actions**: Navigate to the repository's Actions tab
2. **Select Rollback Workflow**: Click "Rollback Production Deployment"
3. **Run Workflow**: Click "Run workflow" and fill in:
   - **Reason**: Required explanation for the rollback
   - **Services**: Choose "all" or specific services (e.g., "api,worker")
   - **Rollback to image**: Optional specific image tag (leaves blank for automatic rollback)
4. **Monitor Progress**: Watch the workflow execution for real-time updates

**Benefits of GitHub Actions Rollback:**
- ✅ **Automated health checks** and validation after rollback
- ✅ **Secure access** using existing AWS security groups and SSH keys
- ✅ **Audit trail** with detailed logs and notifications
- ✅ **Rollbar integration** for monitoring and alerting
- ✅ **Consistent environment** using the same infrastructure as deployments

#### Manual Rollback Options (Advanced)

**Quick Manual Rollback:**
```bash
# Rollback specific service to previous version
docker service rollback trends-earth-prod_api

# Rollback all services
docker service rollback trends-earth-prod_api
docker service rollback trends-earth-prod_worker  
docker service rollback trends-earth-prod_beat
```

**Manual Rollback with Service Inspection:**
```bash
# Check service update history and status
docker service inspect trends-earth-prod_api --format='{{json .UpdateStatus}}'

# If UpdateStatus is null, check the service spec for current image
docker service inspect trends-earth-prod_api --format='{{.Spec.TaskTemplate.ContainerSpec.Image}}'

# List all available image tags in registry (if accessible)
docker image ls | grep trendsearth-api

# Check service version history (shows recent tasks)
docker service ps trends-earth-prod_api --format "table {{.Name}}\t{{.Image}}\t{{.CurrentState}}\t{{.Error}}" --no-trunc

# Rollback to specific image version (replace with desired tag)
docker service update --image registry.company.com:5000/trendsearth-api:previous-tag trends-earth-prod_api

# Alternative: Rollback to previous version (if update history exists)
docker service rollback trends-earth-prod_manager
```

#### Rollback Configuration
Services are configured with:
- **Update Config**: `failure_action: rollback` for automatic rollback on deployment failure
- **Rollback Config**: Controls how rollbacks are performed (parallelism, monitoring)
- **Restart Policy**: Handles service restart failures with exponential backoff

## Security Considerations

### SSH Keys
- Use separate SSH keys for production and staging
- Store private keys securely in GitHub Secrets
- Regularly rotate SSH keys
- Use strong passphrases (handled automatically by the script)

### Docker Registry
- Use strong authentication for the Docker registry
- Consider using Harbor or similar for enhanced security
- Regularly scan images for vulnerabilities

### Environment Variables
- Never commit sensitive data to version control
- Use Docker secrets for sensitive configuration
- Regularly rotate secrets and tokens

### Network Security
- Use VPC/private networks where possible
- Implement proper firewall rules
- Consider using VPN for server access

## Advanced Configuration

### Custom Domains
To use custom domains, configure a reverse proxy (nginx) in front of the Docker services:

```nginx
server {
    listen 80;
    server_name api.company.com;
    
    location / {
        proxy_pass http://localhost:3001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### SSL/TLS
Use Let's Encrypt with nginx for SSL termination:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d api.company.com
```

### Database Backups
Implement automated database backups:

```bash
# Add to crontab
0 2 * * * pg_dump $DATABASE_URL | gzip > /backups/db-$(date +\%Y\%m\%d).sql.gz
```

### Log Management
Configure log rotation and centralized logging:

```yaml
# docker-compose override for logging
version: '3.8'
services:
  manager:
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "5"
```

## Troubleshooting

### Common Issues

**Deployment fails with "permission denied"**
- Check SSH key permissions: `chmod 600 ~/.ssh/private_key`
- Verify user has docker group membership: `groups $USER`

**Services fail to start**
- Check environment variables: `docker service inspect service_name`
- Verify Docker secrets: `docker secret ls`
- Check service logs: `docker service logs service_name`

**Health check failures**
- Verify database connectivity
- Check Redis connection
- Confirm environment variables are set correctly

**Registry authentication issues**
- Verify registry credentials: `docker login registry.company.com:5000`
- Check network connectivity to registry
- Confirm registry is running and accessible

### Getting Help

1. Check GitHub Actions logs for deployment failures
2. Review Docker service logs on the server
3. Verify server resources (disk space, memory)
4. Test network connectivity between services
5. Validate environment configuration

## Maintenance

### Regular Tasks
- Update base Docker images monthly
- Rotate SSH keys quarterly
- Review and update secrets
- Monitor server resources
- Update Docker and system packages

### Monitoring
- Setup alerts for service failures
- Monitor API response times
- Track deployment success rates
- Monitor server resources (CPU, memory, disk)

## References

- [Docker Swarm Documentation](https://docs.docker.com/engine/swarm/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [GitHub CLI Documentation](https://cli.github.com/manual/)
- [Docker Secrets Management](https://docs.docker.com/engine/swarm/secrets/)
- [AWS EC2 Security Groups](https://docs.aws.amazon.com/ec2/latest/userguide/ec2-security-groups.html)

## Project-Specific Documentation

- [Staging Database Setup Guide](staging-database.md) - Detailed staging environment documentation
- [Docker Swarm Multi-Node Guide](docker-swarm-multi-node.md) - Multi-node deployment specifics
- [Scripts Documentation](../scripts/deployment/README.md) - Available deployment scripts
- [Rate Limiting Documentation](../rate-limit-status-example.md) - API rate limiting features

## 🧪 Staging Environment

The staging environment provides a complete, isolated testing environment with:

### **Automated Setup Features**
- **PostgreSQL Database**: Dedicated staging database container (port 5433)
- **Database Migrations**: Automatic schema updates via migrate service
- **Production Data Import**: Recent scripts (past year) copied from production automatically
- **Test Users**: Pre-configured test accounts with proper password hashing
- **Script Ownership**: All scripts owned by test superadmin for consistent testing
- **Comprehensive Testing**: Full integration test suite

### **Staging Architecture**
```
Docker Swarm Stack (trends-earth-staging)
├── postgres service (PostgreSQL database)
├── migrate service (runs once: migrations + staging setup)
├── manager service (API server on port 3002)
├── worker service (Celery workers)
├── beat service (Celery scheduler)
└── redis service (Cache and message broker)
```

### **Test Users (Automatically Created)**
- **Superadmin**: Email from `TEST_SUPERADMIN_EMAIL` secret
- **Admin**: Email from `TEST_ADMIN_EMAIL` secret  
- **User**: Email from `TEST_USER_EMAIL` secret
- **Passwords**: From corresponding `TEST_*_PASSWORD` secrets

### **Automated Data Import**
The migrate service automatically:
1. Applies all database migrations
2. Creates test users with proper roles and password hashing
3. Imports recent production scripts (created/updated in past year)
4. Imports recent status logs (past month)
5. Imports script logs for imported scripts
6. Updates script ownership to test superadmin user
7. Verifies the complete setup

### **Monitoring Staging Setup**
```bash
# Check migrate service logs to monitor setup progress
docker service logs trends-earth-staging_migrate

# Check all staging services status
docker service ls --filter "name=trends-earth-staging"

# View staging database setup verification
docker service logs trends-earth-staging_migrate | grep "VERIFICATION RESULTS" -A 20
```
