# Multi-Application OIDC and Per-App Access Control — Implementation Plan

## Goal

Make the Trends.Earth API a single identity provider that can distinguish which
application is calling it — the **API UI**, the **QGIS plugin**, the **Avoided
Emissions webapp**, and **Rio Coherence** — and gate access to the Avoided
Emissions and Rio Coherence applications behind per-user grants that an
administrator activates individually.

## Phase structure

The work is split into three phases by blast radius:

| Phase | Scope | QGIS plugin | API UI / Avoided Emissions | Nature |
|---|---|---|---|---|
| **1** | All API-side work + **Rio Coherence** end to end | No change required | No change required | Purely additive |
| **2** | **API UI** and **Avoided Emissions** migration | No change required | Coordinated client changes required | Additive on the API, migrating on the clients |
| **3** | **QGIS plugin** migration and legacy deprecation | **Breaking** — requires a plugin release | Already migrated in Phase 2 | Deprecation / cutover |

Rio Coherence sits entirely in Phase 1 because it is **not yet deployed**: it has
no users, no data, and no backwards-compatibility surface, so building it
alongside the API work costs nothing and validates the whole gate end to end
before any existing application is touched.

Phases 1 and 2 must be shippable to production without touching a single
deployed QGIS plugin install. Phase 3 is the only phase permitted to require a
plugin update.

## Settled decisions

1. **The Avoided Emissions webapp and Rio Coherence keep no local user table.**
   Identity, profile, approval workflow, and the app-local role all move to the
   API. Both `users` tables are dropped and every owning table repoints to the
   API's `sub`. Genuinely app-specific authorization — Rio's group/workspace
   memberships — stays app-local. Rio is **not yet deployed**, so for Rio this
   is a schema redefinition rather than a migration; only Avoided Emissions
   needs a backfill and a cutover window.
2. **Users can self-request access.** Requests arrive either explicitly from an
   app the user can already reach, or implicitly when they are turned away at
   `/oauth/authorize`. Every new request notifies the superadmins by email.
3. **Blocked authorization redirects with `error=access_denied`.** The pending
   request is created first, and additional redirect parameters tell the app
   whether to render "request submitted, awaiting approval" or "access denied".

---

## Current state

### Two unrelated token systems

The API today runs two authentication stacks that do not know about each other:

| System | Entry points | Token | Client-aware? |
|---|---|---|---|
| Legacy user session | `POST /auth`, `POST /auth/refresh`, `POST /auth/logout` in [gefapi/__init__.py](../gefapi/__init__.py) | HS256 via `flask_jwt_extended`, `additional_claims={"auth_version": ...}` | **No** |
| Service clients | `POST /api/v1/oauth/token` (client_credentials) in [gefapi/routes/api/v1/oauth2.py](../gefapi/routes/api/v1/oauth2.py) | HS256 with `grant_type`/`scopes` claims | Yes, but service-to-service only |
| OIDC provider | `/oauth/authorize`, `/oauth/token`, `/oauth/refresh`, `/oauth/userinfo`, `/oauth/revoke`, `/oauth/logout`, `/.well-known/*` in [gefapi/routes/oidc.py](../gefapi/routes/oidc.py) | RS256 signed by `OIDC_PRIVATE_KEY`, built in `issue_token()` in [gefapi/services/oidc_service.py](../gefapi/services/oidc_service.py) | **Yes** |

Every `/api/v1/*` resource route is protected with `@jwt_required()`, which
resolves to the HS256 stack only (`JWT_ALGORITHM` is pinned to `HS256` in
[gefapi/config/base.py](../gefapi/config/base.py)).

### How the OIDC provider identifies a caller

Client identity comes from the `client_id` form/query parameter, validated
against the `OAuthClient` row in [gefapi/models/oidc.py](../gefapi/models/oidc.py):

* `redirect_uris` — exact-match allowlist.
* `scopes` — requested scope must be a subset of the registered scope set.
* `is_public` / `client_secret_hash` — confidential clients authenticate at the
  token endpoint.
* `audience` — becomes the `aud` claim of **access tokens**.
* `client_id` — becomes the `aud` and `azp` claims of **ID tokens**.

PKCE S256 is mandatory (`_validated_request()`), as are `state` and `nonce`.

### Gaps

1. **Only Rio uses OIDC.** The API UI and QGIS plugin authenticate with
   email/password against `POST /auth` and receive a token carrying no client
   identity whatsoever. The only signal is the advisory `X-TE-Client` header
   recorded by [gefapi/services/client_tracking_service.py](../gefapi/services/client_tracking_service.py)
   into `user_client_metadata` — telemetry, not authorization.
2. **Access tokens omit `azp`/`client_id`.** `issue_token()` only sets `azp` for
   `id_token`. A resource server that accepts an access token cannot tell which
   client within an audience produced it.
3. **Access-token audiences are resource identifiers, not client identifiers.**
  Multiple clients may target one API audience, so the API must resolve the
  calling client from the signed `client_id`/`azp` claims rather than infer it
  from `aud`.
