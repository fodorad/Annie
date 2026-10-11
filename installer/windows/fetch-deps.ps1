# Download and verify the third-party binaries bundled into Annie-Setup.exe.
#
# Used by the installer build and by the Windows CI job, so both run the exact same,
# checksum-pinned uv, Python and FFmpeg. Output layout (consumed by annie.iss):
#
#   <OutDir>\uv.exe
#   <OutDir>\python\{python.exe, pythonw.exe, Lib\, DLLs\, tcl\, ...}
#   <OutDir>\ffmpeg\bin\{ffmpeg.exe, ffprobe.exe, *.dll}
#   <OutDir>\ffmpeg\LICENSE.txt
#   <OutDir>\ffmpeg\SOURCE.txt
#
# FFmpeg is the GPL *shared* build: shared, because torchcodec links FFmpeg's DLLs;
# GPL, because Annie's render/convert pipelines encode with libx264, which LGPL builds
# lack. Redistributing it means shipping its licence and pointing at its source, which
# SOURCE.txt does.
#
# Python is bundled rather than downloaded by uv on first start: uv's managed Python
# links its minor-version folder to the patch folder with a directory junction, and
# recent Windows 11 builds refuse to traverse junctions a standard user created
# ("untrusted mount point", os error 448). A plain folder has no junction to refuse.
# python-build-standalone is the same CPython uv would have downloaded; the stripped
# install_only build keeps tkinter, which the launcher's progress window uses.
#
# To upgrade any binary, change the URL and its SHA-256 together (torchcodec 0.17
# supports FFmpeg 4-9) and bump LAUNCHER_VERSION in launcher.pyw if Annie needs it.

param(
    [Parameter(Mandatory = $true)][string]$OutDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is ~10x slower with the bar

$UvVersion = "0.13.0"
$UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
$UvSha256 = "088962f9e7b7bd9ea740c04c650b2a21c8928c345bd99ac24350dc924dba656c"

$PythonRelease = "20261009"
$PythonName = "cpython-3.12.15+$PythonRelease-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
$PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/$PythonRelease/$([uri]::EscapeDataString($PythonName))"
$PythonSha256 = "11ed9f8a6ac64fb1b5a55e40e305a6744a3f77a70863c8f8197a6e4349abc64a"

# A month-end BtbN auto-build: those are kept long-term, unlike the rolling "latest".
$FfmpegRelease = "autobuild-2026-09-30-13-08"
$FfmpegName = "ffmpeg-n8.1.3-9-g29e619e767-win64-gpl-shared-8.1"
$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/$FfmpegRelease/$FfmpegName.zip"
$FfmpegSha256 = "dfe81c3aa0a546ee81980b1b824486dd0fe0a1d452f00cb55135379a74be7bcf"

function Get-Verified([string]$Url, [string]$Sha256, [string]$Dest) {
    Write-Host "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Dest
    $actual = (Get-FileHash -Algorithm SHA256 $Dest).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) {
        throw "Checksum mismatch for $Url`n  expected $Sha256`n  actual   $actual"
    }
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("annie-deps-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $work, $OutDir | Out-Null
try {
    # uv: only uv.exe is needed (uvx/uvw are not used by the launcher).
    Get-Verified $UvUrl $UvSha256 "$work\uv.zip"
    Expand-Archive "$work\uv.zip" "$work\uv"
    Copy-Item (Get-ChildItem "$work\uv" -Recurse -Filter uv.exe | Select-Object -First 1).FullName $OutDir

    # Python: the archive holds a single python\ folder, which is the layout annie.iss wants.
    Get-Verified $PythonUrl $PythonSha256 "$work\python.tar.gz"
    $py = Join-Path $OutDir "python"
    if (Test-Path $py) { Remove-Item -Recurse -Force $py }
    tar -xzf "$work\python.tar.gz" -C $OutDir
    if ($LASTEXITCODE) { throw "tar failed with $LASTEXITCODE" }
    if (-not (Test-Path "$py\pythonw.exe")) { throw "unexpected Python archive layout" }

    # FFmpeg: keep bin\ (executables + DLLs) and the licence; drop headers, import libs, ffplay.
    Get-Verified $FfmpegUrl $FfmpegSha256 "$work\ffmpeg.zip"
    Expand-Archive "$work\ffmpeg.zip" "$work\ffmpeg"
    $src = Join-Path "$work\ffmpeg" $FfmpegName
    $dst = Join-Path $OutDir "ffmpeg"
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    New-Item -ItemType Directory -Force -Path "$dst\bin" | Out-Null
    Copy-Item "$src\bin\*" "$dst\bin"
    Remove-Item "$dst\bin\ffplay.exe" -ErrorAction SilentlyContinue
    Copy-Item "$src\LICENSE.txt" "$dst\LICENSE.txt"
    Set-Content -Encoding ascii -Path "$dst\SOURCE.txt" -Value @(
        "FFmpeg $FfmpegName (GPL, shared build) by BtbN/FFmpeg-Builds.",
        "Binaries: $FfmpegUrl",
        "Build scripts and the sources of every bundled component:",
        "  https://github.com/BtbN/FFmpeg-Builds (release $FfmpegRelease)",
        "FFmpeg source: https://ffmpeg.org/download.html"
    )
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
Write-Host "Bundled binaries ready in $OutDir"
