param(
    [string]$ServerUrl
)

$ErrorActionPreference = "Stop"
$configuracion = Join-Path $PSScriptRoot "servidor.txt"
$actual = if (Test-Path -LiteralPath $configuracion) {
    (Get-Content -LiteralPath $configuracion -Raw).Trim()
}
else {
    "http://192.168.0.30:8000"
}

if ([string]::IsNullOrWhiteSpace($ServerUrl)) {
    Write-Host "Configuración de Los Tocayos POS" -ForegroundColor Yellow
    $respuesta = Read-Host "Dirección del servidor [$actual]"
    $ServerUrl = if ([string]::IsNullOrWhiteSpace($respuesta)) { $actual } else { $respuesta.Trim() }
}

$ServerUrl = $ServerUrl.TrimEnd("/")
$uri = $null
if (-not [Uri]::TryCreate($ServerUrl, [UriKind]::Absolute, [ref]$uri) -or
    $uri.Scheme -notin @("http", "https")) {
    Write-Host "La dirección no es una URL HTTP o HTTPS válida." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 2
}

try {
    $response = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri ($ServerUrl + "/api/estado/") `
        -TimeoutSec 3
    $conectado = $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
}
catch {
    $conectado = $false
}

if ($conectado) {
    Write-Host "Servidor encontrado correctamente." -ForegroundColor Green
}
else {
    Write-Host "No se pudo conectar con $ServerUrl." -ForegroundColor Yellow
    $guardar = Read-Host "¿Guardar la dirección de todas formas? [s/N]"
    if ($guardar -notmatch "^[Ss]") {
        exit 3
    }
}

Set-Content -LiteralPath $configuracion -Value $ServerUrl -Encoding ASCII
Write-Host "Configuración guardada: $ServerUrl" -ForegroundColor Green
Read-Host "Presiona Enter para cerrar"
