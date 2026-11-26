# Kubernetes Migration Plan: trends.earth-API

**Status:** Proposed; no Kubernetes migration is implemented in this repository.
**Reviewed against:** repository state on 2026-11-23.
**Purpose:** plan an incremental migration from Docker Swarm/CodeDeploy to AWS EKS without changing API contracts or dropping existing compute and platform capabilities.

This plan describes the current code and checked-in deployment configuration, not undocumented AWS account state. Before provisioning, confirm production database, Redis, S3, GCS, Batch, DNS, registry, and public-share resources from the authorized infrastructure inventory. Do not copy secrets into manifests or this document.

## Executive summary

The API is a Flask/Gunicorn application with a substantial authenticated REST API, a separate OIDC implementation, and Celery background workers. Execution is not one interchangeable backend: scripts may use GEE containers, an external openEO backend, or AWS Batch. The migration must preserve those distinct flows. Public web-map sharing adds optional services outside the API container.

The current deployment uses GitHub Actions with AWS OIDC to build and push images to ECR, followed by CodeDeploy on EC2/Swarm. The production Compose file defines API, Celery workers, beat, a Docker-socket build/execution worker, and Redis; it does not define PostgreSQL. Staging Compose defines a PostgreSQL/PostGIS container. Production database location and version must be confirmed from deployment configuration, not assumed from the Compose file.

Recommended direction:

1. Provision and manage EKS and AWS dependencies as code. Select the newest EKS standard-support Kubernetes version available at implementation time; use EKS Pod Identity by default, with IRSA as a supported alternative.
2. Keep the existing API, authentication, user, script, execution, statistics, and public-share API contracts. Move only infrastructure and orchestration behavior.
3. Keep AWS Batch as the runner for `compute_type="batch"` and preserve openEO’s current remote-job submission and monitoring flow. Move GEE container dispatch from Swarm services to Kubernetes Jobs.
4. Replace Docker-socket builds with an isolated, on-demand build service. Prefer AWS CodeBuild for untrusted uploaded build contexts; use a constrained rootless BuildKit design only if its isolation and operational requirements are validated. Do not adopt Kaniko: its upstream repository was archived in June 2025.
5. Run the existing Celery schedule exactly once per environment, migrate the scheduler and every queue consumer deliberately, and preserve task semantics, logs, status transitions, cancellation, and callbacks.
6. Migrate optional public-share services and infrastructure explicitly; do not treat them as part of the API Deployment or expose their internal tile services.

## 1. Current repository and API inventory

### 1.1 Application and API layout

The Flask application is created in `gefapi/__init__.py`. The versioned API blueprint is mounted at `/api/v1`; OIDC routes are registered separately. Health, authentication, Swagger/OpenAPI, and documentation routes are application-level routes, not members of that blueprint.

| Area | Current code | Current capability to preserve |
|---|---|---|
| Application and middleware | `gefapi/__init__.py`, `gefapi/wsgi.py`, `gunicorn.py` | Flask/Gunicorn serving, CORS, compression, rate limiting, proxy handling, error reporting, API docs, and health checks |
| API v1 routes | `gefapi/routes/api/v1/` | Route families below; retain paths, methods, authorization, response shapes, and pagination/filter behavior |
| Authentication | `gefapi/__init__.py`, `gefapi/routes/api/v1/users.py`, `gefapi/routes/api/v1/oauth2.py` | Password/JWT authentication, refresh/revocation, registration and user management, OAuth client credentials, sessions, rate limits |
| OIDC | `gefapi/routes/oidc.py`, `gefapi/services/oidc_service.py` | Discovery/JWKS, authorization, registration, token/refresh/revoke, userinfo, logout, and administrative client/user operations |
| Persistence | `gefapi/models/`, `migrations/` | SQLAlchemy/Flask-Migrate models and Alembic schema; includes users, scripts, executions/logs, OIDC/refresh-token data, app access, public shares, bulk email, rate-limit and deletion audit data |
| Background processing | `gefapi/celery.py`, `gefapi/tasks/` | Celery worker queues, beat schedule, task retries/cleanup, execution dispatch and monitoring |
| Container execution | `gefapi/services/docker_service.py` | Current Swarm image build/run, service logs and cancellation; replace the production Swarm implementation without changing execution API behavior |
| Other compute backends | `gefapi/services/openeo_service.py`, `gefapi/services/batch_service.py`, `gefapi/tasks/openeo_monitoring.py`, `gefapi/tasks/batch_monitoring.py` | External openEO job submission/status polling and AWS Batch jobs/pipelines, including Nextflow-related dispatch |
| Storage and integrations | `gefapi/s3.py`, `gefapi/services/` | S3 scripts/parameters/results/shares, GEE and GCS integration, Google Groups, email/SparkPost, Rollbar, boundaries, and application access |
| Public web-map sharing | `gefapi/routes/api/v1/public_shares.py`, `gefapi/services/public_share_service.py`, `public-viewer/` | Share lifecycle, multipart uploads, validation, publish/rotate/delete, viewer sessions/manifests, quota enforcement, tile and vector map access |

### 1.2 Endpoint families

The following inventory is intentionally grouped by capability rather than a copy of every route decorator. The authoritative route definitions and OpenAPI output remain the source of truth. Generate a route snapshot from the running app as part of migration testing; do not infer endpoint coverage from this summary.

