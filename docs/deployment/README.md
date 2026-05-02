# Deployment Documentation

The primary deployment documentation is in the main docs directory:

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

## Additional Documentation

- [ROLLBACK-GUIDE.md](ROLLBACK-GUIDE.md) - Rollback procedures (production and staging)
- [staging-database.md](staging-database.md) - Staging database setup and test users

## Scripts

The scripts in `scripts/deployment/` are still useful for manual testing:

- `run-integration-tests.sh` - API integration tests
- `validate-environment.sh` - Environment variable validation
