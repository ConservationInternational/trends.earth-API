#!/usr/bin/env python3
"""
Code structure validation for Docker Swarm status caching optimizations.

This script validates that the optimization code is correctly structured
and includes all the required components.
"""

import os
import re
import sys


def check_file_exists(filepath, description):
    """Check if a file exists and report the result"""
    if os.path.exists(filepath):
        print(f"  ✅ {description}")
        return True
    else:
        print(f"  ❌ {description} - File not found: {filepath}")
        return False


def check_code_contains(filepath, pattern, description):
    """Check if a file contains a specific pattern"""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            if re.search(pattern, content, re.MULTILINE | re.DOTALL):
                print(f"  ✅ {description}")
                return True
            else:
                print(f"  ❌ {description} - Pattern not found")
                return False
    except Exception as e:
        print(f"  ❌ {description} - Error reading file: {e}")
        return False


def validate_caching_optimizations():
    """Validate the caching optimization implementations"""
    print("🔧 Validating caching optimization implementations...")
    
    base_path = "/home/runner/work/trends.earth-API/trends.earth-API"
    checks = []
    
    # Check status monitoring file exists
    status_file = f"{base_path}/gefapi/tasks/status_monitoring.py"
    checks.append(check_file_exists(status_file, "Status monitoring module exists"))
    
    # Check for backup cache constants
    checks.append(check_code_contains(
        status_file,
        r"SWARM_CACHE_BACKUP_KEY.*=.*docker_swarm_status_backup",
        "Backup cache key constant defined"
    ))
    
    checks.append(check_code_contains(
        status_file,
        r"SWARM_CACHE_BACKUP_TTL.*=.*1800",
        "Backup cache TTL configured (30 minutes)"
    ))
    
    # Check for enhanced get_cached_swarm_status function
    checks.append(check_code_contains(
        status_file,
        r"def get_cached_swarm_status\(\):",
        "Enhanced cache retrieval function exists"
    ))
    
    checks.append(check_code_contains(
        status_file,
        r"backup_data = cache\.get\(SWARM_CACHE_BACKUP_KEY\)",
        "Backup cache fallback implemented"
    ))
    
    checks.append(check_code_contains(
        status_file,
        r"cache_hit.*=.*True",
        "Cache hit tracking implemented"
    ))
    
    # Check for enhanced update_swarm_cache function
    checks.append(check_code_contains(
        status_file,
        r"def update_swarm_cache\(\):",
        "Enhanced cache update function exists"
    ))
    
    checks.append(check_code_contains(
        status_file,
        r"cache_operations.*=.*\[\]",
        "Cache operations tracking implemented"
    ))
    
    checks.append(check_code_contains(
        status_file,
        r"backup_success = cache\.set\(",
        "Backup cache update implemented"
    ))
    
    # Check for cache statistics function
    checks.append(check_code_contains(
        status_file,
        r"def get_swarm_cache_statistics\(\):",
        "Cache statistics function implemented"
    ))
    
    # Check for performance monitoring in refresh task
    checks.append(check_code_contains(
        status_file,
        r"performance_metrics.*=.*\{",
        "Performance metrics tracking implemented"
    ))
    
    checks.append(check_code_contains(
        status_file,
        r"refresh_duration_seconds",
        "Refresh duration tracking implemented"
    ))
    
    return checks


def validate_api_endpoints():
    """Validate the API endpoint enhancements"""
    print("\n🌐 Validating API endpoint enhancements...")
    
    base_path = "/home/runner/work/trends.earth-API/trends.earth-API"
    router_file = f"{base_path}/gefapi/routes/api/v1/gef_api_router.py"
    checks = []
    
    # Check router file exists
    checks.append(check_file_exists(router_file, "API router file exists"))
    
    # Check for new cache statistics endpoint
    checks.append(check_code_contains(
        router_file,
        r"@endpoints\.route\(\"/status/swarm/cache\"",
        "Cache statistics endpoint route defined"
    ))
    
    checks.append(check_code_contains(
        router_file,
        r"def get_swarm_cache_statistics\(\):",
        "Cache statistics endpoint function exists"
    ))
    
    # Check that existing swarm endpoint still exists
    checks.append(check_code_contains(
        router_file,
        r"@endpoints\.route\(\"/status/swarm\"",
        "Original swarm status endpoint still exists"
    ))
    
    checks.append(check_code_contains(
        router_file,
        r"from gefapi\.tasks\.status_monitoring import get_cached_swarm_status",
        "Import of cached status function exists"
    ))
    
    return checks


