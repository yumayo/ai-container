# Shared by the Windows installer and its offline tests. PowerShell 5.1+.
Set-StrictMode -Version Latest

function Assert-AiWslPlatform {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [Environment]::OSVersion.Version.Build -lt 22000) {
        throw 'Run this script from PowerShell on Windows 11 (not from inside WSL).'
    }
    if (-not [Environment]::Is64BitProcess) {
        throw 'Use 64-bit PowerShell so that WSL executables are not redirected to SysWOW64.'
    }
}

function Find-AiWslExecutable {
    param([Parameter(Mandatory = $true)][string] $Name)

    # Use the same executable as the calling terminal before trying fixed paths.
    # Get-Command can return multiple applications (e.g. System32 and WindowsApps).
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) { return $command.Source }

    # An installation can finish before the current terminal's PATH is refreshed.
    $candidates = @(
        (Join-Path $env:ProgramFiles "WSL\$Name"),
        (Join-Path $env:SystemRoot "System32\$Name")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

function Get-AiWslVersion {
    param([Parameter(Mandatory = $true)][string] $Wsl)

    # Windows PowerShell represents native stderr as error records. A failed
    # version probe is expected for the inbox stub on a machine without WSL.
    $ErrorActionPreference = 'Continue'
    $PSNativeCommandUseErrorActionPreference = $false
    $output = & $Wsl --version 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WSL version probe failed (exit $LASTEXITCODE): $Wsl --version"
        return $null
    }
    $versionText = ($output -join "`n").Replace([string][char]0, '')
    # The first version is WSL itself; subsequent lines describe the kernel etc.
    if ($versionText -match '(?m)^.*?(\d+\.\d+\.\d+(?:\.\d+)?).*?$') {
        return [version] $Matches[1]
    }
    # Successful but unreadable output is a detection error, not proof that WSL
    # is missing. Never start an elevated installation just because parsing failed.
    throw "Could not read the WSL version from: $Wsl --version. Output: $versionText"
}

function Invoke-AiWslElevatedCommand {
    param(
        [Parameter(Mandatory = $true)][string] $Executable,
        [Parameter(Mandatory = $true)][string[]] $Arguments
    )

    # Only Windows feature setup and WSL installation/update are elevated.
    # Builds stay in the calling user's session: elevated wslc has another store.
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } finally {
        $identity.Dispose()
    }
    $options = @{
        FilePath = $Executable
        ArgumentList = $Arguments
        Wait = $true
        PassThru = $true
    }
    if (-not $isAdmin) { $options.Verb = 'RunAs' }
    # Arguments are setup switches, never user-supplied command text.
    $process = Start-Process @options
    return $process.ExitCode
}

function Invoke-AiWslSetupCommand {
    param(
        [Parameter(Mandatory = $true)][string] $Wsl,
        [Parameter(Mandatory = $true)][string[]] $Arguments
    )
    return Invoke-AiWslElevatedCommand -Executable $Wsl -Arguments $Arguments
}

