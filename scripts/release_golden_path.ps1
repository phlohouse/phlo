[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Partition = "2025-01-15",
    [string]$ProjectDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Find-Python312 {
    $candidates = @(
        @{ Name = "py"; PrefixArguments = @("-3.12") },
        @{ Name = "python"; PrefixArguments = @() },
        @{ Name = "python3"; PrefixArguments = @() }
    )
    $checked = [System.Collections.Generic.List[string]]::new()

    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Name -CommandType Application -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }

        $prefixArguments = [string[]]$candidate.PrefixArguments
        $version = & $command.Source @prefixArguments --version 2>$null
        if ($LASTEXITCODE -eq 0 -and ($version -match "Python 3\.12\.")) {
            return @{
                Executable = $command.Source
                PrefixArguments = $prefixArguments
            }
        }
        $checked.Add($candidate.Name)
    }

    $checkedText = if ($checked.Count -eq 0) { "none" } else { $checked -join ", " }
    throw "Python 3.12 was not found. Install Python 3.12 and make py -3.12 or python available on PATH. Checked: $checkedText."
}

function Quote-CommandPart([string]$Part) {
    return '"' + ($Part -replace '"', '\"') + '"'
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scriptPath = Join-Path $PSScriptRoot "release_golden_path.py"
$python = Find-Python312
$arguments = [System.Collections.Generic.List[string]]::new()
$arguments.AddRange([string[]]$python.PrefixArguments)
$arguments.Add($scriptPath)
$arguments.Add("--repo-root")
$arguments.Add($repoRoot)
$arguments.Add("--partition")
$arguments.Add($Partition)
if ($ProjectDir) {
    $arguments.Add("--project-dir")
    $arguments.Add([System.IO.Path]::GetFullPath($ProjectDir))
}

$displayParts = @((Quote-CommandPart $python.Executable)) + @(
    $arguments | ForEach-Object { Quote-CommandPart $_ }
)
$displayCommand = $displayParts -join " "
Write-Output "release golden path command: $displayCommand"

if ($PSCmdlet.ShouldProcess($displayCommand, "Run release artifact golden path")) {
    Push-Location $repoRoot
    try {
        & $python.Executable @arguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    exit $exitCode
}