4. **No per-application authorization exists.** `User.role` is a single global
   value (`USER` / `ADMIN` / `SUPERADMIN`). There is no `is_approved` column and
   no notion of "this user may use Avoided Emissions but not Rio".
5. **Native-app redirect URIs are unsupported.** Exact-match `redirect_uris`
   cannot express the RFC 8252 loopback pattern (`http://127.0.0.1:<random>/…`)
   a QGIS desktop client needs.

---

## Phase 1 — API foundation, enforcement, and Rio Coherence

Everything in this phase is invisible to every currently deployed client. The
API work is purely additive, and Rio — the one consumer built here — has no
existing users to disrupt.

### 1.1 Application registry

Add `gefapi/utils/app_access.py` defining the canonical app keys and helpers:

```python
APP_KEYS = frozenset({"avoided_emissions", "rio_coherence"})
APP_LABELS = {...}            # human-readable names for admin UI and emails
APP_ROLES = {                 # per-app role vocabularies
    "avoided_emissions": ("member", "admin"),
    "rio_coherence": ("member", "admin"),
}
def has_app_access(user, app_key) -> bool
def app_access_status(user, app_key) -> str | None
def app_role(user, app_key) -> str | None
def active_app_keys(user) -> list[str]
def active_app_roles(user) -> dict[str, str]
def require_app_access(app_key, role=None)   # decorator, applied in §1.10
```

Absence of a row means **no access**. Never default-allow.

### 1.2 `user_app_access` model and migration

New model `gefapi/models/app_access.py`, exported from
[gefapi/models/__init__.py](../gefapi/models/__init__.py):

| Column | Type | Notes |
|---|---|---|
| `id` | `GUID` PK | `default=uuid.uuid4` |
| `user_id` | `GUID` FK → `user.id` | indexed, `ondelete=CASCADE` |
| `app_key` | `String(50)` | one of `APP_KEYS` |
| `status` | `String(20)` | `pending` \| `active` \| `revoked` |
| `role` | `String(20)` | app-local role, default `member`; `admin` for app administrators |
| `requested_at` | `DateTime` | nullable — null for admin-provisioned grants |
| `request_note` | `Text` | nullable — free-text justification supplied by the requester |
| `granted_at` | `DateTime` | nullable |
| `revoked_at` | `DateTime` | nullable |
| `granted_by_user_id` | `GUID` FK → `user.id` | nullable, `ondelete=SET NULL` |
| `note` | `Text` | admin-visible justification for the decision |
| `created_at` / `updated_at` | `DateTime` | |

Constraints: `UNIQUE (user_id, app_key)`; index on `(app_key, status)` for the
admin queue query.

The `role` column carries each application's own role model so that no app needs
a local user store. For Avoided Emissions it replaces that app's
`users.role` enum (`admin` / `user`); for Rio Coherence it replaces
`ApplicationRole` in `src/rio_coherence/domain/models.py`. Both map to
`admin` / `member`. It is independent of the API's global `User.role` — an API
`USER` can be an Avoided Emissions `admin`, and vice versa. Valid values are
declared per app in `APP_ROLES` in `gefapi/utils/app_access.py`.

Likewise, `status` / `requested_at` / `granted_at` / `granted_by_user_id` /
`note` replace Rio's `UserRecord.status`, `decided_at`, `decided_by`, and
`decision_note` columns and its `account_decisions` audit table, which implement
the same approve/deny workflow today.

Add a `User.app_access` relationship (`lazy="dynamic"`,
`cascade="all, delete-orphan"`) mirroring the existing `client_metadata`
relationship.

**Migration rules** (per the repo conventions in
[.github/copilot-instructions.md](../.github/copilot-instructions.md)):

* Revision ID must be a fresh 12-character hex hash.
* File name `{revision}_add_user_app_access.py`.
* Determine `down_revision` by finding the actual head on disk at implementation
  time — the revision that appears as `revision = ` in its own file and is not
  referenced as any other file's `down_revision`. Search for the **full revision
  string**, not `^down_revision = `, so tuple-form merge migrations are caught.

### 1.3 `OAuthClient.required_app_key`

Add a nullable `required_app_key VARCHAR(50)` column to `oauth_clients`.

* `NULL` ⇒ the client is ungated (API UI, QGIS plugin).
* Non-null ⇒ the OIDC flow requires an `active` `user_app_access` row for that
  key.

This keeps the gate declarative: adding a future gated application is a data
change, not a code change. It is read by the enforcement points in §1.10.

### 1.4 Shared resource audiences

Access-token audiences identify resources and may be shared by multiple OAuth
clients. Resolve the calling client from the signed `client_id`/`azp` claims,
then validate the token against that client's configured resource audience.
Legacy tokens without client identity claims may use an audience lookup only
when exactly one active client has that audience; ambiguous tokens fail closed.

### 1.5 Loopback redirect URI support

Extend `OAuthClient.allowed_redirect_uris()` matching (a new
`matches_redirect_uri(uri)` method is cleaner than mutating the list) so that a
registered entry of `http://127.0.0.1/callback` or `http://[::1]/callback`
matches any port, per RFC 8252 §7.3. Constraints:

