#!/usr/bin/env python3
"""
Generate OpenAPI/Swagger specification for Trends.Earth API
"""

import json
import os
from datetime import datetime

from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from flask import Flask
from marshmallow import Schema, fields

from gefapi import create_app


# Define Marshmallow schemas for API documentation
class UserSchema(Schema):
    id = fields.UUID(required=True, description="Unique identifier for the user")
    email = fields.Email(required=True, description="User's email address")
    name = fields.Str(description="User's full name")
    country = fields.Str(description="User's country")
    institution = fields.Str(description="User's institution")
    role = fields.Str(description="User role (USER, ADMIN)")
    created_at = fields.DateTime(description="Account creation timestamp")


class ScriptSchema(Schema):
    id = fields.UUID(required=True, description="Unique identifier for the script")
    name = fields.Str(required=True, description="Script name")
    slug = fields.Str(required=True, description="URL-friendly script identifier")
    status = fields.Str(description="Script status (PENDING, SUCCESS, FAILED)")
    public = fields.Bool(description="Whether script is publicly accessible")
    user_id = fields.UUID(description="ID of script owner")
    created_at = fields.DateTime(description="Script creation timestamp")
    cpu_reservation = fields.Int(description="Reserved CPU resources")
    cpu_limit = fields.Int(description="Maximum CPU limit")
    memory_reservation = fields.Int(description="Reserved memory (MB)")
    memory_limit = fields.Int(description="Maximum memory limit (MB)")


class ExecutionSchema(Schema):
    id = fields.UUID(required=True, description="Unique identifier for the execution")
    script_id = fields.UUID(required=True, description="ID of the executed script")
    user_id = fields.UUID(required=True, description="ID of user who ran the script")
    status = fields.Str(
        description="Execution status (PENDING, RUNNING, FINISHED, FAILED)"
    )
    progress = fields.Int(description="Execution progress percentage (0-100)")
    start_date = fields.DateTime(description="Execution start timestamp")
    end_date = fields.DateTime(description="Execution end timestamp")
    duration = fields.Float(description="Execution duration in seconds (when included)")
    params = fields.Dict(description="Execution parameters")
    results = fields.Dict(description="Execution results")


class StatusLogSchema(Schema):
    id = fields.Int(required=True, description="Unique identifier for the status log")
    timestamp = fields.DateTime(
        required=True, description="Status measurement timestamp"
    )
    executions_active = fields.Int(description="Number of active executions")
    executions_ready = fields.Int(description="Number of ready executions")
    executions_running = fields.Int(description="Number of running executions")
    executions_finished = fields.Int(description="Number of finished executions")
    users_count = fields.Int(description="Total number of users")
    scripts_count = fields.Int(description="Total number of scripts")
    memory_available_percent = fields.Float(description="Available memory percentage")
    cpu_usage_percent = fields.Float(description="CPU usage percentage")


class ExecutionLogSchema(Schema):
    id = fields.Int(required=True, description="Unique identifier for the log entry")
    text = fields.Str(required=True, description="Log message text")
    level = fields.Str(
        required=True, description="Log level (DEBUG, INFO, WARNING, ERROR)"
    )
    register_date = fields.DateTime(description="Log entry timestamp")
    execution_id = fields.UUID(description="ID of associated execution")


class ErrorSchema(Schema):
    error = fields.Str(required=True, description="Error message")
    detail = fields.Str(description="Detailed error description")


