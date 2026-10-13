#Requires -Version 5.1
# Offline deletion-scope tests. No WSL, Docker, elevation, or network access.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../wsl-containers/wsl-containers.ps1')
. (Join-Path $PSScriptRoot '../wsl-containers/remove-ai-resources.ps1')
$script:checks = 0
$run = '0123456789abcdef0123456789abcdef'
$legacyRun = 'fedcba9876543210fedcba9876543210'

function Assert-True {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
    $script:checks++
}

function Assert-Throws {
    param([scriptblock] $Action, [string] $Pattern)
    $caught = $null
    try { & $Action } catch { $caught = $_ }
    Assert-True ($null -ne $caught) "Expected failure: $Pattern"
    Assert-True ([string] $caught -match $Pattern) "Unexpected error: $caught"
}

function New-TestContainer {
    param([string] $Name, [string] $Image = 'yumayo-ai-base', [hashtable] $Labels = @{})
    return @{ Name = "/$Name"; Config = @{ Image = $Image; Labels = $Labels } }
}

function Reset-Fixture {
    $script:reads = New-Object 'System.Collections.Generic.List[string]'
    $script:mutations = New-Object 'System.Collections.Generic.List[string]'
    $script:failRead = ''
    $script:failRemove = ''
    $script:badId = $false
    $script:nativeContainers = @("yumayo-ai-launch-$run", "yumayo-ai-share-$run", 'unrelated-native-app')
    $script:nativeVolumes = @("yumayo-ai-engine-$run", "yumayo-ai-tmp-$run", 'unrelated-native-data')
    $script:containers = [ordered] @{
        ('1' * 64) = (New-TestContainer "yumayo-ai-launch-$run")
        ('2' * 64) = (New-TestContainer "yumayo-ai-share-$run")
        ('3' * 64) = (New-TestContainer "yumayo-ai-relay-$run")
        ('4' * 64) = (New-TestContainer 'ai-session' 'yumayo-ai' @{ 'io.yumayo.aicontainer.wsl-run' = $run })
        ('5' * 64) = (New-TestContainer 'yumayo-ai-proxy-project' 'yumayo-ai-proxy:latest')
        ('6' * 64) = (New-TestContainer 'database' 'postgres')
        ('7' * 64) = (New-TestContainer 'yumayo-ai-proxy-user-app' 'unrelated-image')
        ('8' * 64) = (New-TestContainer "yumayo-ai-relay-$run-extra")
        ('9' * 64) = (New-TestContainer 'invalid-owner' 'yumayo-ai' @{ 'io.yumayo.aicontainer.wsl-run' = 'not-a-run-id' })
    }
    $script:networks = [ordered] @{
        ('a' * 64) = @{ Name = 'yumayo-ai-0123456789'; Labels = @{ 'io.yumayo.aicontainer.wsl-run' = $run } }
        ('b' * 64) = @{ Name = 'yumayo-ai-abcdefghij'; Labels = @{ 'io.yumayo.aicontainer.network' = 'public' } }
        ('c' * 64) = @{ Name = 'shared-services'; Labels = @{} }
        ('d' * 64) = @{ Name = 'yumayo-ai-zzzzzzzzzz'; Labels = @{} }
    }
    $script:dockerVolumes = @(
        "yumayo-ai-engine-$run", "yumayo-ai-tmp-$legacyRun", 'yumayo-ai-proxy-project-sock',
        'database-data', "yumayo-ai-tmp-$run-extra"
    )
    $script:images = @('yumayo-ai:latest', 'yumayo-ai-proxy:latest', 'yumayo-ai-base:latest',
        'yumayo-ai:custom', 'yumayo-ai-custom:latest', 'postgres:latest')
}

function Initialize-AiWsl { throw 'Uninstall must not initialize WSL.' }
function Invoke-AiWslElevatedCommand { throw 'Uninstall must not elevate.' }

