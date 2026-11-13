# Nextflow workflows on AWS Batch

AWS Batch scripts may opt in to the trusted Nextflow driver by declaring
`allowed_workflow_backends: ["nextflow"]` in the publisher-controlled
`batch_execution_config`. A request may then include
`"workflow_backend": "nextflow"`. The request cannot select a workflow file,
Nextflow configuration, driver image, job definition, queue, role, or command.
Existing Batch scripts remain on the legacy path unless both the publisher and
the user opt in.

The API requires the following server-side configuration:

| Setting | Purpose |
| --- | --- |
| `NEXTFLOW_ENABLED=true` | Enables the driver backend |
| `NEXTFLOW_DRIVER_JOB_DEFINITION` | Revision-pinned AWS Batch job-definition ARN for the trusted driver |
| `NEXTFLOW_DRIVER_JOB_QUEUE` | Trusted On-Demand queue for the driver |
| `NEXTFLOW_WORK_ROOT` | S3 URI root for Nextflow work directories |
| `NEXTFLOW_STATE_ROOT` | S3 URI root for `session.json`, `jobs.json`, `status.json`, and `cancelled.json` |
| `NEXTFLOW_ANALYSIS_IMAGE` | Optional trusted ECR image; otherwise the published script image is used |
| `AE_NEXTFLOW_SPOT_QUEUE`, `AE_NEXTFLOW_ONDEMAND_QUEUE` | Optional approved queues passed to the workflow driver |
| `AE_NEXTFLOW_ALLOW_ONDEMAND` | Server-side approval for On-Demand child tasks; defaults to `false` |
| `AE_NEXTFLOW_JOB_ROLE_ARN`, `AE_NEXTFLOW_MAX_FORKS`, `AE_NEXTFLOW_MAX_HOURS`, `AE_NEXTFLOW_MATCH_MEMORY_MIB` | Optional driver limits, task role, and match-task memory |

When `AE_NEXTFLOW_MAX_HOURS` is set, the API sets the AWS Batch driver timeout
to that budget plus 900 seconds for shutdown and cleanup.

Analysis images must be in ECR. Tags are resolved by the API to immutable
`@sha256:` digests before driver submission. The API submits one driver job with
one AWS Batch attempt; workflow task retry/resume is owned by Nextflow.

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
