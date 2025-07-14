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

EC2 Instances
    ├── Production Server
    │   ├── Docker Swarm Manager
    │   ├── Application: /opt/trends-earth-api
    │   └── Stack: trends-earth-prod
    └── Staging Server
        ├── Docker Swarm Manager
        ├── Application: /opt/trends-earth-api-staging
        └── Stack: trends-earth-staging
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

## Quick Start

### 1. Server Setup

Run this on each deployment server (production and staging):

```bash
# Clone the repository
git clone https://github.com/ConservationInternational/trends.earth-API.git
cd trends.earth-API

# Make scripts executable
chmod +x scripts/deployment/*.sh

# Setup Docker Swarm and secrets
./scripts/deployment/setup-docker-swarm.sh
```

### 2. GitHub Secrets Setup

Run this on your local machine:

```bash
# Ensure you're in the repository directory
cd trends.earth-API

# Setup GitHub secrets
./scripts/deployment/setup-github-secrets.sh
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

The following secrets will be configured automatically:

#### Repository Secrets (shared)
- `DOCKER_REGISTRY` - Docker registry URL
- `DOCKER_USERNAME` - Registry username
- `DOCKER_PASSWORD` - Registry password

#### Production Environment Secrets
- `PROD_HOST` - Production server IP/hostname
- `PROD_USERNAME` - SSH username
- `PROD_SSH_KEY` - SSH private key
- `PROD_SSH_PORT` - SSH port (default: 22)
- `PROD_APP_PATH` - Application directory path

#### Staging Environment Secrets
- `STAGING_HOST` - Staging server IP/hostname
- `STAGING_USERNAME` - SSH username
- `STAGING_SSH_KEY` - SSH private key
- `STAGING_SSH_PORT` - SSH port (default: 22)
- `STAGING_APP_PATH` - Application directory path

## Deployment Workflows

### Production Deployment (`.github/workflows/deploy-production.yml`)

Triggers on:
- Push to `master` branch
- Manual workflow dispatch

Process:
1. Build Docker image
2. Push to registry
3. Deploy to production server via SSH
4. Run health checks
5. Send notifications

### Staging Deployment (`.github/workflows/deploy-staging.yml`)

Triggers on:
- Push to `staging` or `develop` branches
- Closed pull requests to `staging`
- Manual workflow dispatch

Process:
1. Build Docker image
2. Push to registry
3. Deploy to staging server via SSH
4. Run health checks
5. Run integration tests
6. Send notifications

## Monitoring and Troubleshooting

### Service Status
```bash
# List Docker services
docker service ls

# Check specific service logs
docker service logs trends-earth-prod_manager
docker service logs trends-earth-staging_manager

# Check service details
docker service inspect trends-earth-prod_manager
```

### Health Checks
```bash
# Production health check
curl http://localhost:3001/api-health

# Staging health check
curl http://localhost:3002/api-health
```

### Update Services
```bash
# Force service update (production)
docker service update --force trends-earth-prod_manager

# Force service update (staging)
docker service update --force trends-earth-staging_manager
```

### Rollback

#### Automatic Rollback
The production deployment includes automatic rollback capabilities:
- **Health Check Failures**: If health checks fail after deployment, services automatically rollback to the previous version
- **Service Failures**: Docker Swarm monitors services and can automatically rollback on failure (configured with `failure_action: rollback`)
- **Update Monitoring**: Services are monitored for 60 seconds after updates with automatic rollback on failure

#### Manual Rollback Options

**Quick Manual Rollback:**
```bash
# Rollback specific service to previous version
docker service rollback trends-earth-prod_manager

# Rollback all services
docker service rollback trends-earth-prod_manager
docker service rollback trends-earth-prod_worker  
docker service rollback trends-earth-prod_beat
```

**Using the Rollback Script:**
```bash
# Interactive rollback with confirmation
./scripts/deployment/rollback-production.sh

# Force rollback without confirmation
./scripts/deployment/rollback-production.sh -f

# Rollback specific service only
./scripts/deployment/rollback-production.sh -s manager

# Dry run to see what would happen
./scripts/deployment/rollback-production.sh --dry-run
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

## 🧪 Staging Environment

The staging environment includes a complete database setup with:

- **PostgreSQL Database**: Dedicated staging database container
- **Production Data**: Recent scripts (past year) copied from production
- **Test Users**: Pre-configured test accounts with different roles
- **Automated Testing**: Comprehensive integration tests

### Staging Features

- **Database Isolation**: Separate staging database (port 5433)
- **Test Users**: 
  - Superadmin: `test-superadmin@example.com`
  - Admin: `test-admin@example.com`
  - User: `test-user@example.com`
- **Data Migration**: Automatic copying of recent production scripts
- **Script Ownership**: All scripts owned by test superadmin for consistent testing

For detailed staging setup information, see [Staging Database Guide](staging-database.md).
