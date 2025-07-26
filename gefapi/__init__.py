"""The GEF API MODULE"""

from datetime import datetime
import logging
import os
import sys

from flask import Flask, got_request_exception, jsonify, request
from flask_compress import Compress
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required
from flask_limiter import Limiter
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
import rollbar
import rollbar.contrib.flask

from gefapi.celery import make_celery
from gefapi.config import SETTINGS
from gefapi.utils.rate_limiting import (
    RateLimitConfig,
    get_rate_limit_key_for_auth,
    get_user_id_or_ip,
    is_rate_limiting_disabled,
    rate_limit_error_handler,
)

# Flask App
app = Flask(__name__)

# Configure CORS with specific origins for security
cors_origins = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:8080"
).split(",")
CORS(
    app,
    origins=cors_origins,
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)

Compress(app)

logger = logging.getLogger()
log_level = SETTINGS.get("logging", {}).get("level", "INFO")
logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

# Ensure all unhandled exceptions are logged, and reported to rollbar
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler = logging.StreamHandler(stream=sys.stdout)
handler.setLevel(logging.INFO)
handler.setFormatter(formatter)
logger.addHandler(handler)

rollbar.init(os.getenv("ROLLBAR_SERVER_TOKEN"), os.getenv("ENV"))
with app.app_context():
    got_request_exception.connect(rollbar.contrib.flask.report_exception, app)

app.config["SQLALCHEMY_DATABASE_URI"] = SETTINGS.get("SQLALCHEMY_DATABASE_URI")
app.config["UPLOAD_FOLDER"] = SETTINGS.get("UPLOAD_FOLDER")
# Transfer rate limiting configuration to Flask app config
app.config["RATE_LIMITING"] = SETTINGS.get("RATE_LIMITING", {})

# Ensure JWT_SECRET_KEY is set with proper fallback
jwt_secret = (
    SETTINGS.get("JWT_SECRET_KEY")
    or SETTINGS.get("SECRET_KEY")
    or os.getenv("JWT_SECRET_KEY")
    or os.getenv("SECRET_KEY")
)

app.config["JWT_SECRET_KEY"] = jwt_secret
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = SETTINGS.get("JWT_ACCESS_TOKEN_EXPIRES")
app.config["JWT_TOKEN_LOCATION"] = SETTINGS.get("JWT_TOKEN_LOCATION")
app.config["broker_url"] = SETTINGS.get("CELERY_BROKER_URL")
app.config["result_backend"] = SETTINGS.get("CELERY_RESULT_BACKEND")

# Configure request size limits for security
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max request size

# Database
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Celery
celery = make_celery(app)

# Rate Limiting (must be after db and celery)

limiter = Limiter(
    app=app,
    key_func=get_user_id_or_ip,  # Default key function that exempts admin users
    storage_uri=RateLimitConfig.get_storage_uri(),
    default_limits=RateLimitConfig.get_default_limits(),
    headers_enabled=True,  # Include rate limit info in response headers
    enabled=True,  # Always enabled, but we'll check dynamically via exempt_when
    on_breach=rate_limit_error_handler,
)


# DB has to be ready!
# Import tasks to register them with Celery
from gefapi import tasks  # noqa: E402,F401
from gefapi.routes.api.v1 import endpoints, error  # noqa: E402

# Blueprint Flask Routing
app.register_blueprint(endpoints, url_prefix="/api/v1")

# Log registered routes for debugging
total_routes = len(list(app.url_map.iter_rules()))
logger.info(f"Registered Flask app with {total_routes} total routes")
for rule in app.url_map.iter_rules():
    if not rule.endpoint.startswith(("static", "health_check", "swagger")):
        logger.debug(f"Registered route: {rule.rule} -> {rule.endpoint}")


@app.route("/api-health", methods=["GET"])
def health_check():
    """Simple health check endpoint"""
    try:
        # Test database connectivity by attempting a simple query
        # This approach checks the connection without relying on specific models
        from sqlalchemy import text

        result = db.session.execute(text("SELECT 1 as health_check")).fetchone()
        db_status = "healthy" if result else "unhealthy"
    except Exception as e:
        logger.warning(f"Database health check failed: {str(e)}")
        db_status = "unhealthy"

    return jsonify(
        {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "database": db_status,
            "version": "1.0",
        }
    ), 200


