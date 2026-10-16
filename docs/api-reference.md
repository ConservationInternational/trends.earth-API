# API Reference

Interactive API documentation is available at `/api/docs/` (Swagger UI):

- **Development**: `http://localhost:3000/api/docs/`
- **Staging**: `https://staging-api.trends.earth/api/docs/`
- **Production**: `https://api.trends.earth/api/docs/`

For authentication details, see [authentication.md](authentication.md).

## Common Query Parameters

All listing endpoints support:

| Parameter | Description |
|-----------|-------------|
| `filter` | SQL-style filter expressions, comma-separated. Operators: `=`, `!=`, `>`, `<`, `>=`, `<=`, `like` |
| `sort` | Comma-separated sort fields. Prefix with `-` for descending. Example: `sort=status desc,name asc` |
| `include` | Comma-separated extra fields to add to each result |
| `exclude` | Comma-separated fields to remove from each result |
| `page` | Page number (pagination only enabled when `page` or `per_page` is provided) |
| `per_page` | Items per page (default: 20, max: 100) |

**Access control:** Non-admin users cannot filter, sort by, or include `user_name` or `user_email` fields.
Attempting to do so returns `HTTP 403 Forbidden`.

## Health Check

```
GET /api-health
```

No authentication required. Returns server status, database connectivity, and API version.

## System Status (Admin+)

```
GET /api/v1/status
```

Returns paginated status log entries with execution counts by state.

Query parameters: `start_date`, `end_date`, `sort` (default: `-timestamp`), `page`, `per_page` (max: 1000).

Response fields include `executions_pending`, `executions_ready`, `executions_running`,
`executions_finished`, `executions_failed`, `executions_cancelled`. Entries also carry
optional `status_from`, `status_to`, and `execution_id` fields that are set when
an entry is created by a status-change event (as opposed to a periodic snapshot).

## Scripts

```
GET    /api/v1/script                         List scripts
GET    /api/v1/script/<id>                    Get script
POST   /api/v1/script                         Create script
PATCH  /api/v1/script/<id>                    Update script
DELETE /api/v1/script/<id>                    Delete script (Admin only)
POST   /api/v1/script/<id>/publish            Publish script
POST   /api/v1/script/<id>/unpublish          Unpublish script
GET    /api/v1/script/<id>/download           Download script
GET    /api/v1/script/<id>/log                Get script logs
PATCH  /api/v1/script/<id>/config             Update script config (Admin only)
```

### Script Access Control

```
GET    /api/v1/script/<id>/access             Get access information
PUT    /api/v1/script/<id>/access/roles       Set allowed roles
PUT    /api/v1/script/<id>/access/users       Set allowed users
POST   /api/v1/script/<id>/access/users/<uid> Add user
DELETE /api/v1/script/<id>/access/users/<uid> Remove user
POST   /api/v1/script/<id>/access/roles/<role> Add role
DELETE /api/v1/script/<id>/access/roles/<role> Remove role
DELETE /api/v1/script/<id>/access             Clear all restrictions
```

Access control fields: `restricted` (boolean), `allowed_roles`, `allowed_users`.

### Script Filtering Examples

```bash
# Public scripts sorted by creation date
GET /api/v1/script?filter=public=true&sort=-created_at

# Scripts with pagination
GET /api/v1/script?page=1&per_page=10

# Include full user object and logs
GET /api/v1/script?include=user,logs

# Exclude large fields (Admin only — include user_name)
GET /api/v1/script?include=user_name&exclude=cpu_reservation,cpu_limit,memory_reservation,memory_limit
```

## Executions

```
POST   /api/v1/script/<id>/run                     Run a script
GET    /api/v1/execution                           List executions (own for users, all for admins)
GET    /api/v1/execution/user                      List current user's executions
GET    /api/v1/execution/<id>                      Get execution
PATCH  /api/v1/execution/<id>                      Update execution (Admin only)
POST   /api/v1/execution/<id>/cancel               Cancel execution
GET    /api/v1/execution/<id>/log                  Get logs
POST   /api/v1/execution/<id>/log                  Add log entry (Admin only)
GET    /api/v1/execution/<id>/download-results      Download results as JSON file
GET    /api/v1/execution/<id>/docker-logs           Get raw Docker service logs (Admin only)
GET    /api/v1/execution/<id>/batch-logs            Get AWS Batch / CloudWatch logs (Admin only)
```

### Execution Cancellation

Cancelling an execution stops the Docker service, then attempts to cancel any associated
Google Earth Engine tasks. Requires ownership or Admin role.

### Execution Filtering Examples

```bash
# Finished executions with duration, sorted by longest first
GET /api/v1/execution?status=FINISHED&include=duration&sort=-duration

# Exclude large fields to reduce response size
GET /api/v1/execution?exclude=params,results

# Running executions with user info (Admin only)
GET /api/v1/execution?status=RUNNING&include=user,user_name,duration&sort=-start_date
```

## Users

```
GET    /api/v1/user                           List users (Admin only)
GET    /api/v1/user/<id>                      Get user (Admin only)
GET    /api/v1/user/me                        Get own profile
POST   /api/v1/user                           Create user
PATCH  /api/v1/user/<id>                      Update user (Admin only)
PATCH  /api/v1/user/me                        Update own profile
PATCH  /api/v1/user/me/change-password        Change own password
PATCH  /api/v1/user/<id>/change-password      Change user password (Admin only)
DELETE /api/v1/user/<id>                      Delete user (SuperAdmin only)
DELETE /api/v1/user/me                        Delete own account
POST   /api/v1/user/<id>/recover-password     Send password recovery email
POST   /api/v1/user/reset-password            Reset password with token
```