function Read-AiWslCommand {
    param([string] $Wslc, [string[]] $Arguments)
    $call = $Arguments -join ' '
    $script:reads.Add($call)
    if ($script:failRead -and $call -eq $script:failRead) { throw 'Inventory failed' }
    switch ($call) {
        'container list --all' { return "NAME STATUS`n" + ($script:nativeContainers -join "`n") }
        'volume list' { return "NAME DRIVER`n" + (($script:nativeVolumes | ForEach-Object { "$_ guest" }) -join "`n") }
        'system session run docker ps -aq --no-trunc' {
            if ($script:badId) { return '--bad-id' }
            return $script:containers.Keys -join "`n"
        }
        'system session run docker network ls -q --no-trunc' { return $script:networks.Keys -join "`n" }
        'system session run docker volume ls --format {{.Name}}' { return $script:dockerVolumes -join "`n" }
        'system session run docker image ls --format {{.Repository}}:{{.Tag}}' { return $script:images -join "`n" }
        default {
            if ($Arguments[4] -eq 'inspect' -and $script:containers.Contains($Arguments[-1])) {
                return ConvertTo-Json -Depth 6 -InputObject @($script:containers[$Arguments[-1]])
            }
            if ($Arguments[4] -eq 'network' -and $Arguments[5] -eq 'inspect' -and $script:networks.Contains($Arguments[-1])) {
                return ConvertTo-Json -Depth 6 -InputObject @($script:networks[$Arguments[-1]])
            }
            throw "Unexpected read: $call"
        }
    }
}

function Invoke-AiWslCommand {
    param([string] $Wslc, [string[]] $Arguments)
    $call = $Arguments -join ' '
    $script:mutations.Add($call)
    if ($script:failRemove -and $Arguments[-1] -eq $script:failRemove) { throw 'Removal failed' }
    if ($Arguments[0] -eq 'rm') {
        $name = $Arguments[-1]
        $script:nativeContainers = @($script:nativeContainers | Where-Object { $_ -ne $name })
        foreach ($id in @($script:containers.Keys)) {
            if ($script:containers[$id].Name -eq "/$name") { $script:containers.Remove($id) }
        }
    } elseif ($Arguments[0] -eq 'volume') {
        $script:nativeVolumes = @($script:nativeVolumes | Where-Object { $_ -ne $Arguments[-1] })
        $script:dockerVolumes = @($script:dockerVolumes | Where-Object { $_ -ne $Arguments[-1] })
    } elseif ($Arguments[4] -eq 'rm') {
        $script:containers.Remove($Arguments[-1])
    } elseif ($Arguments[4] -eq 'network') {
        $script:networks.Remove($Arguments[-1])
    } elseif ($Arguments[4] -eq 'volume') {
        $script:dockerVolumes = @($script:dockerVolumes | Where-Object { $_ -ne $Arguments[-1] })
    } elseif ($Arguments[4] -eq 'image') {
        $script:images = @($script:images | Where-Object { $_ -ne $Arguments[-1] })
    } else { throw "Unexpected mutation: $call" }
}

Reset-Fixture
Remove-AiWslResources -Wslc 'mock' -WhatIf
Assert-True ($script:reads.Count -gt 0 -and $script:mutations.Count -eq 0) 'WhatIf must discover resources without deleting anything.'

