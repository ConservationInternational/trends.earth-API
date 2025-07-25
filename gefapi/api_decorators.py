"""Flask-RESTX decorators for existing API endpoints"""

from flask_restx import Resource

from gefapi.api_docs import (
    auth_ns,
    error_model,
    user_model,
)


# Auth namespace documentation
@auth_ns.route("/check-logged")
class AuthCheck(Resource):
    @auth_ns.doc("check_auth_status")
    @auth_ns.response(200, "User is authenticated", user_model)
    @auth_ns.response(401, "User is not authenticated", error_model)
    def get(self):
        """Check if user is logged in"""
        # Implementation is in the actual route handler


# You can add more documented endpoints here as needed
# This file serves as a way to add Flask-RESTX documentation
# to existing endpoints without modifying the original route files

# Example for future implementation:
# @scripts_ns.route('')
# class ScriptList(Resource):
#     @scripts_ns.doc('list_scripts')
#     @scripts_ns.marshal_with(script_list_model)
#     @scripts_ns.response(200, 'Success')
#     def get(self):
#         """Get list of scripts"""
#         pass
#
#     @scripts_ns.doc('create_script')
#     @scripts_ns.expect(script_create_model)
#     @scripts_ns.marshal_with(script_model)
#     @scripts_ns.response(201, 'Script created')
#     @scripts_ns.response(400, 'Validation error', error_model)
#     def post(self):
#         """Create a new script"""
#         pass
