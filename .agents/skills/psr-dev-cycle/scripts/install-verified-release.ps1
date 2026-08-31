param(
    [string]$PythonLauncher = "py",
    [string]$CrowbarPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
$buildScript = Join-Path $projectRoot "packaging\build_release.ps1"
$sourceExe = Join-Path $projectRoot "dist\props_scaling_recompiler_v2\props_scaling_recompiler.exe"
$installedExe = "C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin\props_scaling_recompiler.exe"

if (-not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
    throw "Release script not found: $buildScript"
}

Push-Location $projectRoot
try {
    if ([string]::IsNullOrWhiteSpace($CrowbarPath)) {
        & $buildScript -PythonLauncher $PythonLauncher
    }
    else {
        & $buildScript -PythonLauncher $PythonLauncher -CrowbarPath $CrowbarPath
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Release build failed with exit code $LASTEXITCODE"
    }

    if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
        throw "Verified release executable not found: $sourceExe"
    }

    $hadFrozenExe = Test-Path Env:PSR_FROZEN_EXE
    $previousFrozenExe = $env:PSR_FROZEN_EXE
    try {
        $env:PSR_FROZEN_EXE = $sourceExe
        & $PythonLauncher -m pytest -q tests/test_frozen_executable.py
        if ($LASTEXITCODE -ne 0) {
            throw "Frozen executable regression failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        if ($hadFrozenExe) {
            $env:PSR_FROZEN_EXE = $previousFrozenExe
        }
        else {
            Remove-Item Env:PSR_FROZEN_EXE -ErrorAction SilentlyContinue
        }
    }

    $installedDirectory = Split-Path -Parent $installedExe
    if (-not (Test-Path -LiteralPath $installedDirectory -PathType Container)) {
        throw "SDK bin directory not found: $installedDirectory"
    }

    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceExe).Hash
    Copy-Item -LiteralPath $sourceExe -Destination $installedExe -Force
    $installedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedExe).Hash

    if ($sourceHash -ne $installedHash) {
        throw "Installed executable hash mismatch: source=$sourceHash installed=$installedHash"
    }

    Write-Host "Release source: $sourceExe"
    Write-Host "Installed executable: $installedExe"
    Write-Host "SHA-256: $sourceHash"
}
finally {
    Pop-Location
}
