#Requires -Version 5.1
<#
.SYNOPSIS
Start AI Container using the Docker engine bundled with WSL Containers.
.DESCRIPTION
Prepare WSL Containers (including WSL installation or update when needed),
build missing images, and start the AI container.
.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-wsl-containers.ps1 -Workspace C:\src\my-project -Tool codex
#>
[CmdletBinding()]
param(
    [string] $Workspace = (Get-Location).Path,
    [ValidateSet('', 'claude', 'codex', 'bash')][string] $Tool = '',
    [string] $LaunchProfileFile = '',
    [switch] $Rebuild,
    [switch] $WebDownload
)

$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'wsl-containers/wsl-containers.ps1')
    Assert-AiWslPlatform
    if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) {
        if ($Workspace -match '^[A-Za-z]:(?![\\/])') {
            $absoluteExample = $Workspace.Insert(2, '\')
            throw "Workspace does not exist: $Workspace. This is a drive-relative path. If you meant a path from the drive root, use: $absoluteExample"
        }
        throw "Workspace does not exist: $Workspace"
    }
    $result = Initialize-AiWsl -WebDownload:$WebDownload
    if ($result.ExitCode -ne 0) { exit $result.ExitCode }
    if ($Rebuild) {
        Build-AiWslImages -Wslc $result.Wslc -Repository $PSScriptRoot -Target rebuild
    } elseif (-not (Test-AiWslImages $result.Wslc)) {
        Build-AiWslImages -Wslc $result.Wslc -Repository $PSScriptRoot
    }
    Start-AiWslContainer -Wslc $result.Wslc -Repository $PSScriptRoot -Workspace $Workspace -Tool $Tool -LaunchProfileFile $LaunchProfileFile
    exit $script:AiWslExitCode
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