* Only for loopback hosts — never for `localhost` by name, never for other hosts.
* Scheme, path, and host must match exactly; only the port is wildcarded.
* Existing exact-match behaviour is unchanged for all other URIs.

This is built here so the `te-qgis-plugin` client can be registered early, but
nothing consumes it until Phase 3.

### 1.6 Client provisioning

Use `scripts/register_oidc_clients.py` (idempotent, runnable via the admin
container) to create or update the first-party clients:

| `client_id` | `audience` | `is_public` | `required_app_key` | Redirect URIs |
|---|---|---|---|---|
| `te-api-ui` | `trends-earth-api-ui` | yes (PKCE) | `NULL` | api-ui staging + prod callbacks |
| `te-qgis-plugin` | `https://api.trends.earth` | yes (PKCE) | `NULL` | `http://127.0.0.1/callback`, `http://[::1]/callback` |
| `te-web` | `https://api.trends.earth` | yes (PKCE) | `NULL` | explicitly configured HTTPS staging + prod callbacks |
| `avoided-emissions-web` | `avoided-emissions` | no (secret) | `avoided_emissions` | AE staging + prod callbacks |
| `rio-coherence` | `rio-coherence` | yes (PKCE) | `rio_coherence` | Rio staging + prod callbacks |

The API UI retains its own access-token audience. The QGIS plugin and `te-web`
share the Trends.Earth API resource audience while retaining distinct client
IDs and ID-token audiences. Set `TE_WEB_REDIRECT_URIS` and
`TE_WEB_POST_LOGOUT_REDIRECT_URIS` to the real HTTPS callback and logout URLs
when provisioning staging or production; the script does not fall back to
localhost in those environments. Likewise, set `RIO_REDIRECT_URIS` and
`RIO_POST_LOGOUT_REDIRECT_URIS` to Rio's actual staging or production URLs.
`API_UI_REDIRECT_URIS`, `API_UI_POST_LOGOUT_REDIRECT_URIS`, and the matching
Avoided Emissions variables must also be configured for deployed environments.
QGIS uses loopback callbacks by design.

The existing `POST /api/v1/admin/oidc-clients` route in
[gefapi/routes/oidc.py](../gefapi/routes/oidc.py) already handles registration;
extend it to accept `required_app_key` and validate it against `APP_KEYS`.

### 1.7 Admin and self-service API for app access

