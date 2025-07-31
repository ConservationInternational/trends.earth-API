#!/usr/bin/env python3
"""
Script Access Control Management Tool

This script provides a command-line interface for managing script access controls.

SECURITY NOTICE: This tool requires ADMIN or SUPERADMIN privileges. Only users with
these roles can modify script access restrictions through the API or this admin tool.

Usage examples:

# List all scripts with their access status
python manage_script_access.py list-scripts

# List all users in the system
python manage_script_access.py list-users

# Show access summary for a script
python manage_script_access.py show-access script-slug-here

# Make a script accessible only to ADMIN and SUPERADMIN roles
python manage_script_access.py restrict-by-role script-slug-here ADMIN SUPERADMIN

# Make a script accessible only to specific users
python manage_script_access.py restrict-by-user script-slug-here user-id-1 user-id-2

# Add a user to an existing restricted script
python manage_script_access.py add-user script-slug-here user-id-3

# Add a role to an existing restricted script
python manage_script_access.py add-role script-slug-here ADMIN

# Remove a user from a script
python manage_script_access.py remove-user script-slug-here user-id-3

# Remove restrictions from a script
python manage_script_access.py clear-restrictions script-slug-here
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gefapi import app, db
from gefapi.models.script import Script
from gefapi.models.user import User
from gefapi.utils.script_access import (
    add_role_to_script,
    add_user_to_script,
    clear_script_restrictions,
    get_access_summary,
    remove_user_from_script,
    set_script_roles,
    set_script_users,
)


def find_script(slug_or_id):
    """Find a script by slug or ID"""
    script = Script.query.filter_by(slug=slug_or_id).first()
    if not script:
        import contextlib

        with contextlib.suppress(Exception):
            script = Script.query.filter_by(id=slug_or_id).first()
    return script


def restrict_by_role(script_slug, roles):
    """Restrict script access to specific roles"""
    script = find_script(script_slug)
    if not script:
        print(f"Script '{script_slug}' not found")
        return False

    valid_roles = ["USER", "ADMIN", "SUPERADMIN"]
    invalid_roles = [r for r in roles if r not in valid_roles]
    if invalid_roles:
        print(f"Invalid roles: {invalid_roles}. Valid roles are: {valid_roles}")
        return False

    set_script_roles(script, roles)
    db.session.commit()
    print(f"Script '{script.name}' is now restricted to roles: {roles}")
    return True


def restrict_by_user(script_slug, user_ids):
    """Restrict script access to specific users"""
    script = find_script(script_slug)
    if not script:
        print(f"Script '{script_slug}' not found")
        return False

    # Validate user IDs exist
    for user_id in user_ids:
        user = User.query.filter_by(id=user_id).first()
        if not user:
            print(f"User '{user_id}' not found")
            return False

    set_script_users(script, user_ids)
    db.session.commit()
    print(f"Script '{script.name}' is now restricted to users: {user_ids}")
    return True


def add_user(script_slug, user_id):
    """Add a user to script access"""
    script = find_script(script_slug)
    if not script:
        print(f"Script '{script_slug}' not found")
        return False

    user = User.query.filter_by(id=user_id).first()
    if not user:
        print(f"User '{user_id}' not found")
        return False

    add_user_to_script(script, user_id)
    db.session.commit()
    print(f"Added user '{user.email}' to script '{script.name}' access list")
    return True


def remove_user(script_slug, user_id):
    """Remove a user from script access"""
    script = find_script(script_slug)
    if not script:
        print(f"Script '{script_slug}' not found")
        return False

    remove_user_from_script(script, user_id)
    db.session.commit()
    print(f"Removed user '{user_id}' from script '{script.name}' access list")
    return True


def add_role(script_slug, role):
    """Add a role to script access"""
    script = find_script(script_slug)
    if not script:
        print(f"Script '{script_slug}' not found")
        return False

    valid_roles = ["USER", "ADMIN", "SUPERADMIN"]
    if role not in valid_roles:
        print(f"Invalid role: {role}. Valid roles are: {valid_roles}")
        return False

    add_role_to_script(script, role)
    db.session.commit()
    print(f"Added role '{role}' to script '{script.name}' access list")
    return True


def clear_restrictions(script_slug):
    """Clear all access restrictions from a script"""
    script = find_script(script_slug)
    if not script:
        print(f"Script '{script_slug}' not found")
        return False

    clear_script_restrictions(script)
    db.session.commit()
    print(f"Cleared all access restrictions from script '{script.name}'")
    return True


def show_access(script_slug):
    """Show access summary for a script"""
    script = find_script(script_slug)
    if not script:
        print(f"Script '{script_slug}' not found")
        return False

    summary = get_access_summary(script)

    print(f"\nAccess Summary for Script: {script.name} (slug: {script.slug})")
    print("=" * 50)
    print(f"Access Type: {summary['access_type']}")
    print(f"Public: {summary['public']}")
    print(f"Restricted: {summary['restricted']}")

    if summary["allowed_roles"]:
        print(f"Allowed Roles: {', '.join(summary['allowed_roles'])}")

    if summary["allowed_users"]:
        print(f"Allowed Users: {', '.join(summary['allowed_users'])}")
        # Try to show email addresses for user IDs
        for user_id in summary["allowed_users"]:
            user = User.query.filter_by(id=user_id).first()
            if user:
                print(f"  - {user_id}: {user.email}")

    if summary["access_type"] == "owner_only":
        owner = User.query.filter_by(id=script.user_id).first()
        if owner:
            print(f"Owner: {owner.email}")

    return True


def list_scripts():
    """List all scripts with their access status"""
    scripts = Script.query.order_by(Script.name).all()

    if not scripts:
        print("No scripts found in the database.")
        return True

    print(f"\nAll Scripts ({len(scripts)} total)")
    print("=" * 80)
    print(f"{'Slug':<30} {'Name':<25} {'Restricted':<10} {'Access Type'}")
    print("-" * 80)

    for script in scripts:
        summary = get_access_summary(script)
        restricted = "Yes" if summary["restricted"] else "No"
        access_type = summary["access_type"]

        # Truncate long names for display
        name = script.name[:24] + "..." if len(script.name) > 24 else script.name
        slug = script.slug[:29] + "..." if len(script.slug) > 29 else script.slug

        print(f"{slug:<30} {name:<25} {restricted:<10} {access_type}")

    return True


def list_users():
    """List all users in the system"""
    users = User.query.order_by(User.email).all()

    if not users:
        print("No users found in the database.")
        return True

    print(f"\nAll Users ({len(users)} total)")
    print("=" * 80)
    print(f"{'User ID':<40} {'Email':<30} {'Role'}")
    print("-" * 80)

    for user in users:
        role = getattr(user, "role", "Unknown")
        print(f"{user.id:<40} {user.email:<30} {role}")

    return True


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    # Use the existing Flask app instance
    with app.app_context():
        command = sys.argv[1]

        if command == "restrict-by-role" and len(sys.argv) >= 4:
            script_slug = sys.argv[2]
            roles = sys.argv[3:]
            restrict_by_role(script_slug, roles)

        elif command == "restrict-by-user" and len(sys.argv) >= 4:
            script_slug = sys.argv[2]
            user_ids = sys.argv[3:]
            restrict_by_user(script_slug, user_ids)

        elif command == "add-user" and len(sys.argv) == 4:
            script_slug = sys.argv[2]
            user_id = sys.argv[3]
            add_user(script_slug, user_id)

        elif command == "remove-user" and len(sys.argv) == 4:
            script_slug = sys.argv[2]
            user_id = sys.argv[3]
            remove_user(script_slug, user_id)

        elif command == "add-role" and len(sys.argv) == 4:
            script_slug = sys.argv[2]
            role = sys.argv[3]
            add_role(script_slug, role)

        elif command == "clear-restrictions" and len(sys.argv) == 3:
            script_slug = sys.argv[2]
            clear_restrictions(script_slug)

        elif command == "show-access" and len(sys.argv) == 3:
            script_slug = sys.argv[2]
            show_access(script_slug)

        elif command == "list-scripts" and len(sys.argv) == 2:
            list_scripts()

        elif command == "list-users" and len(sys.argv) == 2:
            list_users()

        else:
            print(__doc__)


if __name__ == "__main__":
    main()
