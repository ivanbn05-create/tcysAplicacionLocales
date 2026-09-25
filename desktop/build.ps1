$ErrorActionPreference = "Stop"

$desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$salida = Join-Path $desktop "dist"
$directorioTemporal = Join-Path $desktop "build"
$fuente = Join-Path $desktop "TocayosPOS.cs"
$compilador = Join-Path $env:WINDIR "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
$ejecutable = Join-Path $salida "TocayosPOS.exe"
$version = (Get-Content -LiteralPath (Join-Path $desktop "../VERSION") -Raw).Trim()
$coincidencia = [regex]::Match($version, '^([0-9]+)\.([0-9]+)\.([0-9]+)(?:-dev\.([0-9]+))?$')
if (-not $coincidencia.Success) {
    throw "VERSION debe usar X.Y.Z o X.Y.Z-dev.N."
}
$revision = if ($coincidencia.Groups[4].Success) { [int]$coincidencia.Groups[4].Value } else { 99 }
if ($revision -gt 98 -and $coincidencia.Groups[4].Success) {
    throw "El número de candidata dev supera el rango 1..98."
}
$versionArchivo = "{0}.{1}.{2}.{3}" -f $coincidencia.Groups[1].Value, $coincidencia.Groups[2].Value, $coincidencia.Groups[3].Value, $revision
$fuenteVersion = Join-Path $directorioTemporal "Version.generated.cs"

if (-not (Test-Path -LiteralPath $compilador)) {
    throw "No se encontró el compilador de .NET Framework en $compilador"
}
New-Item -ItemType Directory -Force -Path $salida, $directorioTemporal | Out-Null
$atributos = @(
    'using System.Reflection;',
    "[assembly: AssemblyVersion(""$versionArchivo"")]",
    "[assembly: AssemblyFileVersion(""$versionArchivo"")]",
    "[assembly: AssemblyInformationalVersion(""$version"")]"
)
Set-Content -LiteralPath $fuenteVersion -Value $atributos -Encoding UTF8

$argumentos = @("/nologo", "/target:winexe", "/platform:anycpu", "/optimize+", "/debug-", "/reference:System.dll", "/reference:System.Windows.Forms.dll", "/out:$ejecutable", $fuente, $fuenteVersion)
& $compilador @argumentos
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $ejecutable)) {
    throw "La compilación del cliente de escritorio falló."
}

Write-Host "Cliente generado: $ejecutable ($version)" -ForegroundColor Green