def create_api_spec():
    """Create APISpec instance with all schemas and paths"""

    spec = APISpec(
        title="Trends.Earth API",
        version="1.0.0",
        openapi_version="3.0.2",
        info=dict(
            description="API for managing Scripts, Users, and Executions in Trends.Earth",
            contact=dict(name="Trends.Earth Team", url="https://trends.earth"),
            license=dict(name="MIT", url="https://opensource.org/licenses/MIT"),
        ),
        servers=[
            {"url": "https://api.trends.earth", "description": "Production server"},
            {
                "url": "https://staging-api.trends.earth",
                "description": "Staging server",
            },
            {"url": "http://localhost:5000", "description": "Development server"},
        ],
        plugins=[MarshmallowPlugin()],
    )

    # Add security scheme
    spec.components.security_scheme(
        "bearerAuth", {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    )

    # Add schemas
    spec.components.schema("User", schema=UserSchema)
    spec.components.schema("Script", schema=ScriptSchema)
    spec.components.schema("Execution", schema=ExecutionSchema)
    spec.components.schema("StatusLog", schema=StatusLogSchema)
    spec.components.schema("ExecutionLog", schema=ExecutionLogSchema)
    spec.components.schema("Error", schema=ErrorSchema)

    # Add common responses
    spec.components.response(
        "UnauthorizedError",
        {
            "description": "Authentication required",
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
            },
        },
    )

    spec.components.response(
        "ForbiddenError",
        {
            "description": "Insufficient permissions",
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
            },
        },
    )

    spec.components.response(
        "NotFoundError",
        {
            "description": "Resource not found",
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
            },
        },
    )

    # Add paths
    add_auth_paths(spec)
    add_user_paths(spec)
    add_script_paths(spec)
    add_execution_paths(spec)
    add_status_paths(spec)

    return spec


def add_auth_paths(spec):
    """Add authentication endpoints"""
    spec.path(
        path="/auth",
        operations={
            "post": {
                "summary": "Authenticate user",
                "description": "Authenticate user with email and password",
                "tags": ["Authentication"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "email": {"type": "string", "format": "email"},
                                    "password": {"type": "string"},
                                },
                                "required": ["email", "password"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Successful authentication",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "access_token": {"type": "string"},
                                        "user": {"$ref": "#/components/schemas/User"},
                                    },
                                }
                            }
                        },
                    },
                    "401": {"$ref": "#/components/responses/UnauthorizedError"},
                },
            }
        },
    )


def add_user_paths(spec):
    """Add user management endpoints"""
    spec.path(
        path="/api/v1/user",
        operations={
            "get": {
                "summary": "List users",
                "description": "Get list of all users (Admin only)",
                "tags": ["Users"],
                "security": [{"bearerAuth": []}],
                "parameters": [
                    {
                        "name": "include",
                        "in": "query",
                        "description": "Additional data to include",
                        "schema": {"type": "string", "example": "scripts,executions"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "List of users",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/User"
                                            },
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "401": {"$ref": "#/components/responses/UnauthorizedError"},
                    "403": {"$ref": "#/components/responses/ForbiddenError"},
                },
            },
            "post": {
                "summary": "Create user",
                "description": "Create a new user account",
                "tags": ["Users"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "email": {"type": "string", "format": "email"},
                                    "password": {"type": "string"},
                                    "name": {"type": "string"},
                                    "country": {"type": "string"},
                                    "institution": {"type": "string"},
                                },
                                "required": ["email", "password"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "User created successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {"$ref": "#/components/schemas/User"}
                                    },
                                }
                            }
                        },
                    },
                    "400": {"$ref": "#/components/responses/BadRequestError"},
                },
            },
        },
    )


