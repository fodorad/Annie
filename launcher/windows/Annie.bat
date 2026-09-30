@echo off
rem Annie launcher — double-click this file to start Annie.
rem It runs Annie.ps1 with an execution-policy bypass so no Windows setting has to change.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Annie.ps1"
