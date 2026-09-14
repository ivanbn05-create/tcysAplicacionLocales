param(
    [ValidateRange(0, 65535)][int]$Port = 0,
    [string]$HealthHost,
    [switch]$RunBackup,
    [ValidateRange(30, 3600)][int]$BackupTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$envPath = Join-Path $root ".env"
$python = Join-Path $root ".venv\Scripts\python.exe"
$serviceHost = Join-Path $root ".venv\Scripts\pythonservice.exe"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ejecuta este verificador desde PowerShell como administrador."
}

function Get-DotEnvValue {
    param([string]$Name, [AllowEmptyString()][string]$Default = "")
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw "Falta .env en esta instalación." }
    $pattern = "(?i)^\s*" + [Regex]::Escape($Name) + "\s*=(.*)$"
    foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
        if ($line -match $pattern) { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $Default
}

function Test-DotEnvKey {
    param([string]$Name)
    $pattern = "(?i)^\s*" + [Regex]::Escape($Name) + "\s*="
    return @((Get-Content -LiteralPath $envPath -Encoding UTF8) | Where-Object { $_ -match $pattern }).Count -eq 1
}

function Get-RequiredDotEnvValue {
    param([string]$Name)
    if (-not (Test-DotEnvKey -Name $Name)) { throw "$Name debe aparecer exactamente una vez en .env." }
    $value = Get-DotEnvValue -Name $Name
    if ([string]::IsNullOrWhiteSpace($value)) { throw "$Name no puede estar vacío en .env." }
    return $value
}

function ConvertFrom-DotEnvInteger {
    param([string]$Name, [string]$Value, [int]$Minimum, [int]$Maximum)
    $number = 0
    if (-not [int]::TryParse($Value, [ref]$number) -or $number -lt $Minimum -or $number -gt $Maximum) {
        throw "$Name debe ser un entero entre $Minimum y $Maximum."
    }
    return $number
}

function ConvertFrom-DotEnvBoolean {
    param([string]$Name, [string]$Value)
    if ($Value -match '^(?i:1|true|si|sí|yes)$') { return $true }
    if ($Value -match '^(?i:0|false|no)$') { return $false }
    throw "$Name no contiene un booleano válido en .env."
}

function Resolve-ProjectPath {
    param([string]$Value)
    $resolved = if ([IO.Path]::IsPathRooted($Value)) {
        [IO.Path]::GetFullPath($Value)
    } else { [IO.Path]::GetFullPath((Join-Path $root $Value)) }
    if (-not $resolved.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "La ruta configurada escapa de esta instalación."
    }
    return $resolved
}

function Get-ExecutableFromCommandLine {
    param([string]$CommandLine)
    $value = ([string]$CommandLine).Trim()
    if ($value.StartsWith('"')) {
        if ($value -notmatch '^"([^\"]+)"(?:\s|$)') { throw "PathName del servicio no es válido." }
        return [IO.Path]::GetFullPath($Matches[1])
    }
    $executable = ($value -split '\s+', 2)[0]
    if ([string]::IsNullOrWhiteSpace($executable)) { throw "PathName del servicio está vacío." }
    return [IO.Path]::GetFullPath($executable)
}

function Test-SamePath {
    param([string]$First, [string]$Second)
    if ([string]::IsNullOrWhiteSpace($First) -or [string]::IsNullOrWhiteSpace($Second)) { return $false }
    return [IO.Path]::GetFullPath($First).TrimEnd('\').Equals(
        [IO.Path]::GetFullPath($Second).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase
    )
}

function Test-ContainsOrdinalIgnoreCase {
    param([string]$Value, [string]$Expected)
    return ([string]$Value).IndexOf(
        $Expected, [StringComparison]::OrdinalIgnoreCase
    ) -ge 0
}

function Test-AllowedHost {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -ne $Value.Trim() -or
        $Value -match "\s|\*|/|\\|://") {
        return $false
    }
    if ($Value.StartsWith("[") -and $Value.EndsWith("]")) {
        $address = $null
        return [Net.IPAddress]::TryParse($Value.Substring(1, $Value.Length - 2), [ref]$address) -and
            $address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6
    }
    if ($Value.Contains(":")) { return $false }
    $ip = $null
    if ([Net.IPAddress]::TryParse($Value, [ref]$ip)) { return $true }
    return $Value -match "^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
}

