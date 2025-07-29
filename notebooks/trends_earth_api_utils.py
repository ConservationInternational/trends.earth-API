"""
Trends.Earth API Testing Utilities

This module provides utility functions and classes for testing the Trends.Earth API.
Import this module in your notebooks to access shared functionality.

Example usage:
    from trends_earth_api_utils import TrendsEarthAPIClient, SCRIPT_PARAMS

    client = TrendsEarthAPIClient("http://localhost:5000")
    client.login("user@example.com", "password")
"""

import json
import time
from typing import Any, Dict, List, Optional
import warnings

from IPython.display import HTML, display
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# =============================================================================
# CONFIGURATION
# =============================================================================

# Default API configuration
DEFAULT_BASE_URL = "http://localhost:5000"

# Test user credentials - UPDATE THESE FOR YOUR ENVIRONMENT
TEST_USERS = {
    "regular": {
        "email": "test_user@example.com",
        "password": "testpassword123",
        "name": "Test User",
        "country": "US",
        "institution": "Test Organization",
    },
    "admin": {
        "email": "admin@example.com",
        "password": "adminpass123",
        "name": "Admin User",
        "role": "ADMIN",
        "country": "US",
        "institution": "Admin Organization",
    },
}

# Sample GeoJSON for testing (small area in Afghanistan - similar to the examples)
SAMPLE_GEOJSON = [
    {
        "coordinates": [
            [
                [64.98924930187721, 36.84154919400272],
                [65.21373808188198, 36.84154919400272],
                [65.21373808188198, 37.02176775244405],
                [64.98924930187721, 37.02176775244405],
                [64.98924930187721, 36.84154919400272],
            ]
        ],
        "type": "Polygon",
    }
]

# Land cover legend nesting for UNCCD categories
LAND_COVER_LEGEND_NESTING = {
    "child": {
        "key": [
            {
                "code": 1,
                "color": "#787F1B",
                "name_long": "Tree-covered",
                "name_short": "Tree-covered",
            },
            {
                "code": 2,
                "color": "#FFAC42",
                "name_long": "Grassland",
                "name_short": "Grassland",
            },
            {
                "code": 3,
                "color": "#FFFB6E",
                "name_long": "Cropland",
                "name_short": "Cropland",
            },
            {
                "code": 4,
                "color": "#00DB84",
                "name_long": "Wetland",
                "name_short": "Wetland",
            },
            {
                "code": 5,
                "color": "#E60017",
                "name_long": "Artificial",
                "name_short": "Artificial",
            },
            {
                "code": 6,
                "color": "#FFF3D7",
                "name_long": "Other land",
                "name_short": "Bare land",
            },
            {
                "code": 7,
                "color": "#0053C4",
                "name_long": "Water body",
                "name_short": "Water body",
            },
        ],
        "name": "Custom Land Cover",
        "nodata": {
            "code": -32768,
            "color": "#000000",
            "name_long": "No data",
            "name_short": "No data",
        },
    },
    "nesting": {
        "1": [1],
        "2": [2],
        "3": [3],
        "4": [4],
        "5": [5],
        "6": [6],
        "7": [7],
        "-32768": [-32768],
    },
    "parent": {
        "key": [
            {
                "code": 1,
                "color": "#787F1B",
                "name_long": "Tree-covered",
                "name_short": "Tree-covered",
            },
            {
                "code": 2,
                "color": "#FFAC42",
                "name_long": "Grassland",
                "name_short": "Grassland",
            },
            {
                "code": 3,
                "color": "#FFFB6E",
                "name_long": "Cropland",
                "name_short": "Cropland",
            },
            {
                "code": 4,
                "color": "#00DB84",
                "name_long": "Wetland",
                "name_short": "Wetland",
            },
            {
                "code": 5,
                "color": "#E60017",
                "name_long": "Artificial",
                "name_short": "Artificial",
            },
            {
                "code": 6,
                "color": "#FFF3D7",
                "name_long": "Other land",
                "name_short": "Bare land",
            },
            {
                "code": 7,
                "color": "#0053C4",
                "name_long": "Water body",
                "name_short": "Water body",
            },
        ],
        "name": "UNCCD Land Cover",
        "nodata": {
            "code": -32768,
            "color": "#000000",
            "name_long": "No data",
            "name_short": "No data",
        },
    },
}

