#Requires -Version 5.1
# Offline tests: no WSL installation, elevation, Docker, or network access.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../scripts/wsl-containers.ps1')

$originalProbe = ${function:Get-AiWslVersion}
$originalFind = ${function:Find-AiWslExecutable}
$originalCommand = ${function:Invoke-AiWslCommand}
$originalInteractive = ${function:Invoke-AiWslInteractive}
$script:checks = 0
$script:calls = New-Object 'System.Collections.Generic.List[object]'

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

function Reset-Fixture {
    $script:calls.Clear()
    $script:version = [version] '3.0.1.0'
    $script:hasWslc = $true
    $script:featureState = 1
    $script:setupExit = 0
    $script:featureExit = 0
    $script:updateWorks = $true
    $script:failImage = ''
    $script:failCarrierStart = $false
    $script:badMount = $false
    $script:sameMount = $false
    $script:launcherUser = '1000'
    $script:failRunnerCreate = $false
    $script:failVolumeCreateAt = 0
    $script:volumeCreateCount = 0
    $script:failVolumeRemove = $false
}

function Find-AiWslExecutable {
    param([string] $Name)
    if ($Name -eq 'wslc.exe' -and -not $script:hasWslc) { return $null }
    return "C:\Program Files\WSL\$Name"
}

function Get-AiWslVersion {
    param([string] $Wsl)
    return $script:version
}

function Get-AiWslVirtualMachineState { return $script:featureState }

function Invoke-AiWslSetupCommand {
    param([string] $Wsl, [string[]] $Arguments)
    $script:calls.Add([pscustomobject] @{ Kind = 'setup'; Arguments = $Arguments })
    if ($script:updateWorks) {
        $script:version = [version] '3.0.1.0'
        $script:hasWslc = $true
    }
    return $script:setupExit
}

function Invoke-AiWslElevatedCommand {
    param([string] $Executable, [string[]] $Arguments)
    $script:calls.Add([pscustomobject] @{
        Kind = 'feature'; Executable = $Executable; Arguments = $Arguments
    })
    return $script:featureExit
}

function Invoke-AiWslCommand {
    param([string] $Executable, [string[]] $Arguments)
    $script:calls.Add([pscustomobject] @{
        Kind = 'wslc'; Arguments = $Arguments; Directory = (Get-Location).Path
    })
    if ($script:failImage -and $Arguments -contains $script:failImage) { throw 'Build failed' }
    if ($script:failCarrierStart -and $Arguments[0] -eq 'start') { throw 'Share start failed' }
    if ($script:failRunnerCreate -and $Arguments[0] -eq 'create' -and $Arguments[2] -like 'yumayo-ai-launch-*') {
        throw 'Launcher create failed'
    }
    if (($Arguments -join ' ') -match '^system session run docker volume create ') {
        $script:volumeCreateCount++
        if ($script:volumeCreateCount -eq $script:failVolumeCreateAt) { throw 'Volume create failed' }
    }
    if ($script:failVolumeRemove -and ($Arguments -join ' ') -match '^system session run docker volume rm ') {
        throw 'Volume remove failed'
    }
}

function Read-AiWslCommand {
    param([string] $Wslc, [string[]] $Arguments)
    $script:calls.Add([pscustomobject] @{ Kind = 'read'; Arguments = $Arguments })
    if ($Arguments -contains 'inspect') {
        $workspaceSource = '/mnt/workspace-share'
        if ($script:badMount) { $workspaceSource = 'C:\Windows\workspace' }
        $repositorySource = '/mnt/repository-share'
        if ($script:sameMount) { $repositorySource = $workspaceSource }
        return ConvertTo-Json -Depth 4 -InputObject @(@{ Mounts = @(
            @{ Type = 'bind'; Destination = '/aicontainer/workspace'; Source = $workspaceSource },
            @{ Type = 'bind'; Destination = '/aicontainer/repository'; Source = $repositorySource }
        ) })
    }
    if ($Arguments -contains 'stat') { return '998' }
    if ($Arguments -contains 'id') { return $script:launcherUser }
    if ($Arguments -contains 'ps') { return '123456789abc' }
    if ($Arguments -contains 'ls') { return 'abcdef123456' }
    throw "Unexpected read: $Arguments"
}