function Enable-AiWslVirtualMachinePlatform {
    # Installing the WSL application and enabling the Windows feature are
    # separate operations. Do not reinstall an already detected WSL package.
    $dism = Join-Path $env:SystemRoot 'System32\dism.exe'
    $log = Join-Path ([IO.Path]::GetTempPath()) ('aicontainer-vmp-' + [guid]::NewGuid().ToString('N') + '.log')
    Write-Host 'Enabling VirtualMachinePlatform with DISM. Windows may request administrator access.'
    Write-Host "DISM log: $log"
    # Start-Process joins ArgumentList; quote the log path for spaces in TEMP.
    $arguments = @('/Online', '/Enable-Feature', '/FeatureName:VirtualMachinePlatform', '/All', '/NoRestart',
        "/LogPath:`"$log`"")
    $code = Invoke-AiWslElevatedCommand -Executable $dism -Arguments $arguments
    if ($code -notin @(0, 3010)) {
        $hint = 'See docs/WSL.md for diagnostics.'
        if ($code -eq 12006) {
            $cbsLog = Join-Path $env:SystemRoot 'Logs\CBS\CBS.log'
            $hint = "Windows servicing reported 0x80072EE6 (unrecognized URL scheme). If testing installation inside Windows Sandbox, use a Windows VM with nested virtualization and a pre-install checkpoint. On a regular Windows installation, check Windows Update/WSUS and optional-feature source settings. CBS log: $cbsLog. See docs/WSL.md for test and diagnostic instructions."
        }
        throw "Enabling VirtualMachinePlatform failed (exit $code). DISM log: $log. $hint"
    }
}

function Get-AiWslVirtualMachineState {
    $feature = Get-CimInstance Win32_OptionalFeature -Filter "Name='VirtualMachinePlatform'" -ErrorAction Stop
    if (-not $feature) { throw 'VirtualMachinePlatform is unavailable on this Windows installation.' }
    return $feature.InstallState
}

function Initialize-AiWsl {
    param([switch] $WebDownload)

    $wsl = Find-AiWslExecutable 'wsl.exe'
    if (-not $wsl) {
        throw 'wsl.exe was not found. Update Windows 11 before running this installer.'
    }
    Write-Host "Using WSL executable: $wsl"
    $version = Get-AiWslVersion $wsl
    if ($version) { Write-Host "Detected WSL version: $version" }
    $featureState = Get-AiWslVirtualMachineState
    Write-Host "VirtualMachinePlatform state: $featureState (1=enabled, 2=disabled, 3=absent, 4=unknown)"
    if ($featureState -notin @(1, 2, 3)) {
        throw 'Could not determine whether VirtualMachinePlatform is enabled. WSL setup was not started.'
    }
    if (-not $version) {
        Write-Host 'Installing WSL without an Ubuntu distribution. Windows may request administrator access.'
        $arguments = @('--install', '--no-distribution')
        if ($WebDownload) { $arguments += '--web-download' }
        $code = Invoke-AiWslSetupCommand $wsl $arguments
        if ($code -notin @(0, 3010, 1641)) {
            throw "WSL installation failed (exit $code). See the WSL installer output."
        }
        # A first install can return zero even when a reboot is required.
        Write-Host 'Restart Windows, then run the same command again. No automatic reboot will occur.'
        return [pscustomobject] @{ ExitCode = 3010; Wslc = $null }
    }

    if ($featureState -ne 1) {
        Enable-AiWslVirtualMachinePlatform
        # DISM may return zero before a reboot. Do not build or start containers
        # in the same invocation that enabled the virtualization platform.
        Write-Host 'Restart Windows, then run the same command again. No automatic reboot will occur.'
        return [pscustomobject] @{ ExitCode = 3010; Wslc = $null }
    }

    $wslc = Find-AiWslExecutable 'wslc.exe'
    # 2.9.3 introduced the preview; use the generally available release or newer.
    if ($version -lt [version] '3.0.1' -or -not $wslc) {
        Write-Host 'Updating WSL to a stable release with WSL Containers.'
        $arguments = @('--update')
        if ($WebDownload) { $arguments += '--web-download' }
        $code = Invoke-AiWslSetupCommand $wsl $arguments
        if ($code -in @(3010, 1641)) {
            Write-Host 'Restart Windows, then run the same command again.'
            return [pscustomobject] @{ ExitCode = 3010; Wslc = $null }
        }
        if ($code -ne 0) { throw "WSL update failed (exit $code). See the WSL installer output." }
        $wsl = Find-AiWslExecutable 'wsl.exe'
        $version = Get-AiWslVersion $wsl
        $wslc = Find-AiWslExecutable 'wslc.exe'
        if (-not $version -or $version -lt [version] '3.0.1' -or -not $wslc) {
            throw 'WSL 3.0.1+ and wslc.exe are required. Restart Windows and retry; check wsl --version if this persists.'
        }
    }
    Invoke-AiWslCommand $wslc @('version')
    return [pscustomobject] @{ ExitCode = 0; Wslc = $wslc }
}

function Invoke-AiWslCommand {
    param(
        [Parameter(Mandatory = $true)][string] $Executable,
        [Parameter(Mandatory = $true)][string[]] $Arguments
    )
    $PSNativeCommandUseErrorActionPreference = $false
    & $Executable @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "wslc $($Arguments[0]) failed (exit $LASTEXITCODE)."
    }
}

function Build-AiWslImages {
    param(
        [Parameter(Mandatory = $true)][string] $Wslc,
        [Parameter(Mandatory = $true)][string] $Repository,
        [ValidateSet('all', 'base', 'main', 'proxy', 'rebuild')][string] $Target = 'all'
    )

    $context = Join-Path $Repository 'docker/aicontainer'
    # COPY and Dockerfile heredocs require LF, including on Git for Windows.
    # Reject a stale CRLF checkout without silently rewriting source files.
    $linuxFiles = @('Dockerfile.base', 'Dockerfile', 'entrypoint.sh', 'init-firewall.sh', 'ai-cli',
        '../docker-proxy/Dockerfile', '../docker-proxy/entrypoint.sh')
    foreach ($name in $linuxFiles) {
        $file = Join-Path $context $name
        if ([IO.File]::ReadAllText($file).Contains("`r")) {
            throw "CRLF found in $file. Restore this file with LF line endings and retry (see docs/WSL.md)."
        }
    }
    Push-Location -LiteralPath $context
    try {
        if ($Target -in @('all', 'base', 'rebuild')) {
            Write-Host "Building yumayo-ai-base from Dockerfile.base..."
            $arguments = @('build', '-t', 'yumayo-ai-base', '-f', 'Dockerfile.base')
            if ($Target -eq 'rebuild') { $arguments += '--no-cache' }
            Invoke-AiWslCommand $Wslc ($arguments + '.')
        }
        if ($Target -in @('all', 'main', 'rebuild')) {
            Write-Host "Building yumayo-ai from Dockerfile..."
            Invoke-AiWslCommand $Wslc @('build', '--no-cache', '-t', 'yumayo-ai', '-f', 'Dockerfile', '.')
        }
        if ($Target -in @('all', 'proxy', 'rebuild')) {
            Set-Location -LiteralPath (Join-Path $Repository 'docker/docker-proxy')
            Write-Host 'Building yumayo-ai-proxy from Dockerfile...'
            Invoke-AiWslCommand $Wslc @('build', '--no-cache', '-t', 'yumayo-ai-proxy', '-f', 'Dockerfile', '.')
        }
    } finally {
        Pop-Location
    }
}

