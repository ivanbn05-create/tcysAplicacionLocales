# Evidencia de preparacion de release `0.4.0-dev.9`

- Fecha de preparacion: 2026-09-21
- Estado: candidata de desarrollo validada como codigo fuente en laboratorio
- Rama: `codex/candidata-0.4.0-dev.9`
- Produccion: no existe ninguna sucursal en produccion en este momento

## Limite de esta preparacion

Esta revision prepara y valida el codigo fuente de `0.4.0-dev.9` en un checkout
aislado. Por instruccion expresa, **no se instalo ni se actualizo
`C:\LosTocayosPOS`**. Tampoco se ejecuto el instalador, el actualizador,
`-PrepareOnly`, una migracion sobre la base instalada, una impresion fisica ni una
conexion con el VPS.

Las pruebas usaron bases temporales o la base descartable
`runtime/prueba/db.sqlite3`, impresion a archivo, Supabase desactivado y
`VPS_CONSOLIDACION_URL` / `VPS_CONSOLIDACION_TOKEN` vacios. La instalacion local,
su servicio de Windows, su `.env`, su base y sus colas permanecieron intactos.

## Identidad

| Dato | Valor |
| --- | --- |
| Version | `0.4.0-dev.9` |
| Rama | `codex/candidata-0.4.0-dev.9` |
| Commit fuente limpio | **PENDIENTE DE CONGELAR** |
| Commit documental posterior | **PENDIENTE DE CONGELAR** |
| Artefacto ZIP instalable | **NO CONSTRUIDO EN ESTA ETAPA** |
| Reproducibilidad de artefacto | **NO EVALUADA EN ESTA ETAPA** |
| Instalacion / actualizacion | **NO EJECUTADA POR ALCANCE** |

No debe emitirse un artefacto con esta identidad si aparece una correccion posterior
al commit fuente. En ese caso corresponde incrementar la version.

## Alcance funcional integrado

1. **Pedidos de sucursales.** Un pedido procesado puede reactivarse con clave de
   administrador o usuario elevado, vuelve a ser editable y conserva auditoria,
   version y bloqueo. No se reactiva si pertenece a un corte. Su importe queda como
   informacion y ya no genera movimientos ni se suma automaticamente al efectivo.
   Chile usa KG y barbacoa LT.
2. **Comandas extensas.** Desde siete comensales se inserta un renglon y una linea
   punteada despues de cada grupo de seis, tanto en captura numerada como por nombres.
3. **Conflictos de edicion.** Un `409 version_entidad_desactualizada` adopta la
   version vigente, libera la interfaz y exige revisar/reconfirmar. No reenvia de
   forma automatica la operacion anterior ni duplica acciones. Las mutaciones se
   serializan por ticket.
4. **Comanda virtual.** El total superior aparece mas grande y en negritas.
5. **Pedidos para llevar.** El nombre del cliente se imprime con tipografia mayor
   exclusivamente en papel/render de Llevar. El borrador local mas reciente del
   nombre no puede ser sobrescrito por una respuesta asincrona anterior.
6. **Bebidas y consomes.** Se consolidan globalmente, sin comensal; la barbacoa
   conserva su asignacion individual.
7. **Directorio.** Ventas incorpora acceso por icono, listado paginado de todos los
   clientes de la sucursal, busqueda por cualquier campo y edicion operativa sin PIN
   administrativo. La API conserva aislamiento por sucursal.
8. **Fondo siguiente.** El fondo de un corte pasa a fondo anterior del siguiente
   turno, incluso cuando existen varios turnos el mismo dia o hay cruce de fecha.
9. **Previa de corte.** Se puede imprimir sin cerrar ni purgar datos cuando todos los
   pedidos estan pagados o liberados. El corte posterior retira la previa transitoria.
10. **Movimientos.** La cabecera de Ventas ofrece acceso rapido solo a usuarios
    elevados/administradores, solicita PIN y permite volver. La pantalla recibio una
    revision responsive conforme a Impeccable.

