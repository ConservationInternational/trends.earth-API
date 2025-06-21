# Testing Guide for Trends.Earth API

This document provides comprehensive information about testing the Trends.Earth API.

## Test Structure

The test suite is organized into several categories:

### 1. Unit Tests (`tests/test_*.py`)
- **Authentication Tests** (`test_auth.py`): Login, logout, JWT token validation
- **User Tests** (`test_users.py`): User CRUD operations, permissions
- **Script Tests** (`test_scripts.py`): Script upload, management, execution
- **Execution Tests** (`test_executions.py`): Execution lifecycle, monitoring
- **Status Tests** (`test_status.py`): System status monitoring (admin only)

### 2. Integration Tests (`tests/test_integration.py`)
- End-to-end workflows
- Service interaction testing
- Database integration

### 3. API Validation Tests (`tests/test_api_validation.py`)
- Input validation
- Security testing (XSS, SQL injection prevention)
- Error handling
- Rate limiting

### 4. Performance Tests (`tests/test_performance.py`)
- Response time validation
- Concurrent request handling
- Memory usage stability
- Load testing

## Running Tests

### Prerequisites
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Local Testing

#### Using the Test Runner Script
```bash
# Run all tests
python run_tests.py

# Run specific test categories
python run_tests.py --unit          # Unit tests only
python run_tests.py --integration   # Integration tests only
python run_tests.py --validation    # API validation tests only
python run_tests.py --performance   # Performance tests only
python run_tests.py --lint          # Linting only

# Additional options
python run_tests.py --coverage      # Generate coverage report
python run_tests.py --fast          # Skip slow tests
python run_tests.py --verbose       # Verbose output
python run_tests.py --install-deps  # Install dependencies first
```

#### Using pytest Directly
```bash
# All tests
pytest tests/

# Unit tests only (excluding slow and integration tests)
pytest tests/ -m "not slow and not integration"

# Integration tests
pytest tests/test_integration.py

# API validation tests
pytest tests/test_api_validation.py

# Performance tests (fast only)
pytest tests/test_performance.py -m "not slow"

# With coverage
pytest tests/ --cov=gefapi --cov-report=html --cov-report=term
```

### GitHub Actions

Tests are automatically run on:
- **Pull Requests** to `main` or `develop` branches
- **Pushes** to `main` or `develop` branches

The CI pipeline includes:
1. **Linting**: Black, isort, flake8
2. **Type Checking**: mypy
3. **Unit Tests**: Core functionality tests
4. **Integration Tests**: End-to-end workflows
5. **API Validation Tests**: Security and validation
6. **Performance Tests**: Basic performance checks
7. **Security Scans**: Safety and bandit
8. **Coverage Reports**: Uploaded to Codecov

## Test Configuration

### Environment Variables
Tests use the following environment variables:
- `DATABASE_URL`: Test database URL (defaults to SQLite)
- `REDIS_URL`: Redis URL for Celery testing
- `JWT_SECRET_KEY`: JWT secret for authentication
- `FLASK_ENV`: Set to 'testing'
- `TESTING`: Set to 'true'

### Test Database
Tests use a separate test database to avoid conflicts with development data.

### Fixtures
Common test fixtures are defined in `tests/conftest.py`:
- `app`: Flask application instance
- `client`: Test client for API requests
- `admin_user`, `regular_user`: Test users
- `admin_token`, `user_token`: JWT tokens
- `auth_headers_admin`, `auth_headers_user`: Authorization headers
- `sample_script`, `sample_execution`: Test data

## Test Utilities

The `tests/test_utils.py` file provides utility functions:
- `TestUtils`: General testing utilities
- `DateTestUtils`: Date/time testing helpers
- `StatusTestUtils`: Status monitoring test data
- `ErrorTestUtils`: Error response validation
- `DatabaseTestUtils`: Database testing helpers
- `MockServices`: Mock external services

## Writing New Tests

### Test Naming Convention
- Test files: `test_*.py`
- Test classes: `Test*` (e.g., `TestUserAPI`)
- Test methods: `test_*` (e.g., `test_create_user_success`)

### Test Markers
Use pytest markers to categorize tests:
```python
@pytest.mark.slow
def test_long_running_operation():
    pass

@pytest.mark.integration
def test_end_to_end_workflow():
    pass

@pytest.mark.admin
def test_admin_only_feature():
    pass
```

### Example Test Structure
```python
class TestUserAPI:
    """Test user-related API endpoints"""
    
    def test_create_user_success(self, client, auth_headers_admin):
        """Test successful user creation"""
        response = client.post('/api/v1/user', json={
            'email': 'test@example.com',
            'password': 'password123',
            'name': 'Test User',
            'role': 'USER'
        }, headers=auth_headers_admin)
        
        assert response.status_code == 200
        data = response.json['data']
        assert data['email'] == 'test@example.com'
        assert 'password' not in data  # Sensitive data not exposed
    
    def test_create_user_validation_error(self, client, auth_headers_admin):
        """Test user creation with invalid data"""
        response = client.post('/api/v1/user', json={
            'email': 'invalid-email',  # Invalid email format
            'password': '123',         # Too short
        }, headers=auth_headers_admin)
        
        assert response.status_code == 400
        assert 'error' in response.json
```

## Coverage Goals

- **Overall Coverage**: > 80%
- **Critical Paths**: > 95% (authentication, user management, script execution)
- **API Endpoints**: 100% (all endpoints must have tests)

## Continuous Integration

The GitHub Actions workflow (`.github/workflows/run-tests.yml`) includes:

### Test Matrix
- Python versions: 3.8, 3.9, 3.10
- Multiple test categories run in parallel
- Performance tests run only on Python 3.9

### Artifacts
- Test results (JUnit XML)
- Coverage reports (HTML, XML)
- Performance test results
- Security scan reports

### Failure Handling
- Tests must pass for PR to be merged
- Coverage reports uploaded to Codecov
- Security scan results uploaded as artifacts

## Troubleshooting

### Common Issues

1. **Database Connection Errors**
   - Ensure PostgreSQL/Redis services are running
   - Check environment variables

2. **Import Errors**
   - Verify all dependencies are installed
   - Check Python path configuration

3. **Authentication Failures**
   - Verify JWT_SECRET_KEY is set
   - Check user fixtures are properly created

4. **Timeout Issues**
   - Increase timeout values for slow tests
   - Use `@pytest.mark.slow` for long-running tests

### Debugging Tests
```bash
# Run with more verbose output
pytest tests/ -v -s

# Run specific test with debugging
pytest tests/test_auth.py::TestAuth::test_login_success -v -s

# Run with pdb debugger
pytest tests/ --pdb

# Run with coverage and keep coverage files
pytest tests/ --cov=gefapi --cov-report=html --cov-report=term-missing
```

## Best Practices

1. **Test Independence**: Each test should be independent and not rely on other tests
2. **Descriptive Names**: Test names should clearly describe what is being tested
3. **Arrange-Act-Assert**: Structure tests with clear setup, action, and verification
4. **Mock External Services**: Use mocks for external dependencies (S3, email, etc.)
5. **Test Edge Cases**: Include tests for error conditions and boundary cases
6. **Keep Tests Fast**: Use mocks and fixtures to minimize test execution time
7. **Regular Maintenance**: Update tests when API changes occur

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [Flask Testing Documentation](https://flask.palletsprojects.com/en/2.0.x/testing/)
- [Coverage.py Documentation](https://coverage.readthedocs.io/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)

---

For questions about testing, please refer to the team documentation or create an issue in the repository.
