# Called by uninstall-wsl-containers.ps1 after loading wsl-containers.ps1.
Set-StrictMode -Version Latest

function Get-AiWslOwnedNames {
    param([string] $Output, [string] $Pattern)
    # Native wslc lists use localized headings. Match complete reserved names,
    # not column positions or Docker-only --format/--filter options.
    $plain = $Output.Replace([string][char]0, '') -replace '\x1b\[[0-9;]*m', ''
    foreach ($token in ($plain -split '[\s|\u2502",:\[\]{}]+')) {
        if ($token -cmatch $Pattern) { $token }
    }
}

function Get-AiWslResourceLabel {
    param([object] $Labels, [string] $Name)
    if ($null -eq $Labels) { return '' }
    $property = $Labels.PSObject.Properties[$Name]
    if ($null -eq $property) { return '' }
    return [string] $property.Value
}

function Get-AiWslRemovalPlan {
    param([Parameter(Mandatory = $true)][string] $Wslc)
    $docker = @('system', 'session', 'run', 'docker')
    $runPattern = '^[0-9a-f]{32}$'
    $helperPattern = '^yumayo-ai-(launch|share|relay)-[0-9a-f]{32}$'
    $volumePattern = '^yumayo-ai-(tmp|engine|workspace|repository)-[0-9a-f]{32}$'
    $proxyPattern = '^yumayo-ai-proxy(?:-[a-zA-Z0-9][a-zA-Z0-9_.-]*)?$'
    $proxyVolumePattern = '^yumayo-ai-proxy(?:-[a-zA-Z0-9][a-zA-Z0-9_.-]*)?-sock$'
    $plan = New-Object 'System.Collections.Generic.List[object]'

    # Complete discovery before making any changes. A query error must not be
    # treated as an empty installation or trigger a wider cleanup fallback.
    $nativeContainers = @(Get-AiWslOwnedNames (
        Read-AiWslCommand $Wslc @('container', 'list', '--all')
    ) $helperPattern | Sort-Object -Unique)
    $nativeVolumes = @(Get-AiWslOwnedNames (
        Read-AiWslCommand $Wslc @('volume', 'list')
    ) $volumePattern | Sort-Object -Unique)
    foreach ($name in $nativeContainers) {
        $order = 0
        if ($name -like 'yumayo-ai-share-*') { $order = 3 }
        elseif ($name -like 'yumayo-ai-relay-*') { $order = 1 }
        $plan.Add([pscustomobject] @{
            Order = $order; Kind = 'WSL container'; Name = $name
            Arguments = @('rm', '--force', $name)
        })
    }

    $containerIds = Read-AiWslCommand $Wslc ($docker + @('ps', '-aq', '--no-trunc'))
    foreach ($id in ($containerIds -split '\s+')) {
        if (-not $id) { continue }
        if ($id -cnotmatch '^[0-9a-f]{64}$') { throw 'Unexpected Docker container ID in removal inventory.' }
        $items = @(ConvertFrom-Json (Read-AiWslCommand $Wslc ($docker + @('inspect', $id))))
        $container = $items[0]
        $name = $container.Name.TrimStart('/')
        if ($nativeContainers -contains $name) { continue }
        $run = Get-AiWslResourceLabel $container.Config.Labels 'io.yumayo.aicontainer.wsl-run'
        $isProxy = $name -cmatch $proxyPattern -and $container.Config.Image -cin @('yumayo-ai-proxy', 'yumayo-ai-proxy:latest')
        if ($name -cnotmatch $helperPattern -and $run -cnotmatch $runPattern -and -not $isProxy) { continue }
        $order = 2
        if ($name -like 'yumayo-ai-launch-*') { $order = 0 }
        elseif ($name -like 'yumayo-ai-relay-*') { $order = 1 }
        elseif ($name -like 'yumayo-ai-share-*') { $order = 3 }
        # IDs prevent a different container with a reused name being removed.
        $plan.Add([pscustomobject] @{
            Order = $order; Kind = 'Docker container'; Name = $name
            Arguments = $docker + @('rm', '--force', $id)
        })
    }

    $networkIds = Read-AiWslCommand $Wslc ($docker + @('network', 'ls', '-q', '--no-trunc'))
    foreach ($id in ($networkIds -split '\s+')) {
        if (-not $id) { continue }
        if ($id -cnotmatch '^[0-9a-f]{64}$') { throw 'Unexpected Docker network ID in removal inventory.' }
        $items = @(ConvertFrom-Json (Read-AiWslCommand $Wslc ($docker + @('network', 'inspect', $id))))
        $network = $items[0]
        $run = Get-AiWslResourceLabel $network.Labels 'io.yumayo.aicontainer.wsl-run'
        $kind = Get-AiWslResourceLabel $network.Labels 'io.yumayo.aicontainer.network'
        $isPublic = $network.Name -cmatch '^yumayo-ai-[a-z0-9]{10}$' -and $kind -ceq 'public'
        if ($run -cnotmatch $runPattern -and -not $isPublic) { continue }
        $plan.Add([pscustomobject] @{
            Order = 4; Kind = 'Docker network'; Name = $network.Name
            Arguments = $docker + @('network', 'rm', $id)
        })
    }

    foreach ($name in $nativeVolumes) {
        $plan.Add([pscustomobject] @{
            Order = 5; Kind = 'WSL volume'; Name = $name
            Arguments = @('volume', 'remove', '--force', $name)
        })
    }
    $dockerVolumes = Read-AiWslCommand $Wslc ($docker + @('volume', 'ls', '--format', '{{.Name}}'))
    foreach ($name in ($dockerVolumes -split '\s+')) {
        if ($nativeVolumes -contains $name) { continue }
        if ($name -cnotmatch $volumePattern -and $name -cnotmatch $proxyVolumePattern) { continue }
        $plan.Add([pscustomobject] @{
            Order = 5; Kind = 'Docker volume'; Name = $name
            Arguments = $docker + @('volume', 'rm', $name)
        })
    }

    $imageTags = (Read-AiWslCommand $Wslc ($docker + @('image', 'ls', '--format', '{{.Repository}}:{{.Tag}}'))) -split '\s+'
    # Only remove the exact tags installed by this project. Never force image
    # deletion, remove custom tags, or prune the shared engine/build cache.
    $imageOrder = 6
    foreach ($tag in @('yumayo-ai:latest', 'yumayo-ai-proxy:latest', 'yumayo-ai-base:latest')) {
        if ($imageTags -cnotcontains $tag) { continue }
        $plan.Add([pscustomobject] @{
            Order = $imageOrder; Kind = 'Docker image'; Name = $tag
            Arguments = $docker + @('image', 'rm', $tag)
        })
        $imageOrder++
    }
    return $plan | Sort-Object Order
}

function Remove-AiWslResources {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    param([Parameter(Mandatory = $true)][string] $Wslc)

    $plan = @(Get-AiWslRemovalPlan -Wslc $Wslc)
    if ($plan.Count -eq 0) {
        Write-Host 'No AI Container resources were found in this WSL Containers session.'
        return
    }
    $failures = 0
    $removed = 0
    foreach ($resource in $plan) {
        if ($PSCmdlet.ShouldProcess("$($resource.Kind): $($resource.Name)", 'Remove')) {
            try {
                Invoke-AiWslCommand $Wslc $resource.Arguments
                $removed++
            } catch {
                $failures++
                Write-Warning "Could not remove $($resource.Kind) $($resource.Name): $_"
            }
        }
    }
    if ($failures -gt 0) {
        throw "$failures resource removal(s) failed. Close AI sessions, check the errors above, and retry."
    }
    if (-not $WhatIfPreference) {
        Write-Host "Removed $removed AI Container resource(s). WSL and workspace files were kept."
    }
}