## Migracion `0019`

`ventas/migrations/0019_catalogo_sucursales_unidades_dev9.py` depende de
`0018_clave_administrador_instalaciones_existentes` y normaliza el catalogo heredado:

| Producto por `origen_id` | Unidad | Cantidad por precio | Precio |
| --- | --- | ---: | ---: |
| Barbacoa (`1`) | `LT` | `1.000` | Conserva sus precios |
| Chile (`7`) | `KG` | `1.000` | Cambia a `$64.00` solo el precio activo por cliente el 2026-09-21 |

Los precios historicos y futuros de Chile se conservan. La reversa es
`RunPython.noop`; antes de aplicar la migracion en una instalacion se requiere
respaldo restaurable y verificacion del catalogo. Los identificadores heredados
`origen_id=1` y `origen_id=7` no deben convertirse implicitamente en el contrato
de sincronizacion futura con el VPS.

## Decisiones tecnicas relevantes

- El orden de locks de los flujos de reactivacion/corte es
  `Sucursal -> Ticket -> Mesa`, con revalidacion del ticket despues de adquirir el
  lock de sucursal.
- Una segunda tentativa de corte vacio no consume el fondo propagado si no hubo
  tickets, movimientos, fondo nuevo ni otra actividad desde el corte anterior.
- La previa y su trabajo de impresion se crean dentro de una transaccion que mantiene
  el lock de sucursal.
- Los trabajos `PROCESANDO` usan `procesado_en` como lease. El timeout se configura
  con `PRINT_PROCESSING_TIMEOUT_SECONDS` (300 segundos por defecto, rango 30-3600);
  el worker recupera trabajos vencidos y una reactivacion solo bloquea trabajo activo.
- Los perfiles de desarrollo/prueba y `iniciar-prueba-lan.ps1` limpian de forma
  explicita URL/token del VPS y desactivan la fuente remota de pedidos.
- Ocultar un control no reemplaza autorizacion: el servidor comprueba permisos para
  reactivacion, previa, Movimientos y operaciones sensibles.
- Ninguna credencial, PIN, token, cadena de conexion ni contenido de `.env` forma
  parte del commit o de esta evidencia.

## Resultados de validacion

| Validacion | Resultado | Evidencia |
| --- | --- | --- |
| Version y migraciones | **APROBADA** | `VERSION=0.4.0-dev.9`; `makemigrations --check --dry-run`: sin cambios |
| Pruebas focales dev.9 | **49/49 APROBADAS** | negocio, impresion, directorio, frontend y concurrencia |
| Suite Django completa | **241/241 APROBADAS** | 180.265 s |
| Infraestructura Python | **87/87 APROBADAS** | `python -m unittest discover -s tests -v` |
| Validador de despliegue | **APROBADO** | Django 241/241; respaldo SQLite 13/13; host Windows 5/5; chequeos HTTPS/LAN esperados |
| Contrato instalador Windows | **APROBADO CON -SkipAcl** | variante ACL completa aplazada: la sesion actual no es elevada |
| JavaScript / Python | **APROBADA** | `node --check` en `app.js` y `admin.js`; `compileall` correcto |
| Calidad Git | **APROBADA** | `git diff --check` sin errores; avisos CRLF informativos |
| Impeccable | **SIN HALLAZGOS BLOQUEANTES** | 10 avisos de fuentes heredadas; sin anti-patrones nuevos |
| Seguridad del diff | **APROBADA** | sin secretos/archivos accidentales; instalador/updater sin cambios |
| Concurrencia | **APROBADA** | locks, 409 estable y recuperacion sin reenvio automatico cubiertos por pruebas |
| Render de comandas | **APROBADA** | 6/7/13: 0/1/2 separadores en modo numerado y por nombres; Llevar y agrupacion revisados |
| QA visual de interfaz | **APROBADA** | Ventas, Directorio y Movimientos a 1024x768; Tableta a 1280x800; sin desbordamiento horizontal y controles tactiles de al menos 44 px |
| Build reproducible | **NO CONSTRUIDO EN ESTA ETAPA** | se construira cuando se autorice preparar la actualizacion |
| Instalacion / update real | **NO EJECUTAR AHORA** | `C:\LosTocayosPOS` permanece intacto |
| Impresion fisica | **NO EJECUTAR AHORA** | validacion posterior a una actualizacion autorizada |

