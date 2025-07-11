"""The GEF API MODULE"""

from datetime import datetime
import logging
import os
import sys

from flask import Flask, got_request_exception, jsonify, request
from flask_compress import Compress
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token
from flask_limiter import Limiter
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
import rollbar
import rollbar.contrib.flask

# from rollbar.logger import RollbarHandler
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
app.config["JWT_SECRET_KEY"] = SETTINGS.get("SECRET_KEY")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = SETTINGS.get("JWT_ACCESS_TOKEN_EXPIRES")
app.config["JWT_TOKEN_LOCATION"] = SETTINGS.get("JWT_TOKEN_LOCATION")
app.config["broker_url"] = SETTINGS.get("CELERY_BROKER_URL")
app.config["result_backend"] = SETTINGS.get("CELERY_RESULT_BACKEND")

# Configure request size limits for security
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max request size

# Configure rate limiting
# Initialize rate limiter with Redis backend (reuses existing Redis connection)
limiter = Limiter(
    app=app,
    key_func=get_user_id_or_ip,  # Default key function that exempts admin users
    storage_uri=RateLimitConfig.get_storage_uri(),
    default_limits=RateLimitConfig.get_default_limits(),
    headers_enabled=True,  # Include rate limit info in response headers
    enabled=True,  # Always enabled, but we'll check dynamically via exempt_when
    on_breach=rate_limit_error_handler,
)

# Database
db = SQLAlchemy(app)

migrate = Migrate(app, db)

celery = make_celery(app)

# DB has to be ready!
# Import tasks to register them with Celery
from gefapi import tasks  # noqa: E402,F401
from gefapi.routes.api.v1 import endpoints, error  # noqa: E402

# Blueprint Flask Routing
app.register_blueprint(endpoints, url_prefix="/api/v1")


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


# Handle authentication via JWT
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

    user = UserService.authenticate_user(email, password)

    if user is None:
        return jsonify({"msg": "Bad username or password"}), 401

    access_token = create_access_token(identity=user.id)
    return jsonify({"access_token": access_token, "user_id": user.id})


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

    # Prevent clickjacking by denying iframe embedding
    response.headers["X-Frame-Options"] = "DENY"

    # Enable XSS protection in browsers
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Content Security Policy for any HTML content
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'"
    )

    # Force HTTPS if the request is secure
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

    # Prevent referrer information leakage
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Control browser features and APIs
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    return response
