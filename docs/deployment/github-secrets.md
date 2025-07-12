# GitHub Secrets Configuration

This document provides detailed information about configuring GitHub repository secrets for automated deployment.

## Overview

GitHub Secrets are encrypted environment variables that you create in a repository or organization. They are used to store sensitive information like API keys, passwords, and SSH keys securely.

## Secret Categories

### 1. Repository Secrets (Shared)

These secrets are available to all workflows and environments:

| Secret Name | Description | Example Value |
|-------------|-------------|---------------|
| `DOCKER_REGISTRY` | Docker registry URL | `registry.company.com:5000` |
| `DOCKER_USERNAME` | Registry username | `deploy-user` |
| `DOCKER_PASSWORD` | Registry password | `secure-password` |
| `SLACK_WEBHOOK_URL` | Slack notifications (optional) | `https://hooks.slack.com/...` |

### 2. Production Environment Secrets

These secrets are only available to the production environment:

| Secret Name | Description | Example Value |
|-------------|-------------|---------------|
| `PROD_HOST` | Production server IP/hostname | `prod.company.com` or `192.168.1.100` |
| `PROD_USERNAME` | SSH username for production | `ubuntu` or `deploy` |
| `PROD_SSH_KEY` | SSH private key for production | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `PROD_SSH_PORT` | SSH port (optional) | `22` |
| `PROD_APP_PATH` | Application directory path | `/opt/trends-earth-api` |

### 3. Staging Environment Secrets

These secrets are only available to the staging environment:

| Secret Name | Description | Example Value |
|-------------|-------------|---------------|
| `STAGING_HOST` | Staging server IP/hostname | `staging.company.com` or `192.168.1.101` |
| `STAGING_USERNAME` | SSH username for staging | `ubuntu` or `deploy` |
| `STAGING_SSH_KEY` | SSH private key for staging | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `STAGING_SSH_PORT` | SSH port (optional) | `22` |
| `STAGING_APP_PATH` | Application directory path | `/opt/trends-earth-api-staging` |

### Staging Database Configuration

#### Database Connection Secrets
```bash
gh secret set STAGING_DB_HOST --body "localhost"
gh secret set STAGING_DB_PORT --body "5433"
gh secret set STAGING_DB_NAME --body "trendsearth_staging"
gh secret set STAGING_DB_USER --body "trendsearth_staging"
gh secret set STAGING_DB_PASSWORD --body "your-staging-db-password"

# Production database connection (for data migration)
gh secret set PROD_DB_HOST --body "your-prod-db-host"
gh secret set PROD_DB_PORT --body "5432"
gh secret set PROD_DB_NAME --body "trendsearth"
gh secret set PROD_DB_USER --body "your-prod-db-user"
gh secret set PROD_DB_PASSWORD --body "your-prod-db-password"
```

#### Test User Credentials
```bash
# Test user emails (required)
gh secret set TEST_SUPERADMIN_EMAIL --body "test-superadmin@example.com"
gh secret set TEST_ADMIN_EMAIL --body "test-admin@example.com"
gh secret set TEST_USER_EMAIL --body "test-user@example.com"

# Test user passwords (required - no defaults provided)
gh secret set TEST_SUPERADMIN_PASSWORD --body "your-secure-superadmin-password"
gh secret set TEST_ADMIN_PASSWORD --body "your-secure-admin-password"
gh secret set TEST_USER_PASSWORD --body "your-secure-user-password"
```

## Manual Configuration

If you prefer to configure secrets manually through the GitHub web interface:

### Step 1: Navigate to Repository Settings
1. Go to your GitHub repository
2. Click on **Settings** tab
3. Select **Secrets and variables** → **Actions**

### Step 2: Create Repository Secrets
Click **New repository secret** and add each shared secret:

```
Name: DOCKER_REGISTRY
Value: registry.company.com:5000

Name: DOCKER_USERNAME  
Value: your-registry-username

Name: DOCKER_PASSWORD
Value: your-registry-password
```

### Step 3: Create Environment Secrets

#### Create Production Environment
1. Go to **Settings** → **Environments**
2. Click **New environment**
3. Name: `production`
4. Click **Configure environment**
5. Add environment secrets:

```
Name: PROD_HOST
Value: your-production-server-ip

Name: PROD_USERNAME
Value: your-ssh-username

Name: PROD_SSH_KEY
Value: -----BEGIN OPENSSH PRIVATE KEY-----
your-private-key-content
-----END OPENSSH PRIVATE KEY-----
```

#### Create Staging Environment
1. Click **New environment**
2. Name: `staging`
3. Add environment secrets with `STAGING_` prefix

## SSH Key Generation

### Automatic Generation
The setup script automatically generates SSH keys. If you need to do it manually:

