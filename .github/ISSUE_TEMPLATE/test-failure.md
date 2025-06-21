---
name: Test Failure Report
about: Report a failing test case
title: "[TEST] "
labels: ["bug", "tests"]
assignees: []
---

## Test Failure Description
A clear and concise description of which test is failing and why.

## Test Details
- **Test File**: (e.g., `tests/test_users.py`)
- **Test Function**: (e.g., `test_create_user_success`)
- **Test Category**: (unit/integration/performance/validation)

## Error Output
```
Paste the error output here
```

## Environment
- **Python Version**: 
- **OS**: 
- **Dependencies**: (if related to dependency versions)

## Steps to Reproduce
1. Run the test with: `pytest tests/...`
2. See error

## Expected Behavior
A clear and concise description of what you expected to happen.

## Additional Context
Add any other context about the problem here, such as:
- Recent changes that might have caused the issue
- Whether this affects other tests
- Potential impact on CI/CD pipeline

## Screenshots
If applicable, add screenshots of test output or error messages.

## Checklist
- [ ] I have run the tests locally
- [ ] I have checked the latest version of the code
- [ ] I have reviewed recent commits that might affect this test
- [ ] I have checked if this is a known issue