| Prefix or route family | Current functionality |
|---|---|
| Application-level `/api-health`, `/ping` | Health and diagnostic routes; probe behavior and dependency semantics are documented below |
| Application-level `/auth`, `/auth/refresh`, `/auth/logout`, `/auth/logout-all` | First-party login and token/session lifecycle |
| `/api/v1/user*`, `/api/v1/account/profile` | Registration, user lookup/export/profile, password changes/reset, deletion, and related user operations |
| `/api/v1/oauth/*`, `/api/v1/user/me/clients`, `/api/v1/admin/client-stats` | OAuth clients/tokens and client usage tracking |
| `/.well-known/*`, `/oauth/*`, `/api/v1/admin/oidc-clients`, OIDC admin routes | OIDC discovery/JWKS, authorization, registration, credentials, userinfo, logout, and administrative operations; these are not all under `/api/v1` |
| `/api/v1/script*`, `/api/v1/script/<id>/access*` | Script upload/build, list/detail/export/download/log/config, publication, access policy, and deletion |
| `/api/v1/script/<id>/run`, `/api/v1/execution*` | Create/list/export/detail/update executions, callback and callback logs, cancellation, execution logs, Docker/Batch logs, result download, and user execution history |
| `/api/v1/user/me/gee-*`, `/api/v1/gee-oauth/*` | GEE credential management, OAuth, service-account flows, and related credential operations |
| `/api/v1/user/me/openeo-credentials*` | openEO credential management |
| `/api/v1/data/boundaries*` | Boundary data, listing, and update information |
| `/api/v1/status*`, `/api/v1/stats/*`, `/api/v1/rate-limit/*` | Service/cluster status, operational statistics and caches, rate-limit status and administration |
| `/api/v1/admin/app-access*`, `/api/v1/user/me/app-access*`, `/api/v1/app-users*` | Application access, users, and administrative access management |
| `/api/v1/user/me/google-groups*`, `/api/v1/google-groups/info` | Google Groups integration and synchronization |
| `/api/v1/shares*`, `/api/v1/public-shares*` | Public-share management, upload/publish lifecycle, viewer sessions, and public manifests/tiles |
| `/api/v1/news*`, `/api/v1/unsubscribe` | News and unsubscribe operations |
| `/api/v1/bulk-email*` | Recipient lists, drafts, verification, test sends, and bulk-email administration |
| `/api/docs/`, `/swagger.json`, `/static/swagger-ui/*` | Interactive documentation and generated API schema; retain the configured production access policy |

Important compatibility checks:

- `/api/v1/status/swarm` is a legacy endpoint. `/api/v1/status/cluster` is an alias currently present in the source; change the data behind the cluster view without breaking either path or its authorization/response contract.
- `GET /api/v1/execution/<id>/docker-logs` is also legacy-named. Keep it working during the transition even if the source of logs becomes Kubernetes pod logs. Do not rename or remove it as part of the infrastructure migration.
- Execution callback routes are used by execution workloads to report progress/results. Preserve callback authentication, URLs, payload formats, and reachability from the new runtime.
- The standalone OIDC routes, redirect URI validation, issuer, JWKS and signing-key lifecycle must remain stable through DNS and ingress cutover.
- `/debug/routes` exists at the application level. Explicitly decide whether it should remain enabled and restrict it appropriately; do not accidentally expose additional cluster details through it.

### 1.3 Current deployment and external dependencies

| Component | Current state observed in the repository | Migration requirement |
|---|---|---|
| API | Flask/Gunicorn; production Compose has two API replicas, staging has one | Kubernetes Deployment, ClusterIP Service and HTTPS ingress; preserve both environments’ settings and hostname behavior |
| Celery | `default` and `build` queues; production Compose has a default worker, a Docker-socket `build` worker, and one beat service | Separate least-privilege worker Deployments; exactly one beat scheduler per environment |
| Redis | Redis 7 service with AOF-backed local volume in the production Compose file; broker/result backend and other runtime features use configured Redis | Confirm persistence, TLS, authentication, database/index usage, retention, queue durability, and any rate-limit/cache use before moving to managed Redis |
| PostgreSQL/PostGIS | Production Compose obtains `DATABASE_URL` from `prod.env` and defines no database service. Staging Compose defines `postgis/postgis:16-3.6` | Identify actual production provider/version and staging data workflow. Preserve PostGIS and extensions. Do not create a replacement database or run migrations until ownership, backup, restore, and cutover are agreed |
| API image | Existing staging/production workflows use GitHub Actions OIDC and push the API image to ECR using Buildx | Retain ECR and current short-SHA tagging convention or move to digest pinning; do not create a second API registry |
| Script images | `REGISTRY_URL` is used by `docker_service.py` for built user-script images; it is separate from the API image workflow | Inventory existing repositories/images and consumers. Migrate tags/digests before switching dispatch; establish retention that does not remove active scripts |
| AWS storage | S3 is used for scripts, execution parameters/results, and optionally public-share uploads. The exact bucket names are environment configuration | Keep bucket privacy, encryption, lifecycle, CORS, object ownership, access policies, and key layout compatible; use workload IAM roles rather than static AWS keys |
| Google Cloud | GEE and GCS are used by execution workflows; GCS output is not replaced by Kubernetes storage | Preserve service identity and Google credential behavior; test network routes and permissions from the new workload |
| AWS Batch | `batch_service.py` submits standard and pipeline/array jobs; Nextflow configuration is supported | Keep Batch dispatch and monitoring as Batch; provision least-privilege IAM and preserve its S3 parameter/result access |
| openEO | `openeo_run` submits a job to an external openEO backend; Celery monitoring polls that backend | Keep this remote service path and its credential/backend selection; it is not a Kubernetes execution Job in the current code |
| Email and telemetry | SparkPost, Rollbar, and scheduled suppression synchronization are present | Preserve egress, secrets, sender configuration, and privacy-conscious logging |
| Public shares | Optional `docker-compose.public-shares.yml` adds `public-viewer`, `public-viewer-gateway`, and Martin; TiTiler is configured as a private SAM Lambda or internal URL. The feature fails closed unless required settings are present | Migrate or retain each component deliberately; keep viewer/gateway origins and internal tile services private; preserve the TiTiler invocation path and scoped storage roles |
| Deployment workflows | CodeDeploy workflows for staging and production, plus separate rollback, tests, lint, translation, public-share-tiler and public-viewer workflows | Replace only the API deployment path after EKS is ready. Keep unrelated workflows and rollback procedures until their resources are separately retired |

