param(
    [string]$PythonLauncher = "py",
    [string]$CrowbarPath = "",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$specPath = Join-Path $PSScriptRoot "props_scaling_recompiler.spec"
$workPath = Join-Path $projectRoot "build\psr-release"
$rawDist = Join-Path $projectRoot "dist\psr-release-raw"
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "dist"))
$releasePath = [System.IO.Path]::GetFullPath(
    (Join-Path $distRoot "props_scaling_recompiler_v2")
)

$expectedPrefix = $distRoot.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
if (-not $releasePath.StartsWith(
    $expectedPrefix,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to clean release directory outside $distRoot"
}

if (-not $SkipTests) {
    & $PythonLauncher -m pytest -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $PythonLauncher -m PyInstaller `
    --noconfirm `
    --clean `
    --workpath $workPath `
    --distpath $rawDist `
    $specPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exeSource = Join-Path $rawDist "props_scaling_recompiler.exe"
if (-not (Test-Path -LiteralPath $exeSource -PathType Leaf)) {
    throw "PyInstaller did not produce $exeSource"
}

if (Test-Path -LiteralPath $releasePath) {
    Remove-Item -LiteralPath $releasePath -Recurse -Force
}
New-Item -ItemType Directory -Path $releasePath | Out-Null
Copy-Item -LiteralPath $exeSource -Destination $releasePath -Force

if ($CrowbarPath) {
    $crowbar = (Resolve-Path -LiteralPath $CrowbarPath).Path
    $thirdParty = Join-Path $releasePath "third-party"
    New-Item -ItemType Directory -Path $thirdParty -Force | Out-Null
    Copy-Item -LiteralPath $crowbar `
        -Destination (Join-Path $thirdParty "CrowbarCommandLineDecomp.exe") `
        -Force
}

$exe = Get-Item -LiteralPath (Join-Path $releasePath "props_scaling_recompiler.exe")
$targetBytes = 16MB
$hardLimitBytes = 64MB
$sizeMiB = $exe.Length / 1MB
Write-Host ("PSR executable: {0:N2} MiB ({1} bytes)" -f $sizeMiB, $exe.Length)

if ($exe.Length -gt $hardLimitBytes) {
    Write-Error "Release rejected: executable exceeds the 64 MiB hard limit."
    exit 64
}
if ($exe.Length -gt $targetBytes) {
    Write-Warning "Executable exceeds the preferred 16 MiB target. Inspect PyInstaller analysis before release."
}

& (Join-Path $releasePath "props_scaling_recompiler.exe") --version
if ($LASTEXITCODE -ne 0) {
    Write-Error "Frozen executable smoke test failed."
    exit $LASTEXITCODE
}

Write-Host "Release directory: $releasePath"
