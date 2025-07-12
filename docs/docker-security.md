# Docker Security Configuration

## Overview

This document describes the security improvements implemented to address the high-risk issue of containers running as root while maintaining Docker socket access for script execution functionality.

## Security Problem

**Original Issue**: Containers were running as root (`USER root`) which created a significant security risk:
- Complete system compromise if container is breached
- Unnecessary elevated privileges for application processes
- Violation of principle of least privilege

## Solution

### 1. Non-Root User Implementation

**Changes Made**:
- Container now runs as `gef-api` user (non-root)
- User added to `docker` group for socket access
- Proper file ownership and permissions configured

**Files Modified**:
- `Dockerfile`: Added docker group membership, switched to non-root user
- `entrypoint.sh`: Enhanced Docker socket permission handling
- `docker-compose.*.yml`: Added `group_add` configuration

### 2. Docker Socket Access Strategy

Instead of running as root, we use a more secure approach:

1. **Group-based Access**: Add the `gef-api` user to the `docker` group
2. **Host Group Mapping**: Map the host's docker group ID to the container
3. **Permission Validation**: Check socket accessibility at runtime

### 3. Environment Configuration

**New Environment Variable**:
```bash
DOCKER_GROUP_ID=999  # Docker group ID from host system
```

**Auto-Detection Script**:
```bash
./scripts/setup-docker-security.sh
```

This script automatically:
- Detects the host Docker group ID
- Adds current user to docker group if needed
- Updates environment configuration
- Provides guidance for setup completion

## Implementation Details

### Dockerfile Changes

```dockerfile
# Before (INSECURE)
USER root

# After (SECURE)
RUN addgroup docker && adduser $USER docker
USER $USER
```

### Docker Compose Changes

```yaml
services:
  worker:
    # ... other config ...
    group_add:
      - ${DOCKER_GROUP_ID:-999}  # Map host docker group
```

### Runtime Permission Handling

The `entrypoint.sh` script now includes:
- Docker socket accessibility validation
- Group membership verification
- Helpful error messages for troubleshooting

## Security Benefits

✅ **Reduced Attack Surface**: Application runs with minimal privileges
✅ **Container Isolation**: No root access within container
✅ **Maintained Functionality**: Docker operations still work correctly
✅ **Principle of Least Privilege**: Only necessary permissions granted
✅ **Auditability**: Clear permission boundaries and group membership

## Setup Instructions

### Development Environment

1. **Run the setup script**:
   ```bash
   ./scripts/setup-docker-security.sh
   ```

2. **Rebuild containers**:
   ```bash
   docker compose build
   docker compose -f docker-compose.develop.yml up
   ```

### Production Environment

1. **Determine Docker group ID on host**:
   ```bash
   getent group docker | cut -d: -f3
   ```

2. **Set environment variable**:
   ```bash
   echo "DOCKER_GROUP_ID=999" >> prod.env  # Use actual GID
   ```

3. **Deploy with security configuration**:
   ```bash
   docker stack deploy -c docker-compose.prod.yml api
   ```

## Troubleshooting

### Permission Denied Errors

If you see Docker permission errors:

1. **Check user is in docker group**:
   ```bash
   groups $USER
   ```

2. **Add user to docker group** (if missing):
   ```bash
   sudo usermod -aG docker $USER
   # Log out and back in
   ```

3. **Verify Docker socket group**:
   ```bash
   ls -la /var/run/docker.sock
   stat -c %g /var/run/docker.sock
   ```

4. **Update DOCKER_GROUP_ID** in environment file to match host

### Container Startup Issues

If containers fail to start:

1. **Check Docker group exists in container**:
   ```bash
   docker exec <container> getent group docker
   ```

2. **Verify user group membership**:
   ```bash
   docker exec <container> groups gef-api
   ```

3. **Check socket mount**:
   ```bash
   docker exec <container> ls -la /tmp/docker.sock
   ```

## Security Validation

### Verify Non-Root Execution

```bash
# Check running user
docker exec <container> whoami
# Should return: gef-api

# Check user ID
docker exec <container> id
# Should NOT show uid=0 (root)
```

### Verify Docker Access

```bash
# Test Docker socket access
docker exec <container> docker ps
# Should work without permission errors
```

### Verify Group Membership

```bash
# Check docker group membership
docker exec <container> groups
# Should include 'docker' group
```

## Migration Notes

### From Root to Non-Root

When migrating existing deployments:

1. **Backup any mounted volumes** that may have root-owned files
2. **Update file permissions** if needed:
   ```bash
   sudo chown -R 1000:1000 /path/to/mounted/volumes
   ```
3. **Test thoroughly** in staging environment first
4. **Monitor logs** for permission-related errors after deployment

### Environment Variables

Ensure all environment files include:
```bash
DOCKER_GROUP_ID=<actual_docker_group_id>
```

## Compliance & Standards

This implementation aligns with:
- **CIS Docker Benchmark**: Run containers as non-root user
- **NIST Container Security**: Principle of least privilege
- **Docker Security Best Practices**: Avoid privileged containers
- **OWASP Container Security**: Minimize attack surface

## Additional Security Considerations

### Future Improvements

Consider implementing:
1. **Docker-in-Docker**: Isolate Docker operations further
2. **Rootless Docker**: Use rootless Docker on the host
3. **Pod Security Standards**: If migrating to Kubernetes
4. **Runtime Security Monitoring**: Monitor container behavior

### Monitoring

Monitor for:
- Unexpected privilege escalation attempts
- Docker API usage patterns
- File system access patterns
- Network connections from containers

This security configuration significantly reduces risk while maintaining full functionality for script execution and Docker operations.
