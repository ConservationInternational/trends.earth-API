# Trends.Earth API Testing Notebooks

This folder contains comprehensive testing notebooks for the Trends.Earth API, organized into focused, modular components.

## 📁 Structure

### 🔧 **Core Utilities**
- **`trends_earth_api_utils.py`** - Shared utilities module containing:
  - `TrendsEarthAPIClient` class for API interactions
  - Real API parameter structures from GEE scripts
  - Helper functions for authentication, monitoring, and testing
  - Test data and configuration

### 📓 **Testing Notebooks**

#### **`auth_user_management_tests.ipynb`** 🔐
**Focus**: Authentication flows and user management
- Login/logout testing
- Token refresh mechanisms
- User profile management
- Admin user operations
- Multiple session handling

#### **`script_execution_tests.ipynb`** ⚙️
**Focus**: GEE script execution and monitoring
- Script discovery and details
- Script execution with real parameters
- Execution status monitoring
- Parameter validation for different script types
- Execution history analysis

#### **`system_monitoring_tests.ipynb`** 📊
**Focus**: System monitoring and rate limiting
- System status monitoring
- Docker Swarm status checking
- Rate limiting tests (burst, sustained, multi-endpoint)
- Performance testing under load
- Concurrent request handling

#### **`comprehensive_test_suite.ipynb`** 🚀
**Focus**: Complete API validation
- Full test suite runner
- Health checks and connectivity testing
- Results analysis and scoring
- Recommendations based on outcomes
- Usage guide for other notebooks

#### **`api_testing_notebook.ipynb`** 📝
**Legacy**: Original monolithic notebook (kept for reference)

## 🚀 Quick Start

### 1. Configuration
Update the configuration in `trends_earth_api_utils.py`:

```python
# Update API URL
DEFAULT_BASE_URL = "http://your-api-url:5000"

# Update test user credentials
TEST_USERS = {
    "regular": {
        "email": "your-user@example.com",
        "password": "your-password",
        "name": "Your Name",
        "country": "US",
        "institution": "Your Organization"
    },
    "admin": {
        "email": "admin@example.com", 
        "password": "admin-password",
        "name": "Admin User",
        "role": "ADMIN",
        "country": "US",
        "institution": "Admin Organization"
    }
}
```

### 2. Running Tests

#### **Option A: Individual Testing**
Run notebooks in order based on your testing needs:
1. Start with `auth_user_management_tests.ipynb` to verify authentication
2. Run `script_execution_tests.ipynb` to test GEE script functionality
3. Use `system_monitoring_tests.ipynb` for performance and monitoring tests

#### **Option B: Complete Testing**
Run `comprehensive_test_suite.ipynb` for a full API validation

### 3. Custom Testing
Import utilities in your own notebooks:

```python
from trends_earth_api_utils import (
    TrendsEarthAPIClient, 
    SCRIPT_PARAMS,
    TEST_USERS,
    run_comprehensive_test_suite
)

# Initialize client
client = TrendsEarthAPIClient("http://your-api-url:5000")
client.login("user@example.com", "password")

# Use utility functions
from trends_earth_api_utils import get_scripts, run_script
scripts = get_scripts(client)
```

## 📋 Requirements

### Python Dependencies
```bash
pip install requests pandas jupyter matplotlib ipython
```

### API Requirements
- Running Trends.Earth API instance
- Valid user credentials (regular user + admin user recommended)
- Network access to the API

### Optional Dependencies
- `matplotlib` for visualization
- `concurrent.futures` for performance testing (included in Python 3.2+)

## 🔧 Customization

### Adding New Scripts
Update `SCRIPT_PARAMS` in `trends_earth_api_utils.py` with new script parameter structures:

```python
SCRIPT_PARAMS = {
    "your-new-script": {
        "geojsons": SAMPLE_GEOJSON,
        "year_initial": 2015,
        "year_final": 2020,
        # ... other parameters
    }
}
```

### Adding New Test Functions
Add functions to `trends_earth_api_utils.py` and import them in notebooks:

```python
def your_custom_test(client: TrendsEarthAPIClient):
    """Your custom test function"""
    # Implementation here
    pass
```

## 📊 API Coverage

These notebooks test the following API functionality:

### ✅ **Covered Features**
- Authentication (login, token refresh, logout)
- User management (profile, admin operations)
- Script discovery and execution
- Execution monitoring and status
- Rate limiting and performance
- System status monitoring
- Docker Swarm monitoring
- GEE script parameter validation

### 🔄 **GEE Scripts Tested**
- Productivity analysis
- Land cover analysis  
- Soil organic carbon
- Drought vulnerability assessment
- SDG 15.3.1 sub-indicators
- Urban area analysis

## 🐛 Troubleshooting

### Common Issues

#### **Connection Errors**
- Verify API URL is correct and accessible
- Check firewall settings
- Ensure API service is running

#### **Authentication Failures**
- Verify user credentials in `TEST_USERS`
- Check user permissions (admin functions require admin role)
- Ensure users exist in the API database

#### **Import Errors**
- Ensure `trends_earth_api_utils.py` is in the same directory
- Install required Python packages
- Check Python path if using virtual environments

#### **Rate Limiting Issues**
- Reduce request frequency in tests
- Check rate limiting configuration
- Use admin users for monitoring functions

## 📈 Performance Notes

- **Concurrent tests** may trigger rate limiting
- **Large parameter sets** may cause longer execution times
- **System monitoring** requires admin privileges
- **Docker Swarm tests** require swarm mode to be active

## 🤝 Contributing

When adding new tests or features:

1. **Use the shared utilities** - Add common functions to `trends_earth_api_utils.py`
2. **Follow the pattern** - Use similar structure to existing notebooks
3. **Include error handling** - Handle API failures gracefully
4. **Add documentation** - Update this README with new features
5. **Test thoroughly** - Verify with different API configurations

## 📚 Additional Resources

- [Trends.Earth API Documentation](https://github.com/ConservationInternational/trends.earth-API)
- [GEE Script Parameters](https://github.com/ConservationInternational/trends.earth/tree/main/gee)
- [Docker Swarm Documentation](https://docs.docker.com/engine/swarm/)

---

**Last Updated**: July 29, 2025  
**Version**: 1.0  
**Maintainer**: API Testing Team
