$ErrorActionPreference = "Stop"

$desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$salida = Join-Path $desktop "dist"
$fuente = Join-Path $desktop "TocayosPOS.cs"
$compilador = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$ejecutable = Join-Path $salida "TocayosPOS.exe"

if (-not (Test-Path -LiteralPath $compilador)) {
    throw "No se encontró el compilador de .NET Framework en $compilador"
}

New-Item -ItemType Directory -Force -Path $salida | Out-Null

& $compilador `
    /nologo `
    /target:winexe `
    /platform:anycpu `
    /reference:System.dll `
    /reference:System.Windows.Forms.dll `
    "/out:$ejecutable" `
    $fuente

if ($LASTEXITCODE -ne 0) {
    throw "La compilación del aplicativo de escritorio falló."
}

Write-Host "Aplicativo generado en $ejecutable" -ForegroundColor Green
