#Requires -Version 5.1
<#
.SYNOPSIS
Remove this project's AI resources from the current WSL Containers session.
.DESCRIPTION
Remove AI containers, proxies, temporary volumes, dedicated networks, and image
tags. Keep WSL itself, other distributions, Windows features, and workspace files.
Run as the same Windows user and privilege level used to start AI Container.
.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\uninstall-wsl-containers.ps1 -WhatIf
.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\uninstall-wsl-containers.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'wsl-containers/wsl-containers.ps1')
    . (Join-Path $PSScriptRoot 'wsl-containers/remove-ai-resources.ps1')
    Assert-AiWslPlatform
    $wslc = Find-AiWslExecutable 'wslc.exe'
    if (-not $wslc) {
        Write-Host 'wslc.exe was not found. No resources were changed.'
        exit 0
    }
    # Do not call Initialize-AiWsl: uninstall must never install or update WSL.
    Remove-AiWslResources -Wslc $wslc @PSBoundParameters
    exit 0
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