foreach ($required in @($envPath, $python, $serviceHost)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Falta un archivo obligatorio: $required" }
}
$requiredEnvironmentKeys = @(
    "SUCURSAL_CLAVE", "DJANGO_SECRET_KEY", "DJANGO_DEBUG", "DJANGO_ALLOWED_HOSTS",
    "DJANGO_HTTPS", "ALLOW_INSECURE_HTTP_LAN", "WAITRESS_HOST", "WAITRESS_PORT",
    "WAITRESS_THREADS", "WAITRESS_CONNECTION_LIMIT", "WAITRESS_CHANNEL_TIMEOUT",
    "WAITRESS_CLEANUP_INTERVAL", "WAITRESS_TRUSTED_PROXY"
)
foreach ($requiredKey in $requiredEnvironmentKeys) {
    if (-not (Test-DotEnvKey -Name $requiredKey)) { throw "$requiredKey debe aparecer exactamente una vez en .env." }
}
$optionalEnvironmentKeys = @(
    "DB_ENGINE", "SQLITE_PATH", "PRINT_BACKEND", "PRINT_SYNC",
    "PRINTER_CAJA_HOST", "PRINTER_COCINA_HOST", "PRINTER_BARRA_HOST",
    "PRINTER_PORT"
)
foreach ($optionalKey in $optionalEnvironmentKeys) {
    $optionalPattern = "(?i)^\s*" + [Regex]::Escape($optionalKey) + "\s*="
    if (@((Get-Content -LiteralPath $envPath -Encoding UTF8) |
        Where-Object { $_ -match $optionalPattern }).Count -gt 1) {
        throw "$optionalKey no puede aparecer más de una vez en .env."
    }
}
$branchKey = Get-RequiredDotEnvValue -Name "SUCURSAL_CLAVE"
if ($branchKey -notmatch '^[A-Z0-9](?:[A-Z0-9_-]{0,28}[A-Z0-9])?$') { throw "SUCURSAL_CLAVE no tiene el formato canónico esperado." }
$secretKey = Get-RequiredDotEnvValue -Name "DJANGO_SECRET_KEY"
if ($secretKey.Length -lt 50 -or @($secretKey.ToCharArray() | Sort-Object -Unique).Count -lt 5 -or
    $secretKey.ToLowerInvariant().StartsWith("cambiar") -or
    $secretKey.ToLowerInvariant().StartsWith("solo-desarrollo") -or
    $secretKey.ToLowerInvariant().StartsWith("clave-exclusiva-de-prueba") -or
    $secretKey.ToLowerInvariant().StartsWith("django-insecure-")) {
    throw "DJANGO_SECRET_KEY no cumple el contrato de secreto de producción."
}
$debug = ConvertFrom-DotEnvBoolean -Name "DJANGO_DEBUG" -Value (Get-RequiredDotEnvValue -Name "DJANGO_DEBUG")
if ($debug) { throw "DJANGO_DEBUG debe ser false en producción." }
$dbEngine = (Get-DotEnvValue -Name "DB_ENGINE" -Default "sqlite").Trim().ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($dbEngine)) { $dbEngine = "sqlite" }
if ($dbEngine -ne "sqlite") {
    throw "El despliegue Windows verificable sólo admite DB_ENGINE=sqlite; PostgreSQL queda reservado para Docker/VPS."
}
$https = ConvertFrom-DotEnvBoolean -Name "DJANGO_HTTPS" -Value (Get-RequiredDotEnvValue -Name "DJANGO_HTTPS")
$allowInsecureHttpLan = ConvertFrom-DotEnvBoolean -Name "ALLOW_INSECURE_HTTP_LAN" -Value (Get-RequiredDotEnvValue -Name "ALLOW_INSECURE_HTTP_LAN")
$listenAddress = Get-RequiredDotEnvValue -Name "WAITRESS_HOST"
$listenIp = $null
if (-not [Net.IPAddress]::TryParse($listenAddress, [ref]$listenIp)) { throw "WAITRESS_HOST no es una dirección IP válida." }
if ($listenIp.IsMulticast) { throw "WAITRESS_HOST no puede ser una dirección multicast." }
if ($Port -eq 0) {
    $Port = ConvertFrom-DotEnvInteger -Name "WAITRESS_PORT" -Value (Get-RequiredDotEnvValue -Name "WAITRESS_PORT") -Minimum 1 -Maximum 65535
}
$waitressThreads = ConvertFrom-DotEnvInteger -Name "WAITRESS_THREADS" -Value (Get-RequiredDotEnvValue -Name "WAITRESS_THREADS") -Minimum 2 -Maximum 64
$waitressConnectionLimit = ConvertFrom-DotEnvInteger -Name "WAITRESS_CONNECTION_LIMIT" -Value (Get-RequiredDotEnvValue -Name "WAITRESS_CONNECTION_LIMIT") -Minimum 10 -Maximum 500
$waitressChannelTimeout = ConvertFrom-DotEnvInteger -Name "WAITRESS_CHANNEL_TIMEOUT" -Value (Get-RequiredDotEnvValue -Name "WAITRESS_CHANNEL_TIMEOUT") -Minimum 5 -Maximum 300
$waitressCleanupInterval = ConvertFrom-DotEnvInteger -Name "WAITRESS_CLEANUP_INTERVAL" -Value (Get-RequiredDotEnvValue -Name "WAITRESS_CLEANUP_INTERVAL") -Minimum 1 -Maximum 60
$allowedHosts = @((Get-RequiredDotEnvValue -Name "DJANGO_ALLOWED_HOSTS") -split ',')
if (-not $allowedHosts.Count -or @($allowedHosts | Where-Object { -not (Test-AllowedHost -Value $_) }).Count) {
    throw "DJANGO_ALLOWED_HOSTS contiene un host no válido."
}
$printBackend = if (Test-DotEnvKey -Name "PRINT_BACKEND") {
    (Get-DotEnvValue -Name "PRINT_BACKEND").Trim().ToLowerInvariant()
}
else { "archivo" }
if ($printBackend -notin @("archivo", "tcp")) {
    throw "PRINT_BACKEND debe ser archivo o tcp."
}
$printSyncValue = Get-DotEnvValue -Name "PRINT_SYNC" -Default "false"
$printSync = ConvertFrom-DotEnvBoolean -Name "PRINT_SYNC" -Value $printSyncValue
$printerPortValue = Get-DotEnvValue -Name "PRINTER_PORT" -Default "9100"
$printerPort = ConvertFrom-DotEnvInteger -Name "PRINTER_PORT" -Value $printerPortValue -Minimum 1 -Maximum 65535
$printerHosts = @(
    Get-DotEnvValue -Name "PRINTER_CAJA_HOST"
    Get-DotEnvValue -Name "PRINTER_COCINA_HOST"
    Get-DotEnvValue -Name "PRINTER_BARRA_HOST"
)
if ($printBackend -eq "tcp") {
    if (@($printerHosts | Where-Object {
        [string]::IsNullOrWhiteSpace($_) -or -not (Test-AllowedHost -Value $_)
    }).Count) {
        throw "PRINT_BACKEND=tcp exige tres hosts de impresora concretos y válidos."
    }
}
elseif (@($printerHosts | Where-Object {
    -not [string]::IsNullOrWhiteSpace($_) -and -not (Test-AllowedHost -Value $_)
}).Count) {
    throw "La configuración opcional de impresoras contiene un host no válido."
}
$trustedProxy = Get-DotEnvValue -Name "WAITRESS_TRUSTED_PROXY"
$trustedProxyIp = $null
if (-not [string]::IsNullOrWhiteSpace($trustedProxy)) {
    if (-not [Net.IPAddress]::TryParse($trustedProxy, [ref]$trustedProxyIp) -or -not [Net.IPAddress]::IsLoopback($trustedProxyIp)) {
        throw "WAITRESS_TRUSTED_PROXY debe ser una dirección loopback concreta."
    }
    if (-not [Net.IPAddress]::IsLoopback($listenIp)) { throw "Waitress debe escuchar en loopback al confiar en un proxy." }
}
if ($https -and ($allowInsecureHttpLan -or -not [Net.IPAddress]::IsLoopback($listenIp) -or $null -eq $trustedProxyIp)) {
    throw "DJANGO_HTTPS requiere Waitress y proxy en loopback, y prohíbe ALLOW_INSECURE_HTTP_LAN."
}
if (-not $https -and -not [Net.IPAddress]::IsLoopback($listenIp) -and -not $allowInsecureHttpLan) {
    throw "HTTP fuera de loopback requiere ALLOW_INSECURE_HTTP_LAN=true."
}
if ([string]::IsNullOrWhiteSpace($HealthHost)) { $HealthHost = $allowedHosts[0] }
if (-not (Test-AllowedHost -Value $HealthHost)) { throw "HealthHost/DJANGO_ALLOWED_HOSTS no contiene un host concreto utilizable." }
if ($HealthHost -notin $allowedHosts) { throw "HealthHost debe figurar en DJANGO_ALLOWED_HOSTS." }