@app.route("/debug/routes", methods=["GET"])
def debug_routes():
    """Debug endpoint to show all registered routes"""
    import os

    routes_info = []
    for rule in app.url_map.iter_rules():
        routes_info.append(
            {
                "rule": str(rule.rule),
                "endpoint": rule.endpoint,
                "methods": list(rule.methods),
            }
        )

    return jsonify(
        {
            "environment": os.getenv("ENVIRONMENT", "unknown"),
            "total_routes": len(routes_info),
            "routes": routes_info[:20],  # First 20 routes for brevity
            "api_routes": [r for r in routes_info if r["rule"].startswith("/api/v1")][
                :10
            ],
        }
    )


@app.route("/swagger.json", methods=["GET"])
def swagger_spec():
    """Serve the generated OpenAPI/Swagger specification"""
    import os

    from flask import send_from_directory

    # Add debugging info
    environment = os.getenv("ENVIRONMENT", "unknown")
    logger.info(f"Swagger endpoint called. Environment: {environment}")
    logger.info(f"Total registered routes: {len(list(app.url_map.iter_rules()))}")

    # Try to serve the generated swagger.json file from gefapi/static
    swagger_path = os.path.join(os.path.dirname(__file__), "static")
    swagger_file_path = os.path.join(swagger_path, "swagger.json")

    logger.info(f"Checking for static swagger file at: {swagger_file_path}")
    if os.path.exists(swagger_file_path):
        logger.info("Found static swagger.json file, serving it")
        return send_from_directory(swagger_path, "swagger.json")

    # Fallback: Generate swagger spec dynamically using current app context
    try:
        # Generate OpenAPI spec directly using current Flask app
        logger.info("Attempting to generate swagger spec dynamically")
        spec = generate_openapi_spec_from_app()
        paths_count = len(spec.get("paths", {}))
        logger.info(f"Successfully generated swagger spec with {paths_count} paths")
        return jsonify(spec)
    except Exception as e:
        logger.error(f"Failed to generate swagger spec dynamically: {e}")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Exception details: {str(e)}")
        import traceback

        logger.error(f"Full traceback: {traceback.format_exc()}")

        # Final fallback: return a basic swagger spec
        return jsonify(
            {
                "openapi": "3.0.0",
                "info": {
                    "title": "Trends.Earth API",
                    "version": "1.0.0",
                    "description": (
                        "API documentation temporarily unavailable - "
                        "generation error occurred"
                    ),
                },
                "paths": {},
            }
        )


def generate_openapi_spec_from_app():
    """Generate OpenAPI specification from current Flask app routes"""
    import re

    logger.info("Starting dynamic OpenAPI spec generation")
    paths = {}

    # Iterate through all routes in the current app
    route_count = 0
    processed_routes = 0

    try:
        for rule in app.url_map.iter_rules():
            route_count += 1
            # Skip static files and internal endpoints
            if rule.endpoint in [
                "static",
                "health_check",
                "swagger_spec",
                "api_docs",
                "swagger_ui_static",
            ]:
                logger.debug(f"Skipping internal endpoint: {rule.endpoint}")
                continue

            # Get the endpoint function
            try:
                endpoint_func = app.view_functions[rule.endpoint]
            except KeyError:
                logger.warning(f"No view function found for endpoint: {rule.endpoint}")
                continue

            # Convert Flask route to OpenAPI path
            path = str(rule.rule)
            # Convert Flask path parameters to OpenAPI format
            path = path.replace("<", "{").replace(">", "}")
            # Remove parameter types
            path = re.sub(r"\{[^:}]*:", "{", path)

            try:
                path_item = extract_route_info_from_app(rule, endpoint_func)
                paths[path] = path_item
                processed_routes += 1
                logger.debug(f"Successfully processed route: {path}")
            except Exception as e:
                logger.warning(f"Could not process route {path}: {e}")
                continue

        logger.info(f"Processed {processed_routes} out of {route_count} total routes")

        # Build complete OpenAPI spec
        spec = {
            "openapi": "3.0.3",
            "info": {
                "title": "Trends.Earth API",
                "version": "1.0.0",
                "description": (
                    "API for managing Scripts, Users, and Executions in Trends.Earth"
                ),
                "contact": {
                    "name": "Trends.Earth Team",
                    "email": "azvoleff@conservation.org",
                },
                "license": {
                    "name": "MIT",
                    "url": "https://opensource.org/licenses/MIT",
                },
            },
            "servers": [{"url": "/api/v1", "description": "API v1"}],
            "components": {
                "securitySchemes": {
                    "bearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                        "bearerFormat": "JWT",
                    }
                },
                "schemas": {
                    "Error": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "integer"},
                            "detail": {"type": "string"},
                        },
                    }
                },
            },
            "paths": paths,
        }

        logger.info(f"Generated OpenAPI spec with {len(paths)} paths")
        return spec

    except Exception as e:
        logger.error(f"Error during route processing: {e}")
        logger.error(f"Processed {processed_routes} routes before error")
        raise


