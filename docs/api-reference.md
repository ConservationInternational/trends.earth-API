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
