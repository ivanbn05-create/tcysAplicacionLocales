<#
.SYNOPSIS
Activa de forma transaccional la impresion TCP en una instalacion existente.

.DESCRIPTION
Valida que LosTocayosPOS corresponde a la ruta indicada y que el servicio esta
saludable, respalda .env, cambia exclusivamente la configuracion de impresion,
reinicia el servicio y ejecuta el diagnostico TCP sin enviar datos de impresion.
Si falla el reinicio, la salud o el diagnostico, restaura .env byte por byte y
reinicia de nuevo el servicio con su configuracion anterior.

Ejemplo desde una consola elevada:
  .\herramientas\configurar_impresion_instalada.ps1 `
    -HostCaja 192.168.0.33 -HostCocina 192.168.0.33 -HostBarra 192.168.0.33

El archivo de respaldo queda junto a .env y conserva sus permisos. El comando
diagnosticar_impresoras solo abre y cierra sockets TCP; no transmite comandos ni
genera papel.
#>
#Requires -Version 5.1
#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$RutaInstalacion = 'C:\LosTocayosPOS',

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$HostCaja,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$HostCocina,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$HostBarra,

    [ValidateRange(1, 65535)]
    [int]$Puerto = 9100,

    [bool]$PrintSync = $false
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$nombreServicio = 'LosTocayosPOS'
$mutexMantenimiento = $null
$mutexAdquirido = $false
$archivoTemporal = $null
$entornoModificado = $false
[byte[]]$bytesOriginales = @()
$aclOriginal = $null
$rutaEntorno = $null
$rutaRespaldo = $null
$servicioDetenidoPorScript = $false
$estadoColaAcreditado = $null

function Test-PrinterHost {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -cne $Value.Trim() -or
        $Value -match '\s|\*|/|\\|://' -or $Value -match '[\r\n=]') {
        return $false
    }
    if ($Value.StartsWith('[') -and $Value.EndsWith(']')) {
        $direccionV6 = $null
        return [Net.IPAddress]::TryParse(
            $Value.Substring(1, $Value.Length - 2),
            [ref]$direccionV6
        ) -and $direccionV6.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6
    }
    if ($Value.Contains(':')) { return $false }
    $direccion = $null
    if ([Net.IPAddress]::TryParse($Value, [ref]$direccion)) { return $true }
    return $Value -match '^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$'
}

function Get-ServiceExecutablePath {
    param([string]$CommandLine)

    $valor = ([string]$CommandLine).Trim()
    if ($valor.StartsWith('"')) {
        if ($valor -notmatch '^"([^"]+)"(?:\s|$)') {
            throw 'PathName del servicio no es valido.'
        }
        return [IO.Path]::GetFullPath($Matches[1])
    }
    $ejecutable = ($valor -split '\s+', 2)[0]
    if ([string]::IsNullOrWhiteSpace($ejecutable)) {
        throw 'PathName del servicio esta vacio.'
    }
    return [IO.Path]::GetFullPath($ejecutable)
}

function Assert-ServiceBelongsToInstallation {
    param([string]$Root)

    $registro = Get-CimInstance Win32_Service -Filter "Name='$nombreServicio'" -ErrorAction Stop
    if ($null -eq $registro) { throw "No existe el servicio $nombreServicio." }
    $actual = Get-ServiceExecutablePath -CommandLine $registro.PathName
    $esperado = [IO.Path]::GetFullPath((Join-Path $Root '.venv\Scripts\pythonservice.exe'))
    if (-not $actual.Equals($esperado, [StringComparison]::OrdinalIgnoreCase)) {
        throw "El servicio $nombreServicio pertenece a otra instalacion; no se modifico .env."
    }
}

function Get-DotEnvValue {
    param([string]$Text, [string]$Name, [string]$Default = '')

    $patron = '(?m)^[ \t]*' + [Regex]::Escape($Name) + '[ \t]*=[ \t]*([^\r\n]*)'
    $coincidencias = [Regex]::Matches($Text, $patron)
    if ($coincidencias.Count -eq 0) { return $Default }
    if ($coincidencias.Count -gt 1) {
        throw "$Name esta duplicada en .env; corrige la configuracion antes de continuar."
    }
    $valor = $coincidencias[0].Groups[1].Value.Trim()
    if ($valor.Length -ge 2 -and (($valor.StartsWith('"') -and $valor.EndsWith('"')) -or
        ($valor.StartsWith("'") -and $valor.EndsWith("'")))) {
        return $valor.Substring(1, $valor.Length - 2)
    }
    return $valor
}

function Read-Utf8Environment {
    param([string]$Path)

    [byte[]]$bytes = [IO.File]::ReadAllBytes($Path)
    $tieneBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and
        $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $inicio = if ($tieneBom) { 3 } else { 0 }
    $utf8Estricto = New-Object Text.UTF8Encoding($false, $true)
    try {
        $texto = $utf8Estricto.GetString($bytes, $inicio, $bytes.Length - $inicio)
    }
    catch {
        throw '.env no es UTF-8 valido; no se modifico para evitar corromper secretos.'
    }
    if ($texto.Contains([char]0)) {
        throw '.env contiene bytes nulos y no es una configuracion de texto valida.'
    }
    return @{
        Bytes = $bytes
        Text = $texto
        HasBom = $tieneBom
    }
}

function ConvertTo-Utf8EnvironmentBytes {
    param([string]$Text, [bool]$HasBom)

    $codificacion = New-Object Text.UTF8Encoding($HasBom, $true)
    return $codificacion.GetPreamble() + $codificacion.GetBytes($Text)
}

function Set-DotEnvValuesInText {
    param([string]$Text, [Collections.IDictionary]$Values)

    $resultado = $Text
    $salto = if ($resultado.Contains("`r`n")) { "`r`n" } elseif ($resultado.Contains("`n")) {
        "`n"
    } elseif ($resultado.Contains("`r")) { "`r" } else { [Environment]::NewLine }

    foreach ($entrada in $Values.GetEnumerator()) {
        $nombre = [string]$entrada.Key
        $valor = [string]$entrada.Value
        if ($valor -match '[\r\n]') { throw "$nombre contiene un salto de linea no permitido." }
        $patron = '(?m)^[ \t]*' + [Regex]::Escape($nombre) + '[ \t]*=[^\r\n]*'
        $regex = New-Object Text.RegularExpressions.Regex(
            $patron,
            [Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
        $coincidencias = $regex.Matches($resultado)
        if ($coincidencias.Count -gt 1) {
            throw "$nombre esta duplicada en .env; no se eligio una entrada de forma ambigua."
        }
        $linea = "$nombre=$valor"
        if ($coincidencias.Count -eq 1) {
            $resultado = $regex.Replace($resultado, $linea, 1)
            continue
        }
        if ($resultado.Length -eq 0) {
            $resultado = $linea
        }
        elseif ($resultado -match '(?:\r\n|\n|\r)$') {
            $resultado += $linea + $salto
        }
        else {
            $resultado += $salto + $linea
        }
    }
    return $resultado
}

function Write-EnvironmentAtomically {
    param(
        [string]$Path,
        [byte[]]$Bytes,
        [Security.AccessControl.FileSecurity]$Acl
    )

    $directorio = Split-Path -Parent $Path
    $script:archivoTemporal = Join-Path $directorio ('.env.impresion-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllBytes($script:archivoTemporal, $Bytes)
    Set-Acl -LiteralPath $script:archivoTemporal -AclObject $Acl
    if ((Get-Item -LiteralPath $script:archivoTemporal).Length -ne $Bytes.Length) {
        throw 'No se pudo verificar la escritura temporal de .env.'
    }
    $respaldoReemplazo = Join-Path $directorio ('.env.reemplazo-' + [Guid]::NewGuid().ToString('N') + '.bak')
    $reemplazoCompleto = $false
    try {
        [IO.File]::Replace($script:archivoTemporal, $Path, $respaldoReemplazo, $true)
        $script:archivoTemporal = $null
        $reemplazoCompleto = $true
    }
    finally {
        if ($reemplazoCompleto -and (Test-Path -LiteralPath $respaldoReemplazo)) {
            Remove-Item -LiteralPath $respaldoReemplazo -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-HealthContext {
    param([string]$EnvironmentText)

    $listen = Get-DotEnvValue -Text $EnvironmentText -Name 'WAITRESS_HOST' -Default '0.0.0.0'
    $allowed = Get-DotEnvValue -Text $EnvironmentText -Name 'DJANGO_ALLOWED_HOSTS' -Default 'localhost,127.0.0.1,[::1]'
    $portText = Get-DotEnvValue -Text $EnvironmentText -Name 'WAITRESS_PORT' -Default '8000'
    $httpsText = Get-DotEnvValue -Text $EnvironmentText -Name 'DJANGO_HTTPS' -Default 'false'
    $port = 0
    if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw 'WAITRESS_PORT no es valido en la configuracion existente.'
    }
    $hostHeader = @($allowed -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })[0]
    if ([string]::IsNullOrWhiteSpace($hostHeader)) {
        throw 'DJANGO_ALLOWED_HOSTS esta vacio en la configuracion existente.'
    }
    $direccion = if ($listen -eq '0.0.0.0') { '127.0.0.1' } elseif ($listen -eq '::') {
        '::1'
    } else { $listen }
    if ($direccion.Contains(':') -and -not $direccion.StartsWith('[')) {
        $direccion = "[$direccion]"
    }
    $cabeceras = @{ Host = $hostHeader }
    if ($httpsText.Trim().ToLowerInvariant() -in @('1', 'true', 'si', 'si', 'yes')) {
        $cabeceras['X-Forwarded-Proto'] = 'https'
    }
    return @{
        Uri = "http://${direccion}:$port/salud/"
        Headers = $cabeceras
    }
}

function Wait-ServiceHealth {
    param(
        [Collections.IDictionary]$Context,
        [ValidateRange(1, 120)][int]$TimeoutSeconds = 30
    )

    $limite = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $respuesta = Invoke-WebRequest -UseBasicParsing -Uri $Context.Uri `
                -Headers $Context.Headers -TimeoutSec 2
            $contenido = $respuesta.Content | ConvertFrom-Json
            if ($respuesta.StatusCode -eq 200 -and $contenido.estado -eq 'ok') { return }
        }
        catch { }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $limite)
    throw 'La comprobacion local /salud/ no respondio con estado ok.'
}

function Stop-PosService {
    $servicio = Get-Service -Name $nombreServicio -ErrorAction Stop
    if ($servicio.Status -ne 'Stopped') {
        Stop-Service -Name $nombreServicio -ErrorAction Stop
        $script:servicioDetenidoPorScript = $true
        $servicio.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
    else {
        $script:servicioDetenidoPorScript = $true
    }
}

function Start-PosService {
    $servicio = Get-Service -Name $nombreServicio -ErrorAction Stop
    if ($servicio.Status -ne 'Running') {
        Start-Service -Name $nombreServicio -ErrorAction Stop
        $servicio = Get-Service -Name $nombreServicio -ErrorAction Stop
        $servicio.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    }
    $script:servicioDetenidoPorScript = $false
}

function Restart-PosService {
    Stop-PosService
    Start-PosService
}

function Get-BlockingPrintQueueState {
    param([string]$Root, [string]$PythonPath)

    # La consulta se ejecuta con el servicio detenido. Asi Waitress y el worker no
    # pueden crear, reclamar ni enviar un trabajo entre esta lectura y el cambio de .env.
    $codigo = "from impresion.models import TrabajoImpresion; import json; print('COLA_IMPRESION_JSON=' + json.dumps({'pendiente': TrabajoImpresion.objects.filter(estado=TrabajoImpresion.Estado.PENDIENTE).count(), 'procesando': TrabajoImpresion.objects.filter(estado=TrabajoImpresion.Estado.PROCESANDO).count()}, sort_keys=True))"
    $inicio = New-Object Diagnostics.ProcessStartInfo
    $inicio.FileName = $PythonPath
    $inicio.Arguments = 'manage.py shell -c "' + $codigo + '"'
    $inicio.WorkingDirectory = $Root
    $inicio.UseShellExecute = $false
    $inicio.CreateNoWindow = $true
    $inicio.RedirectStandardOutput = $true
    $inicio.RedirectStandardError = $true
    foreach ($nombre in @($inicio.EnvironmentVariables.Keys)) {
        $canonico = ([string]$nombre).ToUpperInvariant()
        if ($canonico -match '^(?:DJANGO_|DB_|WAITRESS_|POSTGRES_|POS_|SUCURSAL_|PRINT_|PRINTER_|PEDIDOS_SUCURSALES_|VPS_CONSOLIDACION_|THERMAL_|PYTHON|PIP_)' -or
            $canonico -in @('ALLOW_INSECURE_HTTP_LAN', 'SQLITE_PATH', 'VIRTUAL_ENV', '__PYVENV_LAUNCHER__')) {
            $inicio.EnvironmentVariables.Remove([string]$nombre)
        }
    }
    $inicio.EnvironmentVariables['PYTHONNOUSERSITE'] = '1'
    $inicio.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $inicio.EnvironmentVariables['PYTHONUTF8'] = '1'
    $inicio.EnvironmentVariables['PIP_CONFIG_FILE'] = 'NUL'
    $inicio.EnvironmentVariables['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    $inicio.EnvironmentVariables['PIP_NO_INPUT'] = '1'

    $proceso = New-Object Diagnostics.Process
    $proceso.StartInfo = $inicio
    try {
        if (-not $proceso.Start()) { throw 'No se pudo iniciar la inspeccion de la cola de impresion.' }
        $salidaTarea = $proceso.StandardOutput.ReadToEndAsync()
        $errorTarea = $proceso.StandardError.ReadToEndAsync()
        if (-not $proceso.WaitForExit(30000)) {
            try { $proceso.Kill() } catch { }
            throw 'La inspeccion de la cola de impresion excedio 30 segundos.'
        }
        $salida = $salidaTarea.GetAwaiter().GetResult()
        [void]$errorTarea.GetAwaiter().GetResult()
        if ($proceso.ExitCode -ne 0) {
            throw 'No se pudo acreditar la cola de impresion con la base instalada.'
        }
    }
    finally {
        $proceso.Dispose()
    }

    $coincidencias = [Regex]::Matches(
        $salida,
        '(?m)^COLA_IMPRESION_JSON=(\{[^\r\n]+\})\s*$'
    )
    if ($coincidencias.Count -ne 1) {
        throw 'La inspeccion de la cola no devolvio una acreditacion JSON unica.'
    }
    try { $resultado = $coincidencias[0].Groups[1].Value | ConvertFrom-Json }
    catch { throw 'La acreditacion de la cola no contiene JSON valido.' }
    $pendientes = 0
    $procesando = 0
    if (-not [int]::TryParse([string]$resultado.pendiente, [ref]$pendientes) -or
        -not [int]::TryParse([string]$resultado.procesando, [ref]$procesando) -or
        $pendientes -lt 0 -or $procesando -lt 0) {
        throw 'La acreditacion de la cola contiene conteos invalidos.'
    }
    return [ordered]@{
        pendiente = $pendientes
        procesando = $procesando
        bloqueantes = $pendientes + $procesando
    }
}

function Invoke-SafePrinterDiagnostic {
    param([string]$Root, [string]$PythonPath)

    $inicio = New-Object Diagnostics.ProcessStartInfo
    $inicio.FileName = $PythonPath
    $inicio.Arguments = 'manage.py diagnosticar_impresoras --exigir-tcp --json'
    $inicio.WorkingDirectory = $Root
    $inicio.UseShellExecute = $false
    $inicio.CreateNoWindow = $true
    $inicio.RedirectStandardOutput = $true
    $inicio.RedirectStandardError = $true
    foreach ($nombre in @($inicio.EnvironmentVariables.Keys)) {
        $canonico = ([string]$nombre).ToUpperInvariant()
        if ($canonico -match '^(?:DJANGO_|DB_|WAITRESS_|POSTGRES_|POS_|SUCURSAL_|PRINT_|PRINTER_|PEDIDOS_SUCURSALES_|VPS_CONSOLIDACION_|THERMAL_|PYTHON|PIP_)' -or
            $canonico -in @('ALLOW_INSECURE_HTTP_LAN', 'SQLITE_PATH', 'VIRTUAL_ENV', '__PYVENV_LAUNCHER__')) {
            $inicio.EnvironmentVariables.Remove([string]$nombre)
        }
    }
    $inicio.EnvironmentVariables['PYTHONNOUSERSITE'] = '1'
    $inicio.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $inicio.EnvironmentVariables['PYTHONUTF8'] = '1'
    $inicio.EnvironmentVariables['PIP_CONFIG_FILE'] = 'NUL'
    $inicio.EnvironmentVariables['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    $inicio.EnvironmentVariables['PIP_NO_INPUT'] = '1'

    $proceso = New-Object Diagnostics.Process
    $proceso.StartInfo = $inicio
    try {
        if (-not $proceso.Start()) { throw 'No se pudo iniciar el diagnostico de impresoras.' }
        $salidaTarea = $proceso.StandardOutput.ReadToEndAsync()
        $errorTarea = $proceso.StandardError.ReadToEndAsync()
        if (-not $proceso.WaitForExit(30000)) {
            try { $proceso.Kill() } catch { }
            throw 'El diagnostico de impresoras excedio 30 segundos.'
        }
        $salida = $salidaTarea.GetAwaiter().GetResult()
        [void]$errorTarea.GetAwaiter().GetResult()
        if ($proceso.ExitCode -ne 0) {
            throw 'El diagnostico TCP no aprobo todos los destinos.'
        }
    }
    finally {
        $proceso.Dispose()
    }

    try { $resultado = $salida.Trim() | ConvertFrom-Json }
    catch { throw 'El diagnostico no devolvio JSON valido.' }
    if ($resultado.backend -cne 'tcp' -or $resultado.impresion_fisica_activa -ne $true -or
        $resultado.envio_de_datos -ne $false) {
        throw 'El diagnostico no confirmo backend tcp seguro.'
    }
    $destinos = @($resultado.destinos)
    if ($destinos.Count -ne 3 -or @($destinos | Where-Object {
        $_.configurada -ne $true -or $_.alcanzable -ne $true
    }).Count -ne 0) {
        throw 'El diagnostico TCP no confirmo caja, cocina y barra.'
    }
    return $resultado
}

function Enter-MaintenanceMutex {
    $mutex = New-Object Threading.Mutex($false, 'Global\LosTocayosPOS-Mantenimiento-v1')
    $adquirido = $false
    try {
        try { $adquirido = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $adquirido = $true }
        if (-not $adquirido) { throw 'Ya existe otro mantenimiento del POS en curso.' }
        $script:mutexAdquirido = $true
        return $mutex
    }
    catch {
        if (-not $adquirido) { $mutex.Dispose() }
        throw
    }
}

function Restore-EnvironmentAndService {
    param(
        [string]$EnvironmentPath,
        [byte[]]$OriginalBytes,
        [Security.AccessControl.FileSecurity]$OriginalAcl,
        [Collections.IDictionary]$HealthContext
    )

    Write-EnvironmentAtomically -Path $EnvironmentPath -Bytes $OriginalBytes -Acl $OriginalAcl
    Restart-PosService
    Wait-ServiceHealth -Context $HealthContext -TimeoutSeconds 30
}

foreach ($hostImpresora in @($HostCaja, $HostCocina, $HostBarra)) {
    if (-not (Test-PrinterHost -Value $hostImpresora)) {
        throw 'Cada host de impresora debe ser una IP o nombre DNS concreto, sin esquema, ruta ni puerto.'
    }
}

$raiz = [IO.Path]::GetFullPath($RutaInstalacion)
if (-not (Test-Path -LiteralPath $raiz -PathType Container)) {
    throw "No existe la instalacion indicada: $raiz"
}
if ((Get-Item -LiteralPath $raiz -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'RutaInstalacion no puede ser un enlace o punto de reparacion.'
}
$rutaEntorno = Join-Path $raiz '.env'
$python = Join-Path $raiz '.venv\Scripts\python.exe'
$managePy = Join-Path $raiz 'manage.py'
$comandoDiagnostico = Join-Path $raiz 'impresion\management\commands\diagnosticar_impresoras.py'
foreach ($archivo in @($rutaEntorno, $python, $managePy, $comandoDiagnostico)) {
    if (-not (Test-Path -LiteralPath $archivo -PathType Leaf)) {
        throw "La instalacion esta incompleta; falta $([IO.Path]::GetFileName($archivo))."
    }
}
if ((Get-Item -LiteralPath $rutaEntorno -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw '.env no puede ser un enlace o punto de reparacion.'
}

$servicioInicial = Get-Service -Name $nombreServicio -ErrorAction Stop
if ($servicioInicial.Status -ne 'Running') {
    throw "El servicio $nombreServicio debe estar Running antes de cambiar la impresion."
}
Assert-ServiceBelongsToInstallation -Root $raiz
$lectura = Read-Utf8Environment -Path $rutaEntorno
$bytesOriginales = $lectura.Bytes
$hashOriginal = (Get-FileHash -LiteralPath $rutaEntorno -Algorithm SHA256).Hash
$aclOriginal = Get-Acl -LiteralPath $rutaEntorno
$contextoSalud = Get-HealthContext -EnvironmentText $lectura.Text

$cambios = [ordered]@{
    PRINT_BACKEND = 'tcp'
    PRINT_SYNC = $PrintSync.ToString().ToLowerInvariant()
    PRINTER_CAJA_HOST = $HostCaja
    PRINTER_COCINA_HOST = $HostCocina
    PRINTER_BARRA_HOST = $HostBarra
    PRINTER_PORT = $Puerto.ToString([Globalization.CultureInfo]::InvariantCulture)
}
$textoNuevo = Set-DotEnvValuesInText -Text $lectura.Text -Values $cambios
$bytesNuevos = ConvertTo-Utf8EnvironmentBytes -Text $textoNuevo -HasBom $lectura.HasBom

$mutexMantenimiento = Enter-MaintenanceMutex
try {
    # No se toca una instalacion que ya estaba degradada antes de este cambio.
    Wait-ServiceHealth -Context $contextoSalud -TimeoutSeconds 10
    if ((Get-FileHash -LiteralPath $rutaEntorno -Algorithm SHA256).Hash -cne $hashOriginal) {
        throw '.env cambio durante las validaciones; reintenta cuando no haya otro mantenimiento.'
    }

    $sello = Get-Date -Format 'yyyyMMdd-HHmmss'
    $rutaRespaldo = Join-Path $raiz ('.env.impresion-backup-' + $sello + '-' +
        [Guid]::NewGuid().ToString('N').Substring(0, 8) + '.bak')
    [IO.File]::WriteAllBytes($rutaRespaldo, $bytesOriginales)
    Set-Acl -LiteralPath $rutaRespaldo -AclObject $aclOriginal
    if ((Get-FileHash -LiteralPath $rutaRespaldo -Algorithm SHA256).Hash -cne
        (Get-FileHash -LiteralPath $rutaEntorno -Algorithm SHA256).Hash) {
        throw 'El respaldo de .env no coincide byte por byte; no se aplico ningun cambio.'
    }

    Stop-PosService
    $estadoColaAcreditado = Get-BlockingPrintQueueState -Root $raiz -PythonPath $python
    if ($estadoColaAcreditado.bloqueantes -ne 0) {
        throw ("La activacion se detuvo: existen {0} trabajos PENDIENTE y {1} PROCESANDO. " +
            'Resuelve la cola con PRINT_BACKEND actual antes de habilitar TCP.') -f
            $estadoColaAcreditado.pendiente, $estadoColaAcreditado.procesando
    }

    Write-EnvironmentAtomically -Path $rutaEntorno -Bytes $bytesNuevos -Acl $aclOriginal
    $entornoModificado = $true
    Start-PosService
    Wait-ServiceHealth -Context $contextoSalud -TimeoutSeconds 30
    $diagnostico = Invoke-SafePrinterDiagnostic -Root $raiz -PythonPath $python

    Write-Host 'Impresion TCP activada y servicio saludable.' -ForegroundColor Green
    Write-Host 'Cola acreditada antes de activar TCP: PENDIENTE=0, PROCESANDO=0.'
    Write-Host "Configuracion: caja=$HostCaja, cocina=$HostCocina, barra=$HostBarra, puerto=$Puerto, PRINT_SYNC=$($cambios.PRINT_SYNC)."
    Write-Host 'El diagnostico no envio datos de impresion ni genero papel.'
    Write-Host "Respaldo protegido: $rutaRespaldo"
    $diagnostico | ConvertTo-Json -Depth 5 -Compress
}
catch {
    $motivo = $_.Exception.Message
    if ($entornoModificado) {
        try {
            Restore-EnvironmentAndService -EnvironmentPath $rutaEntorno `
                -OriginalBytes $bytesOriginales -OriginalAcl $aclOriginal `
                -HealthContext $contextoSalud
        }
        catch {
            throw "Fallo la activacion y la recuperacion automatica requiere revision manual. El respaldo esta en $rutaRespaldo."
        }
        throw "No se activo la impresion TCP. .env fue restaurado y el servicio volvio saludable. Motivo: $motivo"
    }
    if ($servicioDetenidoPorScript) {
        try {
            Start-PosService
            Wait-ServiceHealth -Context $contextoSalud -TimeoutSeconds 30
        }
        catch {
            throw 'No se modifico .env, pero el servicio no pudo recuperarse despues de inspeccionar la cola; requiere revision manual.'
        }
        throw "No se activo la impresion TCP. .env no fue modificado y el servicio volvio saludable. Motivo: $motivo"
    }
    throw
}
finally {
    if ($archivoTemporal -and (Test-Path -LiteralPath $archivoTemporal)) {
        Remove-Item -LiteralPath $archivoTemporal -Force -ErrorAction SilentlyContinue
    }
    if ($null -ne $mutexMantenimiento) {
        try {
            if ($mutexAdquirido) { $mutexMantenimiento.ReleaseMutex() }
        }
        finally { $mutexMantenimiento.Dispose() }
    }
}
