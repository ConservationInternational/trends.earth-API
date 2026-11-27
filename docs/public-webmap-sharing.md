# Expiring public web-map sharing (API)

The API implements the owner lifecycle, private S3 transfers, COG and PMTiles
validation, path-token links, CDN-backed tile proxies, legacy viewer sessions, and cleanup for
the [expiring web-map sharing plan](https://github.com/ConservationInternational/trends.earth/blob/main/docs/EXPIRING_PUBLIC_WEBMAP_SHARING_PLAN.md).
The feature is disabled by default. It does not publish objects, expose TiTiler,
or grant arbitrary object access. A separate Dash public viewer with OpenLayers
and a restricted Nginx gateway is implemented in `public-viewer/`. The QGIS
client remains a separate repository deliverable.

## Remaining release work

The API's draft/upload/validate/publish lifecycle and public viewer are
implemented. A new link-generation endpoint is not needed. The remaining
release work is:

- Run `scripts/setup/setup_public_shares_infrastructure.py` (see
  [Provisioning and deployment automation](#provisioning-and-deployment-automation))
  per environment to create the bucket, IAM, ECR, TiTiler Lambda, ALB routing and
  GitHub settings. It has not yet been applied in AWS. Create the viewer DNS
  record if it is outside this account's Route 53.
- Commit/push the CodeDeploy workflow and hook changes before the first overlay
  deployment. Staging/production workflows now publish viewer/gateway images,
  write sharing settings and bundle/deploy the overlay when
  `PUBLIC_SHARES_INFRA_ENABLED=true`.
- Lambda updates are deployed by rerunning the script, not by CI, so the GitHub
  deployment role does not need CloudFormation/IAM rights.
- Add/run a live sharing acceptance harness, then enable `PUBLIC_SHARES_ENABLED`.
  Current viewer tests use a fixture API, not live S3, API authorization or
  renderer services.

The plugin snapshot/upload/publication implementation is in a separate,
uncommitted plugin worktree and still needs integration into the plugin release.
Result-row shortcuts are a UX follow-up rather than a blocker for its existing
menu-based flow. Proxy-aware signed multipart transport remains plugin work.
The [sharing plan](https://github.com/ConservationInternational/trends.earth/blob/main/docs/EXPIRING_PUBLIC_WEBMAP_SHARING_PLAN.md)
tracks these separately from optional OIDC, persistent resume, plots/tables and
future Kubernetes/Cilium isolation. Keep sharing disabled until the required
deployment and live acceptance checks pass.

### Implementation versus rollout (checked 2026-11-22)

No additional share-generation endpoint, PMTiles conversion service or public
viewer implementation is needed for the current map-only contract. The main
remaining code is release automation and a live acceptance harness spanning
the actual plugin, API, S3, Lambda/Martin and browser, with authorization,
expiry/revocation, version-aware cleanup and quota-release assertions.
Proxy-aware signed uploads are a conditional plugin requirement; representative
export/style failures must be addressed if found during acceptance testing.

The existing plugin code still needs review/integration from its separate
worktree. S3/IAM/lifecycle, SAM/Martin deployment and same-origin HTTPS are
automated by the provisioning script and CodeDeploy overlay but not yet applied;
migrations and worker/beat tasks ship with the normal API deployment. Local source
and workflow inspection does not verify deployed AWS state. Optional result-row
shortcuts, OIDC, persistent resume, plots/tables and Cilium remain outside the
initial menu-based, legacy-login Swarm pilot.

## Public Dash/OpenLayers viewer

The viewer displays mixed raster PNG and vector MVT layers, extent/zoom/pan,
layer toggles, opacity, safe uniform/categorical vector styles, legends,
text-only attribution and feature attributes. OpenLayers owns the map lifecycle
and provides native MVT rendering and feature picking; Dash hosts the responsive
page independently of the authenticated API/dashboard. Current map tiles use
EPSG:3857 and manifest extents use EPSG:4326.

The **Layers** list starts in the owner's order, with the first layer drawn on
top. Viewers can drag a layer's handle, or focus it and press the Up and Down
arrow keys, to restack layers for themselves. This order is local to the page:
it is never sent to the API. It survives manifest refreshes, and layers added
later keep their shared position.

Shares can carry an optional WGS84 GeoJSON Polygon or MultiPolygon display
mask (maximum 512 KiB and 30,000 coordinates). The API validates the geometry,
splits antimeridian crossings, and stores a snapshot with the share. Owners can
replace or remove it with `PUT /api/v1/shares/<id>/mask`, supplying `mask` and
the current `mask_version`; stale versions receive HTTP 409. The authorized
manifest returns the mask and version, and open viewers check for changes every
30 seconds. Migration and the updated viewer must be deployed before clients
create masked shares.

The mask clips shared raster and vector layers in OpenLayers, not the basemap or
borders. It is not access control: authorized viewers can retrieve underlying
tiles and data outside the mask. The mask itself is visible to anyone with the
link and is removed from the database during share cleanup.

After the link is authorized, the viewer adds the same Mapbox basemaps
(Default, Satellite, Topographic and Dark), national-border lines
(`mapbox.mapbox-streets-v8`, `admin` layer, `admin_level` 0, maritime borders
excluded) and dark or light place-name labels (styles
`clgyh74fy00g901qy2bmmerq9` and `clgyr7y4u009l01qt32300lx9`) that Resilience
Atlas uses, with Resilience Atlas's public `pk.` token. Mapbox requests carry
no share secret, cookie or referrer, but they do reveal the viewer's IP address
and the viewed area to Mapbox.

The header shows only the Trends.Earth logo and the map title, and the page uses
the API UI's red-delta favicon. A column of map buttons at the top left holds
zoom in and out, **Show full extent**, a **National borders** toggle, a **Map
labels** picker and a **Basemap** button. Labels start off, as in Resilience
Atlas, and request no tiles until enabled. Viewers can choose off, dark or light
labels independently of the basemap. The
basemap button opens a Resilience Atlas-style panel of basemap swatches, which
closes on Escape or a click outside it. Fitting to the full extent pads the view
so data is not hidden under the button column or the footer. A footer across the
bottom of the map shows, in order:

- the Mapbox and OpenStreetMap copyrights;
- the Trends.Earth boundary disclaimer, with links to the Conservation
  International privacy policy and terms of use;
- the unlisted-snapshot notice;
- the link's expiry time in UTC, rounded down to the minute.

On larger screens the **Layers** list forms a sidebar that starts open. A
chevron button beside the **Layers** title hides it so the map takes the full
width, and a floating **Layers** button reopens it.

On small screens (at most 720 px wide or 500 px tall) the map fills the page
below a one-line header. The **Layers** list opens as a full-height sheet from a
floating **Layers** button. The chevron beside the title (**Show map**) or
Escape closes it. Selecting a vector feature on the map opens the sheet at the
feature's attributes. The hidden part of the page is `inert`, and focus moves
between the two buttons. The footer collapses the disclaimer behind a
**Disclaimer** toggle but always shows the copyrights, notice and expiry time. A
red dot marks the Layers button when the sheet holds a warning or error.

New links use `/share/s/<unguessable-token>` with no separate secret or session
cookie. Copying the browser address bar or forwarding the URL preserves access.
Existing `/share/<UUID>#<secret>` links still work: the early bootstrap removes
the fragment and exchanges it for the API's share-bound HttpOnly cookie.
Owner link retrieval returns the new path form using the existing stored token;
no rotation is needed. All data requests go through the same-origin gateway.
The viewer clears layers and attributes at the known share/session deadline,
checks authorization every 30 seconds and on returning to a cached/hidden page,
and clears data on authorization failures. Already downloaded data cannot be
recalled from recipients; do not describe expiration as DRM.

Nginx permits only share pages, local Dash/assets, public session/refresh,
manifest/tile routes, and health. It blocks owner APIs, dashboard routes and Dash
data callbacks. The Dash service receives no API/database/cloud configuration
or browser cookies; neither Dash nor Martin publishes a port. Both viewer
containers are non-root and read-only with writable temporary files only.
Access logs are disabled; non-tile responses are no-store with no-referrer, noindex and
a CSP using Dash renderer hashes rather than inline-script/eval permissions.
The CSP's only external origin is `https://api.mapbox.com` (`img-src` and
`connect-src`) for basemap and border tiles.
Keep equivalent redaction at the external TLS terminator: never record cookies,
request bodies, path tokens, capability fragments or full viewer URLs. API
Gunicorn access logging excludes token-bearing routes, and application
security/rate-limit event URLs redact tokens. CloudFront access logging is not
enabled by the sharing template.

CloudFront sits in front of this gateway for both TiTiler raster and Martin
vector tiles; neither renderer is directly public. Only successful path-token
tile responses are cacheable, for at most **four days**, shortened to the
remaining share lifetime. The complete token, share ID, layer ID and tile
coordinates are in the cache key. Manifests, cookies, legacy sessions and errors
are not cached. Browser requests use `no-store`; response `max-age=0` prevents
fresh browser reuse, while `s-maxage` controls edge reuse. Expired tiles must
revalidate; stale-on-error serving is not enabled. Rotation/revocation updates
the origin immediately and requests an invalidation covering all token
generations for that map. **Already cached tiles can be served until the
invalidation propagates**; if invalidation fails the API returns 503 explicitly,
and retrying rotation/revocation reissues the per-map invalidation. Do not promise
instant removal from CDN caches or from recipients who downloaded data.
The cleanup worker retries invalidation of revoked shares before deleting their
records, including ownerless shares after account deletion. Account-deletion
invalidations therefore wait for the next cleanup batch (normally five minutes).

Build from the repository root:

```bash
docker build -f public-viewer/Dockerfile -t <registry>/public-viewer:<release> .
docker build -f public-viewer/nginx/Dockerfile -t <registry>/public-viewer-gateway:<release> .
docker push <registry>/public-viewer:<release>
docker push <registry>/public-viewer-gateway:<release>
```

Set `PUBLIC_VIEWER_IMAGE` and `PUBLIC_VIEWER_GATEWAY_IMAGE` to those published
images (prefer immutable digests), then use the existing public-shares Swarm
overlay described below. It adds the separate Dash service, Nginx gateway, an
internal viewer-only overlay and an API/gateway overlay. `PUBLIC_VIEWER_PORT`
defaults to 8080. Route the chosen **HTTPS** viewer host through your TLS
terminator to that gateway port, preserving Host and Origin. Restrict direct
HTTP ingress to the TLS terminator; Secure cookies intentionally do not work
over plain HTTP. Set `PUBLIC_SHARES_PUBLIC_ORIGIN=https://<viewer-host>` and
`PUBLIC_SHARES_VIEWER_BASE_URL=https://<viewer-host>/share` on the API and workers.
The gateway's `/health` is a liveness check; the Dash image also checks its own
`/health`. Verify the full session/manifest/tile path before enabling sharing.
Bucket, migrations, Celery/beat, TiTiler and Martin prerequisites still apply.

For local integration with an API already listening on host port 3000:
`docker compose -f public-viewer/compose.yml up --build`. The gateway binds only
to localhost; supply your own local HTTPS terminator for browser authorization.
For reproducible tests from `public-viewer/`:

```bash
npm ci --ignore-scripts
npm run build
npx playwright install chromium
docker build -f Dockerfile -t trendsearth-public-viewer:local ..
docker build -f nginx/Dockerfile -t trendsearth-public-viewer-gateway:local ..
npm test
```

The browser suite starts an isolated Compose stack with the actual Dash/Nginx
images and a fixture API behind a temporary local TLS terminator. It checks
rendered pixels, controls/attributes, gzip/empty tiles, expiry/revocation/retry,
forwarded path links without cookies, legacy fragment/referrer handling,
bounded tile concurrency/retries, slow tiles, CSP, restricted routes and mobile/keyboard
behavior. It does not replace real AWS/TiTiler/Martin/staging or assistive-device
testing. Ports 18951/18952 can be overridden with `VIEWER_TEST_PORT`/
`FIXTURE_PORT`. The `Public viewer` workflow builds images and runs these tests.

Dash was chosen to allow future linked plots and tables. **Plot/table sharing is
not implemented**: it needs validated asset declarations, quota/expiry cleanup,
authorized data endpoints and safe rendering. Do not expose
`/_dash-update-component` or add unauthenticated data callbacks; authorize every
future data request against the current share/session state.

## Provisioning and deployment automation

`scripts/setup/setup_public_shares_infrastructure.py` provisions one environment
idempotently. It only prints a plan unless `--apply` is passed:

```bash
poetry run python scripts/setup/setup_public_shares_infrastructure.py \
  --environment staging
poetry run python scripts/setup/setup_public_shares_infrastructure.py \
  --environment staging --apply [--trigger-deploy --deploy-ref master]
```

The viewer host defaults to `viewer-staging.trends.earth` for staging and
`viewer.trends.earth` for production, with share links under `/share`
(for example `https://viewer-staging.trends.earth/share/s/<token>`). Pass
`--viewer-host` only to override it.

It requires administrator AWS credentials (`--profile`/`--region`), an
authenticated `gh` CLI, and Docker and the SAM CLI for the tiler (`--sam-cli PATH`,
or `--skip-tiler`). It performs the following steps:

1. Creates/reconciles `trendsearth-public-viewer-<env>-<account>` with blocked
   public access, ACLs disabled, SSE-S3, versioning, a TLS-only policy and
   lifecycle backstops. Current objects expire after 100 days, noncurrent
   versions after 1 day, and incomplete uploads after 2 days.
2. Adds `TrendsEarthPublicViewer-<env>` to the Swarm instance role
   (`trendsearth-api-docker-swarm-role`). This role is used by the API/workers;
   the policy grants list/read/write/delete/version/multipart access only under
   the share prefix. Staging and production share this instance role, so each
   environment's API can technically reach both buckets.
3. Creates `trendsearth-public-viewer-martin-<env>` with read/list access to
   only `<prefix>/vector/`. Its access key is stored only as GitHub environment
   secrets through stdin. Use `--rotate-martin-key` to replace it; the script
   lists old keys to delete after the next successful deployment.
4. Creates the `trendsearth-public-viewer` and
   `trendsearth-public-viewer-gateway` ECR repositories. It adds
   `GitHubActionsPublicViewerECR` push access to the GitHub OIDC deployment role.
5. Builds/deploys the SAM stack `trendsearth-public-viewer-tiler-<env>` and reads
   `RendererFunctionArn`.
6. Adds an HTTPS host rule and `te-public-viewer-<env>` target group to the
   `trendsearth-api` ALB. It registers the same Swarm nodes as the environment's
   API target group, checks the listener certificate, and opens only the gateway
   port from the ALB security group. Ports are 8080 for production and 8081 for
   staging.
7. Deploys the `trendsearth-public-viewer-cdn-<env>` CloudFormation stack in
   `us-east-1` from `config/public-shares/cdn.yaml`. CloudFront forwards HTTPS
   requests to the ALB with the viewer Host header, caches only the path-token
   tile route (four-day maximum, zero minimum/default), and leaves the rest
   uncached. It grants the API role `cloudfront:CreateInvalidation` only on this
   distribution. Errors have zero cache TTL. No paid provisioned concurrency
   or Lambda warm-up schedule is created. A CloudFront 5xx alarm covers renderer
   HTTP failures as well as invocation failures; attach notification actions.
8. Discovers an ACM certificate in `us-east-1` (or uses
   `--cdn-certificate-arn`). If absent it requests a DNS-validated certificate.
   With `--route53-zone-id` it writes the validation CNAMEs; otherwise it prints
   them. If validation is pending, it stops before deploying the CDN; complete
   validation, wait for ISSUED and rerun. The ALB listener must independently
   have a certificate covering the viewer host.
9. Creates the viewer CNAME **to CloudFront, not directly to the ALB**, when
   `--route53-zone-id` is given; otherwise it prints the CNAME to create manually.
10. Sets environment variables `PUBLIC_SHARES_S3_BUCKET`, `PUBLIC_SHARES_S3_PREFIX`,
   `PUBLIC_SHARES_PUBLIC_ORIGIN`, `PUBLIC_SHARES_VIEWER_BASE_URL`,
   `PUBLIC_SHARES_TITILER_LAMBDA_ARN`, `PUBLIC_SHARES_CDN_DISTRIBUTION_ID` and
   `PUBLIC_VIEWER_PORT`. Only when
   `PUBLIC_SHARES_ENABLED` is absent, it sets that variable to `false`. It
   generates the `PUBLIC_SHARES_SESSION_SECRET` secret if absent; use
   `--rotate-session-secret` to replace it, which invalidates viewer sessions
   and makes stored share links unretrievable until each share is rotated.
   Finally it sets `PUBLIC_SHARES_INFRA_ENABLED=true`.

The script never prints secret values and never enables sharing.
`--trigger-deploy` dispatches the environment's CodeDeploy workflow.

With `PUBLIC_SHARES_INFRA_ENABLED=true`, the staging/production workflows build
and push both viewer images, tagged `<env>-<sha>` and `<env>-latest`. They
fail if any sharing variable or secret is missing, then append the settings
to the generated env file. The bundle includes the overlay and Martin
configuration. `application-start.sh` adds the overlay to `docker stack deploy`
and gives Martin's Swarm config a content-hashed name, because Swarm configs are
immutable. `validate-service.sh` fails the deployment if the gateway's `/health`
check fails. A workflow rejects `PUBLIC_SHARES_ENABLED=true` unless the
infrastructure flag is also true. Automatic CodeDeploy rollbacks redeploy the
previous bundle and env file. Commit-specific rollbacks build a minimal env file,
so they omit the overlay. Because stack deploy does not prune, existing viewer
services stay unchanged, and the API fails closed with sharing unconfigured.

## Enablement and deployment requirements

Configure these values before setting `PUBLIC_SHARES_ENABLED=true`:

| Environment variable | Purpose |
|---|---|
| `PUBLIC_SHARES_S3_BUCKET` | Dedicated private S3 bucket; do not reuse execution results or scripts buckets. |
| `PUBLIC_SHARES_S3_PREFIX` | Server-owned prefix (default `public-shares`). |
| `PUBLIC_SHARES_VIEWER_BASE_URL` | HTTPS viewer path, e.g. `https://viewer.example/share`; must have the same origin as the API gateway. |
| `PUBLIC_SHARES_PUBLIC_ORIGIN` | Exact HTTPS origin used by the viewer, e.g. `https://viewer.example` (no path). |
| `PUBLIC_SHARES_TITILER_LAMBDA_ARN` | Private Lambda function/alias ARN, preferably the SAM stack's `RendererFunctionArn` output. Selected for raster tiles when set. |
| `PUBLIC_SHARES_TITILER_INTERNAL_URL` | Legacy internal HTTP TiTiler origin/base path; required only when no Lambda ARN is configured. Must be unreachable by public clients. |
| `PUBLIC_SHARES_MARTIN_INTERNAL_URL` | Internal Martin origin/base path; required for vector declarations and publication, not raster-only deployments. |
| `PUBLIC_SHARES_CDN_DISTRIBUTION_ID` | CloudFront distribution created by the script. Enables four-day edge cache headers and per-map invalidation on rotation/revocation. Without it tiles remain no-store. |
| `PUBLIC_SHARES_SESSION_SECRET` | Dedicated random secret of at least 32 characters for signed viewer cookies; an HKDF-derived key from it also encrypts stored share links. |
| `PUBLIC_SHARES_VIEWER_SESSION_TTL_SECONDS` | Rolling signed-cookie age (default 900 seconds). |
| `PUBLIC_SHARES_MAX_VIEWER_SESSION_LIFETIME_SECONDS` | Absolute session lifetime from capability exchange (default 86400 seconds); refresh does not extend this deadline. |

The API feature flag defaults to false. Configure the dedicated S3 bucket with
block-public-access, server-side encryption, and least-privilege API/worker/
TiTiler roles. Cleanup supports versioned buckets, so workers need permission
to list and delete object versions as well as read objects and abort multipart
uploads; vector promotion additionally requires `s3:PutObject` on the validated
prefix and read access to the staging object/version for S3 CopyObject. Allow
TiTiler to read raster objects. Configure an S3 lifecycle backstop to
abort incomplete multipart uploads and expire orphaned current/noncurrent
objects after a period longer than the maximum share retention and draft TTL.
The API cleanup worker deletes all object versions shortly after each share's
`expires_at`; request-time checks deny access at that exact timestamp. S3
lifecycle execution is asynchronous and is only a backstop, not the share
expiry clock. The API only issues multipart URLs for generated object keys.
Ensure reverse proxies redact request bodies and cookies and never log full
query strings. Keep the bucket and session-key settings available to cleanup
workers even when the feature flag is turned off.

The API image installs Alpine's `gdal-tools` package. Validation runs `gdalinfo`
on a downloaded local file, requires GeoTIFF/COG layout and a declared CRS,
checks the selected band and geographic extent, enforces the declared SHA-256
and size, and records only server-derived metadata. Raster transfers and
validation are bounded by configured share/file/layer quotas, multipart part
sizes, 1 GiB address-space and 16 MiB metadata-file limits for `gdalinfo`, a
90-second timeout, and single-threaded GDAL settings. Do not
expose TiTiler directly: the API authorizes the path token or legacy cookie,
checks share expiry and
layer membership, and constructs every internal COG URL from database-owned
keys and validated style fields.

Validation currently runs in the existing Celery worker. Process limits do not
isolate it from the worker's credentials or network. A private validator with
Cilium-enforced network policy is planned after the migration to Kubernetes.
The PMTiles subprocess has no inherited AWS/database environment, but it runs
as the worker OS user: this does not prevent access to files, instance metadata,
or worker-reachable services. Format validation is not proof of parser safety.

### Private TiTiler deployment (AWS Lambda)

`cloud_functions/titiler_cogs/` supplies a SAM image-based FastAPI/TiTiler/Mangum
renderer, following Resilience Atlas's Lambda packaging pattern. Unlike that
public deployment, this stack creates **no API Gateway, CloudFront distribution,
Function URL or anonymous invocation permission**. The Trends.Earth API
authorizes each viewer tile request and then makes an IAM-signed, synchronous
Lambda invocation. No capability secret, session cookie or bearer header is
forwarded. Martin remains the private Swarm service for vectors.

The renderer accepts only the COG PNG tile route and an allowlist of style
parameters. Sources must be canonical
`s3://<bucket>/<prefix>/<share-UUID>/<layer-UUID>.tif` keys in the configured
bucket. HTTP/local sources, other keys and non-tile endpoints are rejected.
The execution role has raster `s3:GetObject` and its own log-write permissions;
it has no database/API credentials, vector access or storage-write permissions.
Rendering errors are redacted. Successful PNGs are limited to 2 MiB and the API
also bounds and validates the invocation response.

This is private **invocation**, not enforced network isolation. The function
is not VPC-isolated and native raster parsing still carries risk. IAM principals
with invocation permission are trusted data-plane callers: the renderer cannot
independently check database expiry or revocation. Keep invocation permission
restricted to the API runtime role, preserve API authorization, and do not add
public triggers. Future validator/Cilium work remains separate.

Prerequisites:

- Install Docker and AWS SAM CLI and authenticate the deployment principal.
  Deploy in the share bucket's region. Use distinct stack/function names for
  staging and production.
- Provision the private share bucket first. `ShareBucket` and `SharePrefix`
  must match the API settings; the prefix must contain only alphanumeric,
  underscore or hyphen segments separated by `/`, with no leading/trailing `/`.
- Supply `ApiRoleName`, the **existing IAM role actually used by boto3 inside
  the API container**, not the deployment operator's role. The stack attaches
  invocation permission for the function's `live` alias only. Ensure the
  container can obtain that role's AWS credentials and reach the regional
  Lambda Invoke endpoint. A no-NAT VPC deployment needs a Lambda interface
  endpoint and suitable endpoint policy/network rules.
- For customer-managed SSE-KMS, supply `KmsKeyArn` and ensure its key policy
  permits the generated renderer role to decrypt. The template limits decrypt
  to regional S3 and the bucket/prefix encryption context, including S3 Bucket
  Keys. Bucket policies must also permit the renderer role.
- The deployment principal needs CloudFormation/ECR/artifact-bucket and IAM
  creation/attachment/pass-role permissions. The template does not create the
  share bucket or API runtime role.

From the repository root, validate, build and deploy:

```bash
sam validate --lint --template-file cloud_functions/titiler_cogs/template.yaml --region <region>
sam build --template-file cloud_functions/titiler_cogs/template.yaml
sam deploy --template-file .aws-sam/build/template.yaml \
  --stack-name trendsearth-public-viewer-tiler-staging --region <region> \
  --resolve-image-repos --resolve-s3 --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    FunctionName=trendsearth-public-viewer-tiler-staging \
    ShareBucket=<private-share-bucket> SharePrefix=public-shares \
    ApiRoleName=<api-runtime-role>
```

For SSE-KMS add `KmsKeyArn=<key-ARN>` to the overrides. `ReservedConcurrency`
defaults to 50; memory is 2048 MiB and timeout is 30 seconds. This is on-demand
Lambda: reserved concurrency limits capacity but does not keep instances warm
or incur provisioned-concurrency idle charges. A 32 MiB GDAL range cache avoids
the invalid zero-size cache setting. S3 HTTP reads have a ten-second timeout
and one retry. Tune capacity and monitor cost before rollout. The stack creates
CloudWatch alarms for errors, throttles and p95 duration approaching the timeout;
operators must attach notification actions. Logs are retained
for 14 days; do not enable event/body logging. The pinned base is Python 3.14
on `linux/amd64`. SAM's local image build was verified; use SAM for publication.
If publishing manually with Buildx, use a single `linux/amd64` image with
`--provenance=false` rather than a multi-platform/attestation image index,
which Lambda does not support.

Read the stack's `RendererFunctionArn` output and set
`PUBLIC_SHARES_TITILER_LAMBDA_ARN` in the API/worker environment files, then
redeploy the API stack. No TiTiler container, gateway route or HTTP URL is needed.
The ARN takes precedence over the legacy HTTP URL; failures **never fall back**
to HTTP. Invocation throttles retry twice with jitter; SDK reads allow 32 seconds
and do not retry an already-running render. Transport/throttle errors return 503,
timeouts return 504, and malformed output returns 502. Retryable errors include
`Retry-After`. Tiles outside validated layer bounds return 204 before rendering;
the Lambda also maps `TileOutsideBounds` to 204. The viewer permits at most six
concurrent data-tile requests, retries transient/network failures twice with
backoff and `Retry-After`, and only warns after exhaustion. Authorization errors
are never retried. Timeouts increase outwards: Lambda 30 s, SDK 32 s, gateway
35 s, CloudFront 40 s, browser 45 s.

Before enabling sharing, verify a real uploaded categorical and continuous COG
renders through an authorized share session, invalid/expired/revoked sessions
cannot invoke the renderer, S3/KMS permissions work, and no public renderer
endpoint exists. The local tests do not prove deployed IAM/S3 behavior.
For rollback, redeploy a known-good SAM release to update the `live` alias and
keep the API ARN stable. Turning off sharing denies access but does not remove
cleanup configuration; do not remove the renderer while published links are
expected to work.

Local validation includes six Lambda tests with real COG rendering, rejected
sources/routes/queries, error redaction and output-size boundaries, plus the
API sharing/OAuth suite (90 tests). Run the Lambda tests with:

```bash
docker build --platform linux/amd64 --target test \
  -t trendsearth-public-viewer-tiler:test cloud_functions/titiler_cogs/titiler_cogs
```

The `Private sharing tiler` workflow builds/tests the image, lints the template
and verifies SAM packaging; it does not publish or deploy AWS resources.
No AWS stack has been deployed as part of the local implementation.

### Private Martin deployment (opt-in Docker Swarm)

The repository includes `docker-compose.public-shares.yml` and
`config/public-shares/martin.yaml`. Combine the overlay with the normal
production/staging stack only when ready to deploy the public viewer and vectors:

```bash
docker stack deploy \
  -c docker-compose.prod.yml \
  -c docker-compose.public-shares.yml trends-earth
```

Supply the share bucket/prefix and dedicated read-only Martin credentials via
the deployment environment (`PUBLIC_SHARES_MARTIN_AWS_ACCESS_KEY_ID` and
`PUBLIC_SHARES_MARTIN_AWS_SECRET_ACCESS_KEY`); do not commit them. Martin needs
only `s3:GetObject` under `<prefix>/vector/` and `s3:ListBucket` restricted to
that prefix. It does not need staging, PostgreSQL, or write access. The overlay
does not pass the API's environment file to Martin. It pins Martin 1.16.1,
uses a read-only non-root service with CPU/memory limits, publishes no port,
and joins only the API-facing `share_tiles` network. Swarm overlay separation
is not a container-aware enforced egress policy.

Uploads land under `<prefix>/<share-id>/<layer-id>.pmtiles`, outside Martin's
discovery prefix. Only after successful validation does the worker copy the
immutable object/version to `<prefix>/vector/<layer-id>.pmtiles` and delete
all staging versions. Retry recovery revalidates a promoted object if the
S3 move succeeded before the database commit. Cleanup covers both locations
and all versions even if promotion failed. Copies can temporarily require
additional physical storage; declared snapshot bytes remain reserved until
cleanup succeeds. Lifecycle backstops must cover both prefixes.

Martin polls the flat validated prefix every ten seconds. UUID filenames map
to UUID source IDs, avoiding user-controlled names, URL paths, and collisions.
Publication returns a retryable 503 until that source's TileJSON is available
and matches the declared source layer. An upstream source 404 during a tile
request is also reported as retryable 503, not a successful empty tile.
Martin's catalog, TileJSON, and general-purpose endpoints must remain private.
The supplied config disables tile/directory caches; API responses remain
`no-store`. Never configure a separate source under a conflicting UUID.

## PMTiles vector contract

The plugin must generate PMTiles locally; the API neither accepts GeoPackage
uploads nor converts vector data. Each file contains **one** MVT v2 source
layer, with ASCII identifiers matching `[A-Za-z_][A-Za-z0-9_]{0,119}` for the
source layer and attribute names. The API accepts PMTiles v3 with uncompressed
or gzip internal/tile compression and zooms up to the configured maximum
(default 22). Brotli, zstd, raster PMTiles, MLT, nested leaf directories, and
mixed leaf/tile directories are rejected explicitly.

Example vector declaration within the `layers` array:

```json
{
  "kind": "vector",
  "name": "Counterbalancing results",
  "source_layer": "analysis",
  "size_bytes": 12345,
  "sha256": "<lowercase SHA-256 of the PMTiles file>",
  "attribution": "Trends.Earth",
  "style": {
    "kind": "vector",
    "stroke": "#333333",
    "fill": "#cccccc",
    "width": 1,
    "fill_opacity": 0.5,
    "radius": 4,
    "category": {
      "field": "status",
      "values": {"1": "#ff0000", "2": "#00ff00"}
    }
  }
}
```

`band` must be absent or null for vectors. Existing raster declarations remain
unchanged; omitted `kind` defaults to `raster`. Both kinds may be mixed within
one share and use the same owner quota. Vector colors must be hex RGB; width
is 0-10 pixels, fill opacity 0-1, and point radius 0-20 pixels. Optional
`category` has one metadata-declared field and 1-256 string-keyed RGB colors.
Viewers compare category values by string representation; unmatched/missing
values use the base fill/stroke. Category colors apply to polygon fills,
lines, and points. All attribution and labels must be rendered as text, not
HTML, JavaScript, MapLibre expressions, or external URLs.

The worker independently checks section bounds/non-overlap, directories,
tile ranges/pointers/counts, metadata, every unique tile's MVT wire structure,
feature attribute indices, and geometry command structure. It enforces
1 MiB expanded metadata, 4 MiB per directory, 2 MiB per tile (compressed and
expanded), 100,000 total directory entries, 512 MiB total expanded work,
1 GiB process address space, 16 MiB output-file limit, and 90 seconds of
CPU/wall time. Too-large or malformed files fail the share with an
owner-visible validation error; missing runtime support fails the task.
These intentionally bounded checks do not certify topology, analytical
fidelity, or safety against unknown decoder vulnerabilities.

Only sanitized server-derived `source_layer`, `minzoom`, `maxzoom`, and
attribute `fields` are returned as `vector_metadata`. Bounds are geographic
longitude/latitude; vector CRS is EPSG:3857. The manifest supplies `kind`,
`tile_format`, and an API-relative `tiles` template (`.pbf` for vectors,
`.png` for rasters). The API authorizes every request, rejects format/layer
mismatches, bounds coordinates/output, disallows upstream redirects, and
preserves gzip content encoding for vector responses. Absent tiles or zooms
outside the archive range return 204 after authorization.

All attributes actually embedded in MVT remain accessible to link recipients,
including attributes not used for styling. Local export must deliberately
select public-safe fields and check GDAL's feature-dropping/precision behavior.
The public viewer renders MVT natively through OpenLayers and applies the safe
style from the manifest; Martin does not rasterize vector styling. Local PMTiles
export remains the plugin's responsibility, not the API's.

Quotas and limits are configurable with:
`PUBLIC_SHARES_MAX_LAYERS` (10), `PUBLIC_SHARES_MAX_FILE_BYTES` (2 GiB),
`PUBLIC_SHARES_MAX_SHARE_BYTES` (2 GiB),
`PUBLIC_SHARES_MAX_ACTIVE_PER_USER` (10),
`PUBLIC_SHARES_MAX_RESERVED_BYTES_PER_USER` (2 GiB),
`PUBLIC_SHARES_MULTIPART_PART_BYTES` (16 MiB),
`PUBLIC_SHARES_UPLOAD_URL_TTL_SECONDS` (900),
`PUBLIC_SHARES_VIEWER_SESSION_TTL_SECONDS` (900),
`PUBLIC_SHARES_MAX_VIEWER_SESSION_LIFETIME_SECONDS` (86400),
`PUBLIC_SHARES_MAX_TILE_ZOOM` (22), and
`PUBLIC_SHARES_MAX_RASTER_DIMENSION` (100000),
`PUBLIC_SHARES_MAX_RASTER_PIXELS` (250 million),
`PUBLIC_SHARES_MAX_RASTER_BANDS` (16), and
`PUBLIC_SHARES_DRAFT_TTL_SECONDS` (86400). These defaults are conservative
implementation limits, not a substitute for deployment cost and quota review.
The configured reserved-byte limit is the default per-user quota. A superadmin
can set or clear a user's override with
`PUT /api/v1/admin/users/{user_id}/public-share-quota`; `quota_bytes: null`
restores the default. Users can inspect their own totals with
`GET /api/v1/shares/storage` and list/delete individual shares using the owner
routes below. The storage response distinguishes uploaded bytes, total reserved
bytes (including incomplete uploads and pending cleanup), bytes awaiting
cleanup, available quota, and whether the account is over quota. It also
reports `active_share_count`, `active_share_limit`, `max_layers_per_share` and
`max_share_bytes` so clients can show the remaining allowance. Revoked or
expired content continues counting against quota until its S3 objects and
versions are deleted successfully.

## API contract

Owner endpoints require bearer authentication and the dedicated OIDC/service
client scope shown below. Object ownership is always checked against the
authenticated user; optional job IDs are not accepted as an ownership proxy.

| Method and path | Scope | Purpose |
|---|---|---|
| `POST /api/v1/shares` | `webmap:write` | Create a quota-reserved draft with raster/vector declarations, title and retention (7/30/90 days). See the vector contract above. An empty `layers` array creates a map that is immediately `ready` to publish; the plugin creates maps this way and adds layers afterward. |
| `POST /api/v1/shares/{id}/uploads` | `webmap:write` | Start/refresh a multipart upload for a declared layer; returns fixed-size signed parts with a 15-minute default TTL. |
| `POST /api/v1/shares/{id}/complete` | `webmap:write` | Complete every layer, verify actual size, and enqueue COG/PMTiles validation. Repeated completion can retry a lost validation enqueue. |
| `DELETE /api/v1/shares/{id}/layers` | `webmap:write` | Remove published layers using `{"layer_ids": ["..."]}`; an empty map remains accessible. Returns 202 with the updated `share` and `cleanup_scheduled`. |
| `GET /api/v1/shares` and `GET /api/v1/shares/{id}` | `webmap:read` | List/status for the owner only; never returns capability secrets, object keys, or upload credentials. |
| `PUT /api/v1/shares/{id}/layers/order` | `webmap:write` | Reorder an active, idle share's published layers with `{"layer_ids": ["..."]}`, listing every published layer exactly once; the first ID is the top layer in the viewer. New layers are appended at the bottom. Returns the updated share (`no-store`). |
| `GET /api/v1/shares/{id}/link` | `webmap:write` | Return the owner's current viewer link (`{id, link, expires_at}`, `no-store`). Returns 409 when the share is not live or its link was not stored (published before link storage, or after a session-secret rotation); rotate once to fix. |
| `GET /api/v1/shares/storage` | `webmap:read` | Return the owner's uploaded, reserved, pending-cleanup and available storage totals, active-map count and limit, and per-map limits. |
| `POST /api/v1/shares/{id}/publish` | `webmap:write` | After validation, set `ready_at` and `expires_at`, hash a new capability, store it encrypted for the owner, and return its URL. |
| `POST /api/v1/shares/{id}/rotate` | `webmap:write` | Replace the capability and invalidate old viewer sessions without extending expiry. |
| `DELETE /api/v1/shares/{id}` | `webmap:write` | Revoke synchronously, then schedule idempotent object/upload cleanup. |
| `PUT /api/v1/admin/users/{user_id}/public-share-quota` | `admin:write`, SUPERADMIN | Set a non-negative byte override or `null` to use the configured default. |
| `POST /api/v1/public-shares/{id}/session` | Public, same-origin `Origin` required | Exchange the URL-fragment secret for a short-lived HttpOnly, Secure, SameSite=Strict cookie. |
| `POST /api/v1/public-shares/{id}/session/refresh` | Public, same-origin `Origin` required | Refresh a valid open viewer session before its rolling cookie expires, without extending its absolute 24-hour default lifetime. |
| `GET /api/v1/public-shares/{id}/manifest` | Viewer cookie | Return validated display metadata without storage keys or credentials. |
| `GET /api/v1/public-shares/{id}/tiles/{layer}/{z}/{x}/{y}.png` | Viewer cookie | Re-check authorization before invoking private TiTiler Lambda (or the legacy internal HTTP renderer) for bounded PNG tiles. |
| `GET /api/v1/public-shares/{id}/tiles/{layer}/{z}/{x}/{y}.pbf` | Viewer cookie | Re-check authorization before proxying bounded MVT requests to internal Martin. |
| `GET /api/v1/public-shares/s/{token}/manifest` | Current path token | Return uncached metadata and token-bound tile URLs; no cookie/exchange required. |
| `GET /api/v1/public-shares/tiles/{id}/{token}/{layer}/{z}/{x}/{y}.png` (or `.pbf`) | Current path token | Authorize on CDN miss, then invoke TiTiler or proxy Martin; successful/empty tiles may be cached until four days or share expiry, whichever is earlier. |

The capability is 256-bit random data delivered in the URL path and
stored as a SHA-256 hash, which is what authorizes access. So that the owner
can view and copy the link again, the API also stores the secret in
`link_ciphertext`, encrypted with Fernet under a key derived (HKDF-SHA256) from
`PUBLIC_SHARES_SESSION_SECRET`; retrieval re-verifies the decrypted secret
against the hash. Rotation replaces it; revocation and expiry clear it. Anyone
holding both the database and the session secret could recover live links. The
viewer calls APIs on the configured same HTTPS origin. Tokens appear in viewer
and tile URL paths: disable/redact full URL logs at every proxy/telemetry system,
and retain `Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex`.
Links, sessions, manifests and errors are `no-store`; only successful new-style
tiles opt in to public edge caching. Revocation, expiry and rotation are checked
on every manifest request and tile cache miss; cached tiles follow the expiry
cap and invalidation semantics above. Legacy session/token generation and
absolute session expiry remain checked on each legacy request. The rolling cookie TTL
(default 15 minutes) is not an absolute lifetime: refresh preserves the
original 24-hour maximum lifetime. `Origin` is a browser CSRF defense only,
not proof that a caller is authorized.

The state flow is `draft → uploading → validating → ready → revoked/expired`.
`ready` means validation succeeded; publication sets `ready_at` and the expiry
deadline and is required before public access works. Failed validations remain
owner-visible and can be retried by replacing the affected layer. Expiry is
denied immediately by request-time checks; the five-minute cleanup worker is a
storage-retention backstop, not the access-control boundary. Abandoned drafts
and incomplete multipart uploads expire after the configured draft TTL,
including interrupted validation and validated-but-unpublished shares.

## Scope registration and migrations

`webmap:read` and `webmap:write` are supported but are not default scopes. The
QGIS OIDC client definition includes them as an explicit allowlist. Existing
client registrations are not changed automatically; an operator must review
and provision the QGIS client with:

```bash
python scripts/register_oidc_clients.py --client te-qgis-plugin --add-scopes
```

The client will only receive the scopes when it requests them; plugin consent
and token-request changes are intentionally separate. Apply the database
migrations through the normal API migration deployment process, including
`4f68e9d12c3a` for nullable raster bands, layer kind, and vector metadata.
Existing records default to `raster`. Downgrade is blocked while vector
records exist: revoke and clean their S3 objects, then remove the retained
records administratively before downgrading. API rollback
must disable new shares without making the private bucket public; retain the
cleanup configuration until all stored objects and multipart uploads are gone.

Removing layers immediately excludes them from manifests and origin tile
requests while preserving the map link. Cached tiles may remain available until
CDN invalidation propagates. The cleanup worker retries invalidation and deletes
all snapshot versions; their storage quota remains reserved until cleanup
succeeds. Open viewers check for layer changes periodically. Migration
`9c4f7a3d8b20` follows the display-mask migration; its downgrade is blocked
until removed-layer cleanup has finished.