def extract_route_info_from_app(rule, endpoint_func) -> dict[str, dict]:
    """Extract information from a Flask route for current app"""
    import inspect

    methods = list(rule.methods - {"HEAD", "OPTIONS"})  # Remove default methods

    # Get docstring
    doc = inspect.getdoc(endpoint_func) or f"Endpoint: {rule.endpoint}"

    # Extract summary and description
    doc_lines = doc.split("\n")
    summary = doc_lines[0] if doc_lines else rule.endpoint
    description = "\n".join(doc_lines[1:]).strip() if len(doc_lines) > 1 else summary

    # Basic operation info
    operation = {
        "summary": summary,
        "description": description,
        "responses": {
            "200": {
                "description": "Success",
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
            "400": {"description": "Bad Request"},
            "401": {"description": "Unauthorized"},
            "403": {"description": "Forbidden"},
            "404": {"description": "Not Found"},
            "500": {"description": "Internal Server Error"},
        },
    }

    # Add authentication requirement for protected routes
    if "jwt_required" in doc.lower() or "@jwt_required" in inspect.getsource(
        endpoint_func
    ):
        operation["security"] = [{"bearerAuth": []}]

    # Add parameters for path variables
    parameters = []
    try:
        if hasattr(rule, "_converters") and rule._converters:
            for key, converter_info in rule._converters.items():
                try:
                    # Handle different Flask versions
                    if isinstance(converter_info, tuple) and len(converter_info) >= 3:
                        converter, arguments, variable = converter_info[:3]
                    else:
                        variable = key
                        converter = converter_info

                    param_type = "string"  # default type

                    # Determine parameter type based on converter
                    if hasattr(converter, "__class__"):
                        converter_name = converter.__class__.__name__
                    else:
                        converter_name = str(converter)

                    if "Integer" in converter_name:
                        param_type = "integer"
                    elif "Float" in converter_name:
                        param_type = "number"
                    elif "UUID" in converter_name:
                        param_type = "string"

                    parameters.append(
                        {
                            "name": variable,
                            "in": "path",
                            "required": True,
                            "schema": {"type": param_type},
                            "description": f"Path parameter: {variable}",
                        }
                    )
                except Exception as e:
                    logger.warning(f"Could not process parameter {key}: {e}")
                    continue
    except Exception as e:
        logger.warning(f"Could not process route parameters for {rule.rule}: {e}")

    if parameters:
        operation["parameters"] = parameters

    # Create path item with methods
    path_item = {}
    for method in methods:
        path_item[method.lower()] = operation.copy()

        if method in ["POST", "PUT", "PATCH"]:
            path_item[method.lower()]["requestBody"] = {
                "content": {"application/json": {"schema": {"type": "object"}}}
            }

    return path_item


@app.route("/static/swagger-ui/<path:filename>", methods=["GET"])
def swagger_ui_static(filename):
    """Serve Swagger UI static files"""
    import os

    from flask import send_from_directory

    static_path = os.path.join(os.path.dirname(__file__), "static", "swagger-ui")
    return send_from_directory(static_path, filename)


@app.route("/api/docs/", methods=["GET"])
def api_docs():
    """Serve Swagger UI for API documentation"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trends.Earth API Documentation</title>
        <link rel="stylesheet" type="text/css"
              href="/static/swagger-ui/swagger-ui.css" />
    </head>
    <body>
        <div id="swagger-ui"></div>
        <script src="/static/swagger-ui/swagger-ui-bundle.js"></script>
        <script>
            SwaggerUIBundle({
                url: '/swagger.json',
                dom_id: '#swagger-ui',
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIBundle.presets.standalone
                ],
                // Security: Disable unsafe features
                supportedSubmitMethods: ['get', 'post', 'put', 'delete', 'patch'],
                validatorUrl: null, // Disable external validator
                docExpansion: 'list',
                defaultModelsExpandDepth: 1
            });
        </script>
    </body>
    </html>
    """


jwt = JWTManager(app)


from gefapi.models import User  # noqa:E402
from gefapi.services import UserService  # noqa:E402


@app.route("/auth", methods=["POST"])
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_auth_limits()),
    key_func=get_rate_limit_key_for_auth,
    exempt_when=is_rate_limiting_disabled,
)
def create_token():
    logger.info("[JWT]: Attempting auth...")
    email = request.json.get("email", None)
    password = request.json.get("password", None)

    if not email or not password:
        logger.warning("[JWT]: Missing email or password in request")
        return jsonify({"msg": "Email and password are required"}), 400

    try:
        user = UserService.authenticate_user(email, password)
    except Exception as e:
        logger.error(f"[JWT]: Error during authentication: {str(e)}")
        return jsonify({"msg": "Authentication failed"}), 500

    if user is None:
        return jsonify({"msg": "Bad username or password"}), 401

    # Import here to avoid circular imports
    from gefapi.services.refresh_token_service import RefreshTokenService

    # Create access token
    access_token = create_access_token(identity=user.id)

    # Create refresh token
    refresh_token = RefreshTokenService.create_refresh_token(user.id)

    return jsonify(
        {
            "access_token": access_token,
            "refresh_token": refresh_token.token,
            "user_id": user.id,
            "expires_in": 3600,  # 1 hour in seconds
        }
    )