## 2. Target design and non-negotiable behavior

### 2.1 Target runtime

```text
Clients
  -> HTTPS ALB / existing DNS and certificate
  -> API Deployment (Flask/Gunicorn, private ClusterIP)
       -> managed PostgreSQL/PostGIS endpoint (existing or migrated)
       -> managed Redis endpoint (Celery broker/backend/cache)
       -> private S3/GCS and external integrations through scoped identity/egress

Celery beat (one replica)
  -> default workers: queue processor, Batch/openEO and other non-container tasks
  -> orchestration workers: submit/cancel/inspect GEE Kubernetes Jobs
  -> isolated build service: build user script images and push to ECR

GEE execution Jobs (isolated namespace/node capacity)
  -> preserve current callback/result contract with API

AWS Batch and openEO
  -> continue on their existing external execution backends

Optional public-share viewer/gateway/Martin/TiTiler
  -> migrated as separately secured services or retained temporarily;
     no direct public access to internal tile/validation services
```

Use separate staging and production namespaces at minimum. Separate clusters/accounts are preferred where operationally and financially feasible; if one cluster is selected, enforce namespace, IAM, network, quota, and node-pool boundaries. Execution pods must not share platform credentials or unrestricted control-plane access.

### 2.2 Recommended implementation choices

| Concern | Recommendation |
|---|---|
| Infrastructure as code | Terraform, AWS CDK, or the organization’s existing standard. Pick one and commit cluster, IAM, networking, data services, DNS/certificates, policies, and add-on versions as code. Avoid manual-only `eksctl` steps for production. |
| Kubernetes version | Query EKS supported versions during provisioning and choose the newest standard-support version compatible with required add-ons. AWS currently lists 1.37, 1.36, 1.35 and 1.34 in standard support; this is time-sensitive and is not a version pin. Keep control plane, nodes, and add-ons on a tested upgrade cadence. |
| Workload identity | EKS Pod Identity for application, worker, build, and execution identities where supported; IRSA is an acceptable alternative. Use distinct roles per service and restrict S3/ECR/Batch actions and resources. |
| API ingress | AWS Load Balancer Controller with ALB, ACM-managed TLS, HTTPS redirect, health checks, and existing hostnames. Evaluate AWS WAF/rate controls and preserve Flask trusted-proxy settings. If using EKS Auto Mode, use its integrated load balancing rather than installing a duplicate controller. |
| Compute scaling | Keep a small, resilient managed node group for API, default workers and beat. Use Karpenter for burst execution capacity only if justified by measured demand and isolation constraints. Evaluate EKS Auto Mode as an alternative; do not install Auto Mode and a second node-provisioning stack for the same workloads. A permanent warm execution node/pause pod is optional and should be justified by startup-latency and cost measurements, not assumed. |
| Container builds | Prefer isolated on-demand AWS CodeBuild for user-provided build contexts. If BuildKit is chosen, use rootless mode with an explicit isolation/security review, dedicated build capacity, short-lived credentials and no Docker socket/host mounts. Kaniko is archived and must not be used as the new production dependency. |
| Manifests | Use Helm or Kustomize with reviewed staging/production overlays; use one as the source of truth. A GitOps controller is a good fit if the team will operate it. Avoid imperative `kubectl set image` as the deployment record. |
| Images | Deploy immutable image digests, not mutable `latest` tags. Scan images, retain rollback versions, and record the source commit/SBOM/provenance where supported by the build pipeline. |
| Secrets | Store runtime secrets in AWS Secrets Manager (or the organization’s approved vault) and project them through External Secrets Operator or Secrets Store CSI. Never commit populated Kubernetes Secrets, environment files, private keys, or credentials. |
| Network controls | Use private subnets for nodes and data services, private API-to-database/cache paths, VPC endpoints where useful, and enforced NetworkPolicy. Confirm the selected CNI actually enforces policies. Use constrained egress for workloads; do not use an allow-all egress policy as the final state. |
| Migrations | Run one migration Job per release, with a unique release identifier, bounded retries/time, logs retained for diagnosis, and backward-compatible expand/contract schema changes. |
| Observability | Export structured logs/metrics/traces to the selected AWS or organization platform. Preserve Rollbar behavior. Do not log request secrets, execution credentials, task arguments, or full credential-bearing environments. |

### 2.3 Build isolation decision

Script archives are supplied by users and the resulting Dockerfile/build steps execute code. A builder must therefore be treated as an untrusted-code boundary, not as an ordinary API worker.

**Preferred path — AWS CodeBuild:** create a dedicated project or equivalent isolated build configuration, accept only an API-generated S3 context reference, scope the build role to the required input prefix and execution-image repository, and pass no API/database/production secrets into the build. Pin the builder image/toolchain, enforce build timeout and resource limits, capture bounded logs, and make retries/idempotency explicit. Confirm whether the current Dockerfile/build context needs privileged Docker-in-Docker; avoid privileged mode if the build can use a supported daemonless/rootless workflow.

**Alternative — rootless BuildKit Jobs:** only proceed after validating that rootless BuildKit can build the repository’s actual execution Dockerfiles, that its storage/snapshotter works on the chosen EKS nodes, and that hostile build instructions cannot access node credentials, the Kubernetes API, other namespaces, or control-plane metadata. Use a separate build namespace/node pool, no general worker service account, no hostPath or Docker socket, strict pod security, constrained egress, quotas, and short-lived ECR credentials.

