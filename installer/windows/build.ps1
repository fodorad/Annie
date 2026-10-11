# Build Annie-Setup.exe locally, the same way CI does, to try it before pushing.
#
#   powershell -ExecutionPolicy Bypass -File installer\windows\build.ps1         # build only
#   powershell -ExecutionPolicy Bypass -File installer\windows\build.ps1 -Try    # build + install this checkout
#   (or: make installer / make installer-try)
#
# Output: installer\windows\Output\Annie-Setup.exe
#
# -Try also builds a wheel of this checkout and runs the installer with ANNIE_PACKAGE
# pointing at it, so setup installs *your* code instead of the newest release on PyPI
# (which may not have your changes yet). Later starts keep it: the launcher only updates
# when PyPI has a newer version than the one installed.
#
# -Fresh re-downloads the bundled uv, Python (with the Visual C++ runtime) and FFmpeg (otherwise they are reused from
# installer\windows\build after the first run).
#
# Needs Inno Setup 6 (free): winget install JRSoftware.InnoSetup

param(
    [switch]$Try,
    [switch]$Fresh
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$repo = Resolve-Path "$here\..\.."
$deps = Join-Path $here "build"

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "Inno Setup 6 is not installed. It is the free tool that compiles annie.iss into"
    Write-Host "Annie-Setup.exe. Install it once with:"
    Write-Host ""
    Write-Host "    winget install JRSoftware.InnoSetup"
    Write-Host ""
    Write-Host "then run this again."
    exit 1
}

# 1. Bundled binaries (checksum-verified), downloaded once and reused.
$complete = (Test-Path "$deps\uv.exe") -and (Test-Path "$deps\python\pythonw.exe") -and
            (Test-Path "$deps\python\msvcp140.dll") -and (Test-Path "$deps\ffmpeg\bin\ffmpeg.exe")
if ($Fresh -or -not $complete) {
    & "$here\fetch-deps.ps1" -OutDir $deps
}

# 2. Compile. The version only labels the installer; Annie itself comes from PyPI or -Try.
$version = (git -C $repo describe --tags --abbrev=0 2>$null) -replace '^v', ''
if (-not $version) { $version = "0.0.0" }
& $iscc /Qp "/DAppVersion=$version" "$here\annie.iss"
if ($LASTEXITCODE) { throw "Inno Setup failed with exit code $LASTEXITCODE" }
$setup = Join-Path $here "Output\Annie-Setup.exe"
Write-Host ""
Write-Host "Built $setup"

if (-not $Try) { exit 0 }

# 3. -Try: install this checkout. The wheel is built with the bundled Python, so no
#    uv-managed Python (and its directory junction) is needed on this machine either.
$dist = Join-Path $repo "dist"
Remove-Item "$dist\annie-*.whl" -ErrorAction SilentlyContinue
& "$deps\uv.exe" build --wheel --python "$deps\python\python.exe" --out-dir $dist $repo
if ($LASTEXITCODE) { throw "building the wheel failed with exit code $LASTEXITCODE" }
$wheel = (Get-ChildItem "$dist\annie-*.whl" | Select-Object -First 1).FullName
$env:ANNIE_PACKAGE = "annie[media,desktop] @ $(([System.Uri]$wheel).AbsoluteUri)"
Write-Host "Starting the installer with $wheel"
Start-Process $setup -Wait
