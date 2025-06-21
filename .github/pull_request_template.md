# Pull Request

## Description
Brief description of the changes made in this PR.

## Type of Change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Performance improvement
- [ ] Refactoring (no functional changes)

## Related Issues
Fixes #(issue number)

## Changes Made
- List the main changes
- Include any new files added
- Mention any dependencies added/removed

## Testing Checklist

### Local Testing
- [ ] All existing tests pass locally (`python run_tests.py`)
- [ ] New functionality is covered by tests
- [ ] Code coverage is maintained or improved
- [ ] Linting passes (Black, isort, flake8)
- [ ] No security issues detected (bandit, safety)

### Test Categories
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated if needed
- [ ] API validation tests added if new endpoints
- [ ] Performance impact assessed

### CI/CD
- [ ] GitHub Actions workflow passes
- [ ] No test failures in CI
- [ ] Coverage reports are acceptable
- [ ] Security scans pass

## API Changes
If this PR introduces API changes:
- [ ] OpenAPI/Swagger documentation updated
- [ ] Breaking changes are documented
- [ ] Migration guide provided (if needed)
- [ ] Backward compatibility maintained (or breaking change justified)

## Database Changes
If this PR includes database changes:
- [ ] Migration scripts created
- [ ] Migration tested locally
- [ ] Data migration strategy documented
- [ ] Rollback plan documented

## Deployment Considerations
- [ ] Environment variables updated (if needed)
- [ ] Configuration changes documented
- [ ] Docker configuration updated (if needed)
- [ ] Dependencies updated in requirements.txt

## Documentation
- [ ] Code is self-documenting with clear variable and function names
- [ ] Complex logic is commented
- [ ] API documentation updated
- [ ] README updated (if needed)
- [ ] Changelog updated

## Review Guidelines
- [ ] Code follows project style guidelines
- [ ] Functions are focused and do one thing well
- [ ] Error handling is appropriate
- [ ] Security best practices followed
- [ ] Performance implications considered

## Screenshots (if applicable)
Add screenshots to help explain your changes.

## Additional Notes
Any additional information that reviewers should know.

---

### For Reviewers
Please ensure:
- [ ] All tests pass in CI
- [ ] Code review completed
- [ ] Security review completed (if applicable)
- [ ] Performance review completed (if applicable)
- [ ] Documentation review completed