New blueprint `gefapi/routes/api/v1/app_access.py`:

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/admin/app-access` | ADMIN+ | List grants; filters `app`, `status`, `role`, `user_id`; paginated like existing admin list routes |
| `POST` | `/api/v1/admin/users/<user_id>/app-access` | ADMIN+ | Grant or update: `{app_key, status, role, note}` |
| `DELETE` | `/api/v1/admin/users/<user_id>/app-access/<app_key>` | ADMIN+ | Revoke (sets `status='revoked'`, keeps the audit row) |
| `GET` | `/api/v1/user/me/app-access` | any user | The caller's own grants and pending requests |
| `POST` | `/api/v1/user/me/app-access/<app_key>` | any user | Self-request, creates a `pending` row (idempotent), body `{request_note}` |

Note that `/api/v1/admin/users/<subject>` is already owned by the **oidc**
blueprint (`admin_user_lookup` in [gefapi/routes/oidc.py](../gefapi/routes/oidc.py)).
The new rules are deeper paths and do not collide, but the split ownership of
the `/api/v1/admin/users/...` namespace across two blueprints is worth a comment
so the next reader does not go looking in the wrong file.

Details:

* Use `is_admin_or_higher()` from [gefapi/utils/permissions.py](../gefapi/utils/permissions.py)
  for the admin routes. Granting is an admin-level, not superadmin-level, action —
  it does not change `User.role`.
* `role` defaults to `member` on grant and is validated against `APP_ROLES` for
  the given `app_key`. Changing only the role of an existing active grant is
  allowed and does not reset `granted_at`.
* Annotate the admin routes with `@require_scope("admin:write")` / `admin:read`.
  Both are already in `VALID_SCOPES` in [gefapi/utils/scopes.py](../gefapi/utils/scopes.py),
  so no scope registration is needed, and `_has_scope()` returns `True` for
  non-`client_credentials` tokens — so API UI administrators signed in with a
  normal user token are unaffected by the annotation.
* Audit every grant/revoke via [gefapi/utils/security_events.py](../gefapi/utils/security_events.py);
  add `APP_ACCESS_GRANTED` / `APP_ACCESS_REVOKED` / `APP_ACCESS_REQUESTED` events.
* Revoking must also revoke that user's `OIDCRefreshToken` rows **for clients
  whose `required_app_key` matches** — use `revoke_oidc_refresh_token_for_client`
  semantics, scoped by `client_id`. It must **never** touch the `RefreshToken`
  model, which backs legacy `/auth/refresh` sessions for the plugin and API UI.

### 1.7.1 Self-request semantics

A request may be created two ways, and both funnel into the same row:

1. **Explicitly**, via `POST /api/v1/user/me/app-access/<app_key>` — used when
   the user is already signed in somewhere they can reach (typically the API UI)
   and wants access to another app.
2. **Implicitly**, at `/oauth/authorize` when a user is turned away from a gated
   client (see §1.10). This is the common path, since a user who has never had
   access to an app has no token for it.

Rules:

* Creation is **idempotent**. If a `pending` row already exists, update
  `request_note` and return `200` rather than creating a duplicate or resetting
  `requested_at`.
* If the existing row is `active`, return it unchanged — there is nothing to
  request.
* If the existing row is `revoked`, an administrator has already made a
  decision. Do **not** silently reopen it: return `403` with a
  `status: "revoked"` body so the app can tell the user to contact an
  administrator. Re-opening is an explicit admin action.

### 1.7.2 Superadmin notification

Every **newly created** request (either path) sends an email to the superadmins.

* Recipients: all users with `role == "SUPERADMIN"`, plus the configured
  `API_ENVIRONMENT_USER` address resolved through
  `_configured_admin_email()` in [gefapi/utils/permissions.py](../gefapi/utils/permissions.py).
  De-duplicate the list case-insensitively.
* Content: requester name and email, app label, `request_note`, and a deep link
  into the API UI pending-requests view.
* Sent with `EmailService.send_html_email` from
  [gefapi/services/email_service.py](../gefapi/services/email_service.py). This
  is an operational notification, not marketing, so it is **not** gated on the
  recipient's `email_subscription_*` preferences — but it must be dispatched
  through the Celery worker, never inline in the request, so a mail outage never
  fails an authorization redirect.
* Suppress on idempotent re-requests, so a user repeatedly hitting a gated app
  cannot spam the superadmins. Enforce with a coarse per-user-per-app cooldown
  (e.g. one notification per 24 hours) in addition to the "only on creation"
  rule.
* Failures are logged and swallowed — never propagated to the caller.

### 1.7.3 Applicant-facing email

Email the user on grant and on denial using the same service, respecting
`email_notifications_enabled`.

### 1.8 Client identity claims on access tokens

In `issue_token()` in [gefapi/services/oidc_service.py](../gefapi/services/oidc_service.py),
add to the `access_token` branch:

```python
claims["azp"] = client.client_id
claims["client_id"] = client.client_id
claims["role"] = user.role
claims["app_access"] = active_app_keys(user)
claims["app_roles"] = active_app_roles(user)  # {"avoided_emissions": "admin"}
```

`app_access` and `app_roles` are point-in-time convenience claims for clients
rendering UI. They are **not** authoritative — enforcement always re-reads the
database, so a revocation takes effect immediately rather than after token
expiry.

Do not tighten `decode_token()`'s `claims_options` — loosening is safe, and
there are no in-flight OIDC tokens to invalidate while Rio is undeployed, but
keep the constraint in mind once it ships.

### 1.9 RS256 bearer acceptance on `/api/v1/*`

**Constraint:** `app.config["JWT_ALGORITHM"]` must stay `"HS256"` and the
flask-jwt-extended decode key must stay `JWT_SECRET_KEY`. Switching the algorithm
list or the key would invalidate every access token currently held by deployed
QGIS plugins and API UI sessions and would break `/auth/refresh`.

Instead add a **fallback** path in `gefapi/utils/bearer_auth.py`:

1. Try `verify_jwt_in_request()` (HS256). On success, behave exactly as today.
2. Only on failure, attempt `oidc_service.decode_token(raw, "access_token")`.
   * Resolve `OAuthClient` by `azp`/`client_id`, falling back to `audience` for
     tokens issued before §1.8.
   * Re-verify with the explicit `audience=client.audience`.
   * Reject if `jti` is in the blocklist (`is_token_in_blocklist`).
   * Load the `User` by `sub`; reject if `not user.is_active`.
   * Populate the same request-local context (`current_user`, claims accessor)
     the existing routes rely on.

Legacy callers never reach step 2, so their behaviour is bit-for-bit unchanged.

**When both paths fail, the original flask-jwt-extended failure must surface
unchanged.** This is the subtlest compatibility risk in Phase 1. The error
responses for expired, invalid, missing, and revoked tokens are produced by the
`@jwt.expired_token_loader` / `invalid_token_loader` / `unauthorized_loader` /
`revoked_token_loader` callbacks in [gefapi/__init__.py](../gefapi/__init__.py),
which fire from the exceptions `verify_jwt_in_request()` raises. A naive
`try/except` wrapper swallows those exceptions, the loaders never run, and every
caller gets a different status and body than it does today.

Required behaviour:

* Catch the HS256 exception, attempt RS256, and **re-raise the original
  exception** if RS256 also fails — do not synthesise a new error.
* Status code must remain `401`. The QGIS plugin's retry-and-refresh logic in
  `LDMP/api.py` branches on `status_code == 401`; anything else silently breaks
  token refresh and can produce a re-login loop.
* The JSON bodies must stay byte-identical, including the
  `error` discriminators `token_expired`, `invalid_token`,
  `authorization_required`, and `token_revoked`.
* `verify_jwt_in_request(optional=True)`, used by `_is_current_user_admin()` for
  rate-limit tiering, must keep working — do not route it through the wrapper.

Add an explicit test asserting the exact status and body for each of the four
failure modes, before and after the change.

Surface the resolved client on the request context (e.g. `g.oidc_client`) so
downstream code and logging can use it.

### 1.10 Enforcement points

Three layers, defense in depth:

1. **Authorization endpoint** — in `authorize()` in [gefapi/routes/oidc.py](../gefapi/routes/oidc.py),
   after the user is authenticated and before `new_authorization_code()`: if
   `client.required_app_key` and the user has no `active` grant, create or update
   the request per §1.7.1, then **redirect to `redirect_uri` with
   `error=access_denied`** and the original `state`. See §1.10.1 for the exact
   parameters. This is the primary gate.
2. **Token endpoint, `refresh_token` grant** — re-check the grant before issuing
   a new access token; return `invalid_grant` if the grant is no longer active.
   This bounds the window after a revocation to one refresh cycle even if the
   explicit refresh-token revocation in 1.7 is missed.
3. **Resource routes** — `@require_app_access("avoided_emissions")` on
   `/api/v1/*` endpoints that are specific to a gated app, checking the database
   rather than the token claim.

#### 1.10.1 Denied-authorization redirect contract

Redirecting with `error=access_denied` and self-service provisioning are not in
tension, because the request is created **before** the redirect. The user is
bounced back to the application they were trying to reach, which then renders
the right message from the redirect parameters.

The redirect carries:

| Parameter | Value |
|---|---|
| `error` | `access_denied` (RFC 6749 §4.1.2.1) |
| `error_description` | Human-readable, e.g. `Access to Avoided Emissions is pending administrator approval.` |
| `state` | The original `state`, unmodified — required so the client can match the response to its request |
| `te_app_access` | `pending` or `revoked` — the extension parameter the app branches on |
| `te_app_key` | The `required_app_key` that blocked the request |

Application behaviour:

* `te_app_access=pending` → "Your request for access has been submitted and is
  awaiting administrator approval. You'll receive an email when it's approved."
  No further action offered.
* `te_app_access=revoked` → "Your access to this application has been removed.
  Please contact an administrator." No automatic re-request.

Constraints:

* Only redirect when the `client_id` and `redirect_uri` have already been
  validated by `_validated_request()` — never redirect to an unvalidated URI.
* Extra parameters use a `te_` prefix so they cannot collide with registered
  OAuth parameters.
* Clients must treat unknown `te_app_access` values as a generic denial.
* Do not issue an authorization code, ID token, or refresh token on this path.

**Legacy-token rule:** `@require_app_access` must **deny** when the caller
presents a legacy HS256 `/auth` token, because that token carries no client
identity. Therefore apply the decorator **only to routes that no currently
deployed QGIS plugin or API UI version calls** — i.e. new, app-specific routes.
Defaulting to allow would create a bypass; applying it to an existing shared
route would break the plugin.

### 1.11 Discovery document

Update `/.well-known/openid-configuration`:

* `claims_supported` gains `azp`, `client_id`, `role`, `app_access`, `app_roles`.
* Document `required_app_key` behaviour, the `access_denied` error, and the
  `te_app_access` / `te_app_key` extension parameters in
  [docs/authentication.md](authentication.md).

### 1.12 Serializer exposure

Add `app_access` to the user serializer **only behind an explicit
`include=app_access` query parameter** on the user list/detail routes, matching
the existing `include`/`exclude` convention. This avoids changing the default
payload shape that the QGIS plugin and API UI already parse.

`User.serialize(include=..., exclude=...)` in [gefapi/models/user.py](../gefapi/models/user.py)
already ignores unrecognised `include` values rather than erroring, so the new
key is inert for every caller that does not ask for it.

### 1.13 Rio Coherence — the first consumer

Rio is built against the new gate in this phase. Because it is **not yet
deployed** — no production instance, no real users — none of this is a
migration: there is no data to preserve, no backfill, no cutover window, and no
backwards-compatibility constraint. The tables below are simply *defined
differently* before first deploy: delete the models and recreate the schema from
scratch, or collapse the change into the existing migration chain if it is still
being squashed. Any local development or pilot database is disposable — drop and
recreate it.

This is why Rio belongs here rather than in Phase 2. It exercises the whole path
end to end — authorize → denied → self-request → superadmin notification → grant
→ authorize succeeds — at zero risk, surfacing design problems while they are
still cheap to fix and before the API UI or Avoided Emissions depend on them.

**Authentication.** Point `src/rio_coherence/settings.py` at the Trends.Earth
issuer instead of GCP Identity Platform; `providers/gcp/identity.py` becomes a
generic JWKS verifier against `/.well-known/jwks.json`. The session principal is
built from token claims (`sub`, `email`, `name`, `app_roles`), not a database
row.

**Deletions.** Rio's account model duplicates `user_app_access` almost exactly —
it already has a request/approve/deny workflow with an audit trail — so it all
goes:

| Removed | Replaced by |
|---|---|
| `UserRecord` and the `users` table | API `User` + `user_app_access` |
| `UserRecord.role` / `ApplicationRole` | `user_app_access.role` |
| `UserRecord.status`, `decided_at`, `decided_by`, `decision_note` | `user_app_access.status`, `granted_at`, `granted_by_user_id`, `note` |
| `AccountDecisionRecord` / `account_decisions` | API security-audit events (§1.7) |
| `auth/registration.py` self-registration | API registration + `POST /api/v1/user/me/app-access/rio_coherence` |
| Local admin approval screens in `admin.py` | API admin app-access endpoints / API UI |

**What stays app-local.** Rio's `groups`, `memberships`, and `workspaces` encode
*which country workspaces* a user may see — genuinely Rio-specific authorization
with no analogue in the API. `AuthorizationService` in
[src/rio_coherence/auth/authorization.py](../../rio-synergies/src/rio_coherence/auth/authorization.py)
is retained, with two substitutions: `user.role is ApplicationRole.ADMIN` becomes
a check against the `rio_coherence` app role, and `user.active` becomes a check
that the grant is `active`.

**Schema.** The tables that currently reference `users.id` — `memberships`,
`chat_sessions`, and `email_outbox.user_id` — are redefined with a plain
`te_user_id UUID` column holding the API `sub`, with no foreign key (there is no
local target) but the same indexes the FKs provide today. `account_decisions`
and `users` are deleted outright. `UserRecord.oidc_subject` disappears with the
model; note that it currently holds *GCP Identity Platform* subjects, which are
not the Trends.Earth subjects the new columns store — another reason there is
nothing worth carrying forward.

**Denial UX.** Handle the `te_app_access` redirect parameters from §1.10.1 so a
blocked user sees "request submitted, awaiting approval" rather than a generic
OAuth error.

### 1.14 Phase 1 tests

Access-control model:

* `user_app_access` uniqueness, cascade delete with the user, status transitions.
* `has_app_access()` returns `False` for users with no row, `pending`, and
  `revoked`.
* `role` validation rejects values outside the app's `APP_ROLES` vocabulary.
* Self-request: idempotent on `pending`, no-op on `active`, `403` on `revoked`.
* Superadmin notification fires once on creation, is suppressed on re-request
  within the cooldown, addresses all `SUPERADMIN` users plus the configured
  admin email de-duplicated, and a mail failure does not fail the request.
* Admin routes: RBAC (non-admin gets 403), grant/revoke round trip, audit rows
  written, `RefreshToken` rows untouched on revoke.

Client identity and enforcement:

* Cross-client isolation: a token minted for audience A is rejected for a route
  bound to audience B.
* Gate denial at `/oauth/authorize`: the redirect carries `error=access_denied`,
  the original `state`, `te_app_access=pending`, and `te_app_key`; a `pending`
  row is created; the superadmin notification is dispatched; no authorization
  code is issued.
* A second blocked attempt redirects with `te_app_access=pending` but sends no
  further notification within the cooldown.
* A blocked attempt by a user whose grant is `revoked` redirects with
  `te_app_access=revoked` and does **not** reopen the request.
* The denial redirect is never sent to an unvalidated `redirect_uri`.
* Gate denial on `refresh_token` grant after revocation.
* `@require_app_access` denies legacy HS256 tokens.
* Revocation revokes only the matching client's `OIDCRefreshToken` rows and
  leaves `RefreshToken` rows intact.
* Loopback redirect matching: port wildcard accepted; `localhost`, other hosts,
  scheme mismatch, and path mismatch all rejected.
* Clients sharing an audience remain distinguishable by signed client identity;
  legacy tokens with an ambiguous audience and no client identity are rejected.

Regression — proving the plugin, API UI, and Avoided Emissions paths are
untouched:

* `POST /auth` → `/api/v1/user/me` → `/auth/refresh` still works end to end for
  a user with zero `user_app_access` rows.
* A pre-existing HS256 token continues to authenticate against every `/api/v1/*`
  route the plugin uses, with and without an `X-TE-Client` header.
* **Auth failure responses are byte-identical** for all four modes — expired,
  invalid, missing, and revoked — asserting both the `401` status and the exact
  JSON body including the `error` discriminator.
* `verify_jwt_in_request(optional=True)` still resolves an admin for rate-limit
  tiering.
* A `client_credentials` service-client token (as used by the Avoided Emissions
  webapp) is unaffected by the gate and by `@require_scope` on unrelated routes.
* Default `/api/v1/user` and `/api/v1/user/me` payloads contain no `app_access`
  key unless it is explicitly requested.

### 1.15 Phase 1 compatibility guarantees

Audited per client. Nothing in Phase 1 changes behaviour for any of the three
deployed applications.

**QGIS plugin** — authenticates via `POST /auth` (HS256) and calls `/api/v1/*`.

* `JWT_ALGORITHM`, `JWT_SECRET_KEY`, `/auth`, `/auth/refresh`, and
  `/auth/logout` are untouched; existing tokens keep verifying.
* RS256 acceptance is strictly a fallback reached only after HS256 verification
  raises, and the original exception is re-raised when both fail, so the `401`
  status and error bodies its retry logic depends on are preserved (§1.9).
* No `@require_app_access` decorator on any route it calls.
* Its OIDC client row is registered with `required_app_key = NULL`, so even if
  it did use OIDC it would not be gated.
* `X-TE-Client` handling and `user_client_metadata` are unchanged.

**API UI** — same `POST /auth` path, plus admin routes.

* Everything above applies.
* `include=app_access` is opt-in and `User.serialize()` ignores unknown
  `include` keys, so existing user payloads are byte-identical.
* `@require_scope("admin:*")` on the new routes does not affect its normal user
  tokens, and it is not applied to any existing route.

**Avoided Emissions webapp** — local login plus a client-credentials service
client.

* Its own authentication is entirely local in Phase 1 and is not touched.
* Its service client's `client_credentials` tokens are not gated: the app-access
  check runs only on the OIDC authorization-code and refresh-token paths, and
  `@require_app_access` is applied to no route it calls.
* The `avoided-emissions-web` OIDC client is registered with
  `required_app_key = 'avoided_emissions'`, but the app does not use the OIDC
  flow until Phase 2, so the gate is dormant.

**Blast radius if the migration fails.** The `migrate` service in
`docker-compose.prod.yml` runs with `restart_policy: condition: none` and is
separate from the API containers, so a failed migration does not stop the API.
The only Phase 1 code that reads `user_app_access` is OIDC token issuance,
OIDC enforcement, and the new admin routes — none of which the plugin, API UI,
or Avoided Emissions exercise. A failed migration therefore degrades Rio and the
new admin screens only. Verify the `audience` uniqueness precondition against a
production snapshot before deploying so this does not happen at all.

**Deploy ordering.** The superadmin notification runs as a Celery task, so the
worker image must be deployed alongside the API. It is fire-and-forget with
failures swallowed (§1.7.2), so version skew during a rolling deploy loses
notifications at worst — it never fails a request.

---

## Phase 2 — API UI and Avoided Emissions migration (no QGIS plugin changes)

The API-side capability all exists after Phase 1. This phase migrates the two
deployed web applications onto it. The QGIS plugin continues to use
`POST /auth` untouched throughout.

### 2.1 API UI changes

* Migrate login to authorization-code + PKCE against the `te-api-ui` client,
  keeping the existing `/auth` path available as a fallback during rollout.
* Add an **App access** column to the Users tab plus a grant/revoke modal calling
  the Phase 1 admin endpoints.
* Add a **Pending requests** view driven by
  `GET /api/v1/admin/app-access?status=pending`, linked from the superadmin
  notification email.
* Expose the per-app `role` in the grant/revoke modal.
* Add a self-service panel on the user's own profile listing available apps and
  their grant status, with a **Request access** button posting to
  `POST /api/v1/user/me/app-access/<app_key>`.
* Respect the callback-order and store-hydration pitfalls recorded for that
  repo — auth state arriving asynchronously is exactly the scenario that has
  caused Dash initial-callback bugs before.

### 2.2 Avoided Emissions webapp changes — remove the local user table

The webapp stops owning identity entirely. `webapp/models/user.py` is deleted
along with the `users` table; the API is the only user store.

**Authentication.** Replace the local email/password login in `webapp/auth.py`
with the OIDC authorization-code + PKCE flow against the `avoided-emissions-web`
confidential client. Flask-Login's `load_user` returns a lightweight session
principal built from token claims — `sub`, `email`, `name`, `app_roles` — not a
SQLAlchemy row. `is_admin` becomes `app_roles["avoided_emissions"] == "admin"`.

**Deletions.** The following all disappear, since the API now owns them:

| Removed | Replaced by |
|---|---|
| `User` model and `users` table | API `User` + `user_app_access` |
| `password_hash`, password hashing in `webapp/auth.py` | OIDC |
| `PasswordResetToken` model and reset flow | API password reset |
| `RefreshToken` model and local session refresh | OIDC refresh tokens |
| `TrendsEarthCredential` / `webapp/credential_store.py` | The user's own OIDC token |
| `webapp/services/user_admin.py` (`approve_user`, `change_user_role`) | API admin app-access endpoints |
| `is_approved` | `user_app_access.status == "active"` |
| `role` enum | `user_app_access.role` |

**Schema migration.** Every table that references `users.id` — `analysis_tasks`
(`submitted_by`), `covariates` (`started_by`), `user_site_sets`,
`user_site_uploads`, `covariate_presets`, `matching_settings_presets`,
`task_share_links` — repoints to a plain `te_user_id UUID NOT NULL` column
holding the API `sub`, with **no** foreign key (there is no local target) but
with the same indexes the existing FK indexes provide. Sequence:

1. Add nullable `te_user_id` beside each existing `*_user_id` / `submitted_by`
   column.
2. Backfill by matching the local `users.email` to the API user's email via the
   service client, writing the API `sub`. Report and halt on any unmatched row —
   an orphaned row must be resolved deliberately, not dropped.
3. Mirror each existing local user into `user_app_access` with
   `app_key='avoided_emissions'`, `status='active'` where `is_approved`, and
   `role` mapped from the local enum.
4. Make `te_user_id` non-null, drop the FK constraints, drop the old columns,
   drop `users` and the dependent auth tables.

Steps 1–2 are reversible and can ship ahead of the cutover; steps 3–4 are the
cutover itself and need a maintenance window.

**Display data.** Where the UI currently joins to `users` for a name or email
(task lists, share links, admin screens), fetch from the API — `GET
/api/v1/user/<sub>` or a batch lookup — and cache briefly in Redis. Do not
reintroduce a shadow copy of the user table.

**Service client unchanged.** The client-credentials credentials in
`webapp/trendsearth_client.py` remain for execution submission.

### 2.3 Phase 2 tests

* API UI: authorization-code login round trip; the app-access admin column and
  grant/revoke modal call the right endpoints; the pending-requests view renders
  the queue.
* Avoided Emissions: authorization-code login round trip against the
  confidential client; `is_admin` derives from `app_roles`; a user without an
  `avoided_emissions` grant is bounced with `te_app_access=pending` and sees the
  "awaiting approval" screen.
* Avoided Emissions migration: backfill matches every local user to an API `sub`
  or halts; grants are mirrored with the correct `status` and `role`; every
  `te_user_id` is populated and non-null before the FK drop; a dry run against a
  production snapshot completes cleanly.
* Rollback: steps 1–2 of the migration can be reverted without data loss.
* Regression: the QGIS plugin's `POST /auth` path is still untouched.

### 2.4 Phase 2 compatibility guarantees

* `JWT_ALGORITHM` unchanged; `/auth`, `/auth/refresh`, `/auth/logout` unchanged.
* No `@require_app_access` decorator on any route an existing plugin build calls.
* Response shapes for existing routes unchanged except for additive,
  `include`-gated fields.

---

## Phase 3 — Cutover and deprecation (breaking for the QGIS plugin)

Only this phase may require a plugin release.

### 3.1 QGIS plugin migration

* Implement authorization-code + PKCE with an RFC 8252 loopback redirect
  (`http://127.0.0.1:<ephemeral>/callback`) against the `te-qgis-plugin` client.
* Store the refresh token in the QGIS auth manager; stop storing the user's
  password.
* Continue sending `X-TE-Client` for telemetry.
* Ship in a plugin release and allow a long overlap window — users run old plugin
  builds for a long time.

### 3.2 Legacy endpoint deprecation

Staged, each step gated on telemetry from `user_client_metadata` showing the
remaining population on old builds:

1. Mark `POST /auth` deprecated in the OpenAPI spec and add a `Deprecation`
   response header.
2. Add a `Sunset` header with a concrete date.
3. Restrict `POST /auth` to clients that have not yet migrated (allowlist by
   `X-TE-Client` product/version), returning `410 Gone` to everyone else.
4. Remove `POST /auth`, `POST /auth/refresh`, and the `RefreshToken` model once
   the remaining population is negligible.

### 3.3 Unify on OIDC

* Make `@require_app_access` safe to apply to any route, since every caller now
  carries client identity.
* Consider retiring the HS256 path entirely, leaving only RS256 user tokens plus
  the client-credentials service-client tokens.
* Consider folding `/api/v1/oauth/token` (client_credentials) into the OIDC
  provider so there is one token endpoint and one signing key set.

### 3.4 Phase 3 tests

* Full plugin flow end-to-end against a loopback listener.
* `410 Gone` behaviour and header contents for deprecated endpoints.
* Migration of an existing plugin user from a password-based session to an OIDC
  session without losing execution history.

---

## Cross-cutting requirements

* **Linting:** `poetry run ruff check gefapi/ tests/` and
  `poetry run ruff format --check gefapi/ tests/` must pass with zero errors
  before any commit.
* **Tests:** run via `./run_tests.sh`; do not invoke `pytest` directly.
* **Migrations:** always verify the head on disk immediately before generating,
  and verify the new revision ID is not already present.
* **Secrets:** `OIDC_PRIVATE_KEY` must be configured in staging and production —
  [gefapi/__init__.py](../gefapi/__init__.py) already hard-fails without it. The
  `avoided-emissions-web` client secret must be delivered out of band and stored
  in that app's environment, never committed.
* **Rollout order:** Phase 1 to staging → verify the full gate flow against Rio
  → production. Then Phase 2 to staging with the API UI and Avoided Emissions →
  verify the legacy regression suite → production. The Avoided Emissions
  `users`-table cutover (§2.2 steps 3–4) needs its own maintenance window and a
  tested rollback, since it drops tables. Phase 3 only after a plugin release has
  shipped and adoption telemetry supports it.

## Remaining open questions

1. **Superadmin notification batching.** Per-request emails with a 24-hour
   per-user-per-app cooldown are assumed. If request volume turns out to be
   high, a daily digest of the pending queue would be less noisy.
2. **Avoided Emissions backfill orphans.** That cutover migration halts on any
   local user with no matching API account. Whether those users should be
   invited to register first, or their rows reassigned to an admin, needs a
   decision before the maintenance window is scheduled. Rio is unaffected — it
   has no data to migrate.
