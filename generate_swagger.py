#!/usr/bin/env python3
"""
Generate OpenAPI/Swagger specification from Flask routes
"""

import json
import os
import sys
import inspect
from typing import Dict, Any

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gefapi import app


def extract_route_info(rule, endpoint_func) -> Dict[str, Any]:
    """Extract information from a Flask route"""
    methods = list(rule.methods - {'HEAD', 'OPTIONS'})  # Remove default methods
    
    # Get docstring
    doc = inspect.getdoc(endpoint_func) or f"Endpoint: {rule.endpoint}"
    
    # Extract summary and description
    doc_lines = doc.split('\n')
    summary = doc_lines[0] if doc_lines else rule.endpoint
    description = '\n'.join(doc_lines[1:]).strip() if len(doc_lines) > 1 else summary
    
    # Basic operation info
    operation = {
        "summary": summary,
        "description": description,
        "responses": {
            "200": {
                "description": "Success",
                "content": {
                    "application/json": {
                        "schema": {"type": "object"}
                    }
                }
            },
            "400": {"description": "Bad Request"},
            "401": {"description": "Unauthorized"},
            "403": {"description": "Forbidden"},
            "404": {"description": "Not Found"},
            "500": {"description": "Internal Server Error"}
        }
    }
    
    # Add authentication requirement for protected routes
    if 'jwt_required' in doc.lower() or '@jwt_required' in inspect.getsource(endpoint_func):
        operation["security"] = [{"bearerAuth": []}]
    
    # Add parameters for path variables
    parameters = []
    try:
        if hasattr(rule, '_converters') and rule._converters:
            for key, converter_info in rule._converters.items():
                try:
                    # Handle different Flask versions - converter info can be a tuple or object
                    if isinstance(converter_info, tuple) and len(converter_info) >= 3:
                        converter, arguments, variable = converter_info[:3]
                    else:
                        # Fallback for different Flask versions
                        variable = key
                        converter = converter_info
                    
                    param_type = "string"  # default type
                    
                    # Determine parameter type based on converter
                    if hasattr(converter, '__class__'):
                        converter_name = converter.__class__.__name__
                    else:
                        converter_name = str(converter)
                        
                    if 'Integer' in converter_name:
                        param_type = "integer"
                    elif 'Float' in converter_name:
                        param_type = "number"
                    elif 'UUID' in converter_name:
                        param_type = "string"
                        
                    parameters.append({
                        "name": variable,
                        "in": "path",
                        "required": True,
                        "schema": {"type": param_type},
                        "description": f"Path parameter: {variable}"
                    })
                except Exception as e:
                    # Skip problematic converters but continue processing
                    print(f"Warning: Could not process parameter {key}: {e}", file=sys.stderr)
                    continue
    except Exception as e:
        print(f"Warning: Could not process route parameters for {rule.rule}: {e}", file=sys.stderr)
    
    if parameters:
        operation["parameters"] = parameters
    
    # Create path item with methods
    path_item = {}
    for method in methods:
        path_item[method.lower()] = operation.copy()
        
        if method in ['POST', 'PUT', 'PATCH']:
            path_item[method.lower()]["requestBody"] = {
                "content": {
                    "application/json": {
                        "schema": {"type": "object"}
                    }
                }
            }
    
    return path_item


def generate_openapi_spec() -> Dict[str, Any]:
    """Generate OpenAPI specification from Flask app routes"""
    
    paths = {}
    
    with app.app_context():
        for rule in app.url_map.iter_rules():
            # Skip static files and internal endpoints
            if rule.endpoint in ['static', 'health_check', 'swagger_spec', 'api_docs']:
                continue
                
            # Get the endpoint function
            try:
                endpoint_func = app.view_functions[rule.endpoint]
            except KeyError:
                continue
            
            # Convert Flask route to OpenAPI path
            path = str(rule.rule)
            # Convert Flask path parameters to OpenAPI format
            path = path.replace('<', '{').replace('>', '}')
            # Remove parameter types
            import re
            path = re.sub(r'\{[^:}]*:', '{', path)
            
            try:
                path_item = extract_route_info(rule, endpoint_func)
                paths[path] = path_item
            except Exception as e:
                print(f"Warning: Could not process route {path}: {e}", file=sys.stderr)
                continue
    
    # Build complete OpenAPI spec
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "Trends.Earth API",
            "version": "1.0.0",
            "description": "API for managing Scripts, Users, and Executions in Trends.Earth",
            "contact": {
                "name": "Trends.Earth Team",
                "email": "azvoleff@conservation.org"
            },
            "license": {
                "name": "MIT",
                "url": "https://opensource.org/licenses/MIT"
            }
        },
        "servers": [
            {
                "url": "/api/v1",
                "description": "API v1"
            }
        ],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT"
                }
            },
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "integer"},
                        "detail": {"type": "string"}
                    }
                }
            }
        },
        "paths": paths
    }
    
    return spec


if __name__ == "__main__":
    try:
        spec = generate_openapi_spec()
        print(json.dumps(spec, indent=2))
    except Exception as e:
        print(f"Error generating OpenAPI spec: {e}", file=sys.stderr)
        sys.exit(1)