Do not treat the sample Kaniko commands in the old draft as an implementation recipe. Kaniko’s upstream repository is archived.

## 3. Migration phases

### Phase -1: Discovery and migration gates

1. Inventory AWS resources and deployment variables without printing their values: production/staging database engine/version/host ownership, Redis use and persistence, S3 buckets/prefixes, ECR repositories, current script image tags, Batch roles/queues/job definitions, GCS buckets/identities, public-share services, DNS/TLS, and CodeDeploy resources.
2. Export a baseline route/method/endpoint-name snapshot from the application and save a sanitized OpenAPI document. Compare pre- and post-migration snapshots in CI; expected infrastructure-only changes must be reviewed.
3. Record current `celery.conf.task_routes` and `beat_schedule` from `gefapi/celery.py`, each worker’s queue configuration, retry/ack behavior, task timeouts, and Redis database/keys used by caching/rate limits. The schedule includes more than execution monitoring: token/user/deletion cleanup, public-share cleanup, email work, queue processing, stats refresh, Batch/openEO monitors, and Swarm-specific monitoring.
4. Document every environment’s actual variables and secrets by name and owner (not value). At minimum account for database/Redis URLs; JWT/session/OIDC keys; API URLs and trusted proxies; S3/GCS/GEE credentials; ECR/Batch settings; email/Rollbar; Google integrations; openEO; and public-share viewer, origin, storage, session, Martin, and TiTiler settings.
5. Identify in-flight execution handling: callback URLs and tokens, the database execution states, worker message compatibility, active Swarm services, Batch jobs, openEO jobs, and whether a deployment can wait for them to finish.
6. Define a rollback owner, RPO/RTO, backup and restore rehearsal, traffic switch mechanism, and a production change window. Do not approve cutover before restore has been tested.

**Gate:** do not provision or migrate production data until production database ownership/provider and active-job strategy are confirmed.

### Phase 0: Provision AWS infrastructure as code

#### 0.1 EKS, networking, and ingress

- Create staging first. Use private worker subnets, private API-to-data connectivity, controlled outbound internet/NAT or approved egress, and endpoint policies.
- Query the current EKS version catalog at deployment time. On 2026-11-23, AWS’s version page lists Kubernetes 1.37–1.34 as standard-support versions; select an available version after checking add-on, CNI, ingress, CSI and node compatibility.
- Decide between managed node groups plus Karpenter, or EKS Auto Mode. Keep the API/beat/default worker capacity separate from untrusted execution and build workloads. Apply taints, tolerations, node affinity and capacity limits deliberately.
- Install only required add-ons: VPC CNI, CoreDNS, kube-proxy, EKS Pod Identity Agent if used, metrics-server if autoscaling requires it, and storage/ingress components required by the chosen design. Pin supported versions in IaC.
- Create namespaces and resource quotas for platform, build and execution workloads; configure Pod Security Standards (restricted where feasible), default-deny policies, and service-account token automounting off except where a workload needs Kubernetes API access.
- Configure ALB/TLS for existing API hostnames. Preserve `/api-health`, callback paths, API docs, OIDC discovery and OAuth redirects. Test client IP/proxy behavior against `TRUSTED_PROXY_COUNT` and `INTERNAL_NETWORKS`.
- Prefer private EKS API endpoints or restrict public endpoint access to approved administrative/CI sources. CI should assume an AWS role through GitHub OIDC and receive only the Kubernetes permissions it needs.

#### 0.2 Database and Redis

- Production: preserve the existing database initially if it is already a supported managed PostgreSQL/PostGIS service and is reachable privately from EKS. If the current database is self-managed, design and rehearse a migration to managed PostgreSQL with PostGIS and required extensions; do not assume an RDS instance or silently replace it.
- Staging: the checked-in staging Compose file runs PostGIS 16-3.6. Decide whether staging will also move to managed PostgreSQL. Keep its test-user/data refresh procedure and ensure it cannot point to production accidentally.
- Verify PostgreSQL/PostGIS versions, extensions, collation, connection limits, backups/PITR, TLS, security groups, pool sizing, and migration/rollback compatibility. Update `DATABASE_URL` through secret management.
- Move Redis only after identifying all Redis consumers, queue/result retention, rate-limit/cache data, eviction policy, TLS/auth settings, and whether the Celery broker needs persistence/failover. Validate Celery redelivery and duplicate-task behavior after node or Redis failure.
- Never run incompatible old and new migrations concurrently. Use additive schema changes, deploy compatible readers/writers, migrate/backfill, then remove old fields only in a later release.

#### 0.3 Image registries and storage

- Keep the existing ECR repository and build flow for the API image. Create/confirm a separate ECR repository or repository set for execution images; do not assume the API repository currently stores user-script images.
- Inventory all active script image references and their exact registry/tag or digest. Migrate images with integrity verification and preserve every tag needed by scripts in `SUCCESS` state. Use immutable, explicit version/digest references for future builds and executions.
- Configure ECR encryption, scanning, repository policy, lifecycle rules, and retention for active images and rollback images. Do not expire untagged images until the application’s image reference and deletion behavior is verified.
- Preserve S3 bucket privacy and object key layout for scripts, compressed parameters/results, and public shares. Use Pod Identity/IRSA and least-privilege bucket/prefix policies. Add VPC endpoints where appropriate; do not make result or upload buckets public as a migration shortcut.
- Keep GCS/GEE service identity access and AWS Batch role access distinct from API access.

#### 0.4 IAM and secrets

