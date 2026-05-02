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
