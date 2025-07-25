"""Flask-RESTX API Documentation Configuration"""

from flask_restx import Namespace

from gefapi import api

# Simple documentation configuration for Flask-RESTX
# The API endpoints are implemented as Flask Blueprint routes in gef_api_router.py

# Health check namespace for the existing /api-health endpoint
health_ns = Namespace('health', description='API Health Check')
api.add_namespace(health_ns, path='/')

# Simple documentation note
api_info = {
    'title': 'Trends.Earth API',
    'version': '1.0',
    'description': '''
    ## Trends.Earth API Documentation
    
    This API provides endpoints for managing scripts, users, and executions in the Trends.Earth platform.
    
    ### Available Endpoints:
    
    **Authentication:**
    - `POST /auth` - Authenticate and get access token
    - `POST /auth/refresh` - Refresh access token
    - `POST /auth/logout` - Logout
    - `POST /auth/logout-all` - Logout from all devices
    
    **Scripts:**
    - `GET /api/v1/script` - List scripts
    - `POST /api/v1/script` - Create script
    - `GET /api/v1/script/{id}` - Get script
    - `PATCH /api/v1/script/{id}` - Update script
    - `DELETE /api/v1/script/{id}` - Delete script
    - `POST /api/v1/script/{id}/publish` - Publish script
    - `POST /api/v1/script/{id}/unpublish` - Unpublish script
    - `POST /api/v1/script/{id}/run` - Run script
    - `GET /api/v1/script/{id}/download` - Download script
    - `GET /api/v1/script/{id}/log` - Get script log
    
    **Users:**
    - `GET /api/v1/user` - List users (admin)
    - `POST /api/v1/user` - Create user
    - `GET /api/v1/user/{id}` - Get user
    - `PATCH /api/v1/user/{id}` - Update user
    - `DELETE /api/v1/user/{id}` - Delete user
    - `GET /api/v1/user/me` - Get current user
    - `PATCH /api/v1/user/me` - Update current user
    
    **Executions:**
    - `GET /api/v1/execution` - List executions (admin)
    - `GET /api/v1/execution/user` - List user executions
    - `GET /api/v1/execution/{id}` - Get execution
    - `PATCH /api/v1/execution/{id}` - Update execution
    - `GET /api/v1/execution/{id}/log` - Get execution log
    - `POST /api/v1/execution/{id}/log` - Add log entry
    - `GET /api/v1/execution/{id}/results/{filename}` - Download results
    
    **System Status:**
    - `GET /api/v1/status` - Get system status logs (admin only)
    
    **Health:**
    - `GET /api-health` - API health check
    
    ### Authentication:
    Most endpoints require authentication using JWT tokens. Include the token in the Authorization header:
    ```
    Authorization: Bearer <your-jwt-token>
    ```
    
    ### Rate Limiting:
    API endpoints are rate-limited. Check response headers for current limits.
    '''
}