$service = Get-CimInstance Win32_Service -Filter "Name='LosTocayosPOS'"
if (-not $service -or $service.State -ne "Running" -or $service.StartName -ne "NT AUTHORITY\LocalService" -or $service.StartMode -ne "Auto") {
    throw "El servicio no está activo como LocalService con inicio automático."
}
if (-not (Test-SamePath (Get-ExecutableFromCommandLine $service.PathName) $serviceHost)) { throw "El servicio registrado no pertenece a esta instalación." }
$serviceRegistry = Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\LosTocayosPOS"
if ([int]$serviceRegistry.DelayedAutoStart -ne 1) { throw "El servicio no tiene inicio automático retardado." }
$pythonClass = (Get-Item -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\LosTocayosPOS\PythonClass").GetValue("")
$expectedPythonClass = (Join-Path $root "servicio_windows") + ".ServicioTocayosPOS"
if ([string]$pythonClass -cne $expectedPythonClass) { throw "PythonClass no apunta a la clase de servicio de esta instalación." }
[byte[]]$expectedFailureActions = @(
    128,81,1,0, 0,0,0,0, 0,0,0,0, 3,0,0,0, 20,0,0,0,
    1,0,0,0,136,19,0,0, 1,0,0,0,152,58,0,0, 1,0,0,0,96,234,0,0
)
[byte[]]$actualFailureActions = @($serviceRegistry.FailureActions)
if ($actualFailureActions.Length -ne $expectedFailureActions.Length -or
    [BitConverter]::ToString($actualFailureActions) -cne [BitConverter]::ToString($expectedFailureActions)) {
    throw "La recuperación del servicio no coincide con 3 reinicios a 5, 15 y 60 segundos y reinicio diario del contador."
}

$database = $null
if ($dbEngine -eq "sqlite") {
    $database = Resolve-ProjectPath (Get-DotEnvValue -Name "SQLITE_PATH" -Default "db.sqlite3")
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) { throw "Falta la base SQLite configurada: $database" }
}