- Create separate identities for API, default Celery workers, Kubernetes orchestration worker, execution pods, build tasks, public-share components, and any external controller.
- Grant Kubernetes job create/get/list/watch/delete only to the orchestration service account and only in the build/execution namespaces it owns. Scope log-read permission to jobs needed for the existing log endpoint. The build runtime must not inherit API secrets, DB access, or broad cluster administration.
- Use EKS Pod Identity by default where supported; use IRSA where a dependency or environment requires it. Do not expose EC2 instance metadata to pods as an identity mechanism.
- Store secret values in Secrets Manager or the approved vault; sync/project them at runtime. Define rotation and rollback for JWT, session, OIDC signing, execution callback, public-share session, Google, SparkPost, database, and Redis credentials.

#### 0.5 Execution capacity and isolation

- Start with a minimum execution node capacity that meets the agreed latency objective; do not assume an always-on “pause pod” is needed. Measure cold-start duration and cost in staging before adding a warm pool.
- For Karpenter, use a maintained release, supported NodePool/EC2NodeClass APIs, bounded instance families/regions, capacity types and disruption budgets. Ensure interruption handling and graceful termination work with the execution contract. Avoid Spot for long-lived workloads until they are resumable or interruption-safe.
- Use AWS Batch for the workloads already routed to Batch; do not move those jobs into EKS as part of this migration.
- For arbitrary user scripts, consider a sandbox runtime such as gVisor/Kata where compatible and cost-effective. Require non-root, read-only root filesystem where possible, dropped Linux capabilities, seccomp, no privilege escalation, resource and ephemeral-storage requests/limits, active deadline, and no service-account token unless essential.
- Block IPv4 and IPv6 access to instance/control-plane metadata from build and execution pods. Do not rely on an ingress-only NetworkPolicy to enforce egress. Add only the required DNS, API callback, S3, GEE/Google, openEO and other destination flows, using VPC endpoints, egress proxy or CNI FQDN policy as appropriate.

### Phase 1: Kubernetes manifests and deployment packaging

Use one reviewed Helm chart or Kustomize base with explicit staging/production overlays. Keep generated values free of secrets. Include:

- API Deployment, Service, ingress, HPA only after load/resource baselines, PDB, startup/readiness/liveness probes, resource requests/limits, security context, topology spread and graceful shutdown.
- Default Celery worker Deployment for non-orchestration tasks; separate orchestration worker only for tasks requiring Kubernetes API permissions. Set queue names explicitly and preserve worker concurrency, late-ack, reject-on-worker-lost, prefetch and child process memory/task limits from current `make_celery`.
- One Celery beat instance per environment. Use a single-replica Deployment with safe rollout/leader-lock strategy; never scale it horizontally without a scheduler leader mechanism.
- One-shot database migration Job per release. Use the same immutable API image digest, wait for success before serving code that depends on the migration, retain failure logs, and do not auto-delete diagnostics before the release is verified.
- Namespaces, ServiceAccounts, narrowly scoped Roles/RoleBindings, NetworkPolicies, quotas/limits, and optional Karpenter/EKS Auto Mode configuration as infrastructure code.
- Secret integration, external-service CA/trust configuration, egress, logging/metrics, and a documented resource/configuration map for staging and production.

Probe design:

- `/ping` is currently database-independent and is appropriate for a liveness probe.
- `/api-health` executes `SELECT 1` but intentionally returns HTTP 200 even when the database check fails; its JSON reports database state. Do not treat its HTTP status alone as a database-aware readiness check.
- Keep the ALB health check tied to an endpoint whose actual behavior has been inspected. If traffic must stop when required dependencies are unavailable, add a dedicated readiness check with bounded dependency checks and clear failure semantics.
- A liveness probe must not restart all API pods because PostgreSQL, Redis, or a third-party service is temporarily unavailable.
- Add graceful termination so Gunicorn requests and Celery tasks can finish or be redelivered correctly during node drain/rollout.

### Phase 2: Replace Swarm execution and build orchestration

#### 2.1 Preserve compute-type routing

Current dispatch in `gefapi/services/execution_service.py` uses the per-script `compute_type`:

- `gee` (default): current Docker Swarm task; change its container launch to a Kubernetes Job.
- `openeo`: `openeo_run` submits to the configured external openEO backend; keep that flow and `monitor_openeo_jobs`.
- `batch`: `batch_run` submits to AWS Batch; keep that flow and Batch monitoring, including pipeline/array and Nextflow behavior.

Do not route every `compute_type` through a single `k8s_run` task. `ORCHESTRATOR` currently defaults to `"docker"` and is not evidence that a Kubernetes backend is implemented. Define and test an explicit migration configuration only when both backends’ behavior is known.

#### 2.2 GEE Kubernetes Job lifecycle

Implement a small orchestration service/task boundary (for example `gefapi/services/k8s_service.py`) that creates and cancels Jobs using the Kubernetes API. Keep application-level state transitions in the existing execution/status services rather than duplicating them in a new monitor.

Requirements:

1. Preserve the current ID-only Celery dispatch contract: worker messages contain an execution ID, not user credentials or full environments. Load current script, params, owner and required credentials in the trusted worker at execution time using the existing context-loading path.
2. Preserve execution Job inputs, script image selection, environment variables, resource mapping, S3 parameter object layout, `EXECUTION_TOKEN`/callback behavior, and `GET /execution/<id>` result semantics.
3. Use deterministic labels for execution ID, script ID and environment, with Kubernetes-safe values. Use an idempotency strategy for retries so duplicate task delivery does not create multiple active Jobs for one execution.
4. Match Docker resource reservations/limits to Kubernetes requests/limits with explicit byte/nanoCPU conversion, rounding, validation and default behavior. Reject invalid or impossible values before creating a Job.
5. Set `backoffLimit`, `activeDeadlineSeconds`, termination grace period, restart policy, TTL and ephemeral storage based on existing execution semantics and measured maxima. Avoid a short TTL that deletes logs before API clients can retrieve them.
6. On cancellation, transition through the existing cancellation workflow, delete the matching Job with a bounded graceful period, handle missing/already-finished Jobs idempotently, and record the final state once.
7. Prefer workload callbacks for progress and terminal result updates (already represented by execution callback routes). Add a periodic reconciliation task that compares active DB executions with Jobs and repairs missed callbacks. Reconciliation must tolerate eventual consistency, transient API errors, dispatch grace periods, and Job TTL cleanup.
8. Keep execution logs available through the existing DB log path and Docker-named log endpoint. Define bounded pod-log retrieval before deleting Jobs; never return raw environment values or secrets in logs.
9. Limit the orchestration worker’s Kubernetes permissions and namespace scope. Execution pods themselves must not have permission to create/delete Jobs or read other pods.
10. Add tests for duplicate dispatch, missing/deleted Job, callback/reconcile race, success/failure, Job creation error, cancellation race, logs after completion, resource conversion, and task redelivery.