Reset-Fixture
$plan = @(Get-AiWslRemovalPlan -Wslc 'mock')
Assert-True (@($plan | Where-Object { $_.Kind -eq 'WSL volume' }).Count -eq 2) 'Discover native WSL volumes separately from Docker volumes.'
Assert-True (@($plan | Where-Object { $_.Kind -eq 'Docker volume' }).Count -eq 2) 'Include orphaned legacy volumes and persistent proxy sockets without duplicating native names.'
Remove-AiWslResources -Wslc 'mock' -Confirm:$false
Assert-True ($script:mutations[0] -eq "rm --force yumayo-ai-launch-$run") 'Remove the native launcher before its children.'
Assert-True ($script:mutations[1] -eq ('system session run docker rm --force ' + ('3' * 64))) 'Remove the relay after the launcher.'
Assert-True ($script:nativeContainers.Count -eq 1 -and $script:nativeContainers[0] -eq 'unrelated-native-app') 'Keep unrelated native containers.'
foreach ($id in @('6', '7', '8', '9')) {
    Assert-True ($script:containers.Contains($id * 64)) 'Preserve unrelated containers, lookalike names and invalid ownership labels.'
}
Assert-True ($script:containers.Count -eq 4) 'Remove owned AI and proxy containers.'
Assert-True ($script:networks.Count -eq 2 -and $script:networks.Contains('c' * 64) -and $script:networks.Contains('d' * 64)) 'Keep additional networks and unlabeled lookalikes.'
Assert-True ($script:nativeVolumes.Count -eq 1 -and $script:nativeVolumes[0] -eq 'unrelated-native-data') 'Keep unrelated native volumes.'
Assert-True ($script:dockerVolumes.Count -eq 2 -and $script:dockerVolumes -contains 'database-data') 'Keep unrelated Docker volumes.'
Assert-True ($script:images.Count -eq 3 -and $script:images -contains 'yumayo-ai:custom') 'Keep custom tags and other images.'
Assert-True ($script:mutations[-1] -eq 'system session run docker image rm yumayo-ai-base:latest') 'Remove the base tag after its child image.'
$operations = @($script:mutations)
$shareIndex = [array]::IndexOf($operations, "rm --force yumayo-ai-share-$run")
$volumeIndex = [array]::IndexOf($operations, "volume remove --force yumayo-ai-engine-$run")
Assert-True ($shareIndex -ge 0 -and $volumeIndex -gt $shareIndex) 'Release all native volume references before deleting volumes.'
Assert-True (@($operations | Where-Object { $_ -match 'prune|disconnect|image rm --force|volume rm --force|shutdown|unregister' }).Count -eq 0) 'Never prune shared resources or force-delete in-use images and Docker volumes.'
$count = $script:mutations.Count
Remove-AiWslResources -Wslc 'mock' -Confirm:$false
Assert-True ($script:mutations.Count -eq $count) 'Repeated uninstall must not delete additional resources.'

Reset-Fixture
$script:failRead = 'system session run docker image ls --format {{.Repository}}:{{.Tag}}'
Assert-Throws { Remove-AiWslResources -Wslc 'mock' -Confirm:$false } 'Inventory failed'
Assert-True ($script:mutations.Count -eq 0) 'Even a late inventory failure must stop before any deletion.'

Reset-Fixture
$script:badId = $true
Assert-Throws { Remove-AiWslResources -Wslc 'mock' -Confirm:$false } 'Unexpected Docker container ID'
Assert-True ($script:mutations.Count -eq 0) 'Reject malformed native output before constructing removal commands.'

Reset-Fixture
$script:failRemove = "yumayo-ai-engine-$run"
Assert-Throws { Remove-AiWslResources -Wslc 'mock' -Confirm:$false } '1 resource removal\(s\) failed'
Assert-True ($script:nativeVolumes -contains "yumayo-ai-engine-$run") 'Leave failed resources available for retry.'
Assert-True (-not ($script:images -contains 'yumayo-ai:latest')) 'Continue independent cleanup after a removal failure.'
$script:failRemove = ''
Remove-AiWslResources -Wslc 'mock' -Confirm:$false
Assert-True ($script:nativeVolumes.Count -eq 1) 'A retry must remove the previously failed resource.'

# Output normalization and complete-token matching are independent of headings.
$name = "yumayo-ai-tmp-$run"
$text = "NAME DRIVER`r`n$name guest`r`n$name-extra guest`r`n"
$names = @(Get-AiWslOwnedNames ($text.ToCharArray() -join [string][char]0) '^yumayo-ai-tmp-[0-9a-f]{32}$')
Assert-True ($names.Count -eq 1 -and $names[0] -eq $name) 'Do not delete substring matches in a native volume listing.'
$names = @(Get-AiWslOwnedNames ('[{"Name":"' + $name + '","Driver":"guest"}]') '^yumayo-ai-tmp-[0-9a-f]{32}$')
Assert-True ($names.Count -eq 1 -and $names[0] -eq $name) 'Recognize complete names if native output is formatted as JSON.'
Write-Host "Passed $script:checks uninstall assertions."