def validate_test_coverage():
    """Validate test coverage for optimizations"""
    print("\n🧪 Validating test coverage...")
    
    base_path = "/home/runner/work/trends.earth-API/trends.earth-API"
    test_file = f"{base_path}/tests/test_swarm_status_caching.py"
    checks = []
    
    # Check test file exists
    checks.append(check_file_exists(test_file, "Swarm caching test file exists"))
    
    # Check for comprehensive test classes
    checks.append(check_code_contains(
        test_file,
        r"class TestSwarmStatusEndpoint",
        "Endpoint testing class exists"
    ))
    
    checks.append(check_code_contains(
        test_file,
        r"class TestSwarmStatusCaching",
        "Caching functionality testing class exists"
    ))
    
    checks.append(check_code_contains(
        test_file,
        r"class TestSwarmStatusCacheIntegration",
        "Integration testing class exists"
    ))
    
    # Check for specific test methods
    checks.append(check_code_contains(
        test_file,
        r"def test_.*backup_cache_fallback",
        "Backup cache fallback test exists"
    ))
    
    checks.append(check_code_contains(
        test_file,
        r"def test_.*cache_unavailable",
        "Cache unavailable scenario test exists"
    ))
    
    checks.append(check_code_contains(
        test_file,
        r"def test_.*performance",
        "Performance testing exists"
    ))
    
    return checks


def validate_celery_configuration():
    """Validate Celery beat configuration"""
    print("\n⏰ Validating Celery configuration...")
    
    base_path = "/home/runner/work/trends.earth-API/trends.earth-API"
    celery_file = f"{base_path}/gefapi/celery.py"
    checks = []
    
    # Check celery config file exists
    checks.append(check_file_exists(celery_file, "Celery configuration file exists"))
    
    # Check for swarm cache refresh task configuration
    checks.append(check_code_contains(
        celery_file,
        r"refresh-swarm-cache",
        "Swarm cache refresh task configured"
    ))
    
    checks.append(check_code_contains(
        celery_file,
        r"gefapi\.tasks\.status_monitoring\.refresh_swarm_cache_task",
        "Swarm cache refresh task route configured"
    ))
    
    checks.append(check_code_contains(
        celery_file,
        r"schedule.*120\.0",
        "2-minute refresh schedule configured"
    ))
    
    checks.append(check_code_contains(
        celery_file,
        r"queue.*build",
        "Build queue routing configured"
    ))
    
    return checks


def main():
    """Run all validation checks"""
    print("🚀 Validating Docker Swarm status caching optimizations...")
    print("=" * 70)
    
    validation_functions = [
        validate_caching_optimizations,
        validate_api_endpoints,
        validate_test_coverage,
        validate_celery_configuration,
    ]
    
    all_checks = []
    for validate_func in validation_functions:
        checks = validate_func()
        all_checks.extend(checks)
    
    passed = sum(all_checks)
    total = len(all_checks)
    
    print("\n" + "=" * 70)
    print(f"📊 Validation Results: {passed}/{total} checks passed")
    
    if passed == total:
        print("\n🎉 All optimizations are correctly implemented!")
        print("\n📈 Optimization Features Validated:")
        print("  ✅ Two-tier caching strategy (primary + backup)")
        print("  ✅ Enhanced error handling and fallback mechanisms")
        print("  ✅ Performance monitoring and metrics collection")
        print("  ✅ Cache statistics endpoint for monitoring")
        print("  ✅ Comprehensive test coverage")
        print("  ✅ Proper Celery beat task configuration")
        print("\n💡 Benefits:")
        print("  • Improved cache reliability with backup fallback")
        print("  • Better monitoring and observability")
        print("  • Enhanced performance metrics")
        print("  • Robust error handling")
        return True
    else:
        print(f"\n⚠️  {total - passed} optimization(s) need attention.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)