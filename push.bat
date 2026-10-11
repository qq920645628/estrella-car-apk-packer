@echo off
REM Run this in CMD to invoke push.ps1 via PowerShell.
REM Set GH_TOKEN before running this batch, for example:
REM   set GH_TOKEN=ghp_your_token_here
REM   push.bat
if "%GH_TOKEN%"=="" (
    echo GH_TOKEN is not set.
    echo Usage: set GH_TOKEN=ghp_your_token ^&^& push.bat
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0push.ps1"