$task = Get-ScheduledTask -TaskName "LosTocayosPOS-RespaldoSQLite" -ErrorAction SilentlyContinue
$taskState = "NotConfigured"
$info = $null
$backupCreated = $null
if ($task) {
    if ($dbEngine -ne "sqlite") { throw "Existe una tarea SQLite aunque DB_ENGINE usa PostgreSQL." }
    if ($task.Principal.UserId -notin @("SYSTEM", "S-1-5-18", "NT AUTHORITY\SYSTEM")) { throw "La tarea de respaldo no usa SYSTEM." }
    if ([string]$task.Principal.RunLevel -ne "Highest") { throw "La tarea de respaldo no usa privilegios máximos." }
    if ([string]$task.State -eq "Disabled" -or -not [bool]$task.Settings.Enabled -or
        -not [bool]$task.Settings.StartWhenAvailable -or
        [string]$task.Settings.MultipleInstances -ne "IgnoreNew" -or
        [string]$task.Settings.ExecutionTimeLimit -ne "PT1H") {
        throw "La tarea de respaldo está deshabilitada o no conserva sus ajustes de recuperación."
    }
    $triggers = @($task.Triggers)
    if ($triggers.Count -ne 1 -or
        [string]$triggers[0].CimClass.CimClassName -ne "MSFT_TaskDailyTrigger" -or
        -not [bool]$triggers[0].Enabled -or [int]$triggers[0].DaysInterval -ne 1) {
        throw "La tarea de respaldo no contiene un único disparador diario habilitado."
    }
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { throw "La tarea de respaldo no tiene una acción única." }
    $action = $actions[0]
    $systemDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::System)
    $expectedPowerShell = Join-Path $systemDirectory "WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-SamePath $action.Execute $expectedPowerShell)) { throw "La tarea no usa Windows PowerShell desde la ruta del sistema." }
    if (-not (Test-SamePath $action.WorkingDirectory $root)) { throw "La tarea de respaldo usa otro directorio de trabajo." }
    $backupScript = Join-Path $root "respaldar-db-sqlite.ps1"
    $backupRoot = Join-Path $root "backups"
    $backupLog = Join-Path $root "logs\sqlite-backup.log"
    foreach ($expectedPath in @($backupScript, $python, $database, $backupRoot, $backupLog)) {
        if (-not (Test-ContainsOrdinalIgnoreCase -Value ([string]$action.Arguments) -Expected ('"' + $expectedPath + '"'))) {
            throw "La tarea de respaldo no referencia la ruta esperada: $expectedPath"
        }
    }
    foreach ($requiredArgument in @('-NoProfile', '-NonInteractive', '-ExecutionPolicy Bypass', '-File', '-Python', '-DatabasePath', '-BackupRoot', '-LogPath', '-RetentionDays')) {
        if (-not (Test-ContainsOrdinalIgnoreCase -Value ([string]$action.Arguments) -Expected $requiredArgument)) {
            throw "La tarea de respaldo no contiene el argumento obligatorio $requiredArgument."
        }
    }
    if (Test-ContainsOrdinalIgnoreCase -Value ([string]$action.Arguments) -Expected '-ParentHoldsBackupMutex') {
        throw "La tarea de respaldo no puede omitir su mutex propio."
    }
    if ([string]$action.Arguments -notmatch '(?i)(?:^|\s)-RetentionDays\s+([0-9]+)(?:\s|$)' -or
        [int]$Matches[1] -lt 1 -or [int]$Matches[1] -gt 3650) {
        throw "La retención de la tarea de respaldo no es válida."
    }
    if ($RunBackup) {
        $backupArtifactPattern = '^db-\d{8}-\d{6}(?:-\d+)?\.sqlite3(?:\.(?:json|sha256))?$'
        $backupFilePattern = '^db-\d{8}-\d{6}(?:-\d+)?\.sqlite3$'
        $beforeNames = @(
            Get-ChildItem -LiteralPath $backupRoot -Force -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -cmatch $backupArtifactPattern } |
                Select-Object -ExpandProperty Name
        )
        $started = Get-Date
        Start-ScheduledTask -InputObject $task
        $deadline = $started.AddSeconds($BackupTimeoutSeconds)
        do {
            Start-Sleep -Milliseconds 500
            $info = Get-ScheduledTaskInfo -InputObject $task
            $task = Get-ScheduledTask -TaskName $task.TaskName
        } while (($info.LastRunTime -lt $started.AddSeconds(-1) -or $task.State -eq "Running") -and (Get-Date) -lt $deadline)
        if ($task.State -eq "Running" -or $info.LastTaskResult -ne 0 -or $info.LastRunTime -lt $started.AddSeconds(-1)) { throw "La tarea no completó un respaldo correcto dentro de $BackupTimeoutSeconds segundos." }
        $afterNames = @(
            Get-ChildItem -LiteralPath $backupRoot -Force -File |
                Where-Object { $_.Name -cmatch $backupArtifactPattern } |
                Select-Object -ExpandProperty Name
        )
        $newNames = @($afterNames | Where-Object { $_ -notin $beforeNames })
        $newBackupFiles = @($newNames | Where-Object { $_ -cmatch $backupFilePattern })
        if ($newBackupFiles.Count -ne 1) {
            throw "La tarea no publicó exactamente una nueva copia SQLite."
        }
        $expectedNames = @(
            $newBackupFiles[0],
            ($newBackupFiles[0] + ".sha256"),
            ($newBackupFiles[0] + ".json")
        )
        if ($newNames.Count -ne 3 -or
            @(Compare-Object -ReferenceObject $expectedNames -DifferenceObject $newNames).Count) {
            throw "La tarea no publicó un único triplete SQLite/JSON/SHA-256 coherente."
        }
        $backupCreated = $newBackupFiles[0]
    }
    if ($null -eq $info) { $info = Get-ScheduledTaskInfo -InputObject $task }
    $taskState = [string]$task.State
} elseif ($RunBackup) {
    throw "RunBackup requiere una tarea SQLite instalada; pudo omitirse expresamente durante el alta."
}
$healthAddress = if ($listenAddress -eq "0.0.0.0") {
    "127.0.0.1"
} elseif ($listenAddress -eq "::") {
    "::1"
} else {
    $listenAddress
}
if ($healthAddress.Contains(':') -and -not $healthAddress.StartsWith('[')) { $healthAddress = "[$healthAddress]" }
$headers = @{ Host = $HealthHost }
if ($https) { $headers["X-Forwarded-Proto"] = "https" }
$response = Invoke-RestMethod -Uri "http://${healthAddress}:$Port/salud/" -Headers $headers -TimeoutSec 5
if ($response.estado -ne "ok") { throw "/salud/ no confirma estado ok." }

