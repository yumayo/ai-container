#Requires -Version 5.1
<#
.SYNOPSIS
Prepare WSL if necessary and start AI Container using its bundled Docker engine.
.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-wsl.ps1 -Workspace C:\src\my-project -Tool codex
#>
[CmdletBinding()]
param(
    [string] $Workspace = (Get-Location).Path,
    [ValidateSet('', 'claude', 'codex', 'bash')][string] $Tool = '',
    [switch] $Rebuild,
    [switch] $WebDownload
)

$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'scripts/wsl-containers.ps1')
    Assert-AiWslPlatform
    if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) {
        throw "Workspace does not exist: $Workspace"
    }
    $result = Initialize-AiWsl -WebDownload:$WebDownload
    if ($result.ExitCode -ne 0) { exit $result.ExitCode }
    if ($Rebuild) {
        Build-AiWslImages -Wslc $result.Wslc -Repository $PSScriptRoot -Target rebuild
    } elseif (-not (Test-AiWslImages $result.Wslc)) {
        Build-AiWslImages -Wslc $result.Wslc -Repository $PSScriptRoot
    }
    Start-AiWslContainer -Wslc $result.Wslc -Repository $PSScriptRoot -Workspace $Workspace -Tool $Tool
    exit $script:AiWslExitCode
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