La evidencia de render descartable queda en
`runtime/prueba/evidencias-dev9/REPORTE-QA-IMPRESION.md`. El problema transitorio
`windows sandbox failed: helper_unknown_error: setup refresh had errors` impidio
abrir los PNG con `view_image`; se inspeccionaron los mismos bytes mediante el
entorno local de solo lectura y las mediciones instrumentadas quedaron en
`manifiesto.json`.

Las capturas `ventas-1024x768.png`, `directorio-1024x768.png`,
`movimientos-1024x768.png`, `tableta-1280x800.png` y
`tableta-pin-1280x800.png` tambien se revisaron visualmente. Las mediciones de
Movimientos y Tableta estan en `qa-visual-restante.json`.

## Receta reproducible de validacion

Ejecutar desde el checkout, con perfiles de prueba que limpien Supabase/VPS:

```powershell
node --check .\ventas\static\ventas\app.js
node --check .\ventas\static\ventas\admin.js
git diff --check

& .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
& .\.venv\Scripts\python.exe manage.py test `
  ventas.test_dev9_negocio `
  ventas.test_dev9_impresion `
  ventas.test_dev9_directorio `
  ventas.test_dev9_frontend `
  ventas.test_dev9_concurrencia --noinput
& .\.venv\Scripts\python.exe manage.py test --noinput
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
& .\.venv\Scripts\python.exe .\herramientas\validar_despliegue.py

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tests\test_instalador_windows.ps1 -SkipAcl
```

Despues de crear el commit funcional limpio:

```powershell
$repo = (Get-Location).Path
$version = (Get-Content .\VERSION -Raw).Trim()
$commit = (git rev-parse --verify HEAD).Trim()

& .\.venv\Scripts\python.exe .\herramientas\preflight_release.py `
  --source $repo `
  --expected-version $version `
  --expected-commit $commit
```

La variante ACL de `test_instalador_windows.ps1` debe ejecutarse mas adelante desde
una consola elevada. No ejecuta el instalador real, pero esta sesion no posee los
privilegios necesarios para validar esas ACL.

## Integracion futura con VPS y catalogos

La conexion del POS con el VPS, la fuente central de precios y los productos
especificos por sucursal siguen fuera de `dev.9`. El siguiente ciclo debe definir:

- URL y contrato versionado de la API;
- autenticacion y rotacion de credenciales por sucursal;
- autoridad de datos para productos, precios, unidades y vigencias;
- identificador estable de producto;
- catalogo base y anulaciones por sucursal;
- cache local y operacion completa sin Internet;
- frecuencia, idempotencia, conflictos y observabilidad;
- migracion inicial, respaldo, dry-run y rollback;
- separacion entre consolidacion mensual y configuracion operativa;
- piloto de una sucursal antes de habilitar otras.

Si ese frente cambia codigo, esquema o configuracion distribuida, corresponde una
version posterior a `0.4.0-dev.9`.

## Estado de promocion

`0.4.0-dev.9` es una candidata de codigo fuente para laboratorio. No esta instalada,
no actualizo `C:\LosTocayosPOS`, no es version base y no es produccion. La decision
de construir artefactos, aplicar una actualizacion y promoverla requiere una
instruccion posterior. El protocolo general sigue siendo
`PROTOCOLO_RELEASE_ACTUALIZACION_REUTILIZABLE.md`.