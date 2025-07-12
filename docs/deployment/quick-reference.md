# Deployment Quick Reference

## 🚀 Quick Setup Commands

### Server Setup (Run on each deployment server)
```bash
# Clone repository
git clone https://github.com/ConservationInternational/trends.earth-API.git
cd trends.earth-API

# Setup Docker Swarm and secrets
chmod +x scripts/deployment/*.sh
./scripts/deployment/setup-docker-swarm.sh
```

### GitHub Secrets Setup (Run locally)
```bash
# Setup GitHub secrets
./scripts/deployment/setup-github-secrets.sh
```

### Test Deployment
```bash
# Test everything
./scripts/deployment/test-deployment.sh

# Test specific components
./scripts/deployment/test-deployment.sh --check-swarm
./scripts/deployment/test-deployment.sh --check-health
```

## 📋 Required Secrets

### Repository Secrets
- `DOCKER_REGISTRY`: `registry.company.com:5000`
- `DOCKER_USERNAME`: Registry username
- `DOCKER_PASSWORD`: Registry password
- `SLACK_WEBHOOK_URL`: (optional)

### Production Environment
- `PROD_HOST`: Server IP/hostname
- `PROD_USERNAME`: SSH username
- `PROD_SSH_KEY`: SSH private key
- `PROD_SSH_PORT`: SSH port (default: 22)
- `PROD_APP_PATH`: `/opt/trends-earth-api`

### Staging Environment  
- `STAGING_HOST`: Server IP/hostname
- `STAGING_USERNAME`: SSH username
- `STAGING_SSH_KEY`: SSH private key
- `STAGING_SSH_PORT`: SSH port (default: 22)
- `STAGING_APP_PATH`: `/opt/trends-earth-api-staging`

## 🔄 Deployment Triggers

### Production
- **Trigger**: Push to `master` branch
- **Port**: 3001
- **Stack**: `trends-earth-prod`
- **Image**: `registry.company.com:5000/company-api:latest`

### Staging
- **Trigger**: Push to `staging` or `develop` branch
- **Port**: 3002
- **Stack**: `trends-earth-staging`
- **Image**: `registry.company.com:5000/company-api-staging:staging`

## 🛠️ Common Commands

### Docker Swarm Management
```bash
# List services
docker service ls

# Check service logs
docker service logs trends-earth-prod_manager
docker service logs trends-earth-staging_manager

# Scale services
docker service scale trends-earth-prod_manager=3

# Update service
docker service update --force trends-earth-prod_manager

# Rollback service
docker service rollback trends-earth-prod_manager
```

### Stack Management
```bash
# Deploy stack
docker stack deploy -c docker-compose.prod.yml --with-registry-auth trends-earth-prod

# Remove stack
docker stack rm trends-earth-prod

# List stacks
docker stack ls

# List stack services
docker stack services trends-earth-prod
```

### Health Checks
```bash
# Production health
curl http://localhost:3001/api-health

# Staging health
curl http://localhost:3002/api-health

# Service health via Docker
docker service inspect trends-earth-prod_manager --format='{{json .Spec.TaskTemplate.ContainerSpec.Healthcheck}}'
```

### Secret Management
```bash
# List secrets
docker secret ls

# Create secret
echo "secret-value" | docker secret create secret-name -

# Remove secret
docker secret rm secret-name

# Inspect secret (metadata only)
docker secret inspect secret-name
```

## 🔍 Troubleshooting

### Deployment Fails
1. Check GitHub Actions logs
2. Verify SSH connectivity: `ssh user@server`
3. Check Docker Swarm status: `docker node ls`
4. Verify secrets: `docker secret ls`

### Service Won't Start
1. Check service logs: `docker service logs service-name`
2. Inspect service: `docker service inspect service-name`
3. Check environment variables and secrets
4. Verify image exists: `docker pull image-name`

### Health Check Fails
1. Check database connectivity
2. Verify Redis connection
3. Check environment variables
4. Review application logs

### SSH Issues
1. Test SSH key: `ssh -i key-file user@server`
2. Check key permissions: `chmod 600 key-file`
3. Verify key is in authorized_keys on server
4. Check SSH service is running on server

## 📁 File Structure

```
trends.earth-API/
├── .github/
│   └── workflows/
│       ├── deploy-production.yml
│       └── deploy-staging.yml
├── scripts/
│   └── deployment/
│       ├── setup-docker-swarm.sh
│       ├── setup-github-secrets.sh
│       └── test-deployment.sh
├── docs/
│   └── deployment/
│       ├── README.md
│       └── github-secrets.md
├── docker-compose.prod.yml
├── docker-compose.staging.yml
├── prod.env (create this)
└── staging.env (create this)
```

## 🔒 Security Checklist

- [ ] SSH keys are unique per environment
- [ ] Environment files are not in version control
- [ ] Registry credentials are secure
- [ ] Firewall rules are configured
- [ ] SSL/TLS is configured for production
- [ ] Database backups are automated
- [ ] Log retention is configured
- [ ] Monitoring and alerts are set up

## 🎯 Deployment Checklist

### Before First Deployment
- [ ] Docker Swarm initialized on servers
- [ ] Environment files created (prod.env, staging.env)
- [ ] GitHub secrets configured
- [ ] SSH keys deployed to servers
- [ ] Registry access configured
- [ ] Firewall rules configured

### For Each Deployment
- [ ] Test deployment script passes
- [ ] Database migrations ready (if needed)
- [ ] Health checks pass
- [ ] Monitoring shows services healthy
- [ ] Rollback plan ready

## 📞 Support

### Log Locations
- **GitHub Actions**: Repository → Actions tab
- **Docker Services**: `docker service logs service-name`
- **Application**: Check service logs for application-specific logs
- **System**: `/var/log/` on deployment servers

### Monitoring URLs
- **Production**: `http://your-prod-server:3001/api-health`
- **Staging**: `http://your-staging-server:3002/api-health`

### Key Files to Monitor
- Environment files (prod.env, staging.env)
- Docker Compose files
- GitHub workflow files
- SSH key files

## 🔧 Advanced Operations

### Manual Deployment
```bash
# Build and push image manually
docker build -t registry.company.com:5000/company-api:manual .
docker push registry.company.com:5000/company-api:manual

# Deploy manually
docker stack deploy -c docker-compose.prod.yml --with-registry-auth trends-earth-prod
```

### Database Operations
```bash
# Run migrations manually
docker service create --name temp-migrate \
  --env-file prod.env \
  --network backend \
  registry.company.com:5000/company-api:latest migrate

# Database backup
docker exec -it $(docker ps -q -f name=postgres) pg_dump -U user database > backup.sql
```

### Cleanup Operations
```bash
# Remove unused images
docker image prune -f

# Remove unused volumes
docker volume prune -f

# Remove unused networks
docker network prune -f

# Complete system cleanup
docker system prune -af
```