@app.route("/auth/refresh", methods=["POST"])
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_auth_limits()),
    key_func=get_rate_limit_key_for_auth,
    exempt_when=is_rate_limiting_disabled,
)
def refresh_token():
    logger.info("[JWT]: Attempting token refresh...")
    refresh_token_string = request.json.get("refresh_token", None)

    if not refresh_token_string:
        return jsonify({"msg": "Refresh token is required"}), 400

    # Import here to avoid circular imports
    from gefapi.services.refresh_token_service import RefreshTokenService

    access_token, user = RefreshTokenService.refresh_access_token(refresh_token_string)

    if not access_token:
        return jsonify({"msg": "Invalid or expired refresh token"}), 401

    return jsonify(
        {
            "access_token": access_token,
            "user_id": user.id,
            "expires_in": 3600,  # 1 hour in seconds
        }
    )


@app.route("/auth/logout", methods=["POST"])
@jwt_required()
def logout():
    logger.info("[JWT]: User logout...")
    refresh_token_string = request.json.get("refresh_token", None)

    if refresh_token_string:
        # Import here to avoid circular imports
        from gefapi.services.refresh_token_service import RefreshTokenService

        RefreshTokenService.revoke_refresh_token(refresh_token_string)

    return jsonify({"msg": "Successfully logged out"}), 200


@app.route("/auth/logout-all", methods=["POST"])
@jwt_required()
def logout_all():
    logger.info("[JWT]: User logout from all devices...")
    from flask_jwt_extended import current_user

    # Import here to avoid circular imports
    from gefapi.services.refresh_token_service import RefreshTokenService

    revoked_count = RefreshTokenService.revoke_all_user_tokens(current_user.id)

    return jsonify(
        {"msg": f"Successfully logged out from {revoked_count} devices"}
    ), 200


@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data):
    identity = jwt_data["sub"]
    return User.query.filter_by(id=identity).one_or_none()


@app.errorhandler(403)
def forbidden(e):
    return error(status=403, detail="Forbidden")


@app.errorhandler(404)
def page_not_found(e):
    return error(status=404, detail="Not Found")


@app.errorhandler(405)
def method_not_allowed(e):
    return error(status=405, detail="Method Not Allowed")


@app.errorhandler(410)
def gone(e):
    return error(status=410, detail="Gone")


@app.errorhandler(413)
def request_entity_too_large(e):
    return error(status=413, detail="Request too large")


@app.errorhandler(500)
def internal_server_error(e):
    return error(status=500, detail="Internal Server Error")


@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"

    # Prevent clickjacking by denying iframe embedding (except for API docs)
    if request.path == "/api/docs/":
        # Allow iframe from same origin for Swagger UI
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    else:
        response.headers["X-Frame-Options"] = "DENY"

    # Enable XSS protection in browsers
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Content Security Policy for any HTML content
    if request.path == "/api/docs/":
        # Strict CSP for API documentation - only allow self-hosted resources
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self' data:"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'"
        )

    # Force HTTPS if the request is secure
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )

    # Prevent referrer information leakage
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Control browser features and APIs
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    # Add additional security headers
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

    return response
