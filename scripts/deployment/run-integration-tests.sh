#!/bin/bash
# Staging Integration Tests Script
# Comprehensive testing of staging environment functionality

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Configuration
STAGING_URL="http://127.0.0.1:3002"
TEST_RESULTS=()

# Test API health endpoint
test_health_endpoint() {
    print_status "Testing health endpoint..."
    
    if curl -f "$STAGING_URL/api-health" >/dev/null 2>&1; then
        print_success "✅ Health endpoint working"
        TEST_RESULTS+=("PASS: Health endpoint")
        return 0
    else
        print_error "❌ Health endpoint failed"
        TEST_RESULTS+=("FAIL: Health endpoint")
        return 1
    fi
}

# Test user authentication
test_user_authentication() {
    local email="$1"
    local password="$2"
    local role="$3"
    
    print_status "Testing $role authentication..."
    
    # Check if credentials are provided
    if [ -z "$email" ] || [ -z "$password" ]; then
        print_warning "⚠️ Credentials not provided for $role, skipping test"
        TEST_RESULTS+=("SKIP: $role authentication - no credentials")
        return 0
    fi
    
    local response=$(curl -s -X POST "$STAGING_URL/auth" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"$email\",\"password\":\"$password\"}" \
        -w "%{http_code}")
    
    local http_code="${response: -3}"
    local body="${response%???}"
    
    if [ "$http_code" = "200" ]; then
        local token=$(echo "$body" | jq -r '.data.token // empty' 2>/dev/null)
        if [ -n "$token" ] && [ "$token" != "null" ]; then
            print_success "✅ $role authentication successful"
            TEST_RESULTS+=("PASS: $role authentication")
            echo "$token"
            return 0
        fi
    fi
    
    print_error "❌ $role authentication failed (HTTP: $http_code)"
    TEST_RESULTS+=("FAIL: $role authentication")
    return 1
}

# Test API endpoint with token
test_authenticated_endpoint() {
    local token="$1"
    local endpoint="$2" 
    local description="$3"
    
    if [ -z "$token" ]; then
        print_warning "⚠️ No token provided for $description, skipping test"
        TEST_RESULTS+=("SKIP: $description - no token")
        return 0
    fi
    
    print_status "Testing $description..."
    
    local response=$(curl -s -H "Authorization: Bearer $token" \
        "$STAGING_URL$endpoint" \
        -w "%{http_code}")
    
    local http_code="${response: -3}"
    
    if [ "$http_code" = "200" ]; then
        print_success "✅ $description working"
        TEST_RESULTS+=("PASS: $description")
        return 0
    else
        print_error "❌ $description failed (HTTP: $http_code)"
        TEST_RESULTS+=("FAIL: $description")
        return 1
    fi
}

# Test database content
test_database_content() {
    local token="$1"
    
    if [ -z "$token" ]; then
        print_warning "⚠️ No admin token provided, skipping database content tests"
        return 0
    fi
    
    print_status "Testing database content..."
    
    # Test script count
    local script_response=$(curl -s -H "Authorization: Bearer $token" "$STAGING_URL/script")
    local script_count=$(echo "$script_response" | jq '.data | length' 2>/dev/null || echo "0")
    print_status "📊 Scripts in staging database: $script_count"
    
    # Test user count
    local user_response=$(curl -s -H "Authorization: Bearer $token" "$STAGING_URL/user")
    local user_count=$(echo "$user_response" | jq '.data | length' 2>/dev/null || echo "0")
    print_status "👥 Users in staging database: $user_count"
    
    TEST_RESULTS+=("INFO: Scripts: $script_count, Users: $user_count")
}

# Display test summary
show_test_summary() {
    print_status "🧪 Integration Test Summary:"
    print_status "================================"
    
    local total_tests=0
    local passed_tests=0
    local failed_tests=0
    local skipped_tests=0
    
    for result in "${TEST_RESULTS[@]}"; do
        echo "  $result"
        total_tests=$((total_tests + 1))
        
        if [[ $result == PASS:* ]]; then
            passed_tests=$((passed_tests + 1))
        elif [[ $result == FAIL:* ]]; then
            failed_tests=$((failed_tests + 1))
        elif [[ $result == SKIP:* ]]; then
            skipped_tests=$((skipped_tests + 1))
        fi
    done
    
    print_status "================================"
    print_status "Total: $total_tests | Passed: $passed_tests | Failed: $failed_tests | Skipped: $skipped_tests"
    
    if [ $failed_tests -gt 0 ]; then
        print_error "❌ Some tests failed"
        return 1
    else
        print_success "✅ All tests passed"
        return 0
    fi
}

# Main execution
main() {
    print_status "🧪 Running staging integration tests..."
    
    # Test basic health endpoint
    test_health_endpoint
    
    # Test user authentication and get tokens
    local superadmin_token=""
    local admin_token=""
    local user_token=""
    
    if superadmin_token=$(test_user_authentication "$TEST_SUPERADMIN_EMAIL" "$TEST_SUPERADMIN_PASSWORD" "superadmin"); then
        test_authenticated_endpoint "$superadmin_token" "/user" "superadmin user list access"
        test_authenticated_endpoint "$superadmin_token" "/script" "superadmin script list access"
    fi
    
    if admin_token=$(test_user_authentication "$TEST_ADMIN_EMAIL" "$TEST_ADMIN_PASSWORD" "admin"); then
        test_authenticated_endpoint "$admin_token" "/user" "admin user list access"
        test_authenticated_endpoint "$admin_token" "/script" "admin script list access"
    fi
    
    if user_token=$(test_user_authentication "$TEST_USER_EMAIL" "$TEST_USER_PASSWORD" "user"); then
        test_authenticated_endpoint "$user_token" "/user/me" "user profile access"
    fi
    
    # Test database content (use superadmin token if available, fallback to admin)
    local admin_token_for_db="${superadmin_token:-$admin_token}"
    test_database_content "$admin_token_for_db"
    
    # Show summary and exit with appropriate code
    show_test_summary
}

# Run main function
main "$@"