# Common script parameters for different GEE scripts (based on real API examples)
SCRIPT_PARAMS = {
    "productivity": {
        "crosses_180th": False,
        "crs": 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]',
        "geojsons": SAMPLE_GEOJSON,
        "productivity": {
            "asset_climate": None,
            "asset_productivity": "users/geflanddegradation/toolbox_datasets/ndvi_modis_2001_2024",
            "mode": "TrendsEarth-LPD-5",
            "perf_year_final": 2020,
            "perf_year_initial": 2015,
            "state_year_bl_end": 2018,
            "state_year_bl_start": 2015,
            "state_year_tg_end": 2020,
            "state_year_tg_start": 2019,
            "traj_method": "ndvi_trend",
            "traj_year_final": 2020,
            "traj_year_initial": 2015,
        },
        "task_name": "productivity_test",
        "task_notes": "Testing productivity analysis",
    },
    "land-cover": {
        "crosses_180th": False,
        "crs": 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]',
        "download_annual_lc": False,
        "fl": "per pixel",
        "geojsons": SAMPLE_GEOJSON,
        "legend_nesting_custom_to_ipcc": LAND_COVER_LEGEND_NESTING,
        "legend_nesting_esa_to_custom": {
            "nesting": {
                "1": [50, 60, 61, 62, 70, 71, 72, 80, 81, 82, 90, 100],
                "2": [110, 120, 121, 122, 130, 140, 150, 151, 152, 153],
                "3": [10, 11, 12, 20, 30, 40],
                "4": [160, 170, 180],
                "5": [190],
                "6": [200, 201, 202, 220],
                "7": [210],
                "-32768": [-32768],
            }
        },
        "year_final": 2020,
        "year_initial": 2015,
        "task_name": "land_cover_test",
        "task_notes": "Testing land cover analysis",
    },
    "soil-organic-carbon": {
        "crosses_180th": False,
        "crs": 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]',
        "geojsons": SAMPLE_GEOJSON,
        "soil_organic_carbon": {
            "fl": 0.8,
            "legend_nesting_custom_to_ipcc": LAND_COVER_LEGEND_NESTING,
            "legend_nesting_esa_to_custom": {
                "nesting": {
                    "1": [50, 60, 61, 62, 70, 71, 72, 80, 81, 82, 90, 100],
                    "2": [110, 120, 121, 122, 130, 140, 150, 151, 152, 153],
                    "3": [10, 11, 12, 20, 30, 40],
                    "4": [160, 170, 180],
                    "5": [190],
                    "6": [200, 201, 202, 220],
                    "7": [210],
                    "-32768": [-32768],
                }
            },
            "year_final": 2020,
            "year_initial": 2015,
        },
        "task_name": "soc_test",
        "task_notes": "Testing soil organic carbon analysis",
    },
    "drought-vulnerability": {
        "crosses_180th": False,
        "crs": 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]',
        "geojsons": SAMPLE_GEOJSON,
        "land_cover": {
            "asset": "users/geflanddegradation/toolbox_datasets/lcov_esacc_1992_2022",
            "source": "ESA CCI",
        },
        "population": {
            "asset": "users/geflanddegradation/toolbox_datasets/worldpop_mf_v1_300m",
            "source": "Gridded Population Count (gender breakdown)",
        },
        "spi": {
            "asset": "users/geflanddegradation/toolbox_datasets/spi_gamma_gpcc_monthly_v2020",
            "lag": 12,
            "source": "GPCC V6 (Global Precipitation Climatology Centre)",
        },
        "year_final": 2019,
        "year_initial": 2000,
        "task_name": "drought_vulnerability_test",
        "task_notes": "Testing drought vulnerability assessment",
    },
    # Fallback minimal parameters
    "default": {
        "crosses_180th": False,
        "geojsons": SAMPLE_GEOJSON,
        "year_start": 2018,
        "year_end": 2020,
        "task_name": "api_test",
        "task_notes": "API testing execution",
    },
}

# =============================================================================
# API CLIENT CLASS
# =============================================================================


