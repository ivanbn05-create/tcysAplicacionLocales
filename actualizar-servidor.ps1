<#
.SYNOPSIS
Actualiza el código ya colocado del servidor POS sin cambiar su configuración.
.DESCRIPTION
Conserva byte por byte .env, no vuelve a aprovisionar la sucursal, no carga
catálogos ni solicita cuentas. Conserva horario y retención de respaldo al
normalizar las tareas administradas; no modifica el firewall. La conmutación y
el rollback de la release completa corresponden al actualizador de laboratorio.
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
