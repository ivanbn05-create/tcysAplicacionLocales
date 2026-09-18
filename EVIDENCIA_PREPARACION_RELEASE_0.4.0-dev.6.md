# Evidencia de preparación y actualización `0.4.0-dev.6`

Fecha: 18 de septiembre de 2026
Entorno: laboratorio local, sin sucursales en producción.

## Identidad del release

| Dato | Valor |
| --- | --- |
| Rama | `codex/candidata-0.4.0-dev.6` |
| Commit fuente | `8beb9a7957bc4f3833868454230519f5dac21392` |
| Versión | `0.4.0-dev.6` |
| ZIP SHA-256 | `2926e53234047f2acb0a646e7d8c3054c12efdee07dc0b7992e2134798d624c8` |
| Manifiesto SHA-256 | `617e16d20478bac92fb8a7d44bfee77c91a4ca7488691034faaeb750ca0ffe9a` |
| Tamaño del ZIP | 32,340,953 bytes |
| Archivos verificados | 209 |

Dos construcciones independientes produjeron el mismo tamaño, manifiesto y SHA-256.
Ambas pasaron el verificador oficial de release.

## Motivo de dev.6

`dev.5` centralizó la identidad de versión del runtime, la PWA y los módulos. Sus
dos ZIP fueron reproducibles, pero el primer intento de actualización real se detuvo
porque `herramientas/validar_despliegue.py` omitía el archivo raíz `VERSION` al
crear su snapshot efímero. El actualizador restauró automáticamente `dev.4`; no se
perdieron configuración ni datos.

`dev.6` incluye `VERSION` en ese snapshot y agrega una prueba de regresión. Se usó
una versión nueva para mantener inmutables los artefactos ya emitidos.

## Validación automatizada

- Suite Django dentro del snapshot efímero: 182/182 aprobadas.
- Suite de infraestructura: 74/74 aprobadas.
- Respaldo SQLite dentro del validador: 13/13 aprobadas.
- Host de servicio Windows dentro del validador: 5/5 aprobadas.
- `makemigrations --check --dry-run`: sin cambios.
- Check de despliegue HTTPS: sin incidencias.
- Check HTTP LAN: sólo los cuatro avisos TLS/cookies esperados para ese perfil.
- `git diff --check`: aprobado antes del commit y la construcción.

## Actualización real del laboratorio

La actualización de `C:\LosTocayosPOS` terminó con estado `ok` entre
`2026-09-18T11:18:45-06:00` y `2026-09-18T11:25:16-06:00`.

| Comprobación | Resultado |
| --- | --- |
| Versión instalada | `0.4.0-dev.6` |
| Commit instalado | `8beb9a7957bc4f3833868454230519f5dac21392` |
| Artefacto instalado | SHA-256 `2926e53234047f2acb0a646e7d8c3054c12efdee07dc0b7992e2134798d624c8` |
| `.env` | Conservado sin cambios |
| Salud HTTP | `ok` |
| Servicio | `Running`, inicio `Auto`, cuenta `NT AUTHORITY\LocalService` |
| Tareas | Respaldo SQLite y purgas físicas presentes en estado `Ready` |
| Respaldo previo | `C:\LosTocayosPOS-respaldo-lab-20260918-111903-ed525a08` |

La verificación oficial creó además un respaldo SQLite verificable antes de migrar.
No había migraciones pendientes.

## PWA y tabletas

La comprobación HTTP independiente confirmó:

- `Cache-Control: no-cache`;
- caché `tocayos-pos-0.4.0-dev.6`;
- cinco recursos CSS/JavaScript con `?v=0.4.0-dev.6`;
- ninguna referencia a `dev.3`, `dev.4` o `dev.5`;
- `skipWaiting()` y `clients.claim()`;
- manifiesto de tableta con `start_url=/tabletas/` y
  `display_override=fullscreen,standalone`.

Las tabletas que ya tuvieran la aplicación abierta deben recargar o volver a abrirla
una vez para usar los recursos nuevos.

## Datos y configuración conservados

Una lectura posterior desde el runtime instalado confirmó:

| Dato | Valor |
| --- | ---: |
| Clientes | 1,730 |
| Teléfonos | 1,434 |
| Domicilios | 1,772 |
| Pedidos de sucursal importados | 5 |
| Pedidos de sucursal importados activos | 5 |

La fuente de pedidos sigue siendo `supabase`, la sincronización automática está
activa y el módulo `pedidos_sucursales` permanece habilitado. La impresión conserva
`PRINT_BACKEND=archivo`; esta actualización no envió papel.

## Evidencia operativa

- Resultado: `C:\LosTocayosPOS-lab-actualizaciones\evidencia\resultado-20260918-111846.json`
- Transcript:
  `C:\LosTocayosPOS-lab-actualizaciones\evidencia\actualizacion-20260918-111846.txt`
- Verificación oficial:
  `C:\LosTocayosPOS-lab-actualizaciones\evidencia\verificacion-20260918-111846.txt`
- Datos posteriores:
  `C:\LosTocayosPOS-lab-actualizaciones\evidencia\postupdate-datos-dev6.json`

## Estado de promoción

`0.4.0-dev.6` es la candidata técnica vigente del laboratorio. No se declara base
estable ni versión de producción. Aún faltan la aceptación manual completa en una
tableta Android real, una impresión física controlada y la decisión explícita de
promoción.