Do not use the draft’s old synchronous polling sample as a design. A Celery task that waits in a loop for a job to finish occupies a worker for the execution duration, obscures cancellation, and makes retry semantics fragile.

#### 2.3 Script image build flow

Replace `docker_build`/Docker SDK daemon operations with an asynchronous build backend:

1. API upload behavior remains compatible: validate the archive/configuration, store the source archive in S3, and mark/log the build state through existing models.
2. Submit a build using only a script ID and an API-generated, bounded S3 context reference. Use CodeBuild by default or the approved rootless BuildKit alternative from Section 2.3.
3. Build from the repository’s actual execution Dockerfile/context and pass the same script environment/version build arguments currently used. Validate build compatibility against all active script types.
4. Push to ECR with immutable build identity (script ID plus source/version metadata) and record the resulting digest/tag in a compatible model field or existing contract. Never overwrite an image reference that a running execution may be using.
5. Update script status/logs once on success/failure, cap log text and build duration, make retries safe, and expose failures through the existing script status/log endpoints.
6. Give the build service only source-prefix read, target-repository push and logging permissions. Do not mount `/var/run/docker.sock`, pass database credentials to an untrusted build, or allow the build role to push arbitrary repositories.

Before removing the old builder, rebuild representative GEE scripts and verify identical runtime behavior, dependency versions, image architecture, ECR pull access, and rollback references.

#### 2.4 Cluster status and logs

- Update the implementation behind `/api/v1/status/cluster` to report a stable, sanitized Kubernetes view appropriate for the existing ADMIN+ permission. Preserve `/status/swarm` as a compatibility alias during transition; do not expose pod names, internal IPs, IAM data, or sensitive failure details to ordinary users.
- Migrate `/status` and `/api/v1/stats/health` according to their current public/administrative contracts; they are not substitutes for Kubernetes liveness probes.
- Keep the route name `/execution/<id>/docker-logs` for compatibility while it reads retained execution logs or pod logs. Preserve authorization and 404/empty behavior.
- Remove Docker daemon status/cancellation/log calls only after the replacement is serving those contracts and active Swarm jobs are drained.

### Phase 3: Celery schedule, queues, and operations

Reconcile `gefapi/celery.py`, `gefapi/tasks/__init__.py`, Compose queue configuration, and the task modules together. Do not copy a partial schedule from an older draft.

Preserve and test all active scheduled work, including:

- execution queue processing, stale/finished/failed execution cleanup, and execution cancellation;
- Swarm cache/failure/completion monitoring (replace only the Swarm-specific behavior);
- AWS Batch and openEO monitors;
- refresh-token, user-account and deletion-audit cleanup;
- public-share cleanup and validation tasks;
- SparkPost suppression synchronization and bulk-email tasks;
- dashboard/execution/user statistics cache refresh and warmup;
- app-access notification tasks and any other registered task discovered during the schedule audit.

For rollout:

1. Deploy compatible worker code and task names before changing producers. Keep the old implementation available while queued/in-flight task formats are drained.
2. Ensure only one beat scheduler and one effective execution queue processor run per environment. Avoid duplicate periodic cleanup, email, share-validation, or execution dispatch.
3. Decide which queue performs Kubernetes Job creation and log/status reads. Do not route all periodic tasks to a privileged orchestration worker.
4. Preserve Celery late acknowledgement, requeue-on-worker-loss, prefetch and process recycling settings; test duplicate delivery and visibility timeout against the chosen Redis service.
5. Pause new submissions or use an explicit queue drain/cutover procedure before removing old workers. Account for Batch and openEO jobs that are still running remotely.

Update `docs/background-tasks.md` and the runbook after the deployed schedule has been verified.

### Phase 4: Public web-map sharing

Treat public shares as a distinct feature slice. In the current repository, the API can manage shares and validate PMTiles/COG content; the optional Swarm overlay separately runs a viewer, viewer gateway and Martin, and the configured TiTiler is a private Lambda or internal service.

- Inventory whether public shares are enabled in each environment and the actual viewer/API origins, S3 bucket, session key, TiTiler ARN/URL, Martin configuration, upload limits, quotas and lifecycle rules.
- Migrate viewer and gateway as separate stateless workloads with read-only filesystems, non-root users, resource bounds, and a private service path behind the intended public gateway. Do not make Martin, TiTiler or the API’s internal paths directly public.
- Preserve short-lived viewer sessions, origin checks, manifest authorization, object privacy, multipart upload completion, quotas, and cleanup.
- Preserve Martin’s read-only S3 access as a distinct identity. Preserve TiTiler invocation permissions and ensure its Lambda/API remains private to the intended caller.
- Keep validation fail-closed. If validation is later isolated into a dedicated sandboxed service, scope it as a separate security project; do not grant it database, AWS, or Kubernetes credentials.
- Test public-share creation/upload/publish/rotate/delete, expiry cleanup, valid/invalid session, raster/vector display, gateway behavior, S3 policy, and disabled-feature behavior before cutover.

