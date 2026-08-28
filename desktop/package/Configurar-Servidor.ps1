param(
    [string]$ServerUrl,
    [switch]$AllowInsecureHttp
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
    $uri.Scheme -notin @("http", "https") -or
    -not [string]::IsNullOrEmpty($uri.UserInfo) -or
    $uri.AbsolutePath -ne "/" -or
    -not [string]::IsNullOrEmpty($uri.Query) -or
    -not [string]::IsNullOrEmpty($uri.Fragment)) {
    Write-Host "La dirección debe ser sólo el origen HTTP(S), sin credenciales, ruta, consulta ni fragmento." -ForegroundColor Red
    Read-Host "Presiona Enter para cerrar"
    exit 2
}

if ($uri.Scheme -eq "http" -and -not $AllowInsecureHttp) {
    Write-Host ""
    Write-Host "ADVERTENCIA: HTTP permite leer o alterar sesiones y pedidos desde la LAN." -ForegroundColor Red
    $aceptacion = Read-Host "Escribe exactamente HTTP LAN para guardar esta dirección"
    if ($aceptacion -cne "HTTP LAN") {
        Write-Host "Cambio cancelado. Conserva o configura una dirección HTTPS." -ForegroundColor Yellow
        exit 4
    }
}

try {
    $response = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri ($ServerUrl + "/salud/") `
        -TimeoutSec 3
    $contenido = $response.Content | ConvertFrom-Json
    $conectado = $response.StatusCode -eq 200 -and $contenido.estado -eq "ok"
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
