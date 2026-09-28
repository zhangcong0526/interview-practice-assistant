param(
    [switch]$Repair
)

$ErrorActionPreference = "Stop"
$Port = 5173

function Test-FrontendPort {
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        return $true
    }

    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $Port
    )
    try {
        $listener.Start()
        $listener.Stop()
        return $true
    }
    catch {
        $exception = $_.Exception
        while ($exception.InnerException) {
            $exception = $exception.InnerException
        }
        if (
            $exception -is [System.Net.Sockets.SocketException] -and
            $exception.SocketErrorCode -eq [System.Net.Sockets.SocketError]::AddressAlreadyInUse
        ) {
            return $true
        }
        $message = $exception.Message
        if (-not $Repair) {
            Write-Host "FRONTEND_PORT_CHECK_FAILED: $message"
        }
        return $false
    }
}

function Repair-FrontendPort {
    $service = Get-Service -Name winnat -ErrorAction SilentlyContinue
    $wasRunning = $service -and $service.Status -eq "Running"

    if ($wasRunning) {
        net stop winnat | Out-Null
    }

    try {
        $exclusions = netsh interface ipv4 show excludedportrange protocol=tcp
        $alreadyReserved = $exclusions -match "^\s*$Port\s+$Port\s*$"
        if (-not $alreadyReserved) {
            netsh interface ipv4 add excludedportrange protocol=tcp startport=$Port numberofports=1 store=persistent | Out-Null
        }
    }
    finally {
        if ($wasRunning) {
            net start winnat | Out-Null
        }
    }
}

if (Test-FrontendPort) {
    exit 0
}

if ($Repair) {
    Repair-FrontendPort
    if (Test-FrontendPort) {
        exit 0
    }
    exit 1
}

try {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-Repair"
    )
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    if ($null -eq $exitCode) {
        $exitCode = 1
    }
    exit [int]$exitCode
}
catch {
    Write-Host "FRONTEND_PORT_REPAIR_CANCELLED: $($_.Exception.Message)"
    exit 2
}