$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen)
if (-not $listeners.Count) { throw "No existe un listener en el puerto configurado." }
$listenerProcesses = @(
    foreach ($owningProcessId in @($listeners.OwningProcess | Sort-Object -Unique)) {
        Get-CimInstance Win32_Process -Filter ("ProcessId=" + $owningProcessId)
    }
)
$basePythonLines = @(& $python -I -c "import sys; print(sys._base_executable)")
if ($LASTEXITCODE -ne 0 -or $basePythonLines.Count -ne 1 -or
    [string]::IsNullOrWhiteSpace([string]$basePythonLines[0])) {
    throw "No se pudo resolver el CPython base de la .venv."
}
$basePython = [IO.Path]::GetFullPath(([string]$basePythonLines[0]).Trim())
$invalidListenerProcesses = @(
    foreach ($listenerProcess in $listenerProcesses) {
        $belongsToService = $false
        if (Test-SamePath $listenerProcess.ExecutablePath $python) {
            $belongsToService = (
                [uint32]$listenerProcess.ParentProcessId -eq
                [uint32]$service.ProcessId
            )
        }
        elseif (Test-SamePath $listenerProcess.ExecutablePath $basePython) {
            $redirector = Get-CimInstance Win32_Process -Filter (
                "ProcessId=" + [uint32]$listenerProcess.ParentProcessId
            )
            $belongsToService = (
                $null -ne $redirector -and
                (Test-SamePath $redirector.ExecutablePath $python) -and
                [uint32]$redirector.ParentProcessId -eq [uint32]$service.ProcessId
            )
        }
        $commandLine = [string]$listenerProcess.CommandLine
        foreach ($requiredArgument in @(
            "-m waitress", "--host=$listenAddress", "--port=$Port",
            "pos.wsgi:application"
        )) {
            if (-not (Test-ContainsOrdinalIgnoreCase -Value $commandLine -Expected $requiredArgument)) {
                $belongsToService = $false
            }
        }
        if (-not $belongsToService) { $listenerProcess }
    }
)
if (-not $listenerProcesses.Count -or $invalidListenerProcesses.Count) {
    throw "Todos los listeners deben ser Waitress y descender del host mediante el Python de esta .venv."
}

