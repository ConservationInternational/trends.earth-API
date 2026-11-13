# Nextflow workflows on AWS Batch

AWS Batch scripts may opt in to the trusted Nextflow driver by declaring
`allowed_workflow_backends: ["nextflow"]` in the publisher-controlled
`batch_execution_config`. A request may then include
`"workflow_backend": "nextflow"`. The request cannot select a workflow file,
Nextflow configuration, driver image, driver job definition, driver queue,
role, or command. Publishers can also set `default_workflow_backend: "nextflow"`
in that policy; the API resolves and persists the default before dispatch.
Scripts without that default retain their existing dispatch behavior.
Non-Batch execution behavior and parameters are unchanged.

The API handles only generic workflow lifecycle concerns. Workflow-specific
inputs are passed unchanged to the driver, which validates and interprets them.
Nextflow requests cannot include legacy `pipeline` or `batch` overrides.
The trusted driver job definition owns the command and workflow-specific
environment; the API does not override its command.

The API requires the following server-side configuration:

| Setting | Purpose |
| --- | --- |
| `NEXTFLOW_ENABLED=true` | Enables the driver backend |
| `NEXTFLOW_DRIVER_JOB_DEFINITION` | Fallback revision-pinned driver job-definition ARN; the published Script's `batch_job_definition` takes precedence |
| `NEXTFLOW_DRIVER_JOB_QUEUE` | Fallback driver queue; the published Script's `batch_job_queue` takes precedence |
| `NEXTFLOW_WORK_ROOT` | S3 URI root for Nextflow work directories |
| `NEXTFLOW_STATE_ROOT` | S3 URI root for `session.json`, `jobs.json`, `status.json`, and `cancelled.json` |
| `NEXTFLOW_TASK_IMAGE` | Optional trusted ECR task image; otherwise the published script image is used |
| `NEXTFLOW_MAX_HOURS` | Optional overall workflow budget passed to the driver |
| `NEXTFLOW_CHILD_JOB_QUEUES` | Optional comma-separated queues for tagged-child discovery; absent this, discover all queues using `batch:DescribeJobQueues` |

The CodeDeploy workflows read these from GitHub Actions variables using the
`PRODUCTION_NEXTFLOW_` and `STAGING_NEXTFLOW_` prefixes, then write them into
the deployed environment file. `NEXTFLOW_ENABLED` defaults to `false`; the
other settings default to empty. Configure separate work and state roots for
staging and production.

Provision the dedicated bucket and lifecycle rules with:

```bash
python scripts/setup/setup_nextflow_s3_bucket.py --profile your-profile --region us-east-1
```

The script creates `trendsearth-api-nextflow-{account-id}`, blocks public
access, enables encryption and versioning, and sets lifecycle expiration for
the `production/` and `staging/` prefixes. Current work objects expire after
30 days and current state objects after 365 days; noncurrent versions are
removed one day after becoming noncurrent, and expired delete markers are
cleaned up. It prints the
`*_NEXTFLOW_WORK_ROOT` and `*_NEXTFLOW_STATE_ROOT` values to enter as GitHub
Actions variables. Ensure workflows complete within the work retention period
and configure state retention to cover the desired resume window. The script
does not modify IAM; grant the API role access to state prefixes and the
Nextflow driver/task roles access to the work and state prefixes they use.

When `NEXTFLOW_MAX_HOURS` is set, the API sets the AWS Batch driver timeout
to that budget plus 900 seconds for shutdown and cleanup.

Analysis images must be in ECR. Tags are resolved by the API to immutable
`@sha256:` digests before driver submission. The API submits one driver job with
one AWS Batch attempt; workflow task retry/resume is owned by Nextflow.

Different scripts can publish different driver job definitions and queues.
Each trusted driver must implement this protocol:

- Read `EXECUTION_ID`, `PARAMS_S3_BUCKET`, `PARAMS_S3_PREFIX`, `AWS_REGION`,
  `NEXTFLOW_TASK_IMAGE`, `NEXTFLOW_WORK_URI`, `NEXTFLOW_STATE_URI`,
  `NEXTFLOW_RESUME`, and optional `NEXTFLOW_MAX_HOURS`.
- Load the compressed execution parameters from
  `{PARAMS_S3_PREFIX}/{EXECUTION_ID}.json.gz`, preserving the original object
  and immutable inputs on explicit resume.
- Persist the session, child-job manifest, and terminal status described below.
  Tag children with `execution_id` so cancellation and orphan cleanup can find
  jobs submitted immediately before a driver failure.
- Honor `cancelled.json`, terminate active children on failure, and publish
  the compressed results before reporting success.

Task batching, resource tiers, queue selection, scientific parameters, and
workflow-specific validation belong in each workflow's repository and driver,
not in the API.

The API provides `POST /api/v1/execution/{id}/resume` for an owner or
administrator with `execution:write` scope. It only accepts failed Nextflow
executions and resumes the same execution, parameter object, S3 state path,
and Nextflow session. It does not create a replacement execution or reset
inputs. Normal execution concurrency limits apply, and a resume may be queued.

The driver writes a `session.json` document containing its stable
`session_id`, a `jobs.json` manifest of child Batch job IDs, and a
`status.json` document with lowercase `phase` values (`launching`, `running`,
`succeeded`, `failed`, or `cancelled`), `success`, and, on failure, `error`.
Successful terminal state must have `phase: "succeeded"` and `success: true`;
the API only marks the execution successful if the compressed results object
also exists at
`s3://{PARAMS_S3_BUCKET}/{PARAMS_S3_PREFIX}/{execution_id}_results.json.gz`.
Cancellation writes `cancelled.json` before terminating manifest-listed and
tag-discovered child jobs plus the driver. If the driver fails, the monitor
uses the same manifest and `execution_id` Batch-tag discovery to terminate
orphaned children and requires two clean checks before marking the execution
failed or allowing resume.
