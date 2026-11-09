@echo off
REM watch data dir and auto-push to GitHub after each clean
cd /d "%~dp0"
start "" /min pythonw sync_clean_to_github.py --watch
