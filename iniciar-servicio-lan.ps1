param(
    [ValidateRange(1, 65535)][int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$entorno = Join-Path $raiz ".env"
$nombreServicio = "LosTocayosPOS"
$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidad)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ejecuta este script desde PowerShell como administrador."
}

function Get-DotEnvValue {
    param([string]$Name)

    if (-not (Test-Path -LiteralPath $entorno)) { return $null }
    $patron = "^\s*" + [Regex]::Escape($Name) + "\s*=(.*)$"
    foreach ($linea in Get-Content -LiteralPath $entorno) {
        if ($linea -match $patron) { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $null
}

if (-not $PSBoundParameters.ContainsKey("Port")) {
    $puertoConfigurado = Get-DotEnvValue -Name "WAITRESS_PORT"
    $puertoLeido = 0
    if ($puertoConfigurado -and [int]::TryParse($puertoConfigurado, [ref]$puertoLeido) -and
        $puertoLeido -ge 1 -and $puertoLeido -le 65535) {
        $Port = $puertoLeido
    }
    elseif ($puertoConfigurado) {
        throw "WAITRESS_PORT no es un puerto válido."
    }
}

$servicio = Get-Service -Name $nombreServicio -ErrorAction SilentlyContinue
if (-not $servicio) {
    throw "El servicio no está instalado. Ejecuta primero instalar-servidor.ps1."
}
if ($servicio.Status -ne "Running") {
    Start-Service -Name $nombreServicio
    $servicio.WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
}

$hostConfigurado = Get-DotEnvValue -Name "WAITRESS_HOST"
if ([string]::IsNullOrWhiteSpace($hostConfigurado) -or $hostConfigurado -in @("0.0.0.0", "::")) {
    $hostConfigurado = "127.0.0.1"
}
$direccionSalud = $null
if (-not [Net.IPAddress]::TryParse($hostConfigurado, [ref]$direccionSalud)) {
    throw "WAITRESS_HOST no es una dirección IP válida."
}
$hostUrl = if ($direccionSalud.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6) {
    "[$hostConfigurado]"
}
else {
    $hostConfigurado
}
$url = "http://${hostUrl}:$Port/salud/"
$headers = @{}
$hostsPermitidos = Get-DotEnvValue -Name "DJANGO_ALLOWED_HOSTS"
if (-not [string]::IsNullOrWhiteSpace($hostsPermitidos)) {
    $headers["Host"] = ($hostsPermitidos -split ",")[0]
}
if ((Get-DotEnvValue -Name "DJANGO_HTTPS") -match "^(1|true|si|sí|yes)$") {
    $headers["X-Forwarded-Proto"] = "https"
}
$limite = (Get-Date).AddSeconds(30)
do {
    try {
        $respuesta = Invoke-WebRequest -UseBasicParsing -Uri $url -Headers $headers -TimeoutSec 2
        $contenido = $respuesta.Content | ConvertFrom-Json
        if ($respuesta.StatusCode -eq 200 -and $contenido.estado -eq "ok") {
            Write-Host "Los Tocayos POS está activo en el puerto $Port." -ForegroundColor Green
            exit 0
        }
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
} while ((Get-Date) -lt $limite)

throw "El servicio figura activo, pero la comprobación HTTP falló. Revisa logs\waitress.log."
