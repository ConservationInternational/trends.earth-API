"""Flask-RESTX API Documentation Models an    'role': fields.String(
    description='User role', enum=['USER', 'ADMIN', 'SUPERADMIN']
), Namespaces"""

from datetime import datetime

from flask_restx import Namespace, Resource, fields

from gefapi import api

# Define namespaces
auth_ns = Namespace("auth", description="Authentication operations")
scripts_ns = Namespace("scripts", description="Script management operations")
executions_ns = Namespace("executions", description="Execution management operations")
users_ns = Namespace("users", description="User management operations")
system_ns = Namespace("system", description="System health and status operations")

# Add namespaces to the API
api.add_namespace(auth_ns, path="/auth")
api.add_namespace(scripts_ns, path="/script")
api.add_namespace(executions_ns, path="/execution")
api.add_namespace(users_ns, path="/user")
api.add_namespace(system_ns, path="/")

# Common models
error_model = api.model(
    "Error",
    {
        "status": fields.Integer(required=True, description="HTTP status code"),
        "detail": fields.String(required=True, description="Error message"),
        "errors": fields.List(fields.String, description="Validation errors"),
    },
)

pagination_model = api.model(
    "Pagination",
    {
        "page": fields.Integer(required=True, description="Current page number"),
        "per_page": fields.Integer(required=True, description="Items per page"),
        "total": fields.Integer(required=True, description="Total number of items"),
        "pages": fields.Integer(required=True, description="Total number of pages"),
    },
)

# User models
user_base_model = api.model(
    "UserBase",
    {
        "email": fields.String(required=True, description="User email address"),
        "name": fields.String(required=True, description="User full name"),
        "institution": fields.String(description="User institution"),
        "country": fields.String(description="User country"),
        "role": fields.String(
            description="User role", enum=["USER", "ADMIN", "SUPERADMIN"]
        ),
    },
)

user_model = api.inherit(
    "User",
    user_base_model,
    {
        "id": fields.String(required=True, description="User ID"),
        "created_at": fields.DateTime(description="Creation timestamp"),
        "updated_at": fields.DateTime(description="Last update timestamp"),
        "is_active": fields.Boolean(description="Whether user is active"),
    },
)

user_create_model = api.inherit(
    "UserCreate",
    user_base_model,
    {"password": fields.String(required=True, description="User password")},
)

user_update_model = api.model(
    "UserUpdate",
    {
        "name": fields.String(description="User full name"),
        "institution": fields.String(description="User institution"),
        "country": fields.String(description="User country"),
        "role": fields.String(
            description="User role", enum=["USER", "ADMIN", "SUPERADMIN"]
        ),
        "is_active": fields.Boolean(description="Whether user is active"),
    },
)

# Authentication models
login_model = api.model(
    "Login",
    {
        "email": fields.String(required=True, description="User email"),
        "password": fields.String(required=True, description="User password"),
    },
)

token_model = api.model(
    "Token",
    {
        "access_token": fields.String(required=True, description="JWT access token"),
        "refresh_token": fields.String(description="JWT refresh token"),
        "expires_in": fields.Integer(description="Token expiration time in seconds"),
    },
)

refresh_token_model = api.model(
    "RefreshToken",
    {"refresh_token": fields.String(required=True, description="JWT refresh token")},
)

# Script models
script_base_model = api.model(
    "ScriptBase",
    {
        "name": fields.String(required=True, description="Script name"),
        "slug": fields.String(description="Script slug"),
        "description": fields.String(description="Script description"),
        "source_code": fields.String(description="Script source code"),
        "params": fields.Raw(description="Script parameters"),
        "public": fields.Boolean(description="Whether script is public"),
        "status": fields.String(
            description="Script status",
            enum=["PENDING", "RUNNING", "COMPLETE", "ERROR"],
        ),
    },
)

script_model = api.inherit(
    "Script",
    script_base_model,
    {
        "id": fields.String(required=True, description="Script ID"),
        "user": fields.String(description="Script owner user ID"),
        "created_at": fields.DateTime(description="Creation timestamp"),
        "updated_at": fields.DateTime(description="Last update timestamp"),
    },
)

script_create_model = api.inherit("ScriptCreate", script_base_model, {})

# Execution models
execution_base_model = api.model(
    "ExecutionBase",
    {
        "status": fields.String(
            description="Execution status",
            enum=["PENDING", "RUNNING", "COMPLETE", "ERROR"],
        ),
        "progress": fields.Integer(description="Execution progress percentage"),
        "params": fields.Raw(description="Execution parameters"),
        "results": fields.Raw(description="Execution results"),
    },
)

execution_model = api.inherit(
    "Execution",
    execution_base_model,
    {
        "id": fields.String(required=True, description="Execution ID"),
        "script": fields.String(description="Script ID"),
        "user": fields.String(description="User ID"),
        "created_at": fields.DateTime(description="Creation timestamp"),
        "updated_at": fields.DateTime(description="Last update timestamp"),
        "start_date": fields.DateTime(description="Execution start time"),
        "end_date": fields.DateTime(description="Execution end time"),
    },
)

execution_create_model = api.model(
    "ExecutionCreate", {"params": fields.Raw(description="Execution parameters")}
)

execution_update_model = api.model(
    "ExecutionUpdate",
    {
        "status": fields.String(
            description="Execution status",
            enum=["PENDING", "RUNNING", "COMPLETE", "ERROR"],
        ),
        "progress": fields.Integer(description="Execution progress percentage"),
        "results": fields.Raw(description="Execution results"),
    },
)

# Health check model
health_model = api.model(
    "Health",
    {
        "status": fields.String(required=True, description="Service status"),
        "timestamp": fields.DateTime(
            required=True, description="Health check timestamp"
        ),
        "database": fields.String(required=True, description="Database status"),
        "version": fields.String(required=True, description="API version"),
    },
)

# Response models
user_list_model = api.model(
    "UserList",
    {
        "data": fields.List(fields.Nested(user_model)),
        "pagination": fields.Nested(pagination_model),
    },
)

script_list_model = api.model(
    "ScriptList",
    {
        "data": fields.List(fields.Nested(script_model)),
        "pagination": fields.Nested(pagination_model),
    },
)

execution_list_model = api.model(
    "ExecutionList",
    {
        "data": fields.List(fields.Nested(execution_model)),
        "pagination": fields.Nested(pagination_model),
    },
)


# Flask-RESTX Resources for documentation
@system_ns.route("/api-health")
class HealthCheck(Resource):
    @system_ns.doc("health_check")
    @system_ns.marshal_with(health_model)
    @system_ns.response(200, "Success")
    def get(self):
        """Get API health status"""
        try:
            # Test database connectivity by attempting a simple query
            from sqlalchemy import text

            from gefapi import db

            result = db.session.execute(text("SELECT 1 as health_check")).fetchone()
            db_status = "healthy" if result else "unhealthy"
        except Exception:
            db_status = "unhealthy"

        return {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "database": db_status,
            "version": "1.0",
        }, 200
