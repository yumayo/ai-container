#Requires -Version 5.1
<#
.SYNOPSIS
Install WSL Containers and build the existing AI Container Dockerfiles.
.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-wsl.ps1
.EXAMPLE
.\install-wsl.ps1 -Target setup -WebDownload
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'setup', 'base', 'main', 'proxy', 'rebuild')]
    [string] $Target = 'all',
    [switch] $WebDownload
)

$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'wsl-containers/wsl-containers.ps1')
    Assert-AiWslPlatform
    $result = Initialize-AiWsl -WebDownload:$WebDownload
    if ($result.ExitCode -ne 0) { exit $result.ExitCode }
    if ($Target -ne 'setup') {
        Build-AiWslImages -Wslc $result.Wslc -Repository $PSScriptRoot -Target $Target
        Write-Host 'Image build complete. Run start-wsl.ps1 -Workspace <project directory> to start AI Container.'
    }
    exit 0
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