function Invoke-AiWslInteractive {
    param([string] $Wslc, [string] $Name)
    $script:calls.Add([pscustomobject] @{ Kind = 'interactive'; Arguments = @('start', $Name) })
    $script:AiWslExitCode = 17
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ('ai wsl test ' + [guid]::NewGuid().ToString('N'))
$context = Join-Path $temporary 'docker/aicontainer'
$null = New-Item -ItemType Directory -Path $context -Force
$context = (Get-Item -LiteralPath $context).FullName
$proxyContext = Join-Path $temporary 'docker/docker-proxy'
$null = New-Item -ItemType Directory -Path $proxyContext -Force
[IO.File]::WriteAllText((Join-Path $proxyContext 'Dockerfile'), "# LF fixture`n")
[IO.File]::WriteAllText((Join-Path $proxyContext 'entrypoint.sh'), "# LF fixture`n")
$originalSystemRoot = $env:SystemRoot
try {
    # Allow these mocked Windows commands to be tested with PowerShell on Linux.
    if (-not $env:SystemRoot) { $env:SystemRoot = $temporary }
    # A working terminal command must take precedence over another installed copy.
    & {
        function Get-Command {
            param($Name, $CommandType, $ErrorAction)
            return [pscustomobject] @{ Source = 'C:\Windows\System32\wsl.exe' }
        }
        function Test-Path { throw 'Fixed paths must not override the terminal executable.' }
        Assert-True ((& $originalFind 'wsl.exe') -eq 'C:\Windows\System32\wsl.exe') 'Reuse the executable resolved by the terminal.'
    }

    & {
        function Get-Command {
            param($Name, $CommandType, $ErrorAction)
            [pscustomobject] @{ Source = "C:\Windows\System32\$Name" }
            [pscustomobject] @{ Source = "C:\Users\Test\AppData\Local\Microsoft\WindowsApps\$Name" }
        }
        function Test-Path { throw 'Fixed paths must not override the terminal executable.' }
        foreach ($name in @('wsl.exe', 'wslc.exe')) {
            $executable = & $originalFind $name
            Assert-True ($executable -is [string]) 'Multiple PATH matches must return a single string for native command parameters.'
            Assert-True ($executable -eq "C:\Windows\System32\$name") 'Select the first application in PATH order.'
        }
    }

    Reset-Fixture
    $script:version = $null
    $script:featureState = 2
    $result = Initialize-AiWsl
    Assert-True ($result.ExitCode -eq 3010) 'A fresh installation must stop for reboot even on exit zero.'
    Assert-True ($script:calls.Count -eq 1) 'No wslc calls may run before reboot.'
    Assert-True (($script:calls[0].Arguments -join ' ') -eq '--install --no-distribution') 'Do not install Ubuntu.'

    Reset-Fixture
    $script:version = $null
    $script:featureState = 2
    $result = Initialize-AiWsl -WebDownload
    Assert-True ($result.ExitCode -eq 3010) 'A web installation must stop for reboot.'
    Assert-True ($script:calls[0].Arguments -contains '--web-download') 'Pass the explicitly requested download mode.'

    foreach ($state in @(2, 3)) {
        foreach ($code in @(0, 3010)) {
            Reset-Fixture
            $script:featureState = $state
            $script:featureExit = $code
            $result = Initialize-AiWsl -WebDownload
            Assert-True ($result.ExitCode -eq 3010 -and $null -eq $result.Wslc) 'Enabling the VM platform must stop for reboot even on exit zero.'
            Assert-True ($script:calls.Count -eq 1 -and $script:calls[0].Kind -eq 'feature') 'Installed WSL must enable the feature without reinstalling or invoking wslc.'
            $call = $script:calls[0]
            Assert-True ($call.Executable -eq (Join-Path $env:SystemRoot 'System32\dism.exe')) 'Use the Windows DISM executable.'
            foreach ($argument in @('/Online', '/Enable-Feature', '/FeatureName:VirtualMachinePlatform', '/All', '/NoRestart')) {
                Assert-True ($call.Arguments -contains $argument) "Missing feature setup argument: $argument"
            }
            Assert-True (-not ($call.Arguments -contains '--web-download')) 'The WSL download switch does not apply to DISM.'
            $logArgument = @($call.Arguments | Where-Object { $_ -like '/LogPath:*' })
            Assert-True ($logArgument.Count -eq 1 -and $logArgument[0] -match '^/LogPath:".+aicontainer-vmp-[0-9a-f]{32}\.log"$') 'Preserve diagnostics in a unique, quoted log path.'
        }
    }

    foreach ($code in @(-1, 5, 87)) {
        Reset-Fixture
        $script:featureState = 2
        $script:featureExit = $code
        Assert-Throws { Initialize-AiWsl } "VirtualMachinePlatform failed \(exit $code\).*DISM log:"
        Assert-True ($script:calls.Count -eq 1 -and $script:calls[0].Kind -eq 'feature') 'Feature setup failure must stop without reinstalling WSL or invoking wslc.'
    }

    foreach ($code in @(3010, 1641)) {
        Reset-Fixture
        $script:version = [version] '2.7.0'
        $script:setupExit = $code
        $result = Initialize-AiWsl
        Assert-True ($result.ExitCode -eq 3010 -and $script:calls.Count -eq 1) 'Stop when update requests reboot.'
    }

    Reset-Fixture
    $result = Initialize-AiWsl
    Assert-True ($result.ExitCode -eq 0) 'Existing GA installation must be reusable.'
    Assert-True ($script:calls.Count -eq 1 -and $script:calls[0].Kind -eq 'wslc') 'Do not elevate or update on every invocation.'

    foreach ($state in @(4, $null)) {
        Reset-Fixture
        $script:featureState = $state
        Assert-Throws { Initialize-AiWsl } 'Could not determine whether VirtualMachinePlatform is enabled'
        Assert-True ($script:calls.Count -eq 0) 'An unknown feature state must not trigger installation.'
    }

    & {
        Reset-Fixture
        function Get-AiWslVersion {
            param([string] $Wsl)
            throw 'Could not read the WSL version'
        }
        Assert-Throws { Initialize-AiWsl } 'Could not read the WSL version'
        Assert-True ($script:calls.Count -eq 0) 'A version parsing error must not trigger installation.'
    }

    Reset-Fixture
    $script:version = [version] '2.9.3'
    $script:hasWslc = $false
    $result = Initialize-AiWsl
    Assert-True ($result.ExitCode -eq 0 -and $script:calls.Count -eq 2) 'Update preview and rediscover the executable.'
    Assert-True (($script:calls[0].Arguments -join ' ') -eq '--update') 'Use stable WSL, without pre-release flags.'

    Reset-Fixture
    $script:hasWslc = $false
    $script:updateWorks = $false
    Assert-Throws { Initialize-AiWsl } 'WSL 3.0.1'
    Assert-True ($script:calls.Count -eq 1) 'A failed upgrade must never proceed to wslc.'

    foreach ($existingVersion in @($null, [version] '2.7.0')) {
        Reset-Fixture
        $script:version = $existingVersion
        $script:setupExit = 5
        Assert-Throws { Initialize-AiWsl } 'exit 5'
        Assert-True ($script:calls.Count -eq 1) 'Install/update failure must stop subsequent commands.'
    }

    foreach ($name in @('Dockerfile.base', 'Dockerfile', 'entrypoint.sh', 'init-firewall.sh', 'ai-cli')) {
        [IO.File]::WriteAllText((Join-Path $context $name), "# LF fixture`n")
    }
    $startingLocation = (Get-Location).Path
    Reset-Fixture
    Build-AiWslImages -Wslc 'wslc.exe' -Repository $temporary
    Assert-True ($script:calls.Count -eq 3) 'Build base, main and the existing Docker proxy.'
    Assert-True (($script:calls[0].Arguments -join ' ') -eq 'build -t yumayo-ai-base -f Dockerfile.base .') 'Build the original base first.'
    Assert-True (($script:calls[1].Arguments -join ' ') -eq 'build --no-cache -t yumayo-ai -f Dockerfile .') 'Build the original main second.'
    Assert-True (($script:calls[2].Arguments -join ' ') -eq 'build --no-cache -t yumayo-ai-proxy -f Dockerfile .') 'Build the proxy in its own context.'
    Assert-True ($script:calls[0].Directory -eq $context) 'Resolve COPY paths against the repository, regardless of invocation directory.'
    Assert-True ((Get-Location).Path -eq $startingLocation) 'Restore the working directory.'

    Reset-Fixture
    Build-AiWslImages -Wslc 'wslc.exe' -Repository $temporary -Target rebuild
    Assert-True ($script:calls[0].Arguments -contains '--no-cache') 'Rebuild must invalidate the base cache too.'

    foreach ($target in @('base', 'main', 'proxy')) {
        Reset-Fixture
        Build-AiWslImages -Wslc 'wslc.exe' -Repository $temporary -Target $target
        Assert-True ($script:calls.Count -eq 1) 'An explicit build target must only build one image.'
    }

    Reset-Fixture
    $script:failImage = 'yumayo-ai-base'
    Assert-Throws { Build-AiWslImages -Wslc 'wslc.exe' -Repository $temporary } 'Build failed'
    Assert-True ($script:calls.Count -eq 1) 'Never build the main image after a base build failure.'
    Assert-True ((Get-Location).Path -eq $startingLocation) 'Restore the directory after failure too.'

    Reset-Fixture
    [IO.File]::WriteAllText((Join-Path $context 'entrypoint.sh'), "#!/bin/bash`r`n")
    Assert-Throws { Build-AiWslImages -Wslc 'wslc.exe' -Repository $temporary } 'CRLF'
    Assert-True ($script:calls.Count -eq 0) 'Reject stale Windows checkouts before starting a build.'

    $scripts = Join-Path $temporary 'scripts'
    $null = New-Item -ItemType Directory -Path $scripts
    [IO.File]::WriteAllText((Join-Path $scripts 'wsl-launch.sh'), "# fixture`n")
    [IO.File]::WriteAllText((Join-Path $temporary '.bash_ai_container'), "# fixture`n")
    Reset-Fixture
    Start-AiWslContainer -Wslc 'wslc.exe' -Repository $temporary -Workspace $temporary -Tool codex
    Assert-True ($script:AiWslExitCode -eq 17) 'Cleanup must preserve the interactive process exit code.'
    $create = @($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^create --name yumayo-ai-launch-' })
    Assert-True ($create.Count -eq 1) 'Use native wslc creation for the launcher attached by wslc.'
    Assert-True (@($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^system session run docker create ' }).Count -eq 0) 'Do not mix Docker creation with native wslc attachment.'
    $arguments = $create[0].Arguments
    $runner = $arguments[2]
    $runId = $runner.Substring('yumayo-ai-launch-'.Length)
    Assert-True ($arguments -contains '1000:998' -and -not ($arguments -contains '--group-add')) 'Keep the AI user UID and use the actual socket GID with supported wslc options.'
    Assert-True ($arguments -contains '--interactive' -and $arguments -contains '--tty') 'Prepare the launcher terminal before attachment.'
    Assert-True ($arguments -contains "io.yumayo.aicontainer.wsl-launch=$runId" -and -not ($arguments -contains "io.yumayo.aicontainer.wsl-run=$runId")) 'Keep the native launcher out of Docker-only child cleanup.'
    Assert-True ($arguments -contains 'DOCKER_HOST=unix:///var/run/aicontainer-engine/docker.sock') 'Use the socket exposed at the dedicated launcher mount.'
    $volumes = @($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^system session run docker volume create ' })
    Assert-True ($volumes.Count -eq 4) 'Prepare local volumes for the engine socket directory, temporary files, workspace and repository.'
    $expectedBindings = @(
        @{ Kind = 'engine'; Source = '/var/run'; Target = '/var/run/aicontainer-engine'; Suffix = ',readonly' },
        @{ Kind = 'tmp'; Source = '/tmp'; Target = '/tmp'; Suffix = '' },
        @{ Kind = 'workspace'; Source = '/mnt/workspace-share'; Target = '/mnt/workspace-share'; Suffix = '' },
        @{ Kind = 'repository'; Source = '/mnt/repository-share'; Target = '/mnt/repository-share'; Suffix = ',readonly' }
    )
    foreach ($binding in $expectedBindings) {
        $name = "yumayo-ai-$($binding.Kind)-$runId"
        $volume = @($volumes | Where-Object { $_.Arguments[-1] -eq $name })
        Assert-True ($volume.Count -eq 1) "Create only one volume for $($binding.Kind)."
        foreach ($value in @('local', 'type=none', 'o=bind', "device=$($binding.Source)", "io.yumayo.aicontainer.wsl-run=$runId")) {
            Assert-True ($volume[0].Arguments -contains $value) "Use an owned bind volume with $value."
        }
        Assert-True ($arguments -contains "type=volume,source=$name,target=$($binding.Target),volume-nocopy$($binding.Suffix)") 'Preserve VM paths and read-only access without copying image contents into source directories.'
    }
    Assert-True (@($arguments | Where-Object { $_ -like 'type=bind,*' }).Count -eq 0) 'Never ask native wslc to translate VM bind paths as Windows paths.'
    Assert-True ($arguments[-1] -eq 'codex') 'Preserve tool selection.'
    Assert-True (-not ($arguments -contains '--privileged')) 'The launcher does not need a privileged container.'
    $probe = $script:calls[0].Arguments
    Assert-True ($probe -contains '--cap-add=NET_ADMIN' -and -not ($probe -contains '--mount')) 'Probe capabilities without exposing a workspace.'
    $operations = @($script:calls | ForEach-Object { $_.Arguments -join ' ' })
    Assert-True ($operations -contains 'system session run docker rm --force 123456789abc') 'Clean up owned AI containers after interrupted attachment.'
    Assert-True ($operations -contains 'system session run docker network rm abcdef123456') 'Clean up owned dedicated networks.'
    Assert-True ($operations -contains "rm --force $runner") 'Remove the launcher through its native wslc lifecycle.'
    Assert-True ([array]::IndexOf($operations, "rm --force $runner") -lt [array]::IndexOf($operations, 'system session run docker rm --force 123456789abc')) 'Stop the launcher before querying/removing its children.'
    $removedVolumes = @($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^system session run docker volume rm ' })
    Assert-True ($removedVolumes.Count -eq $volumes.Count) 'Remove every temporary bind volume after attachment ends.'
    foreach ($volume in $volumes) {
        $remove = "system session run docker volume rm $($volume.Arguments[-1])"
        Assert-True ([array]::IndexOf($operations, $remove) -gt [array]::IndexOf($operations, 'system session run docker rm --force 123456789abc')) 'Release volumes after launcher and AI cleanup.'
    }
    Assert-True ($operations[-1] -match '^rm --force yumayo-ai-share-') 'Release Windows shares last.'
    $filters = @($script:calls | Where-Object { $_.Arguments -contains '--filter' })
    Assert-True ($filters.Count -eq 2) 'Filter both container and network cleanup.'
    foreach ($filter in $filters) {
        Assert-True ($filter.Arguments[-1] -match '^label=io\.yumayo\.aicontainer\.wsl-run=[0-9a-f]{32}$') 'Never clean up unrelated resources.'
    }

    Reset-Fixture
    $script:sameMount = $true
    Start-AiWslContainer -Wslc 'wslc.exe' -Repository $temporary -Workspace $temporary
    $create = @($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^create --name yumayo-ai-launch-' })
    $sharedBinds = @($create[0].Arguments | Where-Object { $_ -like '*target=/mnt/workspace-share*' })
    Assert-True ($sharedBinds.Count -eq 1) 'Deduplicate mounts when repository and workspace share the same source.'
    Assert-True ($script:volumeCreateCount -eq 3) 'Do not create a redundant repository volume for the same VM source.'

    Reset-Fixture
    $script:launcherUser = '1234'
    Start-AiWslContainer -Wslc 'wslc.exe' -Repository $temporary -Workspace $temporary
    $create = @($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^create --name yumayo-ai-launch-' })
    Assert-True ($create[0].Arguments -contains '1234:998') 'Resolve the image user ID rather than assuming UID 1000.'

    Reset-Fixture
    $script:launcherUser = 'not a uid'
    Assert-Throws { Start-AiWslContainer -Wslc 'wslc.exe' -Repository $temporary -Workspace $temporary } 'launcher user ID'
    Assert-True ($script:volumeCreateCount -eq 0) 'Invalid user identity must stop before creating volumes.'
    Assert-True (@($script:calls | Where-Object { $_.Kind -eq 'interactive' }).Count -eq 0) 'Never attach with an invalid user identity.'

    foreach ($failureAt in @(1, 3)) {
        Reset-Fixture
        $script:failVolumeCreateAt = $failureAt
        Assert-Throws { Start-AiWslContainer -Wslc 'wslc.exe' -Repository $temporary -Workspace $temporary } 'Volume create failed'
        $volumes = @($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^system session run docker volume create ' })
        $removedVolumes = @($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^system session run docker volume rm ' })
        Assert-True ($removedVolumes.Count -eq ($failureAt - 1)) 'On partial failure, remove only successfully created volumes.'
        for ($i = 0; $i -lt $removedVolumes.Count; $i++) {
            Assert-True ($removedVolumes[$i].Arguments[-1] -eq $volumes[$i].Arguments[-1]) 'Clean up recorded volumes from this invocation only.'
        }
        Assert-True (@($script:calls | Where-Object { $_.Kind -eq 'interactive' }).Count -eq 0) 'Do not attach after mount preparation fails.'
        Assert-True (($script:calls[-1].Arguments -join ' ') -match '^rm --force yumayo-ai-share-') 'Release shares after a partial volume failure.'
    }

    Reset-Fixture
    $script:failRunnerCreate = $true
    Assert-Throws { Start-AiWslContainer -Wslc 'wslc.exe' -Repository $temporary -Workspace $temporary } 'Launcher create failed'
    Assert-True (@($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^system session run docker volume rm ' }).Count -eq 4) 'Clean up prepared volumes if native launcher creation fails.'
    Assert-True (@($script:calls | Where-Object { $_.Kind -eq 'interactive' }).Count -eq 0) 'Never attach after native creation fails.'
    Assert-True (($script:calls[-1].Arguments -join ' ') -match '^rm --force yumayo-ai-share-') 'Release shares after native creation failure.'

    Reset-Fixture
    $script:failVolumeRemove = $true
    Start-AiWslContainer -Wslc 'wslc.exe' -Repository $temporary -Workspace $temporary
    Assert-True ($script:AiWslExitCode -eq 17) 'Volume cleanup errors must not replace the interactive exit code.'
    Assert-True (@($script:calls | Where-Object { ($_.Arguments -join ' ') -match '^system session run docker volume rm ' }).Count -eq 4) 'One volume removal failure must not prevent the remaining removal attempts.'
    Assert-True (($script:calls[-1].Arguments -join ' ') -match '^rm --force yumayo-ai-share-') 'Attempt share cleanup after volume cleanup errors.'

    Reset-Fixture
    $script:badMount = $true
    Assert-Throws { Start-AiWslContainer -Wslc 'wslc.exe' -Repository $temporary -Workspace $temporary } 'VM bind source'
    Assert-True (@($script:calls | Where-Object { $_.Kind -eq 'interactive' }).Count -eq 0) 'Invalid mappings must stop before launching.'
    Assert-True (($script:calls[-1].Arguments -join ' ') -match '^rm --force yumayo-ai-share-') 'Release shares on inspection failure.'

    Reset-Fixture
    $script:failCarrierStart = $true
    Assert-Throws { Start-AiWslContainer -Wslc 'wslc.exe' -Repository $temporary -Workspace $temporary } 'Share start failed'
    Assert-True (($script:calls[-1].Arguments -join ' ') -match '^rm --force yumayo-ai-share-') 'Release a carrier whose start failed.'

    & {
        Reset-Fixture
        function Test-InteractiveFailure { $global:LASTEXITCODE = 23 }
        function Read-AiWslCommand {
            param([string] $Wslc, [string[]] $Arguments)
            $script:calls.Add([pscustomobject] @{ Kind = 'diagnostic'; Arguments = $Arguments })
            $global:LASTEXITCODE = 0
            return '{"Status":"created","ExitCode":0,"Error":""}'
        }
        & $originalInteractive 'Test-InteractiveFailure' 'test-launcher'
        Assert-True ($script:AiWslExitCode -eq 23) 'Successful diagnostics must not replace the failed attachment exit code.'
        Assert-True ($script:calls.Count -eq 1 -and $script:calls[0].Arguments -contains '{{json .State}}') 'Inspect only runtime state after attachment failure.'

        function Read-AiWslCommand {
            param([string] $Wslc, [string[]] $Arguments)
            $global:LASTEXITCODE = 1
            throw 'Engine unavailable'
        }
        & $originalInteractive 'Test-InteractiveFailure' 'test-launcher'
        Assert-True ($script:AiWslExitCode -eq 23) 'Failed diagnostics must preserve the attachment exit code and allow cleanup.'
    }

    # Check real native exit handling using a local PowerShell child, not WSL.
    $shell = (Get-Process -Id $PID).Path
    $child = Join-Path $temporary 'native command.ps1'
    [IO.File]::WriteAllText($child, "Write-Output 'WSL: 3.0.1.0'`nexit 0`n")
    # The version probe adds --version, so use a function with native exit semantics.
    function Test-VersionOutput {
        param([string] $Unused)
        $global:LASTEXITCODE = 0
        "WSL: 3.0.1.0`nKernel: 6.18.0"
    }
    $version = & $originalProbe 'Test-VersionOutput'
    Assert-True ($version -eq [version] '3.0.1.0') 'Read WSL version rather than kernel version.'
    function Test-LocalizedVersionOutput {
        param([string] $Unused)
        $global:LASTEXITCODE = 0
        # Japanese "version", keeping this source ASCII for Windows PowerShell 5.1.
        $label = -join (0x30D0, 0x30FC, 0x30B8, 0x30E7, 0x30F3 | ForEach-Object { [char] $_ })
        $text = "WSL ${label}: 3.0.1.0`r`nKernel: 6.18.40.1-1`r`n"
        # WSL's redirected UTF-16 text can arrive with embedded NUL characters.
        return ($text.ToCharArray() -join [string][char]0)
    }
    Assert-True ((& $originalProbe 'Test-LocalizedVersionOutput') -eq [version] '3.0.1.0') 'Read localized output containing NULs.'
    function Test-UnreadableVersionOutput {
        param([string] $Unused)
        $global:LASTEXITCODE = 0
        'Unexpected version output'
    }
    Assert-Throws { & $originalProbe 'Test-UnreadableVersionOutput' } 'Could not read the WSL version'
    function Test-VersionFailure {
        param([string] $Unused)
        $global:LASTEXITCODE = 1
        'Kernel: 6.18.0'
    }
    Assert-True ($null -eq (& $originalProbe 'Test-VersionFailure')) 'Failed version probes must not return the kernel version.'
    & $originalCommand $shell @('-NoProfile', '-File', $child)
    [IO.File]::WriteAllText($child, "exit 17`n")
    Assert-Throws { & $originalCommand $shell @('-NoProfile', '-File', $child) } 'exit 17'

    Write-Host "Passed $script:checks assertions."
} finally {
    $env:SystemRoot = $originalSystemRoot
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
