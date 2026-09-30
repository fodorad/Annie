# Annie — Windows launcher
#
# What this does, in order, every time it runs:
#   1. Checks that Docker Desktop is installed and running.
#   2. On the first run (or if the saved video folder is gone), asks the user to pick the
#      folder on their external drive that holds the video recordings, and remembers it.
#   3. Writes the .env that docker-compose reads, so the chosen folder is mounted into Annie.
#   4. Pulls the latest published Annie image (this is the auto-update: releasing a new
#      version means users get it on their next launch, with no action on their part).
#   5. Starts Annie, waits for it to be ready, and opens the web browser to it.
#   6. Stays open; closing this window stops Annie.
#
# It is deliberately chatty and written for a non-technical user watching the window.

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

$ConfigFile = Join-Path $Here "annie-config.txt"
$EnvFile    = Join-Path $Here ".env"
$Url        = "http://127.0.0.1:8080"

function Show-Info($msg)  { Write-Host "  $msg" -ForegroundColor Cyan }
function Show-Ok($msg)    { Write-Host "  $msg" -ForegroundColor Green }
function Show-Warn($msg)  { Write-Host "  $msg" -ForegroundColor Yellow }
function Show-Error($msg) { Write-Host "  $msg" -ForegroundColor Red }

function Pause-AndExit($code) {
    Write-Host ""
    Read-Host "Press Enter to close this window"
    exit $code
}

# A native Windows folder-picker dialog. Returns the chosen path, or $null if cancelled.
function Pick-Folder($description) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = $description
    $dialog.ShowNewFolderButton = $false
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        return $dialog.SelectedPath
    }
    return $null
}

Write-Host ""
Write-Host "  ============================================" -ForegroundColor White
Write-Host "   Annie — video annotation" -ForegroundColor White
Write-Host "  ============================================" -ForegroundColor White
Write-Host ""

# ── 1. Docker Desktop present and running? ────────────────────────────────────
Show-Info "Checking Docker Desktop..."
$dockerOk = $false
try {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
} catch { $dockerOk = $false }

if (-not $dockerOk) {
    Show-Error "Docker Desktop is not running (or not installed)."
    Show-Warn  "Annie needs Docker Desktop. It is a free, one-time install."
    Show-Warn  "1) Install it from:  https://www.docker.com/products/docker-desktop"
    Show-Warn  "2) Start Docker Desktop and wait until it says 'Running'."
    Show-Warn  "3) Run Annie again."
    try { Start-Process "https://www.docker.com/products/docker-desktop" } catch {}
    Pause-AndExit 1
}
Show-Ok "Docker Desktop is running."

# ── 2. Where are the videos? (first run, or the saved folder disappeared) ─────
$videoDir = $null
if (Test-Path $ConfigFile) {
    $videoDir = (Get-Content $ConfigFile -Raw).Trim()
}

if (-not $videoDir -or -not (Test-Path $videoDir)) {
    if ($videoDir) {
        Show-Warn "The saved video folder is not available:"
        Show-Warn "  $videoDir"
        Show-Warn "(Your external drive may be unplugged, or its drive letter changed.)"
        Show-Info "Please select it again."
    } else {
        Show-Info "First run: please choose the folder that contains your video recordings."
    }
    $videoDir = Pick-Folder "Select the folder with your video recordings (on your external drive)"
    if (-not $videoDir) {
        Show-Error "No folder selected. Annie cannot start without your videos."
        Pause-AndExit 1
    }
    Set-Content -Path $ConfigFile -Value $videoDir -Encoding UTF8
}
Show-Ok "Videos: $videoDir"

# ── 3. Write the .env docker-compose reads ────────────────────────────────────
# annie-home (Annie's own state: session DBs, saved configs, exports) lives next to this
# launcher so the user can find their exported files in Explorer.
$annieHome = Join-Path $Here "annie-home"
$envLines = @(
    "ANNIE_PORT=8080",
    "ANNIE_HOME_HOST=$annieHome",
    "ANNIE_VIDEO_DIR=$videoDir"
)
Set-Content -Path $EnvFile -Value $envLines -Encoding UTF8

# ── 4. Pull the latest image (auto-update) ────────────────────────────────────
Show-Info "Checking for the latest version of Annie..."
docker compose pull 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Show-Ok "Up to date."
} else {
    Show-Warn "Could not check for updates (offline?). Starting the installed version."
}

# ── 5. Start and open the browser ─────────────────────────────────────────────
Show-Info "Starting Annie..."
docker compose up -d 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Show-Error "Annie failed to start. Please make sure Docker Desktop is running and try again."
    Pause-AndExit 1
}

Show-Info "Waiting for Annie to be ready..."
$ready = $false
foreach ($i in 1..60) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}

if ($ready) {
    Show-Ok "Annie is ready."
    Start-Process $Url
} else {
    Show-Warn "Annie is taking longer than usual. Opening the browser anyway;"
    Show-Warn "if the page is blank, wait a few seconds and refresh."
    Start-Process $Url
}

Write-Host ""
Show-Ok  "Annie is running at $Url"
Show-Info "You can keep working in your browser."
Write-Host ""
Show-Warn "When you are finished, come back here and press Enter to stop Annie."
Read-Host | Out-Null

Show-Info "Stopping Annie..."
docker compose down 2>&1 | Out-Null
Show-Ok "Stopped. You can close this window."
Start-Sleep -Seconds 2
