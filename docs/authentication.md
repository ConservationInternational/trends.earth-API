# Authentication

The API uses JWT (JSON Web Tokens) with refresh token support.

## Login

```http
POST /auth
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "user_password"
}
```

Response:

```json
{
  "access_token": "eyJ...",
  "refresh_token": "dGhp...",
  "user_id": "user-uuid",
  "expires_in": 3600
}
```

## Refresh Access Token

```http
POST /auth/refresh
Content-Type: application/json

{
  "refresh_token": "dGhp..."
}
```

Response (same shape as login):

```json
{
  "access_token": "eyJ...",
  "refresh_token": "dGhp...",
  "user_id": "user-uuid",
  "expires_in": 3600
}
```

By default the same refresh token is returned. Pass `?rotate=true` to issue a fresh
refresh token (rotating invalidates the old one).

## Logout

```http
POST /auth/logout
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "refresh_token": "dGhp..."
}
```

Logout from all devices:

```http
POST /auth/logout-all
Authorization: Bearer <access_token>
```

## Token Details

- **Access token**: Short-lived (1 hour). Include in `Authorization: Bearer <token>` header.
- **Refresh token**: Long-lived (30 days). Used to obtain new access tokens without re-login.
  Stored in the `refresh_tokens` database table; can be revoked via `/auth/logout` (single token)
  or `/auth/logout-all` (all tokens for the user).

## OAuth2 Client Credentials

For service-to-service authentication (e.g., admin tooling):

```http
POST /api/v1/oauth/token
Content-Type: application/json

{
  "grant_type": "client_credentials",
  "client_id": "<CLIENT_ID>",
  "client_secret": "<CLIENT_SECRET>"
}
```

Tokens are valid for 30 minutes.

Service-client tokens are limited by their scopes, and some actions have
extra rules:

- To edit, promote, or delete another user (`PATCH`/`DELETE /api/v1/user/<id>`)
  or to reset another user's password (`PATCH /api/v1/user/<id>/change-password`),
  the token needs both `user:write` and `admin:write`. The owner must also pass
  the usual role checks (SUPERADMIN, or ADMIN for most password resets).
  `admin:write` therefore gives full administrative power. Grant it only to
  trusted automation.
- A service-client token cannot reset its owner's password through the admin
  endpoint. To change the owner's password, use
  `PATCH /api/v1/user/me/change-password`, which checks the old password.
- Service-client tokens cannot create service clients (`POST /api/v1/oauth/clients`),
  even with `client:manage` or full access. Create clients from a signed-in user
  session, such as the API UI. Tokens with `client:manage` can still list and
  revoke clients.

These rules do not change ordinary signed-in user sessions.

## OIDC User Tokens (first-party applications)

Applications such as Avoided Emissions and Rio Coherence sign users in through
the Trends.Earth OIDC provider (`/oauth/authorize`, `/oauth/token`). The
resulting RS256 access tokens are accepted on the application-scoped API and on
the script/execution routes listed in [api-reference.md](api-reference.md),
provided they carry the required scope (`script:read`, `execution:read`, or
`execution:write`).

Legacy tokens from `POST /auth` are always checked first, so the QGIS plugin and
API UI behave exactly as before. OIDC access tokens include an `auth_version`
claim and stop working after a password change, password reset, or account
deactivation.

## Execution-only callback credentials

Newly dispatched workloads receive `API_EXECUTION_TOKEN` and
`API_EXECUTION_CALLBACK_PATH` alongside the existing `API_USER`, `API_PASSWORD`,
and environment-account variables. Existing computation images continue using
their username/password login without modification. These passwords have **not**
yet been removed; the shared-credential escalation risk remains until runtimes
are migrated and the shared credentials are rotated.

A future runtime can send `Authorization: Bearer <API_EXECUTION_TOKEN>` to:

- `PATCH /api/v1/execution/<id>/callback` with `status`, `progress`, and/or `results`.
- `POST /api/v1/execution/<id>/callback/log` with `text` and `level`.

These are separate from the existing administrative update/log endpoints.
Callback tokens cannot authenticate to ordinary user APIs, create users, refresh
user sessions, or update another execution. User access tokens cannot be used on
the callback endpoints. Callback tokens expire after
`EXECUTION_TOKEN_TTL_SECONDS` (default seven days), and stop working when the
execution becomes terminal, its owner is disabled, or the owner's authentication
version changes. Send final logs before reporting terminal completion.

The callback signing key is domain-separated from user JWT signing by default.
Set `EXECUTION_TOKEN_SECRET` to manage an independent signing key. Never expose
either signing key to workloads; only their individual token is injected.
Choose a lifetime covering the full pipeline, including Batch queue time, retries,
and sequential steps. There is no callback-token refresh endpoint in this phase.

Migration order: deploy callback support, update computation images to use these
credentials, verify their progress/log/result callbacks, and only then separately
remove workload passwords. No plugin authentication migration is required.

## Common Authentication Errors

| Status | Meaning |
|--------|---------|
| 401 Unauthorized | Access token expired — use refresh token to get a new one |
| 401 Unauthorized | Refresh token invalid or expired — user must log in again |
| 403 Forbidden | Valid token but insufficient permissions for the requested operation |

## Troubleshooting

```bash
# Check if refresh tokens are being created
docker compose logs <service_name> | grep -i "refresh"

# Verify refresh token table exists
docker compose exec database psql -U root -d gefdb -c "\d refresh_tokens"

# Clean up expired tokens
docker compose run --rm api python -c "
from gefapi.services.refresh_token_service import RefreshTokenService
print(f'Cleaned up {RefreshTokenService.cleanup_expired_tokens()} expired tokens')
"
```
