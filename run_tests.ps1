# PowerShell script to run tests in Docker environment
# This ensures the database and redis services are running before tests
#
# Usage:
#   .\run_tests.ps1                                                          # Run all tests
#   .\run_tests.ps1 tests/test_integration.py                               # Run all tests in a file
#   .\run_tests.ps1 tests/test_integration.py::TestAPIIntegration            # Run all tests in a class
#   .\run_tests.ps1 tests/test_integration.py::TestAPIIntegration::test_admin_management_workflow  # Run specific test
#   .\run_tests.ps1 -v --no-cov tests/test_integration.py                   # Run with pytest options
#   .\run_tests.ps1 -x                                                      # Stop on first failure
#   .\run_tests.ps1 -ResetDb                                                # Reset test database before running

param(
    [switch]$ResetDb,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

# Set error action preference to stop on errors
$ErrorActionPreference = "Stop"

# Parse arguments for -x flag and build pytest arguments
$StopOnFail = ""
$FilteredArgs = @()

foreach ($arg in $PytestArgs) {
    if ($arg -eq "-x") {
        $StopOnFail = "--exitfirst"
        $FilteredArgs += $arg
    } else {
        $FilteredArgs += $arg
    }
}

try {
    Write-Host "Starting necessary services..." -ForegroundColor Green
    docker compose -f docker-compose.develop.yml up -d postgres redis
    
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start services"
    }

    Write-Host "Waiting for services to be ready..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5

    # Get database configuration from environment (with defaults)
    $DbUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "trendsearth_develop" }
    $DbPassword = if ($env:POSTGRES_PASSWORD) { $env:POSTGRES_PASSWORD } else { "postgres" }
    $DbName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "trendsearth_develop_db" }

    Write-Host "Creating test database if it doesn't exist..." -ForegroundColor Yellow
    $createDbCmd = "docker compose -f docker-compose.develop.yml exec -T postgres env PGPASSWORD=`"$DbPassword`" psql -U `"$DbUser`" -d `"$DbName`" -c `"CREATE DATABASE gef_test;`""
    
    try {
        Invoke-Expression $createDbCmd 2>$null
        Write-Host "Test database created or already exists" -ForegroundColor Green
    } catch {
        Write-Host "Test database already exists" -ForegroundColor Yellow
    }

    # Optionally drop and recreate the test database if -ResetDb flag is set
    if ($ResetDb) {
        Write-Host "Dropping and recreating test database (-ResetDb flag)..." -ForegroundColor Yellow
        
        $dropDbCmd = "docker compose -f docker-compose.develop.yml exec -T postgres env PGPASSWORD=`"$DbPassword`" psql -U `"$DbUser`" -d `"$DbName`" -c `"DROP DATABASE IF EXISTS gef_test;`""
        $createDbCmd = "docker compose -f docker-compose.develop.yml exec -T postgres env PGPASSWORD=`"$DbPassword`" psql -U `"$DbUser`" -d `"$DbName`" -c `"CREATE DATABASE gef_test;`""
        
        Invoke-Expression $dropDbCmd
        Invoke-Expression $createDbCmd
        
        Write-Host "Test database reset complete" -ForegroundColor Green
    }

    Write-Host "Running tests..." -ForegroundColor Green

    if ($FilteredArgs.Count -eq 0) {
        Write-Host "No arguments provided, running all tests..." -ForegroundColor Cyan
        docker compose -f docker-compose.develop.yml run --rm test
    } else {
        $argsString = $FilteredArgs -join " "
        Write-Host "Running with arguments: $argsString" -ForegroundColor Cyan
        
        # Build the complete command
        $testCmd = "docker compose -f docker-compose.develop.yml run --rm test python -m pytest $argsString"
        if ($StopOnFail) {
            $testCmd += " $StopOnFail"
        }
        
        Invoke-Expression $testCmd
    }

    $testExitCode = $LASTEXITCODE

} catch {
    Write-Host "Error occurred: $_" -ForegroundColor Red
    $testExitCode = 1
} finally {
    Write-Host "Stopping services..." -ForegroundColor Yellow
    docker compose -f docker-compose.develop.yml down
    
    if ($testExitCode -eq 0) {
        Write-Host "Tests completed successfully!" -ForegroundColor Green
    } else {
        Write-Host "Tests failed with exit code: $testExitCode" -ForegroundColor Red
    }
}

exit $testExitCode