function Read-AiWslCommand {
    param([string] $Wslc, [string[]] $Arguments)
    $PSNativeCommandUseErrorActionPreference = $false
    $output = & $Wslc @Arguments
    if ($LASTEXITCODE -ne 0) { throw "wslc $($Arguments[0]) failed (exit $LASTEXITCODE)." }
    return ($output -join "`n")
}

function Test-AiWslImages {
    param([string] $Wslc)
    # First distinguish a broken engine from missing images.
    Invoke-AiWslCommand $Wslc @('system', 'session', 'run', 'docker', 'info')
    $PSNativeCommandUseErrorActionPreference = $false
    $ErrorActionPreference = 'Continue'
    & $Wslc system session run docker image inspect yumayo-ai-base yumayo-ai yumayo-ai-proxy 1>$null 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Get-AiWslMountSource {
    param([object[]] $Mounts, [string] $Destination)
    $mountEntries = @($Mounts | Where-Object { $_.Destination -eq $Destination -and $_.Type -eq 'bind' })
    if ($mountEntries.Count -ne 1 -or $mountEntries[0].Source -notmatch '^/mnt/[a-zA-Z0-9_{}./-]+$' -or
        $mountEntries[0].Source -match '(^|/)\.\.(/|$)') {
        throw "WSL Containers did not expose the expected VM bind source for $Destination. See docs/WSL.md."
    }
    # Do not guess /mnt/c or pass a Windows path to the VM's Docker daemon.
    return $mountEntries[0].Source
}

function Get-AiWslVolumeMountSource {
    param([object[]] $Mounts, [string] $Destination)
    # A WSL volume may be presented to Docker as a bind or a volume. Its engine
    # name and backing path need not match the name registered with wslc.
    $mountEntries = @($Mounts | Where-Object {
        $_.Destination -eq $Destination -and $_.Type -in @('bind', 'volume')
    })
    if ($mountEntries.Count -ne 1 -or $mountEntries[0].Source -notmatch '^/[a-zA-Z0-9_{}./-]+$' -or
        $mountEntries[0].Source -match '(^|/)\.\.(/|$)') {
        throw "Could not determine the WSL volume backing path for $Destination."
    }
    return $mountEntries[0].Source
}

function Invoke-AiWslInteractive {
    param([string] $Wslc, [string] $Name)
    $PSNativeCommandUseErrorActionPreference = $false
    # session run provides pipes, not a PTY. Use the native container terminal
    # for the runner created through wslc. Do not capture/pipe this output.
    Write-Host "Starting interactive launcher: $Name"
    & $Wslc start --attach --interactive $Name
    $script:AiWslExitCode = $LASTEXITCODE
    if ($script:AiWslExitCode -ne 0) {
        Write-Warning "wslc start --attach --interactive $Name exited with code $script:AiWslExitCode."
        try {
            # Capture only runtime state, before cleanup removes the container.
            # Full inspect output can contain credentials in environment variables.
            $state = Read-AiWslCommand $Wslc @(
                'system', 'session', 'run', 'docker', 'inspect', '--format', '{{json .State}}', $Name
            )
            Write-Host "Launcher state before cleanup: $state"
        } catch {
            Write-Warning "Could not read launcher state: $_"
        }
    }
}

function Start-AiWslContainer {
    param(
        [Parameter(Mandatory = $true)][string] $Wslc,
        [Parameter(Mandatory = $true)][string] $Repository,
        [Parameter(Mandatory = $true)][string] $Workspace,
        [ValidateSet('', 'claude', 'codex', 'bash')][string] $Tool = ''
    )

    $workspaceItem = Get-Item -LiteralPath $Workspace -ErrorAction Stop
    if (-not $workspaceItem.PSIsContainer -or $workspaceItem.PSProvider.Name -ne 'FileSystem') {
        throw 'Workspace must be an existing filesystem directory.'
    }
    $Workspace = $workspaceItem.FullName
    $Repository = (Get-Item -LiteralPath $Repository -ErrorAction Stop).FullName
    foreach ($file in @('.bash_ai_container', 'wsl-containers/wsl-launch.sh', 'wsl-containers/wsl-docker-relay/run-relay.sh')) {
        if ([IO.File]::ReadAllText((Join-Path $Repository $file)).Contains("`r")) {
            throw "CRLF found in $file. Restore LF line endings before starting."
        }
    }

    $runId = [guid]::NewGuid().ToString('N')
    $carrier = "yumayo-ai-share-$runId"
    $runner = "yumayo-ai-launch-$runId"
    $relay = "yumayo-ai-relay-$runId"
    $runtimeVolume = "yumayo-ai-tmp-$runId"
    $engineVolume = "yumayo-ai-engine-$runId"
    $runtimeDestination = "/aicontainer/runtime-$runId"
    $engineDestination = "/aicontainer/engine-$runId"
    $owner = "io.yumayo.aicontainer.wsl-run=$runId"
    $docker = @('system', 'session', 'run', 'docker')
    $carrierCreated = $false
    $runnerCreated = $false
    $relayCreated = $false
    $createdVolumes = New-Object 'System.Collections.Generic.List[string]'
    $script:AiWslExitCode = 1
    try {
        # Prove that the bundled engine accepts the required capabilities.
        # This isolated probe has no workspace or credentials mounted.
        Invoke-AiWslCommand $Wslc ($docker + @(
            'run', '--rm', '--network', 'none', '--user', '0',
            '--cap-add=NET_ADMIN', '--cap-add=NET_RAW', '--entrypoint', '/bin/sh',
            'yumayo-ai', '-c', 'iptables -S && ip6tables -S && ipset list'
        ))

        # Register with WSL before using the volume in a native container.
        # guest does not accept Docker local-driver options (type/o/device).
        foreach ($volume in @($runtimeVolume, $engineVolume)) {
            Invoke-AiWslCommand $Wslc @('volume', 'create', '--driver', 'guest', '--label', $owner, $volume)
            $createdVolumes.Add($volume)
        }

        # Keep WSL's Windows/virtiofs shares alive while the nested launcher runs.
        # This container runs only sleep; it has no Docker socket or AI process.
        Invoke-AiWslCommand $Wslc @(
            'create', '--name', $carrier, '--network', 'none',
            '--volume', "${Workspace}:/aicontainer/workspace",
            '--volume', "${Repository}:/aicontainer/repository:ro",
            '--mount', "type=volume,source=$runtimeVolume,target=$runtimeDestination",
            '--mount', "type=volume,source=$engineVolume,target=$engineDestination",
            '--entrypoint', '/bin/sleep', 'yumayo-ai-base', 'infinity'
        )
        $carrierCreated = $true
        Invoke-AiWslCommand $Wslc @('start', $carrier)
        $inspection = @(ConvertFrom-Json (Read-AiWslCommand $Wslc ($docker + @('inspect', $carrier))))
        $workspaceSource = Get-AiWslMountSource $inspection[0].Mounts '/aicontainer/workspace'
        $repositorySource = Get-AiWslMountSource $inspection[0].Mounts '/aicontainer/repository'
        $runtimeSource = Get-AiWslVolumeMountSource $inspection[0].Mounts $runtimeDestination
        $engineSource = Get-AiWslVolumeMountSource $inspection[0].Mounts $engineDestination
        $socketGroup = (Read-AiWslCommand $Wslc @('system', 'session', 'run', 'stat', '-c', '%g', '/var/run/docker.sock')).Trim()
        if ($socketGroup -notmatch '^\d+$') { throw 'Could not determine the bundled Docker socket group.' }
        $launcherUser = (Read-AiWslCommand $Wslc ($docker + @(
            'run', '--rm', '--network', 'none', '--entrypoint', 'id', 'yumayo-ai-base', '-u', 'ubuntu'
        ))).Trim()
        if ($launcherUser -notmatch '^\d+$') { throw 'Could not determine the launcher user ID.' }

        # WSL has now mounted the runtime volume. Use its observed VM bind path
        # with Docker; do not look up the WSL volume name in Docker's registry.
        # Initialize ownership without exposing the workspace or engine socket.
        Invoke-AiWslCommand $Wslc ($docker + @(
            'run', '--rm', '--network', 'none', '--user', '0',
            '--mount', "type=bind,source=$runtimeSource,target=/aicontainer-runtime",
            '--mount', "type=bind,source=$engineSource,target=/aicontainer-engine",
            '--entrypoint', 'install', 'yumayo-ai-base',
            '-d', '-m', '0700', '-o', $launcherUser, '-g', $socketGroup, '/aicontainer-runtime', '/aicontainer-engine'
        ))

        # Only this trusted relay sees the raw engine socket. It forwards bytes
        # over a private Unix socket, including Docker's interactive streams.
        # Node is already installed in the base image; no rebuild is needed.
        # run-relay.sh loads Node, then execs docker-socket-relay.cjs.
        Invoke-AiWslCommand $Wslc ($docker + @(
            'create', '--name', $relay, '--network', 'none', '--user', "${launcherUser}:$socketGroup",
            '--label', "io.yumayo.aicontainer.wsl-relay=$runId",
            '--mount', "type=bind,source=$engineSource,target=/aicontainer-engine",
            '--mount', 'type=bind,source=/var/run/docker.sock,target=/aicontainer-upstream/docker.sock,readonly',
            '--mount', "type=bind,source=$repositorySource/wsl-containers/wsl-docker-relay,target=/aicontainer-relay,readonly",
            'yumayo-ai-base', 'bash', '/aicontainer-relay/run-relay.sh',
            'serve', '/aicontainer-engine/docker.sock', '/aicontainer-upstream/docker.sock'
        ))
        $relayCreated = $true
        Invoke-AiWslCommand $Wslc ($docker + @('start', $relay))
        try {
            # Probe the Docker API through the relay, not just socket existence.
            Invoke-AiWslCommand $Wslc ($docker + @(
                'exec', $relay, 'bash', '/aicontainer-relay/run-relay.sh',
                'check', '/aicontainer-engine/docker.sock'
            ))
        } catch {
            try { Invoke-AiWslCommand $Wslc ($docker + @('logs', '--tail', '20', $relay)) }
            catch { Write-Warning "Could not read Docker relay logs: $_" }
            throw 'The private Docker relay is not ready. See the relay output above.'
        }

        # Share Windows folders again using native Windows bind sources, but
        # keep their destinations identical to the paths observed by Docker.
        # The carrier keeps the original VM shares alive for Docker's AI mounts.
        # The unique target is absent in the image, so wslc's default volume
        # population cannot copy image files into this runtime volume.
        $mountArguments = @(
            '--mount', "type=volume,source=$runtimeVolume,target=$runtimeSource",
            '--mount', "type=volume,source=$engineVolume,target=/aicontainer-engine,readonly",
            '--volume', "${Workspace}:$workspaceSource"
        )
        if ($repositorySource -ne $workspaceSource) {
            $mountArguments += @('--volume', "${Repository}:${repositorySource}:ro")
        }

        # Only this trusted launcher receives the relay socket. The AI container
        # is created by .bash_ai_container with its existing restrictions.
        # Use wslc for both creation and attachment so it owns the lifecycle.
        # wslc has no --group-add; keep ubuntu's UID and use the socket's GID.
        # A distinct launcher label keeps Docker-only child cleanup away from it.
        $arguments = @(
            'create', '--name', $runner, '--interactive', '--tty', '--network', 'none',
            '--user', "${launcherUser}:$socketGroup", '--label', "io.yumayo.aicontainer.wsl-launch=$runId",
            '--env', 'DOCKER_HOST=unix:///aicontainer-engine/docker.sock', '--env', 'TERM=xterm-256color',
            '--env', "AICONTAINER_RUNTIME_ROOT=$runtimeSource"
        ) + $mountArguments
        $arguments += @('yumayo-ai-base', 'bash', "$repositorySource/wsl-containers/wsl-launch.sh",
            $repositorySource, $workspaceSource, $runId)
        if ($Tool) { $arguments += $Tool }
        Invoke-AiWslCommand $Wslc $arguments
        $runnerCreated = $true
        Invoke-AiWslInteractive $Wslc $runner
    } finally {
        # Stop the trusted launcher first so it cannot create another child
        # between querying and removing this invocation's AI containers.
        if ($runnerCreated) {
            try { Invoke-AiWslCommand $Wslc @('rm', '--force', $runner) }
            catch { Write-Warning "Could not remove launcher ${runner}: $_" }
        }
        if ($relayCreated) {
            try { Invoke-AiWslCommand $Wslc ($docker + @('rm', '--force', $relay)) }
            catch { Write-Warning "Could not remove Docker relay ${relay}: $_" }
        }
        try {
            $children = Read-AiWslCommand $Wslc ($docker + @('ps', '-aq', '--filter', "label=$owner"))
            foreach ($id in ($children -split '\s+')) {
                if ($id -match '^[0-9a-f]{12,64}$') {
                    Invoke-AiWslCommand $Wslc ($docker + @('rm', '--force', $id))
                }
            }
            $networks = Read-AiWslCommand $Wslc ($docker + @('network', 'ls', '-q', '--filter', "label=$owner"))
            foreach ($id in ($networks -split '\s+')) {
                if ($id -match '^[0-9a-f]{12,64}$') {
                    Invoke-AiWslCommand $Wslc ($docker + @('network', 'rm', $id))
                }
            }
        } catch { Write-Warning "Could not clean up AI resources for ${runId}: $_" }
        if ($carrierCreated) {
            try { Invoke-AiWslCommand $Wslc @('rm', '--force', $carrier) }
            catch { Write-Warning "Could not release Windows shares ${carrier}: $_" }
        }
        # Both native containers must release their references before WSL can
        # remove the volumes. --force ignores missing names, not active users.
        foreach ($volume in $createdVolumes) {
            try { Invoke-AiWslCommand $Wslc @('volume', 'remove', '--force', $volume) }
            catch { Write-Warning "Could not remove launcher volume ${volume}: $_" }
        }
    }
}
