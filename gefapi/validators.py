"""GEFAPI VALIDATORS"""

from functools import wraps
import re
import unicodedata

import bleach
from flask import request

from gefapi.config import SETTINGS
from gefapi.routes.api.v1 import error

ROLES = SETTINGS.get("ROLES")
EMAIL_REGEX = re.compile(r"^[A-Za-z0-9\.\+_-]+@[A-Za-z0-9\._-]+\.[a-zA-Z]*$")


def sanitize_text(text, max_length=None, allow_html=False):
    """
    Sanitize text input while preserving international characters
    """
    if not text:
        return text

    # Convert to string if not already
    text = str(text).strip()

    # Remove or escape HTML/XML tags
    if not allow_html:
        # Remove all HTML tags but preserve international characters
        text = bleach.clean(text, tags=[], strip=True)
    else:
        # Allow only safe HTML tags
        allowed_tags = ["b", "i", "em", "strong", "p", "br", "ul", "ol", "li"]
        text = bleach.clean(text, tags=allowed_tags, strip=True)

    # Block dangerous patterns while preserving international text
    dangerous_patterns = [
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"vbscript:",
        r"on\w+\s*=",  # event handlers like onclick=
        r"data:text/html",
        r"<iframe",
        r"<object",
        r"<embed",
        r"<form",
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            raise ValueError("Invalid content detected")

    # Normalize unicode characters (NFC normalization)
    text = unicodedata.normalize("NFC", text)

    # Apply length limit if specified
    if max_length and len(text) > max_length:
        text = text[:max_length].strip()

    return text


def validate_name(name):
    """
    Validate names with international character support
    """
    if not name:
        raise ValueError("Name is required")

    # Sanitize but preserve international characters
    clean_name = sanitize_text(name, max_length=120)

    # Check minimum length
    if len(clean_name.strip()) < 1:
        raise ValueError("Name cannot be empty")

    # Allow letters, spaces, apostrophes, hyphens, dots (international support)
    # Using a more compatible regex pattern for international characters
    import unicodedata

    # Check each character is a letter, mark, space, or allowed punctuation
    for char in clean_name:
        if not (
            unicodedata.category(char).startswith("L")  # Letters
            or unicodedata.category(char).startswith("M")  # Marks (accents, etc.)
            or char in " '-."  # Allowed punctuation
            or unicodedata.category(char) == "Zs"
        ):  # Spaces
            raise ValueError("Name contains invalid characters")

    return clean_name


def validate_email(email):
    """
    Validate email addresses
    """
    if not email:
        raise ValueError("Email is required")

    email = email.strip().lower()

    if len(email) > 254:  # RFC 5321 limit
        raise ValueError("Email address too long")

    if not EMAIL_REGEX.match(email):
        raise ValueError("Invalid email format")

    return email


def validate_password(password):
    """
    Simple password validation - just check if it exists
    """
    if not password:
        raise ValueError("Password is required")

    return password


def validate_country(country):
    """
    Validate country names with international support
    """
    if not country:
        return country  # Optional field

    clean_country = sanitize_text(country, max_length=120)

    # Allow letters, spaces, hyphens, dots, apostrophes using unicodedata
    import unicodedata

    for char in clean_country:
        if not (
            unicodedata.category(char).startswith("L")  # Letters
            or unicodedata.category(char).startswith("M")  # Marks
            or char in " '-."  # Allowed punctuation
            or unicodedata.category(char) == "Zs"
        ):  # Spaces
            raise ValueError("Country contains invalid characters")

    return clean_country


def validate_institution(institution):
    """
    Validate institution names with international support
    """
    if not institution:
        return institution  # Optional field

    clean_institution = sanitize_text(institution, max_length=200)

    # Allow letters, numbers, spaces, common punctuation using unicodedata
    import unicodedata

    for char in clean_institution:
        if not (
            unicodedata.category(char).startswith("L")  # Letters
            or unicodedata.category(char).startswith("M")  # Marks
            or unicodedata.category(char).startswith("N")  # Numbers
            or char in " '-.()&,[]/"  # Allowed punctuation
            or unicodedata.category(char) == "Zs"
        ):  # Spaces
            raise ValueError("Institution contains invalid characters")

    return clean_institution


def validate_script_name(name):
    """
    Validate script names
    """
    if not name:
        raise ValueError("Script name is required")

    clean_name = sanitize_text(name, max_length=120)

    if len(clean_name.strip()) < 3:
        raise ValueError("Script name must be at least 3 characters")

    # Allow letters, numbers, spaces, hyphens, underscores, dots using unicodedata
    import unicodedata

    for char in clean_name:
        if not (
            unicodedata.category(char).startswith("L")  # Letters
            or unicodedata.category(char).startswith("M")  # Marks
            or unicodedata.category(char).startswith("N")  # Numbers
            or char in " -_."  # Allowed punctuation
            or unicodedata.category(char) == "Zs"
        ):  # Spaces
            raise ValueError("Script name contains invalid characters")

    return clean_name


def validate_description(description, max_length=1000):
    """
    Validate descriptions with limited HTML support
    """
    if not description:
        return description  # Optional field

    # Allow basic HTML formatting in descriptions
    clean_description = sanitize_text(
        description, max_length=max_length, allow_html=True
    )

    return clean_description


def validate_user_creation(func):
    """Enhanced User Creation Validation with international support"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()

        try:
            # Validate required fields
            if "email" not in json_data:
                return error(status=400, detail="Email is required")
            if "name" not in json_data:
                return error(status=400, detail="Name is required")

            # Validate and sanitize email
            json_data["email"] = validate_email(json_data["email"])

            # Validate and sanitize name with international support
            json_data["name"] = validate_name(json_data["name"])

            # Validate password if provided - simple check only
            if "password" in json_data and not json_data["password"]:
                return error(status=400, detail="Password is required")

            # Validate optional fields
            if "country" in json_data:
                json_data["country"] = validate_country(json_data["country"])

            if "institution" in json_data:
                json_data["institution"] = validate_institution(
                    json_data["institution"]
                )

            # Validate role
            if "role" in json_data:
                role = json_data.get("role")
                if role not in ROLES:
                    return error(status=400, detail="Invalid role")

        except ValueError as e:
            return error(status=400, detail=str(e))

        return func(*args, **kwargs)

    return wrapper


def validate_user_update(func):
    """Enhanced User Update Validation with international support"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()

        try:
            # Validate and sanitize fields if present
            if "name" in json_data:
                json_data["name"] = validate_name(json_data["name"])

            if "email" in json_data:
                json_data["email"] = validate_email(json_data["email"])

            if "country" in json_data:
                json_data["country"] = validate_country(json_data["country"])

            if "institution" in json_data:
                json_data["institution"] = validate_institution(
                    json_data["institution"]
                )

            if "role" in json_data:
                role = json_data.get("role")
                if role not in ROLES:
                    return error(status=400, detail="Invalid role")

        except ValueError as e:
            return error(status=400, detail=str(e))

        return func(*args, **kwargs)

    return wrapper


def validate_script_creation(func):
    """Script Creation Validation with international support"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()

        try:
            # Validate script name if provided in JSON
            if "name" in json_data:
                json_data["name"] = validate_script_name(json_data["name"])

            # Validate description if provided
            if "description" in json_data:
                json_data["description"] = validate_description(
                    json_data["description"]
                )

        except ValueError as e:
            return error(status=400, detail=str(e))

        return func(*args, **kwargs)

    return wrapper


def validate_password_change(func):
    """Simple Password Change Validation"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()

        if "password" not in json_data or "repeatPassword" not in json_data:
            return error(status=400, detail="Password and repeat password are required")

        password = json_data.get("password")
        repeat_password = json_data.get("repeatPassword")

        if password != repeat_password:
            return error(status=400, detail="Passwords do not match")

        # Simple validation - just check if password exists
        if not password:
            return error(status=400, detail="Password is required")

        return func(*args, **kwargs)

    return wrapper


def validate_profile_update(func):
    """Enhanced Profile Update Validation - DEPRECATED, use validate_password_change"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()
        if "password" not in json_data or "repeatPassword" not in json_data:
            return error(status=400, detail="Password and repeat password are required")
        password = json_data.get("password")
        repeat_password = json_data.get("repeatPassword")
        if password != repeat_password:
            return error(status=400, detail="Passwords do not match")

        try:
            # Simple validation - just check if password exists
            if not password:
                return error(status=400, detail="Password is required")

        except ValueError as e:
            return error(status=400, detail=str(e))

        return func(*args, **kwargs)

    return wrapper


def validate_file(func):
    """Enhanced Script File Validation"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if "file" not in request.files:
            return error(status=400, detail="File Required")

        file = request.files.get("file", None)
        if file is None:
            return error(status=400, detail="File Required")

        # Validate file size (10MB limit)
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning

        if file_size > 10 * 1024 * 1024:  # 10MB
            return error(status=400, detail="File too large (max 10MB)")

        # Validate file extension
        filename = file.filename or ""
        if not filename.endswith(".tar.gz"):
            return error(status=400, detail="File must be a .tar.gz archive")

        # Sanitize filename
        try:
            clean_filename = sanitize_text(filename, max_length=255)
            if clean_filename != filename:
                return error(status=400, detail="Invalid characters in filename")
        except ValueError as e:
            return error(status=400, detail=f"Invalid filename: {str(e)}")

        return func(*args, **kwargs)

    return wrapper


def validate_execution_update(func):
    """Enhanced Execution Update Validation"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()

        if (
            "status" not in json_data
            and "progress" not in json_data
            and "results" not in json_data
        ):
            return error(status=400, detail="Status, progress or results are required")

        try:
            # Validate status if provided
            if "status" in json_data:
                status = json_data["status"]
                valid_statuses = [
                    "SUBMITTED",
                    "READY",
                    "RUNNING",
                    "FINISHED",
                    "FAILED",
                    "CANCELLED",
                ]
                if status not in valid_statuses:
                    return error(status=400, detail="Invalid status")

            # Validate progress if provided
            if "progress" in json_data:
                progress = json_data["progress"]
                if (
                    not isinstance(progress, (int, float))
                    or progress < 0
                    or progress > 100
                ):
                    return error(
                        status=400, detail="Progress must be between 0 and 100"
                    )

            # Sanitize results if provided
            if "results" in json_data and json_data["results"]:
                # Limit results size to prevent abuse
                from gefapi.config import SETTINGS

                max_results_size = SETTINGS.get("MAX_RESULTS_SIZE", 50000)
                results_str = str(json_data["results"])
                if len(results_str) > max_results_size:
                    return error(status=400, detail="Results data too large")

        except ValueError as e:
            return error(status=400, detail=str(e))

        return func(*args, **kwargs)

    return wrapper


def validate_execution_log_creation(func):
    """Enhanced Execution Log Creation Validation"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        json_data = request.get_json()

        if "text" not in json_data or "level" not in json_data:
            return error(status=400, detail="Text and level are required")

        try:
            # Validate log level
            level = json_data["level"]
            valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            if level.upper() not in valid_levels:
                return error(status=400, detail="Invalid log level")

            # Sanitize log text
            log_text = json_data["text"]
            if len(log_text) > 10000:  # 10KB limit for log entries
                return error(status=400, detail="Log text too long")

            # Basic sanitization - remove dangerous content but preserve log formatting
            json_data["text"] = sanitize_text(
                log_text, max_length=10000, allow_html=False
            )

        except ValueError as e:
            return error(status=400, detail=str(e))

        return func(*args, **kwargs)

    return wrapper
