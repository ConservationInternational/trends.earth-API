"""News routes for the Trends.Earth API."""

import logging

import dateutil.parser
from flask import jsonify, request
from flask_jwt_extended import current_user, jwt_required

from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.news_service import NewsService
from gefapi.utils.permissions import can_access_admin_features

logger = logging.getLogger()


@endpoints.route("/news", strict_slashes=False, methods=["GET"])
@jwt_required(optional=True)
def get_news():
    """
    Retrieve news items for display in clients.

    **Authentication**: Optional (provides role-based filtering if authenticated)
    **Purpose**: Fetch news and announcements for the QGIS plugin, web app, or API UI

    **Query Parameters**:
    - `platform`: Filter by platform (app, webapp, api-ui)
    - `version`: Filter by plugin version compatibility
    - `lang`: Language code for translations (e.g., 'es', 'fr', 'zh')
    - `sort`: Sort field (prefix with '-' for descending)
    - `page`: Page number for pagination (default: 1)
    - `per_page`: Items per page (1-100, default: 20)

    **Response Schema**:
    ```json
    {
      "data": [
        {
          "id": "uuid-string",
          "title": "New Feature Available",
          "message": "We've released a new analysis tool...",
          "link_url": "https://docs.trends.earth/new-feature",
          "link_text": "Learn more",
          "created_at": "2025-01-15T10:30:00Z",
          "publish_at": "2025-01-15T10:30:00Z",
          "expires_at": null,
          "target_platforms": ["app", "webapp"],
          "target_roles": ["USER", "ADMIN"],
          "min_version": "2.0.0",
          "max_version": null,
          "priority": 10,
          "news_type": "info"
        }
      ],
      "page": 1,
      "per_page": 20,
      "total": 5
    }
    ```

    **News Types**:
    - `info`: General information or announcements
    - `warning`: Important warnings or notices
    - `alert`: Critical alerts requiring attention
    - `update`: Software update notifications

    **Platform Filtering**:
    - `app`: QGIS plugin
    - `webapp`: Web application
    - `api-ui`: API admin UI

    **Role Filtering**:
    - News items can be targeted to specific roles (USER, ADMIN, SUPERADMIN)
    - Unauthenticated users only see news with no role restrictions
    - Authenticated users see news targeted to their role or with no restrictions

    **Version Filtering**:
    - News items can specify min_version and/or max_version
    - Use semantic versioning (e.g., "2.1.0")

    **Note**: Dismissal of news items is handled client-side (localStorage/QgsSettings).
    """
    logger.info("[ROUTER]: Getting news items")

    # Get user role if authenticated
    user_role = None
    if current_user:
        user_role = getattr(current_user, "role", None)

    # Parse query parameters
    platform = request.args.get("platform")
    version = request.args.get("version")
    lang = request.args.get("lang")  # Language for translations
    sort = request.args.get("sort")

    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1

    try:
        per_page = min(int(request.args.get("per_page", 20)), 100)
    except ValueError:
        per_page = 20

    try:
        news_items, total = NewsService.get_news_items(
            platform=platform,
            version=version,
            user_role=user_role,
            sort=sort,
            page=page,
            per_page=per_page,
        )

        return jsonify(
            {
                "data": [item.serialize(language=lang) for item in news_items],
                "page": page,
                "per_page": per_page,
                "total": total,
            }
        )

    except Exception as e:
        logger.error(f"[ROUTER]: Error getting news items: {e}")
        return error(status=500, detail="Failed to retrieve news items")


@endpoints.route("/news/<news_id>", strict_slashes=False, methods=["GET"])
def get_news_item(news_id):
    """
    Retrieve a single news item by ID.

    **Authentication**: Not required
    **Path Parameters**:
    - `news_id`: UUID of the news item

    **Response**: Single news item object (see GET /news for schema)

    **Error Responses**:
    - `404 Not Found`: News item not found
    """
    logger.info(f"[ROUTER]: Getting news item {news_id}")

    news_item = NewsService.get_news_item(news_id)
    if not news_item:
        return error(status=404, detail="News item not found")

    return jsonify({"data": news_item.serialize()})


