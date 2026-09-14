<#
.SYNOPSIS
Actualiza el código ya colocado del servidor POS sin cambiar su configuración.
.DESCRIPTION
Conserva byte por byte .env, no vuelve a aprovisionar la sucursal, no carga
catálogos, no solicita cuentas y no modifica firewall ni tareas programadas.
Esta primera fase aún no descarga ni conmuta releases de forma atómica.
#>
[CmdletBinding()]
param(
    [switch]$AllowOnlineDependencies,
    [switch]$AllowUnverifiedDevelopmentTree
)

$ErrorActionPreference = "Stop"
$motor = Join-Path $PSScriptRoot "instalar-servicio-lan.ps1"
& $motor `
    -Modo Actualizar `
    -AllowOnlineDependencies:$AllowOnlineDependencies `
    -AllowUnverifiedDevelopmentTree:$AllowUnverifiedDevelopmentTree
