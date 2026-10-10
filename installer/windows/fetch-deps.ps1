# Download and verify the third-party binaries bundled into Annie-Setup.exe.
#
# Used by the installer build and by the Windows CI job, so both run the exact same,
# checksum-pinned uv and FFmpeg. Output layout (consumed by annie.iss):
#
#   <OutDir>\uv.exe
#   <OutDir>\ffmpeg\bin\{ffmpeg.exe, ffprobe.exe, *.dll}
#   <OutDir>\ffmpeg\LICENSE.txt
#   <OutDir>\ffmpeg\SOURCE.txt
#
# FFmpeg is the GPL *shared* build: shared, because torchcodec links FFmpeg's DLLs;
# GPL, because Annie's render/convert pipelines encode with libx264, which LGPL builds
# lack. Redistributing it means shipping its licence and pointing at its source, which
# SOURCE.txt does. To upgrade either binary, change the URL and its SHA-256 together
# (torchcodec 0.16 supports FFmpeg 4-8) and bump ANNIE_LAUNCHER_VERSION if Annie needs it.

param(
    [Parameter(Mandatory = $true)][string]$OutDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is ~10x slower with the bar

$UvVersion = "0.13.0"
$UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
$UvSha256 = "088962f9e7b7bd9ea740c04c650b2a21c8928c345bd99ac24350dc924dba656c"

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