# Admin endpoints for managing news


@endpoints.route("/admin/news", strict_slashes=False, methods=["GET"])
@jwt_required()
def admin_get_news():
    """
    Admin endpoint to retrieve all news items including inactive and expired.

    **Authentication**: JWT token required
    **Access**: ADMIN and SUPERADMIN only

    **Query Parameters**:
    - Same as GET /news plus:
    - `include_inactive`: Include inactive items (default: true for admin)
    - `include_expired`: Include expired items (default: true for admin)

    **Response**: Same as GET /news
    """
    logger.info("[ROUTER]: Admin getting all news items")

    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")

    # Parse query parameters
    platform = request.args.get("platform")
    version = request.args.get("version")
    include_inactive = request.args.get("include_inactive", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    include_expired = request.args.get("include_expired", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    sort = request.args.get("sort")

    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1

    try:
        per_page = min(int(request.args.get("per_page", 50)), 100)
    except ValueError:
        per_page = 50

    try:
        news_items, total = NewsService.get_news_items(
            platform=platform,
            version=version,
            include_inactive=include_inactive,
            include_expired=include_expired,
            sort=sort,
            page=page,
            per_page=per_page,
        )

        return jsonify(
            {
                "data": [item.serialize() for item in news_items],
                "page": page,
                "per_page": per_page,
                "total": total,
            }
        )

    except Exception as e:
        logger.error(f"[ROUTER]: Error getting admin news items: {e}")
        return error(status=500, detail="Failed to retrieve news items")


@endpoints.route("/admin/news/<news_id>", strict_slashes=False, methods=["GET"])
@jwt_required()
def admin_get_news_item(news_id):
    """
    Admin endpoint to retrieve a single news item by ID.

    **Authentication**: JWT token required
    **Access**: ADMIN and SUPERADMIN only
    **Path Parameters**:
    - `news_id`: UUID of the news item

    **Response**: Single news item object (includes all fields even if inactive/expired)

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Not authorized
    - `404 Not Found`: News item not found
    """
    logger.info(f"[ROUTER]: Admin getting news item {news_id}")

    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")

    news_item = NewsService.get_news_item(news_id, include_inactive=True)
    if not news_item:
        return error(status=404, detail="News item not found")

    # Admin view always includes translations
    return jsonify(news_item.serialize(include_translations=True))


@endpoints.route("/admin/news", strict_slashes=False, methods=["POST"])
@jwt_required()
def create_news_item():
    """
    Create a new news item.

    **Authentication**: JWT token required
    **Access**: ADMIN and SUPERADMIN only

    **Request Body**:
    ```json
    {
      "title": "New Feature Released",
      "message": "We're excited to announce...",
      "link_url": "https://docs.trends.earth/feature",
      "link_text": "Read more",
      "publish_at": "2025-01-15T10:00:00Z",
      "expires_at": "2025-02-15T10:00:00Z",
      "target_platforms": "app,webapp",
      "target_roles": "USER,ADMIN",
      "min_version": "2.0.0",
      "max_version": null,
      "is_active": true,
      "priority": 10,
      "news_type": "info"
    }
    ```

    **Required Fields**: title, message
    **Optional Fields**: All others (see schema above)
    **target_roles**: Comma-separated list of roles (USER, ADMIN, SUPERADMIN).
                      Empty or null means visible to all users.

    **Response**: Created news item object

    **Error Responses**:
    - `400 Bad Request`: Missing required fields
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Not authorized (ADMIN+ required)
    """
    logger.info("[ROUTER]: Creating news item")

    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")

    data = request.get_json()
    if not data:
        return error(status=400, detail="Request body required")

    # Validate required fields
    title = data.get("title")
    message = data.get("message")

    if not title or not message:
        return error(status=400, detail="title and message are required")

    # Parse dates
    publish_at = None
    expires_at = None

    if data.get("publish_at"):
        try:
            publish_at = dateutil.parser.parse(data["publish_at"])
        except Exception:
            return error(status=400, detail="Invalid publish_at date format")

    if data.get("expires_at"):
        try:
            expires_at = dateutil.parser.parse(data["expires_at"])
        except Exception:
            return error(status=400, detail="Invalid expires_at date format")

    # Validate news_type
    news_type = data.get("news_type", "announcement")
    valid_types = ["announcement", "warning", "release", "tip", "maintenance"]
    if news_type not in valid_types:
        return error(
            status=400, detail=f"news_type must be one of: {', '.join(valid_types)}"
        )

    try:
        news_item = NewsService.create_news_item(
            title=title,
            message=message,
            created_by_id=str(identity.id),
            link_url=data.get("link_url"),
            link_text=data.get("link_text"),
            publish_at=publish_at,
            expires_at=expires_at,
            target_platforms=data.get("target_platforms", "app,webapp,api-ui"),
            target_roles=data.get("target_roles"),
            min_version=data.get("min_version"),
            max_version=data.get("max_version"),
            is_active=data.get("is_active", True),
            priority=data.get("priority", 0),
            news_type=news_type,
        )

        return jsonify({"data": news_item.serialize()}), 201

    except Exception as e:
        logger.error(f"[ROUTER]: Error creating news item: {e}")
        return error(status=500, detail="Failed to create news item")


@endpoints.route(
    "/admin/news/<news_id>", strict_slashes=False, methods=["PUT", "PATCH"]
)
@jwt_required()
def update_news_item(news_id):
    """
    Update an existing news item.

    **Authentication**: JWT token required
    **Access**: ADMIN and SUPERADMIN only
    **Path Parameters**:
    - `news_id`: UUID of the news item to update

    **Request Body**: Same as POST (all fields optional for partial update)

    **Response**: Updated news item object

    **Error Responses**:
    - `400 Bad Request`: Invalid data
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Not authorized
    - `404 Not Found`: News item not found
    """
    logger.info(f"[ROUTER]: Updating news item {news_id}")

    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")

    data = request.get_json()
    if not data:
        return error(status=400, detail="Request body required")

    # Parse dates if provided
    update_data = {}

    for field in [
        "title",
        "message",
        "link_url",
        "link_text",
        "target_platforms",
        "target_roles",
        "min_version",
        "max_version",
        "is_active",
        "priority",
        "news_type",
    ]:
        if field in data:
            update_data[field] = data[field]

    if "publish_at" in data and data["publish_at"]:
        try:
            update_data["publish_at"] = dateutil.parser.parse(data["publish_at"])
        except Exception:
            return error(status=400, detail="Invalid publish_at date format")

    if "expires_at" in data:
        if data["expires_at"]:
            try:
                update_data["expires_at"] = dateutil.parser.parse(data["expires_at"])
            except Exception:
                return error(status=400, detail="Invalid expires_at date format")
        else:
            update_data["expires_at"] = None

    # Validate news_type if provided
    if "news_type" in update_data:
        valid_types = ["announcement", "warning", "release", "tip", "maintenance"]
        if update_data["news_type"] not in valid_types:
            return error(
                status=400, detail=f"news_type must be one of: {', '.join(valid_types)}"
            )

    try:
        news_item = NewsService.update_news_item(news_id, **update_data)

        if not news_item:
            return error(status=404, detail="News item not found")

        return jsonify({"data": news_item.serialize()})

    except Exception as e:
        logger.error(f"[ROUTER]: Error updating news item: {e}")
        return error(status=500, detail="Failed to update news item")


@endpoints.route("/admin/news/<news_id>", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_news_item(news_id):
    """
    Delete a news item.

    **Authentication**: JWT token required
    **Access**: ADMIN and SUPERADMIN only
    **Path Parameters**:
    - `news_id`: UUID of the news item to delete

    **Response**:
    ```json
    {
      "status": "success",
      "message": "News item deleted"
    }
    ```

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Not authorized
    - `404 Not Found`: News item not found
    """
    logger.info(f"[ROUTER]: Deleting news item {news_id}")

    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")

    if NewsService.delete_news_item(news_id):
        return jsonify({"status": "success", "message": "News item deleted"})
    return error(status=404, detail="News item not found")


# Translation endpoints


@endpoints.route(
    "/admin/news/<news_id>/translations", strict_slashes=False, methods=["GET"]
)
@jwt_required()
def get_news_translations(news_id):
    """
    Get all translations for a news item.

    **Authentication**: JWT token required
    **Access**: ADMIN and SUPERADMIN only

    **Path Parameters**:
    - `news_id`: UUID of the news item

    **Response**:
    ```json
    {
      "data": {
        "es": {
          "id": "uuid",
          "language_code": "es",
          "title": "Título traducido",
          "message": "Mensaje traducido...",
          "link_text": "Leer más",
          "is_machine_translated": true
        },
        "fr": { ... }
      }
    }
    ```
    """
    logger.info(f"[ROUTER]: Getting translations for news item {news_id}")

    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")

    news_item = NewsService.get_news_item(news_id, include_inactive=True)
    if not news_item:
        return error(status=404, detail="News item not found")

    translations = {
        t.language_code: t.serialize() for t in news_item.translations.all()
    }

    return jsonify({"data": translations})


@endpoints.route(
    "/admin/news/<news_id>/translations", strict_slashes=False, methods=["PUT"]
)
@jwt_required()
def update_news_translations(news_id):
    """
    Update translations for a news item.

    **Authentication**: JWT token required
    **Access**: ADMIN and SUPERADMIN only

    **Path Parameters**:
    - `news_id`: UUID of the news item

    **Request Body**:
    ```json
    {
      "translations": {
        "es": {
          "title": "Título traducido",
          "message": "Mensaje traducido...",
          "link_text": "Leer más",
          "is_machine_translated": true
        },
        "fr": {
          "title": "Titre traduit",
          "message": "Message traduit...",
          "link_text": "En savoir plus",
          "is_machine_translated": true
        }
      }
    }
    ```

    **Notes**:
    - Supported languages: ar, es, fa, fr, pt, ru, sw, zh
    - To delete a translation, pass null or omit it from the request
    - `is_machine_translated` defaults to true

    **Response**: Updated translations object

    **Error Responses**:
    - `400 Bad Request`: Invalid data
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Not authorized
    - `404 Not Found`: News item not found
    """
    logger.info(f"[ROUTER]: Updating translations for news item {news_id}")

    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")

    news_item = NewsService.get_news_item(news_id, include_inactive=True)
    if not news_item:
        return error(status=404, detail="News item not found")

    data = request.get_json()
    if not data or "translations" not in data:
        return error(status=400, detail="translations object required")

    translations_data = data["translations"]

    try:
        updated = NewsService.update_translations(news_id, translations_data)
        return jsonify({"data": updated})
    except ValueError as e:
        return error(status=400, detail=str(e))
    except Exception as e:
        logger.error(f"[ROUTER]: Error updating translations: {e}")
        return error(status=500, detail="Failed to update translations")


@endpoints.route(
    "/admin/news/<news_id>/translations/<lang>",
    strict_slashes=False,
    methods=["DELETE"],
)
@jwt_required()
def delete_news_translation(news_id, lang):
    """
    Delete a specific translation for a news item.

    **Authentication**: JWT token required
    **Access**: ADMIN and SUPERADMIN only

    **Path Parameters**:
    - `news_id`: UUID of the news item
    - `lang`: Language code to delete (e.g., 'es', 'fr')

    **Response**:
    ```json
    {
      "status": "success",
      "message": "Translation deleted"
    }
    ```
    """
    logger.info(f"[ROUTER]: Deleting {lang} translation for news item {news_id}")

    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")

    if NewsService.delete_translation(news_id, lang):
        return jsonify({"status": "success", "message": "Translation deleted"})
    return error(status=404, detail="Translation not found")
