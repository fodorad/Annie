@echo off
rem Annie launcher, installed by Annie-Setup.exe (see annie.iss).
rem
rem On every start it installs Annie (first run) or updates it to the newest release on
rem PyPI, then starts it. Everything uv downloads (Python, Annie and its dependencies)
rem stays inside this folder, so uninstalling removes all of it. The user's own work
rem lives in %USERPROFILE%\Annie and survives updates and reinstalls.
rem
rem Flow control uses goto labels rather than ( ... ) blocks on purpose: a user folder
rem containing ")" would otherwise break the blocks when %APP% is expanded.
setlocal
title Annie

rem Bump together with REQUIRED_LAUNCHER_VERSION in annie/core/runtime.py when this file
rem or the bundled FFmpeg changes in a way Annie relies on.
set "ANNIE_LAUNCHER_VERSION=1"

set "APP=%~dp0"
if "%APP:~-1%"=="\" set "APP=%APP:~0,-1%"
set "UV=%APP%\uv.exe"

rem uv: keep the tool environment and Python inside this folder and never touch PATH,
rem the registry or any system/user Python. No cache: nothing is left behind in %TEMP%.
set "UV_TOOL_DIR=%APP%\tools"
set "UV_TOOL_BIN_DIR=%APP%\bin"
set "UV_PYTHON_INSTALL_DIR=%APP%\python"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_NO_CACHE=1"
set "UV_HTTP_TIMEOUT=30"

rem FFmpeg: the bundled shared build. torchcodec loads its DLLs, Annie runs ffmpeg/ffprobe.
set "PATH=%APP%\ffmpeg\bin;%PATH%"
set "ANNIE_FFMPEG_LIB_DIR=%APP%\ffmpeg\bin"

rem Annie: user data in a visible folder (not hidden AppData, not OneDrive-synced Documents).
if not defined ANNIE_HOME set "ANNIE_HOME=%USERPROFILE%\Annie"
if not defined ANNIE_CONFIG_DIR set "ANNIE_CONFIG_DIR=%ANNIE_HOME%\configs"
if not defined ANNIE_OPEN_BROWSER set "ANNIE_OPEN_BROWSER=1"
rem ANNIE_PACKAGE lets CI install a locally built wheel instead of the PyPI release.
if not defined ANNIE_PACKAGE set "ANNIE_PACKAGE=annie[media]"

cd /d "%APP%"

if not exist "%APP%\bin\annie.exe" goto install

rem Already running (e.g. a second double-click)? Don't update files that are in use;
rem Annie itself notices the running instance and just opens the browser.
tasklist /FI "IMAGENAME eq annie.exe" 2>nul | find /I "annie.exe" >nul
if not errorlevel 1 goto start

echo Checking for Annie updates...
"%UV%" tool upgrade annie --quiet
if errorlevel 1 echo Could not update right now. Starting the installed version.
goto start

:install
echo Preparing Annie for first use.
echo This one-time download is several hundred MB and takes a few minutes...
echo.
"%UV%" tool install --python 3.12 "%ANNIE_PACKAGE%"
if errorlevel 1 goto install_failed

:start
echo.
echo Starting Annie. Your browser opens in a moment.
echo Keep this window open while you work. Close it to stop Annie.
echo.
"%APP%\bin\annie.exe"
if errorlevel 1 pause
exit /b

:install_failed
echo.
echo Annie could not be installed. Check the internet connection, then start Annie again.
pause
exit /b 1
