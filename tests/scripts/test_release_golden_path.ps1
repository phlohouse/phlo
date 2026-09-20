$ErrorActionPreference = "Stop"

function Assert-Contains([string]$Value, [string]$Expected) {
    if (-not $Value.Contains($Expected)) {
        throw "Expected output to contain '$Expected', got: $Value"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$launcher = Join-Path $repoRoot "scripts/release_golden_path.ps1"
$pwsh = Join-Path $PSHOME "pwsh.exe"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("phlo-release-golden-path-" + [Guid]::NewGuid())
$whatIfProject = Join-Path $tempRoot "what-if-project"
$fakeBin = Join-Path $tempRoot "fake-bin"
$emptyBin = Join-Path $tempRoot "empty-bin"
$originalPath = $env:Path

New-Item -ItemType Directory -Path $fakeBin, $emptyBin | Out-Null

try {
    $env:Path = $originalPath
    $whatIfOutput = & $pwsh -NoProfile -File $launcher `
        -Partition "2025-02-03" -ProjectDir $whatIfProject -WhatIf 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "WhatIf invocation failed with exit code $LASTEXITCODE. Output: $whatIfOutput"
    }
    Assert-Contains $whatIfOutput "release golden path command:"
    Assert-Contains $whatIfOutput "release_golden_path.py"
    Assert-Contains $whatIfOutput "--partition"
    Assert-Contains $whatIfOutput "2025-02-03"
    Assert-Contains $whatIfOutput "--project-dir"
    Assert-Contains $whatIfOutput $whatIfProject

    $fakePython = Join-Path $fakeBin "py.cmd"
    Set-Content -Path $fakePython -Encoding ascii -Value @(
        "@echo off"
        'if "%1"=="-3.12" if "%2"=="--version" ('
        "  echo Python 3.12.9"
        "  exit /b 0"
        ")"
        "exit /b 23"
    )
    $env:Path = $fakeBin
    $preservedOutput = & $pwsh -NoProfile -File $launcher `
        -Partition "2025-02-03" -ProjectDir (Join-Path $tempRoot "exit-project") 2>&1 | Out-String
    if ($LASTEXITCODE -ne 23) {
        throw "Launcher did not preserve the Python exit code. Expected 23, got $LASTEXITCODE. Output: $preservedOutput"
    }
    Assert-Contains $preservedOutput "--partition"

    $env:Path = $emptyBin
    $missingOutput = & $pwsh -NoProfile -File $launcher -WhatIf 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        throw "Launcher accepted a missing Python 3.12 interpreter. Output: $missingOutput"
    }
    Assert-Contains $missingOutput "Python 3.12 was not found"
    Assert-Contains $missingOutput "py -3.12"
}
finally {
    $env:Path = $originalPath
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output "PowerShell release golden-path contract checks passed"
exit 0