### Phase 5: CI/CD and environment cutover

The current CodeDeploy workflows already use GitHub OIDC and ECR/Buildx. Preserve that secure credential pattern while replacing the deployment stage. Do not replace unrelated translation, test, public-viewer, or tiler workflows without a separate reason.

Recommended release workflow:

1. CI runs unit/integration tests, Ruff, dependency/security checks, and any container tests required for the image.
2. Build and scan the API and auxiliary images. Publish immutable references and record digests, SBOM/provenance when available, and commit SHA.
3. Assume a narrowly scoped AWS role using GitHub OIDC. Do not use long-lived AWS access keys or print environment/secrets.
4. Apply the versioned Helm/Kustomize release for staging. Run the one-shot database migration Job and require it to succeed before rollout.
5. Wait for rollout, then run smoke tests against API health, auth/OIDC discovery, representative authenticated routes, and feature-specific worker integrations.
6. Promote the same image digests/configuration to production behind the existing approval gate. Use a small canary/controlled traffic shift if supported by the ingress design.
7. Use declarative rollback to a prior known-good release. Database changes must remain backward compatible with that release; rolling back a container is not a database rollback.
8. Keep the current CodeDeploy workflows and rollback path intact until production EKS rollback is rehearsed and the old stack is decommissioned.

#### Staging rollout

1. Deploy the EKS staging stack with API traffic restricted to an approved test path/hostname.
2. Use staging database and Redis resources only. Verify no staging workload can reach production DB or use production-only credentials.
3. Import/migrate required test fixtures and representative scripts/images.
4. Exercise each execution backend independently: GEE Job, openEO remote job, AWS Batch single job and pipeline/array path.
5. Exercise OIDC, JWT/refresh/revocation, GEE/openEO credential flows, Google Groups/app access as configured, email, stats, rate limiting, S3 results and optional public sharing.
6. Verify logs, traces, queue depth, retries/redelivery, node interruption, Job cleanup, ingress callbacks, scaling, pod restart and database failover/restore behavior.
7. Run workload and resource tests to tune API/worker requests, limits, autoscaling, DB connection pool, and execution capacity. Replace earlier hand-estimated cost figures with measured AWS pricing and observed usage.

#### Production cutover

1. Obtain backup/restore and rollback sign-off. Confirm DNS TTL, TLS, WAF/ingress, RDS/Redis security groups, ECR access, callbacks, and all external service identities.
2. Announce a bounded submission pause or a documented compatible queue strategy. Let active Swarm executions finish where practical; otherwise document which jobs can safely continue and how callbacks will reach the migrated API.
3. Ensure only one beat scheduler/queue processor can dispatch or run periodic side effects. Quiesce old producers before starting new ones unless cross-version compatibility has been proven.
4. Deploy the compatible EKS release and run migrations once. Verify health, route snapshot, OIDC, database, Redis, S3 and callback connectivity.
5. Switch traffic/DNS or load-balancer targets. Verify clients and callbacks use the stable public/internal API URLs.
6. Resume submissions and monitor API errors/latency, Celery queue age, worker restarts, Job start/completion rates, callback reconciliation, DB connections/replication/locks, Redis memory/evictions, ECR/S3 errors, and external integrations.
7. Keep Swarm resources and rollback images available for the agreed observation period. Do not destroy old database, Redis, registry or object data during the initial cutover.

### Phase 6: Decommission legacy infrastructure

Only after the agreed stability period, restore drill and explicit sign-off:

1. Verify no API, worker, scheduled task, script, Batch job, viewer, gateway or operator depends on Swarm endpoints, overlays, Docker socket, self-hosted registry, EC2 metadata, CodeDeploy hooks or old hostnames.
2. Confirm active execution images are in ECR, still retrievable by digest, and covered by lifecycle protections.
3. Remove Swarm stacks and EC2 nodes in a staged, reversible order. Retain backups and required logs.
4. Retire the self-hosted registry only after consumers are migrated and rollback window closes.
5. Remove obsolete CodeDeploy resources, deployment roles, security groups, DNS and secrets only after verifying no other service uses them.
6. Update deployment docs, API/background-task docs, service ownership, alerting, cost budgets, disaster recovery and on-call procedures.

## 4. Acceptance criteria

### API and compatibility

- [ ] Generated route/method snapshot has no unexplained endpoint removals, additions or method changes.
- [ ] `/api-health`, `/ping`, `/api/docs/`, `/swagger.json`, application auth endpoints, `/api/v1` routes, OIDC discovery/JWKS and OAuth redirect flows behave as before.
- [ ] Authentication, scopes, admin permissions, rate limits, CORS, trusted proxy handling, pagination, sorting, filters, exports and response/error shapes are covered.
- [ ] Legacy `/status/swarm` and `/execution/<id>/docker-logs` work with their established contract; `/status/cluster` reports only intended information.
- [ ] Script upload/build/status/log/download, execution submission/status/progress/results/logs/cancellation and callback authorization pass end-to-end.
- [ ] User/session/OIDC token lifecycle, GEE/openEO credentials, app access, Google Groups, boundaries, news, bulk email and unsubscribe are tested where enabled.
- [ ] Public-share lifecycle and viewer routes pass in enabled environments; disabled environments fail closed.

### Compute and operations