class TrendsEarthAPIClient:
    """Main client class for interacting with the Trends.Earth API"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url
        self.api_base = f"{base_url}/api/v1"
        self.access_token = None
        self.refresh_token = None
        self.user_id = None
        self.session = requests.Session()

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """Login and get access tokens"""
        url = f"{self.base_url}/auth"
        payload = {"email": email, "password": password}

        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()

            data = response.json()
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            self.user_id = data.get("user_id")

            # Set default authorization header
            self.session.headers.update(
                {"Authorization": f"Bearer {self.access_token}"}
            )

            print(f"✅ Login successful for {email}")
            return data

        except requests.exceptions.RequestException as e:
            print(f"❌ Login failed: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            return {}

    def refresh_access_token(self) -> bool:
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            print("❌ No refresh token available")
            return False

        url = f"{self.base_url}/auth/refresh"
        payload = {"refresh_token": self.refresh_token}

        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()

            data = response.json()
            self.access_token = data.get("access_token")
            self.user_id = data.get("user_id")

            # Update authorization header
            self.session.headers.update(
                {"Authorization": f"Bearer {self.access_token}"}
            )

            print("✅ Token refreshed successfully")
            return True

        except requests.exceptions.RequestException as e:
            print(f"❌ Token refresh failed: {e}")
            return False

    def logout(self) -> bool:
        """Logout and revoke refresh token"""
        if not self.refresh_token:
            return True

        url = f"{self.base_url}/auth/logout"
        payload = {"refresh_token": self.refresh_token}

        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()

            # Clear tokens
            self.access_token = None
            self.refresh_token = None
            self.user_id = None
            self.session.headers.pop("Authorization", None)

            print("✅ Logout successful")
            return True

        except requests.exceptions.RequestException as e:
            print(f"❌ Logout failed: {e}")
            return False

    def make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make authenticated API request with automatic token refresh"""
        url = (
            f"{self.api_base}{endpoint}"
            if endpoint.startswith("/")
            else f"{self.api_base}/{endpoint}"
        )

        try:
            response = getattr(self.session, method.lower())(url, **kwargs)

            # If unauthorized, try to refresh token and retry
            if response.status_code == 401 and self.refresh_token:
                print("🔄 Token expired, refreshing...")
                if self.refresh_access_token():
                    response = getattr(self.session, method.lower())(url, **kwargs)

            return response

        except requests.exceptions.RequestException as e:
            print(f"❌ Request failed: {e}")
            raise


# =============================================================================
# USER MANAGEMENT FUNCTIONS
# =============================================================================