def add_script_paths(spec):
    """Add script management endpoints"""
    spec.path(
        path="/api/v1/script",
        operations={
            "get": {
                "summary": "List scripts",
                "description": "Get list of user's scripts",
                "tags": ["Scripts"],
                "security": [{"bearerAuth": []}],
                "parameters": [
                    {
                        "name": "include",
                        "in": "query",
                        "description": "Additional data to include",
                        "schema": {"type": "string", "example": "executions,logs"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "List of scripts",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/Script"
                                            },
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "401": {"$ref": "#/components/responses/UnauthorizedError"},
                },
            }
        },
    )


def add_execution_paths(spec):
    """Add execution management endpoints"""
    spec.path(
        path="/api/v1/execution",
        operations={
            "get": {
                "summary": "List executions",
                "description": "Get list of executions with filtering and sorting",
                "tags": ["Executions"],
                "security": [{"bearerAuth": []}],
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "description": "Filter by execution status",
                        "schema": {
                            "type": "string",
                            "enum": ["PENDING", "RUNNING", "FINISHED", "FAILED"],
                        },
                    },
                    {
                        "name": "start_date_gte",
                        "in": "query",
                        "description": "Filter executions started after this date",
                        "schema": {"type": "string", "format": "date-time"},
                    },
                    {
                        "name": "start_date_lte",
                        "in": "query",
                        "description": "Filter executions started before this date",
                        "schema": {"type": "string", "format": "date-time"},
                    },
                    {
                        "name": "end_date_gte",
                        "in": "query",
                        "description": "Filter executions ended after this date",
                        "schema": {"type": "string", "format": "date-time"},
                    },
                    {
                        "name": "end_date_lte",
                        "in": "query",
                        "description": "Filter executions ended before this date",
                        "schema": {"type": "string", "format": "date-time"},
                    },
                    {
                        "name": "sort",
                        "in": "query",
                        "description": "Sort field (prefix with - for descending)",
                        "schema": {"type": "string", "example": "-start_date"},
                    },
                    {
                        "name": "include",
                        "in": "query",
                        "description": "Additional data to include",
                        "schema": {"type": "string", "example": "duration,user,script"},
                    },
                    {
                        "name": "exclude",
                        "in": "query",
                        "description": "Data to exclude",
                        "schema": {"type": "string", "example": "params,results"},
                    },
                    {
                        "name": "page",
                        "in": "query",
                        "description": "Page number",
                        "schema": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    {
                        "name": "per_page",
                        "in": "query",
                        "description": "Items per page",
                        "schema": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 20,
                        },
                    },
                ],
                "responses": {
                    "200": {
                        "description": "List of executions",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/Execution"
                                            },
                                        },
                                        "page": {"type": "integer"},
                                        "per_page": {"type": "integer"},
                                        "total": {"type": "integer"},
                                    },
                                }
                            }
                        },
                    },
                    "401": {"$ref": "#/components/responses/UnauthorizedError"},
                },
            }
        },
    )


def add_status_paths(spec):
    """Add status monitoring endpoints"""
    spec.path(
        path="/api/v1/status",
        operations={
            "get": {
                "summary": "Get system status logs",
                "description": "Get system monitoring data (Admin only)",
                "tags": ["System Status"],
                "security": [{"bearerAuth": []}],
                "parameters": [
                    {
                        "name": "start_date",
                        "in": "query",
                        "description": "Filter logs from this date",
                        "schema": {"type": "string", "format": "date-time"},
                    },
                    {
                        "name": "end_date",
                        "in": "query",
                        "description": "Filter logs until this date",
                        "schema": {"type": "string", "format": "date-time"},
                    },
                    {
                        "name": "sort",
                        "in": "query",
                        "description": "Sort field",
                        "schema": {"type": "string", "default": "-timestamp"},
                    },
                    {
                        "name": "page",
                        "in": "query",
                        "description": "Page number",
                        "schema": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    {
                        "name": "per_page",
                        "in": "query",
                        "description": "Items per page",
                        "schema": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 1000,
                            "default": 100,
                        },
                    },
                ],
                "responses": {
                    "200": {
                        "description": "System status logs",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/StatusLog"
                                            },
                                        },
                                        "page": {"type": "integer"},
                                        "per_page": {"type": "integer"},
                                        "total": {"type": "integer"},
                                    },
                                }
                            }
                        },
                    },
                    "401": {"$ref": "#/components/responses/UnauthorizedError"},
                    "403": {"$ref": "#/components/responses/ForbiddenError"},
                },
            }
        },
    )


def main():
    """Generate and save OpenAPI specification"""
    # Create Flask app to get application context
    app = create_app()

    with app.app_context():
        # Create API specification
        spec = create_api_spec()

        # Convert to dictionary
        spec_dict = spec.to_dict()

        # Add generation timestamp
        spec_dict["info"]["x-generated-at"] = datetime.utcnow().isoformat()

        # Save to file
        with open("swagger.json", "w") as f:
            json.dump(spec_dict, f, indent=2, sort_keys=True)

        print("✅ OpenAPI specification generated successfully!")
        print(f"📄 File: swagger.json")
        print(f"📊 Endpoints documented: {len(spec_dict.get('paths', {}))}")
        print(
            f"🏗️  Schemas defined: {len(spec_dict.get('components', {}).get('schemas', {}))}"
        )


if __name__ == "__main__":
    main()