- [ ] GEE container execution runs as a Kubernetes Job with correct identity, image digest, parameters, resources, status, callbacks, logs, cancellation, cleanup and duplicate-dispatch handling.
- [ ] `compute_type="openeo"` still submits to and monitors the configured external backend; it is not accidentally moved to the GEE Job path.
- [ ] `compute_type="batch"` and supported AWS Batch pipeline/Nextflow behaviors remain intact.
- [ ] No user build or execution pod has Docker socket access, broad Kubernetes permissions, static cloud credentials, unrestricted metadata access, or unnecessary secrets.
- [ ] All Celery tasks and beat entries are accounted for; one scheduler runs per environment and workers consume every configured queue.
- [ ] Redis failover/redelivery behavior, task idempotency, graceful termination, logs/metrics, alarms and runbooks are validated.
- [ ] PostgreSQL/PostGIS version/extensions, backups, restore, migration and connection limits are verified.
- [ ] Staging and production IAM, network policies, secrets and data stores are isolated as intended.
- [ ] Rollback to the last Swarm release and to the last EKS release is documented and rehearsed; database compatibility is included.

## 5. Risk register

| Risk | Required mitigation |
|---|---|
| Incorrect assumption about production DB or staging data | Inventory actual endpoints/provider and rehearse backup/restore before provisioning or changing `DATABASE_URL` |
| Active or queued work is lost/duplicated during worker cutover | Enumerate Swarm, Batch, openEO and Celery work; pause producers, drain or preserve compatible workers, and ensure beat/queue processor singleton behavior |
| Script image references break after registry migration | Export script-to-image references, migrate and verify images by digest, preserve active/rollback tags, and test ECR pulls from each backend |
| Untrusted script build compromises platform | Use isolated build service and distinct role; no Docker socket, DB/API credentials, broad ECR access or cluster-admin token |
| Untrusted execution reaches metadata/internal systems | Separate nodes/namespaces, block IPv4/IPv6 metadata, use enforced NetworkPolicy/egress, pod hardening and sandbox runtime evaluation |
| GEE callback/result paths fail from Jobs | Test stable internal API URL, TLS/trust, callback token, retry/idempotency and result storage from execution subnets |
| OpenEO/Batch behavior is changed by a broad dispatcher rewrite | Preserve compute-type-specific backends and add separate regression tests before dispatch refactoring |
| Job TTL deletes logs needed by API clients | Set retention from observed retrieval behavior; preserve logs in durable execution records before Job deletion |
| Database changes prevent rollback | Expand/contract migrations; keep old and new application versions schema-compatible until rollback window closes |
| Monitoring endpoint leaks cluster details or has broad RBAC | Keep ADMIN+ authorization, return only sanitized status, and grant read permissions to the smallest required resources |
| Managed Redis configuration loses broker/cache state or causes redelivery storms | Audit every Redis consumer, retention and visibility behavior; rehearse recovery and queue drain |
| EKS version/add-on incompatibility or surprise support cost | Query current EKS lifecycle, pin compatible add-ons, maintain upgrade policy, avoid extended support unless explicitly approved |
| Public viewer/tile endpoints become directly exposed | Preserve the viewer gateway boundary, private Martin/TiTiler access, signed sessions and strict S3 policies |
| Stale schedules/tasks are missed in migration | Compare live Celery configuration and registered task modules against the full deployed schedule; update docs and alert on unconsumed queues |

## 6. Repository files to change or retain

Expected code/configuration touchpoints (exact changes depend on the chosen build and IaC designs):

| Path | Planned treatment |
|---|---|
| `gefapi/services/docker_service.py` | Replace production Swarm build/run/cancel/log operations in stages; retain local Docker support only if explicitly needed |
| `gefapi/services/execution_service.py` | Keep compute-type routing and ID-only dispatch; add a tested Kubernetes path only for GEE/container execution |
| `gefapi/services/script_service.py` | Dispatch script image builds through the isolated build backend |
| `gefapi/tasks/` | Replace Swarm monitors; retain Batch, openEO, queue, cleanup, stats, email, user, and public-share tasks |
| `gefapi/celery.py`, `gefapi/tasks/__init__.py` | Preserve all task routes/schedules and worker settings; remove Swarm-specific entries only after replacement |
| `gefapi/config/base.py`, `prod.py`, `staging.py` | Add minimal backend configuration; remove Docker settings only after all Docker paths are retired |
| `gefapi/routes/api/v1/monitoring.py` | Implement sanitized cluster status while keeping legacy route compatibility |
| `tests/` | Add orchestration/build/task integration tests and route-contract checks; update tests for old/new cutover behavior |
| `docs/background-tasks.md`, `docs/deployment/` | Update schedules, worker roles, release, rollback, secrets, and operational procedures |
| `.github/workflows/codedeploy_staging.yml`, `codedeploy_production.yml` | Replace deployment stage after EKS release path works; retain the existing AWS OIDC image build security pattern |
| `docker-compose*.yml`, `scripts/codedeploy/`, `appspec.yml`, `deployment_utils.py` | Keep until local development and production rollback no longer depend on them; remove only after reference search and owner sign-off |
| `public-viewer/`, `docker-compose.public-shares.yml`, `.github/workflows/public-viewer.yml`, `public-share-tiler.yml` | Retain or migrate as a separate public-sharing workstream; do not delete as generic Swarm cleanup |

## 7. Recommended external references

- [Amazon EKS Kubernetes version lifecycle](https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html) — supported versions and support dates; recheck before implementation.
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html) — workload identity; use IRSA if a required integration calls for it.
- [Amazon EKS Auto Mode](https://docs.aws.amazon.com/eks/latest/userguide/automode.html) — evaluate against managed node groups plus Karpenter and separately managed add-ons.
- [Kaniko upstream repository](https://github.com/GoogleContainerTools/kaniko) — archived June 2025; not recommended for a new migration.
- [BuildKit](https://github.com/moby/buildkit) — potential rootless builder; evaluate isolation and compatibility before adoption.
- [Kubernetes NetworkPolicies](https://kubernetes.io/docs/concepts/services-networking/network-policies/) — policies require an enforcing network implementation.