### User Filtering Examples

```bash
# Users from USA, sorted by name
GET /api/v1/user?filter=country=USA&sort=name

# Admin users with pagination
GET /api/v1/user?filter=role=ADMIN&page=1&per_page=10
```

## Application-scoped user API (OIDC user tokens)

Bounded identity and app-access API shared by OIDC-gated applications
(currently `rio_coherence` and `avoided_emissions`; unknown keys return
`404 unknown_app` after authentication). There are no application-specific
handlers or aliases: behaviour comes from the explicit, fail-closed policy
registry in `gefapi/utils/app_access.py` (`APP_POLICIES`: roles, default role,
admin roles, superadmin role, member discovery, directory fields).

Every route requires an **RS256 OIDC access token issued to an OAuth client
whose `required_app_key` equals the `{app_key}` in the path**. Legacy
`POST /auth` (HS256) tokens, service-client tokens, ID tokens and tokens for
other clients/apps are rejected. Account state, token revocation, email
verification and the caller's current grant are re-checked on every request
(stale signed app claims are never trusted); app `admin` comes from the
current grant role or an effective SUPERADMIN (global `ADMIN` does not confer
it).

Scopes: every route requires `openid email profile` plus the operation scope
below. The effective scopes are the token's granted `scope` **intersected with
the client's registered allowlist**, so removing a scope from the client
disables it for already-issued tokens. Scopes never confer roles.

```
GET   /api/v1/apps/{app_key}/me                      app:read          Own profile + effective access
GET   /api/v1/apps/{app_key}/users?email=<exact>     app-users:read    Exact-email lookup
GET   /api/v1/apps/{app_key}/users                   app-users:read    Directory (app admin)
POST  /api/v1/apps/{app_key}/users/resolve           app-users:read    Batch subject lookup (max 100)
PUT   /api/v1/apps/{app_key}/users/<subject>/access  app-access:write  Set status/role (app admin)
PATCH /api/v1/account/profile                        account:write     Update own name/institution
```

App profile shape (`GET /me`, lookups, directory and access responses wrap
profiles in `data`):

```json
{"sub": "<uuid>", "email": "a@b.org", "email_verified": true, "name": "A",
 "institution": "B", "active": true, "app_access": "active", "app_role": "member"}
```

`app_access` is `active` when access is effective (including via SUPERADMIN),
otherwise the grant status (`pending`, `revoked`) or `null`; `app_role` is a
policy role only while access is effective, else `null`.

- `PATCH /api/v1/account/profile` is the only profile write (there is no
  `PATCH /apps/{app_key}/me`). It accepts only `name` and `institution`; any
  other field returns `400 field_not_allowed`. It requires an OIDC client
  (`403 client_identity_required` otherwise), a current grant when the client
  is app-gated, and a verified email. The response is identity-only:
  `{"data": {"sub", "email", "email_verified", "name", "institution",
  "active"}}` (no `app_access`/`app_role`).
- Member discovery (`?email=` and `resolve` for non-admins) is a policy flag:
  enabled for `rio_coherence`, disabled for `avoided_emissions`
  (`403 app_role_required`). App admins can always use them.
- `GET /users?email=` returns `{"data": [profile]}` only for an active,
  verified user with effective access, otherwise `{"data": []}`.
- `GET /users` (no `email`, admin only) returns `{"data": [...], "page",
  "per_page", "total"}` listing users with any grant for the app
  (pending/active/revoked) plus effective superadmins. `per_page` defaults to
  50 and must be 1-100; optional `status=pending|active|revoked` filter.
- `POST /users/resolve` body `{"subjects": ["<uuid>", ...]}` (<=100) returns
  eligible users; admins additionally receive users holding a pending/revoked
  grant. Unrelated users are never returned.
- `PUT /users/<subject>/access` body `{"status": "active|pending|revoked",
  "role": "<policy role>", "note": "..."}` updates only that app's grant.
  Revoking/demoting an effective superadmin returns `403 superadmin_protected`;
  revoking/demoting yourself returns `409 self_demotion_forbidden`; unknown
  subjects return `404 not_found`.

Errors: `401` for missing/invalid/expired/revoked tokens or disabled accounts;
`403` with `error` of `client_identity_required`, `client_app_mismatch`,
`insufficient_scope` (includes `required_scopes` and `missing_scopes`),
`email_unverified`, `app_access_required` (includes `app_access`), or
`app_role_required`; `404 unknown_app`; `400 invalid_request`.

## Rate Limiting (SuperAdmin only)

```
GET  /api/v1/rate-limit/status    Query current rate limit state
POST /api/v1/rate-limit/reset     Reset all rate limit counters
```

Rate limits apply per user (authenticated) or per IP address (unauthenticated). Admin and
SuperAdmin users are automatically exempt.

## Access Control Summary

| Role | Capabilities |
|------|-------------|
| Regular user | Own scripts/executions, public scripts, change own password/profile |
| Admin | All scripts/executions, user data fields, most user management |
| SuperAdmin | All Admin capabilities + user role changes, user deletion, rate limit management |
