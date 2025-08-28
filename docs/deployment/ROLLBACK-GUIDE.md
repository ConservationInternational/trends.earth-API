# Production Rollback Quick Guide

This document provides a quick reference for using the new production rollback GitHub Action.

## When to Use Rollback

Use the production rollback action when:
- ❌ Health checks fail after deployment
- ❌ Performance issues are detected in production
- ❌ Critical bugs are discovered after deployment
- ❌ Services are not responding correctly
- ❌ Database migrations cause issues

## How to Trigger Rollback

1. **Navigate to GitHub Actions**
   - Go to: `https://github.com/ConservationInternational/trends.earth-API/actions`

2. **Select Rollback Workflow**
   - Click on "Rollback Production Deployment"

3. **Click "Run workflow"**
   - Choose the `master` branch (or current production branch)

4. **Fill in Required Fields:**
   - **Reason** (required): Brief explanation of why you're rolling back
     - Example: "Health check failures after v2.1.0 deployment"
   - **Services** (optional, default: "all"): Which services to rollback
     - "all" - Rollback all services (recommended)
     - "api,worker" - Rollback specific services only
     - Available services: api, worker, beat, docker, redis

5. **Choose Rollback Method** (pick ONE):
   
   **Option A: Automatic Rollback** (recommended)
   - Leave both image and commit fields blank
   - Uses Docker Swarm's built-in rollback to previous version
   
   **Option B: Rollback to Specific Image Tag**
   - **Rollback to image**: Specific image tag to rollback to
   - Example: "master-abc1234", "v2.0.0", "latest"
   
   **Option C: Rollback to Specific Commit SHA** ✨ *New Feature*
   - **Rollback to commit**: Git commit SHA to rollback to
   - Example: "abc123456789" (minimum 7 characters)
   - The workflow will automatically find the corresponding image tag
   
   ⚠️ **Important**: Do not specify both image tag AND commit SHA - choose only one method

6. **Monitor Progress**
   - Watch the workflow execution in real-time
   - Check logs for detailed progress information

## Rollback Process

The workflow will automatically:
1. ✅ Set up secure AWS access for the GitHub runner
2. ✅ Validate your inputs and service names
3. ✅ Connect to production server securely via SSH
4. ✅ Perform Docker service rollbacks for specified services
5. ✅ Wait for services to stabilize after rollback
6. ✅ Run comprehensive health checks
7. ✅ Test basic API functionality
8. ✅ Notify Rollbar monitoring system
9. ✅ Clean up AWS security access

## Expected Timeline

- **Total Duration**: 5-10 minutes
- **Rollback Process**: 2-3 minutes
- **Health Verification**: 3-5 minutes
- **Integration Tests**: 1-2 minutes

## Success Indicators

✅ **Successful Rollback:**
- All workflow steps complete with green checkmarks
- Health checks pass (returns 200 status)
- API endpoints respond correctly
- Services show "1/1" replicas in Docker Swarm
- Rollbar receives rollback notification

## Troubleshooting

❌ **If Rollback Fails:**
1. Check the workflow logs for specific error messages
2. Verify production server is accessible
3. Check if services have rollback history available (for automatic rollbacks)
4. For commit SHA rollbacks: verify the commit SHA exists and an image was built for it
5. For image tag rollbacks: verify the image tag exists in the registry
6. Consider manual rollback if automated rollback fails

❌ **If Health Checks Fail After Rollback:**
1. Check service logs: `docker service logs trends-earth-prod_api`
2. Verify database connectivity
3. Check for any infrastructure issues
4. Consider rolling back to a different commit SHA or image tag
5. Try automatic rollback if specific image/commit rollback failed

❌ **Common Rollback Errors:**
- **"Invalid commit SHA format"**: Ensure commit SHA is at least 7 alphanumeric characters
- **"Image not found in registry"**: The specified image tag or commit doesn't have a built image
- **"Cannot specify both rollback methods"**: Choose only one: automatic, image tag, OR commit SHA
- **"No update history found"**: Service hasn't been updated recently, cannot use automatic rollback

## Manual Fallback

If the GitHub Action fails, you can perform manual rollback via SSH:

```bash
# Connect to production server
ssh user@production-server

# Navigate to application directory
cd /opt/trends-earth-api

# Option 1: Automatic rollback to previous version
docker service rollback trends-earth-prod_api
docker service rollback trends-earth-prod_worker
docker service rollback trends-earth-prod_beat
docker service rollback trends-earth-prod_docker
docker service rollback trends-earth-prod_redis

# Option 2: Rollback to specific image tag
IMAGE_TAG="master-abc1234"  # Replace with desired tag
docker service update --image $DOCKER_REGISTRY/trendsearth-api:$IMAGE_TAG trends-earth-prod_api
docker service update --image $DOCKER_REGISTRY/trendsearth-api:$IMAGE_TAG trends-earth-prod_worker
# Repeat for other services as needed

# Option 3: Rollback to specific commit SHA
COMMIT_SHA="abc123456789"  # Replace with desired commit
SHORT_SHA="${COMMIT_SHA:0:7}"
IMAGE_TAG="master-$SHORT_SHA"
docker service update --image $DOCKER_REGISTRY/trendsearth-api:$IMAGE_TAG trends-earth-prod_api
docker service update --image $DOCKER_REGISTRY/trendsearth-api:$IMAGE_TAG trends-earth-prod_worker
# Repeat for other services as needed

# Check service status
docker service ls --filter "name=trends-earth-prod"

# Verify health
curl http://localhost:3001/api-health
```

## Important Notes

⚠️ **Security**: The rollback action uses the same security infrastructure as deployments (AWS security groups, SSH keys)

⚠️ **Permissions**: Only users with `production` environment access can trigger rollbacks

⚠️ **Audit Trail**: All rollback actions are logged in GitHub Actions and reported to Rollbar

⚠️ **Data Safety**: Rollbacks only affect application code, not database data or persistent volumes

## Contact

For issues with the rollback process:
1. Check the GitHub Actions logs first
2. Review this guide for common solutions
3. Contact the development team with specific error messages