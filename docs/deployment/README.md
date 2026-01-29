# Deployment Documentation

> ⚠️ **DEPRECATED**: This directory contains legacy SSH-based deployment documentation. 
> The current deployment system uses **AWS CodeDeploy** for more secure and reliable deployments.

## Current Deployment Guide

See the main deployment documentation at:

**[📖 CodeDeploy Setup Guide](../CODEDEPLOY_SETUP.md)**

This guide covers:
- AWS infrastructure setup (OIDC, ECR, CodeDeploy)
- GitHub secrets configuration
- EC2 instance setup
- Deployment workflows
- Monitoring and troubleshooting
- Rollback procedures

## Quick Reference

| Environment | Trigger | Port | Workflow |
|-------------|---------|------|----------|
| Production | Push to `master` | 3001 | `codedeploy_production.yml` |
| Staging | Push to `staging`/`develop` | 3002 | `codedeploy_staging.yml` |

## Key Differences from Legacy Deployment

| Feature | Legacy (SSH) | Current (CodeDeploy) |
|---------|--------------|---------------------|
| Authentication | SSH keys + AWS access keys | OIDC (no long-lived credentials) |
| Image Registry | Self-hosted registry | AWS ECR |
| Deployment Agent | SSH commands | CodeDeploy agent |
| Build Caching | None | Docker buildx with GitHub Actions cache |
| Rollback | Manual via SSH | Automatic on failure |

## Legacy Documentation (Archived)

The following files are kept for historical reference but are no longer maintained:

- [ROLLBACK-GUIDE.md](ROLLBACK-GUIDE.md) - Legacy rollback procedures (SSH-based)
- [github-secrets.md](github-secrets.md) - Legacy secrets configuration (SSH-based)
- [staging-database.md](staging-database.md) - Staging database setup (still relevant)

## Scripts

The scripts in `scripts/deployment/` are still useful for manual testing:

- `run-integration-tests.sh` - API integration tests
- `validate-environment.sh` - Environment variable validation
