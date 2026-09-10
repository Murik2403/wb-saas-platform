@echo off
chcp 65001 >nul
"%~dp0tenant-app\.venv\Scripts\python.exe" -X utf8 "%~dp0scripts\connect_wb_local.py"
pause