$firewall = @(Get-NetFirewallRule -DisplayName "Los Tocayos POS - LAN privada" -ErrorAction SilentlyContinue)
$firewallState = "NotConfigured"
$firewallProgram = $null
$firewallRemoteAddress = $null
if ($https) {
    if ($firewall.Count) { throw "HTTPS no debe conservar la regla HTTP LAN del POS." }
} elseif ($firewall.Count) {
    if ($firewall.Count -ne 1 -or [string]$firewall[0].Enabled -ne "True" -or
        [string]$firewall[0].Profile -ne "Private" -or [string]$firewall[0].Action -ne "Allow" -or
        [string]$firewall[0].Direction -ne "Inbound") {
        throw "La regla de firewall no está limitada a entrada en perfil privado."
    }
    $program = $firewall | Get-NetFirewallApplicationFilter
    $portFilter = $firewall | Get-NetFirewallPortFilter
    $addressFilter = $firewall | Get-NetFirewallAddressFilter
    if (-not (Test-SamePath $program.Program $basePython) -or
        $portFilter.LocalPort -ne [string]$Port -or $portFilter.Protocol -ne "TCP" -or
        @($addressFilter.RemoteAddress).Count -ne 1 -or @($addressFilter.RemoteAddress)[0] -ne "LocalSubnet") {
        throw "El firewall no coincide con el CPython base, puerto o subred de esta instalación."
    }
    $firewallState = "Configured"
    $firewallProgram = $program.Program
    $firewallRemoteAddress = $addressFilter.RemoteAddress
}

