$ErrorActionPreference = "Stop"
$instalacionEsperada = [IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA "Programs\LosTocayosPOS"))
$instalacionActual = [IO.Path]::GetFullPath($PSScriptRoot)

if ($instalacionActual -ne $instalacionEsperada) {
    throw "El desinstalador no se encuentra en la carpeta de instalación esperada."
}

$confirmacion = Read-Host "¿Desinstalar Los Tocayos POS de esta computadora? [s/N]"
if ($confirmacion -notmatch "^[Ss]") {
    exit 0
}

$escritorio = [Environment]::GetFolderPath("Desktop")
$accesoEscritorio = Join-Path $escritorio "Los Tocayos POS.lnk"
if (Test-Path -LiteralPath $accesoEscritorio) {
    Remove-Item -LiteralPath $accesoEscritorio -Force
}

$menuInicio = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Los Tocayos POS"
if (Test-Path -LiteralPath $menuInicio) {
    Remove-Item -LiteralPath $menuInicio -Recurse -Force
}

$registro = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LosTocayosPOS"
if (Test-Path -LiteralPath $registro) {
    Remove-Item -LiteralPath $registro -Recurse -Force
}

$limpiador = Join-Path $env:TEMP ("desinstalar-tocayos-" + [Guid]::NewGuid().ToString("N") + ".ps1")
$rutaSegura = $instalacionEsperada.Replace("'", "''")
$contenido = @"
Start-Sleep -Seconds 2
`$destino = [IO.Path]::GetFullPath('$rutaSegura')
`$esperado = [IO.Path]::GetFullPath((Join-Path `$env:LOCALAPPDATA 'Programs\LosTocayosPOS'))
if (`$destino -eq `$esperado -and (Test-Path -LiteralPath `$destino)) {
    Remove-Item -LiteralPath `$destino -Recurse -Force
}
Remove-Item -LiteralPath `$MyInvocation.MyCommand.Path -Force
"@
Set-Content -LiteralPath $limpiador -Value $contenido -Encoding UTF8
Start-Process `
    -FilePath (Join-Path $PSHOME "powershell.exe") `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $limpiador) `
    -WindowStyle Hidden

Write-Host "Los Tocayos POS fue desinstalado." -ForegroundColor Green
