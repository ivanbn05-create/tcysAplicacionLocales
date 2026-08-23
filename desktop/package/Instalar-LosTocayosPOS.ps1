param(
    [string]$ServerUrl,
    [string]$InstallRoot,
    [switch]$SkipIntegration
)

$ErrorActionPreference = "Stop"
$nombre = "Los Tocayos POS"
$instalacion = if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    Join-Path $env:LOCALAPPDATA "Programs\LosTocayosPOS"
}
else {
    [IO.Path]::GetFullPath($InstallRoot)
}
$ejecutableOrigen = Join-Path $PSScriptRoot "TocayosPOS.exe"
$configuracionOrigen = Join-Path $PSScriptRoot "servidor.txt"
$configuracionDestino = Join-Path $instalacion "servidor.txt"

function Test-ServerUrl {
    param([string]$Value)

    $uri = $null
    return [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -and
        $uri.Scheme -in @("http", "https")
}

function Test-ServerConnection {
    param([string]$Value)

    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri ($Value.TrimEnd("/") + "/api/estado/") `
            -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $ejecutableOrigen)) {
    throw "El paquete está incompleto: no se encontró TocayosPOS.exe."
}

$predeterminado = "http://192.168.0.30:8000"
if (Test-Path -LiteralPath $configuracionDestino) {
    $predeterminado = (Get-Content -LiteralPath $configuracionDestino -Raw).Trim()
}
elseif (Test-Path -LiteralPath $configuracionOrigen) {
    $predeterminado = (Get-Content -LiteralPath $configuracionOrigen -Raw).Trim()
}

if ([string]::IsNullOrWhiteSpace($ServerUrl)) {
    Write-Host ""
    Write-Host "Instalación de $nombre" -ForegroundColor Yellow
    Write-Host "La dirección debe apuntar a la computadora principal del local."
    $respuesta = Read-Host "Dirección del servidor [$predeterminado]"
    $ServerUrl = if ([string]::IsNullOrWhiteSpace($respuesta)) {
        $predeterminado
    }
    else {
        $respuesta.Trim()
    }
}

$ServerUrl = $ServerUrl.TrimEnd("/")
if (-not (Test-ServerUrl -Value $ServerUrl)) {
    Write-Host "La dirección '$ServerUrl' no es una URL HTTP o HTTPS válida." -ForegroundColor Red
    exit 2
}

Write-Host "Comprobando $ServerUrl ..."
$conectado = Test-ServerConnection -Value $ServerUrl
if ($conectado) {
    Write-Host "Servidor encontrado en la red." -ForegroundColor Green
}
else {
    Write-Host "No fue posible conectar con el servidor en este momento." -ForegroundColor Yellow
    Write-Host "Puede instalarse ahora y corregirse después desde Configurar servidor."
    $continuar = Read-Host "¿Continuar con la instalación? [S/n]"
    if ($continuar -match "^[Nn]") {
        exit 3
    }
}

New-Item -ItemType Directory -Force -Path $instalacion | Out-Null

$archivos = @(
    "TocayosPOS.exe",
    "Configurar-Servidor.ps1",
    "Desinstalar-LosTocayosPOS.ps1",
    "LEEME.txt"
)
foreach ($archivo in $archivos) {
    $origen = Join-Path $PSScriptRoot $archivo
    if (Test-Path -LiteralPath $origen) {
        Copy-Item -LiteralPath $origen -Destination (Join-Path $instalacion $archivo) -Force
    }
}
Set-Content -LiteralPath $configuracionDestino -Value $ServerUrl -Encoding ASCII

if ($SkipIntegration) {
    Write-Host "$nombre quedó copiado correctamente en $instalacion." -ForegroundColor Green
    exit 0
}

$wsh = New-Object -ComObject WScript.Shell
$escritorio = [Environment]::GetFolderPath("Desktop")
$menuInicio = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Los Tocayos POS"
New-Item -ItemType Directory -Force -Path $menuInicio | Out-Null

function New-Shortcut {
    param(
        [string]$Path,
        [string]$Target,
        [string]$Arguments,
        [string]$Description
    )

    $shortcut = $wsh.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $instalacion
    $shortcut.Description = $Description
    $shortcut.IconLocation = "$Target,0"
    $shortcut.Save()
}

$ejecutableDestino = Join-Path $instalacion "TocayosPOS.exe"
New-Shortcut `
    -Path (Join-Path $escritorio "Los Tocayos POS.lnk") `
    -Target $ejecutableDestino `
    -Arguments "" `
    -Description "Punto de venta Los Tocayos"
New-Shortcut `
    -Path (Join-Path $menuInicio "Los Tocayos POS.lnk") `
    -Target $ejecutableDestino `
    -Arguments "" `
    -Description "Punto de venta Los Tocayos"
New-Shortcut `
    -Path (Join-Path $menuInicio "Los Tocayos POS - Tableta.lnk") `
    -Target $ejecutableDestino `
    -Arguments "--tableta" `
    -Description "Interfaz táctil de Los Tocayos"

$powershell = Join-Path $PSHOME "powershell.exe"
New-Shortcut `
    -Path (Join-Path $menuInicio "Configurar servidor.lnk") `
    -Target $powershell `
    -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $instalacion "Configurar-Servidor.ps1") + '"') `
    -Description "Cambiar y comprobar el servidor de la sucursal"
New-Shortcut `
    -Path (Join-Path $menuInicio "Desinstalar Los Tocayos POS.lnk") `
    -Target $powershell `
    -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $instalacion "Desinstalar-LosTocayosPOS.ps1") + '"') `
    -Description "Desinstalar Los Tocayos POS"

$registro = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LosTocayosPOS"
New-Item -Path $registro -Force | Out-Null
Set-ItemProperty -Path $registro -Name DisplayName -Value $nombre
Set-ItemProperty -Path $registro -Name DisplayVersion -Value "0.2.0"
Set-ItemProperty -Path $registro -Name Publisher -Value "Los Tocayos"
Set-ItemProperty -Path $registro -Name InstallLocation -Value $instalacion
Set-ItemProperty -Path $registro -Name DisplayIcon -Value $ejecutableDestino
Set-ItemProperty `
    -Path $registro `
    -Name UninstallString `
    -Value ('"' + $powershell + '" -NoProfile -ExecutionPolicy Bypass -File "' +
        (Join-Path $instalacion "Desinstalar-LosTocayosPOS.ps1") + '"')
Set-ItemProperty -Path $registro -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $registro -Name NoRepair -Value 1 -Type DWord

Write-Host ""
Write-Host "$nombre quedó instalado correctamente." -ForegroundColor Green
Write-Host "Servidor configurado: $ServerUrl"
Write-Host "Acceso directo: $(Join-Path $escritorio 'Los Tocayos POS.lnk')"
exit 0