def create_user(
    client: TrendsEarthAPIClient, user_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Create a new user"""
    try:
        response = client.make_request("POST", "/user", json=user_data)
        response.raise_for_status()

        data = response.json()
        print(f"✅ User created: {user_data['email']}")
        return data

    except requests.exceptions.RequestException as e:
        print(f"❌ User creation failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response: {e.response.text}")
        return {}


def get_users(client: TrendsEarthAPIClient, **params) -> List[Dict[str, Any]]:
    """Get list of users (admin only)"""
    try:
        response = client.make_request("GET", "/user", params=params)
        response.raise_for_status()

        data = response.json()
        users = data.get("data", [])
        print(f"✅ Retrieved {len(users)} users")
        return users

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get users: {e}")
        return []


def get_current_user(client: TrendsEarthAPIClient) -> Dict[str, Any]:
    """Get current user profile"""
    try:
        response = client.make_request("GET", "/user/me")
        response.raise_for_status()

        data = response.json()
        user = data.get("data", {})
        print(f"✅ Current user: {user.get('email', 'Unknown')}")
        return user

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get current user: {e}")
        return {}


def update_user_profile(
    client: TrendsEarthAPIClient, updates: Dict[str, Any]
) -> Dict[str, Any]:
    """Update current user profile"""
    try:
        response = client.make_request("PATCH", "/user/me", json=updates)
        response.raise_for_status()

        data = response.json()
        print("✅ Profile updated successfully")
        return data

    except requests.exceptions.RequestException as e:
        print(f"❌ Profile update failed: {e}")
        return {}


# =============================================================================
# SCRIPT MANAGEMENT FUNCTIONS
# =============================================================================


def get_scripts(client: TrendsEarthAPIClient, **params) -> List[Dict[str, Any]]:
    """Get list of available scripts"""
    try:
        response = client.make_request("GET", "/script", params=params)
        response.raise_for_status()

        data = response.json()
        scripts = data.get("data", [])
        print(f"✅ Retrieved {len(scripts)} scripts")
        return scripts

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get scripts: {e}")
        return []


def get_script_details(client: TrendsEarthAPIClient, script_id: str) -> Dict[str, Any]:
    """Get details of a specific script"""
    try:
        response = client.make_request("GET", f"/script/{script_id}")
        response.raise_for_status()

        data = response.json()
        script = data.get("data", {})
        print(f"✅ Retrieved script: {script.get('name', script_id)}")
        return script

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get script details: {e}")
        return {}


def run_script(
    client: TrendsEarthAPIClient, script_id: str, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Execute a script with given parameters"""
    try:
        response = client.make_request("POST", f"/script/{script_id}/run", json=params)
        response.raise_for_status()

        data = response.json()
        execution = data.get("data", {})
        print(f"✅ Script execution started: {execution.get('id', 'Unknown')}")
        return execution

    except requests.exceptions.RequestException as e:
        print(f"❌ Script execution failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response: {e.response.text}")
        return {}


# =============================================================================
# EXECUTION MANAGEMENT FUNCTIONS
# =============================================================================


def get_executions(client: TrendsEarthAPIClient, **params) -> List[Dict[str, Any]]:
    """Get list of executions"""
    try:
        response = client.make_request("GET", "/execution", params=params)
        response.raise_for_status()

        data = response.json()
        executions = data.get("data", [])
        print(f"✅ Retrieved {len(executions)} executions")
        return executions

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get executions: {e}")
        return []


def get_user_executions(client: TrendsEarthAPIClient, **params) -> List[Dict[str, Any]]:
    """Get current user's executions"""
    try:
        response = client.make_request("GET", "/execution/user", params=params)
        response.raise_for_status()

        data = response.json()
        executions = data.get("data", [])
        print(f"✅ Retrieved {len(executions)} user executions")
        return executions

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get user executions: {e}")
        return []


def get_execution_details(
    client: TrendsEarthAPIClient, execution_id: str
) -> Dict[str, Any]:
    """Get details of a specific execution"""
    try:
        response = client.make_request("GET", f"/execution/{execution_id}")
        response.raise_for_status()

        data = response.json()
        execution = data.get("data", {})
        print(f"✅ Retrieved execution: {execution.get('id', execution_id)}")
        return execution

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get execution details: {e}")
        return {}


def monitor_execution(
    client: TrendsEarthAPIClient, execution_id: str, max_wait: int = 300
) -> Dict[str, Any]:
    """Monitor execution until completion or timeout"""
    start_time = time.time()
    print(f"🔄 Monitoring execution {execution_id}...")

    while time.time() - start_time < max_wait:
        execution = get_execution_details(client, execution_id)
        if not execution:
            break

        status = execution.get("status", "UNKNOWN")
        print(f"📊 Status: {status}", end="")

        if "progress" in execution:
            print(f" (Progress: {execution['progress']}%)")
        else:
            print()

        if status in ["SUCCESS", "FAILED", "CANCELLED"]:
            print(f"✅ Execution completed with status: {status}")
            return execution

        time.sleep(10)  # Wait 10 seconds before next check

    print(f"⏰ Monitoring timeout after {max_wait} seconds")
    return execution if "execution" in locals() else {}


# =============================================================================
# SYSTEM MONITORING FUNCTIONS
# =============================================================================


def get_system_status(client: TrendsEarthAPIClient, **params) -> List[Dict[str, Any]]:
    """Get system status logs (admin only)"""
    try:
        response = client.make_request("GET", "/status", params=params)
        response.raise_for_status()

        data = response.json()
        status_logs = data.get("data", [])
        print(f"✅ Retrieved {len(status_logs)} status log entries")
        return status_logs

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get system status: {e}")
        return []


def get_swarm_status(client: TrendsEarthAPIClient) -> Dict[str, Any]:
    """Get Docker Swarm status (admin only)"""
    try:
        response = client.make_request("GET", "/status/swarm")
        response.raise_for_status()

        data = response.json()
        swarm_info = data.get("data", {})
        print("✅ Retrieved Docker Swarm status")
        return swarm_info

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get swarm status: {e}")
        return {}


def display_system_overview(client: TrendsEarthAPIClient):
    """Display comprehensive system overview"""
    print("🖥️  SYSTEM OVERVIEW")
    print("=" * 50)

    # Get recent status
    status_logs = get_system_status(client, per_page=1, sort="-timestamp")
    if status_logs:
        latest = status_logs[0]
        print(f"📊 Latest Status (timestamp: {latest.get('timestamp', 'Unknown')})")
        print(f"   Executions Active: {latest.get('executions_active', 0)}")
        print(f"   Executions Running: {latest.get('executions_running', 0)}")
        print(f"   Executions Ready: {latest.get('executions_ready', 0)}")
        print(f"   Total Users: {latest.get('users_count', 0)}")
        print(f"   Total Scripts: {latest.get('scripts_count', 0)}")
        print()

    # Get swarm status
    swarm_info = get_swarm_status(client)
    if swarm_info:
        print("🐳 Docker Swarm Status")
        print(f"   Active: {swarm_info.get('swarm_active', False)}")
        print(f"   Total Nodes: {swarm_info.get('total_nodes', 0)}")
        print(f"   Managers: {swarm_info.get('total_managers', 0)}")
        print(f"   Workers: {swarm_info.get('total_workers', 0)}")


# =============================================================================
# RATE LIMITING FUNCTIONS
# =============================================================================


def get_rate_limit_status(client: TrendsEarthAPIClient) -> Dict[str, Any]:
    """Get current rate limiting status (superadmin only)"""
    try:
        response = client.make_request("GET", "/rate-limit/status")
        response.raise_for_status()

        data = response.json()
        status = data.get("data", {})
        print("✅ Retrieved rate limit status")
        return status

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to get rate limit status: {e}")
        return {}


def reset_rate_limits(client: TrendsEarthAPIClient) -> bool:
    """Reset all rate limits (superadmin only)"""
    try:
        response = client.make_request("POST", "/rate-limit/reset")
        response.raise_for_status()

        print("✅ Rate limits reset successfully")
        return True

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to reset rate limits: {e}")
        return False


def test_rate_limiting(
    client: TrendsEarthAPIClient,
    endpoint: str = "/user/me",
    requests_count: int = 10,
    delay: float = 0.1,
):
    """Test rate limiting by making multiple requests"""
    print(f"🚀 Testing rate limiting with {requests_count} requests to {endpoint}")

    results = []
    for i in range(requests_count):
        try:
            start_time = time.time()
            response = client.make_request("GET", endpoint)
            end_time = time.time()

            results.append(
                {
                    "request": i + 1,
                    "status_code": response.status_code,
                    "response_time": round((end_time - start_time) * 1000, 2),
                    "rate_limited": response.status_code == 429,
                }
            )

            if response.status_code == 429:
                print(f"⚠️  Request {i + 1}: Rate limited (429)")
            else:
                print(f"✅ Request {i + 1}: Success ({response.status_code})")

        except Exception as e:
            print(f"❌ Request {i + 1}: Error - {e}")
            results.append(
                {
                    "request": i + 1,
                    "status_code": 500,
                    "response_time": 0,
                    "rate_limited": False,
                    "error": str(e),
                }
            )

        time.sleep(delay)

    # Summary
    rate_limited_count = sum(1 for r in results if r.get("rate_limited", False))
    success_count = sum(1 for r in results if r.get("status_code", 0) < 400)

    print("\n📊 Rate Limiting Test Results:")
    print(f"   Total Requests: {requests_count}")
    print(f"   Successful: {success_count}")
    print(f"   Rate Limited: {rate_limited_count}")
    print(f"   Success Rate: {(success_count / requests_count) * 100:.1f}%")

    return results


# =============================================================================
# GEE SCRIPT TESTING FUNCTIONS
# =============================================================================


def test_gee_script_execution(
    client: TrendsEarthAPIClient,
    script_name: str,
    custom_params: Optional[Dict[str, Any]] = None,
):
    """Test execution of a specific GEE script"""
    print(f"🌍 Testing {script_name} script execution")

    # Get available scripts first
    scripts = get_scripts(client)
    script_info = None

    # Find the script by name (partial match)
    for script in scripts:
        if (
            script_name.lower() in script.get("name", "").lower()
            or script_name.lower() in script.get("slug", "").lower()
        ):
            script_info = script
            break

    if not script_info:
        print(f"❌ Script '{script_name}' not found in available scripts")
        return None

    script_id = script_info.get("id") or script_info.get("slug")
    print(f"📝 Found script: {script_info.get('name', script_id)}")

    # Use custom parameters or default ones
    params = custom_params or SCRIPT_PARAMS.get(script_name, {})
    if not params:
        print(f"⚠️  No parameters defined for {script_name}, using minimal params")
        params = {"year_start": 2018, "year_end": 2020, "geojson": SAMPLE_GEOJSON}

    print(f"📋 Parameters: {json.dumps(params, indent=2)}")

    # Execute the script
    execution = run_script(client, script_id, params)
    if not execution:
        return None

    return execution


def test_all_gee_scripts(client: TrendsEarthAPIClient, monitor: bool = False):
    """Test execution of all available GEE scripts"""
    print("🌍 Testing all GEE scripts")
    print("=" * 50)

    scripts = get_scripts(client)
    if not scripts:
        print("❌ No scripts available")
        return []

    results = []

    for script in scripts[:5]:  # Limit to first 5 scripts to avoid overwhelming
        script_name = script.get("name", "").lower()
        script_id = script.get("id") or script.get("slug")

        print(f"\n🧪 Testing script: {script.get('name', script_id)}")

        # Determine appropriate parameters
        params = None
        for key, default_params in SCRIPT_PARAMS.items():
            if key in script_name:
                params = default_params
                break

        if not params:
            params = {"year_start": 2018, "year_end": 2020, "geojson": SAMPLE_GEOJSON}

        execution = run_script(client, script_id, params)
        result = {
            "script_name": script.get("name", script_id),
            "script_id": script_id,
            "execution_id": execution.get("id") if execution else None,
            "success": bool(execution),
            "params": params,
        }

        if monitor and execution:
            print("🔄 Monitoring execution...")
            final_execution = monitor_execution(client, execution["id"], max_wait=60)
            result["final_status"] = final_execution.get("status", "UNKNOWN")

        results.append(result)
        time.sleep(2)  # Brief pause between executions

    # Summary
    successful = sum(1 for r in results if r["success"])
    print("\n📊 GEE Script Testing Summary:")
    print(f"   Scripts tested: {len(results)}")
    print(f"   Successful executions: {successful}")
    print(f"   Success rate: {(successful / len(results)) * 100:.1f}%")

    return results


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def display_execution_summary(executions: List[Dict[str, Any]]):
    """Display a nice summary of executions"""
    if not executions:
        print("No executions to display")
        return

    # Create summary table
    df = pd.DataFrame(
        [
            {
                "ID": exec.get("id", "")[:8] + "...",
                "Script": exec.get("script_id", "Unknown")[:20],
                "Status": exec.get("status", "Unknown"),
                "Created": exec.get("created_at", "")[:19]
                if exec.get("created_at")
                else "",
                "Progress": f"{exec.get('progress', 0)}%"
                if exec.get("progress") is not None
                else "N/A",
            }
            for exec in executions
        ]
    )

    display(HTML(df.to_html(index=False, escape=False)))


def run_comprehensive_test_suite(api_url: str = DEFAULT_BASE_URL):
    """Run comprehensive test suite"""
    print("🚀 COMPREHENSIVE API TEST SUITE")
    print("=" * 50)

    # Initialize client
    client = TrendsEarthAPIClient(api_url)

    # Test 1: Authentication
    print("\n1️⃣  Testing Authentication")
    print("-" * 30)
    admin_creds = TEST_USERS["admin"]
    login_result = client.login(admin_creds["email"], admin_creds["password"])

    if not login_result:
        print("❌ Cannot proceed without authentication")
        return None

    # Test 2: User Management
    print("\n2️⃣  Testing User Management")
    print("-" * 30)
    current_user = get_current_user(client)
    users = get_users(client, per_page=5)

    # Test 3: Script Management
    print("\n3️⃣  Testing Script Management")
    print("-" * 30)
    scripts = get_scripts(client, per_page=10)

    # Test 4: Rate Limiting
    print("\n4️⃣  Testing Rate Limiting")
    print("-" * 30)
    rate_test_results = test_rate_limiting(client, requests_count=5, delay=0.2)

    # Test 5: System Monitoring
    print("\n5️⃣  Testing System Monitoring")
    print("-" * 30)
    display_system_overview(client)

    # Test 6: GEE Script Execution (if scripts available)
    if scripts:
        print("\n6️⃣  Testing GEE Script Execution")
        print("-" * 30)
        gee_results = test_all_gee_scripts(client, monitor=False)

    # Test 7: Execution Monitoring
    print("\n7️⃣  Testing Execution Monitoring")
    print("-" * 30)
    user_executions = get_user_executions(client, per_page=5)
    display_execution_summary(user_executions)

    print("\n✅ COMPREHENSIVE TEST SUITE COMPLETED")
    print("=" * 50)

    # Logout
    client.logout()

    return {
        "authentication": bool(login_result),
        "user_management": len(users) > 0,
        "scripts_available": len(scripts),
        "rate_limiting_working": any(
            r.get("rate_limited", False) for r in rate_test_results
        ),
        "executions_found": len(user_executions),
    }
