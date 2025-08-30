@echo off
REM Batch file wrapper for PowerShell test runner
REM This calls the PowerShell script with all arguments passed through

powershell.exe -ExecutionPolicy Bypass -File "%~dp0run_tests.ps1" %*
