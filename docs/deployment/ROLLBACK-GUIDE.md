# Rollback Guide

This document explains how to roll back a failed or broken deployment using the GitHub Actions rollback workflows.

## When to Use Rollback

- ❌ Health checks fail after deployment
- ❌ Performance issues are detected
- ❌ Critical bugs are discovered after deployment
- ❌ Services are not responding correctly
- ❌ Database migrations cause issues

## Production Rollback

### How to Trigger

1. Go to the **Actions** tab in GitHub:
   `https://github.com/ConservationInternational/trends.earth-API/actions`

2. Select **"Rollback Production Deployment"** and click **"Run workflow"**.

3. Fill in the required fields:

   - **Rollback to commit** *(optional)*: Leave blank for automatic rollback to the
     previous successful deployment. Or provide a Git commit SHA (minimum 7 characters)
     to redeploy from that specific commit's ECR image.

   - **Reason** *(required)*: Brief description of why you're rolling back.
     Example: `"Health check failures after v2.1.0 deployment"`

   - **Type "CONFIRM"** *(required)*: Type `CONFIRM` exactly to confirm the production
     rollback.

4. Click **"Run workflow"** and monitor progress in the Actions log.

### Rollback Process

The workflow automatically:
1. ✅ Validates inputs and confirms the `CONFIRM` phrase
2. ✅ Configures secure AWS access via OIDC (no long-lived credentials)
3. ✅ Stops any in-progress deployments via CodeDeploy API
4. ✅ Determines rollback strategy (automatic or commit-specific)
5. ✅ **Automatic**: Redeploys the previous successful revision from S3 via CodeDeploy
6. ✅ **Commit-specific**: Locates the ECR image for the target commit and deploys it via CodeDeploy
7. ✅ Waits for the CodeDeploy deployment to succeed
8. ✅ Runs health checks against the production API
9. ✅ Notifies Rollbar of the rollback event

### Expected Timeline

- **Total Duration**: 5–10 minutes
- **Deployment**: 2–3 minutes
- **Health Verification**: 3–5 minutes

## Staging Rollback

The staging rollback workflow (`rollback-staging.yml`) works identically but does not require the `CONFIRM` safety check.

1. Go to Actions → **"Rollback Staging Deployment"** → **"Run workflow"**
2. Fill in:
   - **Rollback to commit** *(optional)*: blank for automatic, or a commit SHA
   - **Reason** *(required)*: brief description

## Success Indicators

- All workflow steps have green checkmarks
- Health check returns HTTP 200
- `docker service ls` shows correct replica counts
- Rollbar receives rollback notification

## Troubleshooting

**If the rollback workflow fails:**
1. Check the Actions logs for specific error messages
2. Verify no stale in-progress deployments exist in the CodeDeploy console
3. For commit-specific rollback: confirm the ECR image for that commit still exists
   (images are retained according to the ECR lifecycle policy; very old commits may not have images)

**If health checks fail after rollback:**
1. Check service logs: `docker service logs trends-earth-prod_api`
2. Verify database connectivity
3. Try rolling back to a different commit SHA, or use the automatic rollback option

**Common errors:**
- `"Invalid commit SHA format"` — SHA must be at least 7 alphanumeric characters
- `"Commit not found in repository"` — The SHA doesn't exist in the git history
- `"CODEDEPLOY_S3_BUCKET secret is not set"` — The `CODEDEPLOY_S3_BUCKET` secret is missing from the environment
- `"No ECR image found for commit"` — No ECR image was built for the target commit; try automatic rollback instead

## Emergency Manual Rollback

If the GitHub Actions workflow is unavailable, roll back directly via the AWS CLI or Docker Swarm on the EC2 instance.

**Option 1 — AWS CodeDeploy CLI (redeploy a previous revision):**
```bash
# Find the previous successful deployment
aws deploy list-deployments \
  --application-name trendsearth-api \
  --deployment-group-name trendsearth-api-production \
  --include-only-statuses Succeeded \
  --max-items 3

# Redeploy a specific revision
aws deploy create-deployment \
  --application-name trendsearth-api \
  --deployment-group-name trendsearth-api-production \
  --s3-location bucket=<CODEDEPLOY_S3_BUCKET>,key=production/<previous-revision>.zip,bundleType=zip
```

**Option 2 — Docker Swarm image swap on EC2 (emergency only):**
```bash
# SSH into the EC2 instance
ssh ubuntu@<ec2-instance>

# Find the previous ECR image tag
aws ecr describe-images \
  --repository-name trendsearth-api \
  --query 'sort_by(imageDetails, &imagePushedAt)[-10:].imageTags[0]' \
  --output table

# Roll back Docker Swarm services to a previous ECR image
ECR_IMAGE="<account-id>.dkr.ecr.us-east-1.amazonaws.com/trendsearth-api:<previous-tag>"
docker service update --image "$ECR_IMAGE" trends-earth-prod_api
docker service update --image "$ECR_IMAGE" trends-earth-prod_worker
docker service update --image "$ECR_IMAGE" trends-earth-prod_beat
docker service update --image "$ECR_IMAGE" trends-earth-prod_docker

# Verify
docker service ls --filter "name=trends-earth-prod"
curl http://localhost:3001/api-health
```

## Important Notes

⚠️ **Permissions**: Only users with access to the `production` GitHub environment can trigger production rollbacks

⚠️ **Audit Trail**: All rollback actions are logged in GitHub Actions and reported to Rollbar

⚠️ **Data Safety**: Rollbacks only affect application code — database data and persistent volumes are not affected