```bash
# Generate production SSH key
ssh-keygen -t ed25519 -f ~/.ssh/trends_earth_production_deploy -N "" -C "trends-earth-production-deploy"

# Generate staging SSH key
ssh-keygen -t ed25519 -f ~/.ssh/trends_earth_staging_deploy -N "" -C "trends-earth-staging-deploy"
```

### Deploy Public Keys
Copy the public keys to your servers:

```bash
# Production
ssh-copy-id -i ~/.ssh/trends_earth_production_deploy.pub user@production-server

# Staging
ssh-copy-id -i ~/.ssh/trends_earth_staging_deploy.pub user@staging-server
```

### Add Private Keys to GitHub
Copy the **private key** content to GitHub Secrets:

```bash
# Display private key (copy this to GitHub)
cat ~/.ssh/trends_earth_production_deploy
```

## Security Best Practices

### SSH Keys
- **Use separate keys** for production and staging
- **Use Ed25519 keys** for better security (shorter, faster)
- **No passphrases** for automation (keys are encrypted in GitHub)
- **Rotate keys regularly** (quarterly recommended)
- **Limit key permissions** on servers

### Registry Credentials
- **Use dedicated service accounts** for registry access
- **Limit registry permissions** to push/pull only
- **Rotate passwords regularly**
- **Monitor registry access logs**

### Environment Separation
- **Use different servers** for production and staging
- **Separate database instances**
- **Different domain names/IPs**
- **Isolated networks where possible**

## Validation

### Test SSH Connection
```bash
# Test production SSH (replace with your details)
ssh -i ~/.ssh/trends_earth_production_deploy user@production-server

# Test staging SSH
ssh -i ~/.ssh/trends_earth_staging_deploy user@staging-server
```

### Test Docker Registry
```bash
# Test registry login
echo "your-password" | docker login registry.company.com:5000 -u your-username --password-stdin

# Test image pull/push
docker pull hello-world
docker tag hello-world registry.company.com:5000/test:latest
docker push registry.company.com:5000/test:latest
```

### Verify GitHub Secrets
Use GitHub CLI to list secrets:

```bash
# List repository secrets
gh secret list

# List environment secrets
gh secret list --env production
gh secret list --env staging
```

## Troubleshooting

### Common Issues

**SSH connection fails**
- Verify public key is in `~/.ssh/authorized_keys` on server
- Check SSH key format (should start with `-----BEGIN OPENSSH PRIVATE KEY-----`)
- Ensure no extra whitespace in the secret value
- Test SSH connection manually

**Docker registry authentication fails**
- Verify registry URL is correct (no `http://` prefix)
- Check username/password are correct
- Test registry connection from deployment server
- Verify registry is accessible from GitHub Actions runners

**Environment secrets not found**
- Ensure environment names match exactly (`production`, `staging`)
- Verify secrets are added to the correct environment
- Check workflow environment configuration

**Workflow permissions**
- Ensure repository has Actions enabled
- Check if organization has restrictions on Actions
- Verify user has admin access to configure secrets

### Debug Workflows

Add debug steps to workflows for troubleshooting:

```yaml
- name: Debug secrets
  run: |
    echo "Host: ${{ secrets.PROD_HOST }}"
    echo "Username: ${{ secrets.PROD_USERNAME }}"
    echo "SSH key length: ${#PROD_SSH_KEY}"
  env:
    PROD_SSH_KEY: ${{ secrets.PROD_SSH_KEY }}
```

**Note**: Never echo actual secret values, only their presence or length.

## Automation Scripts

The repository includes scripts to automate secret configuration:

### `setup-github-secrets.sh`
- Interactive script for all secret configuration
- Generates SSH keys automatically
- Sets up GitHub environments
- Configures all required secrets

### Usage
```bash
# Make script executable
chmod +x scripts/deployment/setup-github-secrets.sh

# Run the script
./scripts/deployment/setup-github-secrets.sh
```

The script will:
1. Check GitHub CLI authentication
2. Generate SSH key pairs
3. Create GitHub environments
4. Configure all secrets interactively
5. Display summary of configured secrets

## Updates and Maintenance

### Rotating SSH Keys
1. Generate new SSH key pairs
2. Deploy public keys to servers
3. Update GitHub secrets with new private keys
4. Remove old public keys from servers
5. Test deployment

### Updating Registry Credentials
1. Create new registry credentials
2. Update GitHub secrets
3. Test registry access
4. Revoke old credentials

### Regular Audits
- Review secret usage in workflows
- Check for unused secrets
- Verify secret expiration dates
- Monitor access logs
- Update documentation

## References

- [GitHub Encrypted Secrets](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [GitHub Environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
- [SSH Key Management](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)
- [GitHub CLI Secrets](https://cli.github.com/manual/gh_secret)