$count = 0
$violations = New-Object 'System.Collections.Generic.List[string]'
$queue = New-Object 'System.Collections.Generic.Queue[string]'
$queue.Enqueue($root)
while ($queue.Count) {
    $path = $queue.Dequeue()
    $item = Get-Item -LiteralPath $path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Se encontro un enlace/junction; no se sigue durante la auditoria."
    }
    $relative = $path.Substring($root.Length).TrimStart('\')
    $acl = Get-Acl -LiteralPath $path
    if (-not $acl.AreAccessRulesProtected) {
        $violations.Add($relative + ": conserva herencia de ACL")
    }
    try {
        $ownerSid = (New-Object Security.Principal.NTAccount($acl.Owner)).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        try { $ownerSid = (New-Object Security.Principal.SecurityIdentifier($acl.Owner)).Value }
        catch { $ownerSid = "" }
    }
    if ($ownerSid -ne "S-1-5-32-544") {
        $violations.Add($relative + ": propietario distinto de Administradores")
    }
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if (@($rules | Where-Object { $_.IsInherited }).Count) {
        $violations.Add($relative + ": contiene ACE heredadas")
    }
    foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
        $full = @($rules | Where-Object {
            $_.IdentityReference.Value -eq $sid -and $_.AccessControlType -eq "Allow" -and
            ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0 -and
            $_.FileSystemRights -eq [Security.AccessControl.FileSystemRights]::FullControl
        })
        if ($full.Count -ne 1) { $violations.Add($relative + ": control administrativo no canónico") }
    }
    if (@($rules | Where-Object {
        $_.AccessControlType -ne "Allow" -or
        $_.IdentityReference.Value -notin @("S-1-5-18", "S-1-5-32-544", "S-1-5-19")
    }).Count) { $violations.Add($relative + ": identidad o denegacion no prevista") }
    $ls = @($rules | Where-Object {
        $_.IdentityReference.Value -eq "S-1-5-19" -and $_.AccessControlType -eq "Allow" -and
        ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0
    })
    [Security.AccessControl.FileSystemRights]$localServiceRights = 0
    foreach ($localServiceRule in $ls) {
        $localServiceRights = $localServiceRights -bor $localServiceRule.FileSystemRights
    }
    if ($relative -match '^(backups|\.git|tmp|\.venv-roto-[^\\]+)(\\|$)' -or $relative -match '^db\.sqlite3(?:-wal|-shm|-journal)?$') {
        if ($ls.Count) { $violations.Add($relative + ": LocalService accede a datos privados") }
    } elseif ($relative -match '^(runtime|logs|media)(\\|$)') {
        $requiredModify = (
            [Security.AccessControl.FileSystemRights]::Modify -bor
            [Security.AccessControl.FileSystemRights]::Synchronize
        )
        if ($ls.Count -ne 1 -or $localServiceRights -ne $requiredModify) {
            $violations.Add($relative + ": escritura operativa no canónica")
        }
    } else {
        $requiredRead = if ($relative -eq ".env") {
            [Security.AccessControl.FileSystemRights]::Read
        } else {
            [Security.AccessControl.FileSystemRights]::ReadAndExecute
        }
        $requiredRead = (
            $requiredRead -bor [Security.AccessControl.FileSystemRights]::Synchronize
        )
        if ($ls.Count -ne 1 -or $localServiceRights -ne $requiredRead) {
            $violations.Add($relative + ": acceso de lectura/ejecución del servicio no canónico")
        }
    }
    $count++
    if ($item.PSIsContainer) {
        foreach ($child in Get-ChildItem -LiteralPath $path -Force) { $queue.Enqueue($child.FullName) }
    }
}
if ($violations.Count) { throw ("ACL no conformes: " + $violations.Count + "; " + ($violations | Select-Object -First 10) -join "; ") }
[pscustomobject]@{
    Service = $service.Name
    ServiceState = $service.State
    ServiceAccount = $service.StartName
    ServiceExecutable = Get-ExecutableFromCommandLine $service.PathName
    ServiceStartMode = $service.StartMode
    ServiceDelayedAutoStart = $true
    DatabaseEngine = $dbEngine
    BackupTask = if ($task) { $task.TaskName } else { $null }
    BackupTaskState = $taskState
    BackupLastResult = if ($info) { $info.LastTaskResult } else { $null }
    BackupLastRun = if ($info) { $info.LastRunTime } else { $null }
    BackupCreated = $backupCreated
    Database = $database
    Health = $response.estado
    AuditedAclItems = $count
    AclViolations = $violations.Count
    FirewallState = $firewallState
    FirewallProgram = $firewallProgram
    FirewallRemoteAddress = $firewallRemoteAddress
}
