# Preparación de release `0.4.0-dev.3`

Fecha de actualización: 2026-09-17.

## Estado de la candidata

`0.4.0-dev.3` es una candidata funcional de laboratorio. **No existe ninguna
sucursal en producción y este documento no autoriza un despliegue productivo.**
La implementación y su validación automatizada están completas, pero todavía no se
ha construido el artefacto reproducible, no se ha terminado una instalación limpia
desde ese artefacto y no se ha actualizado `C:\LosTocayosPOS`.

Por esas tres razones, y por la aceptación física Android pendiente, la candidata no
debe etiquetarse todavía como base estándar.

## Resultado de validación automatizada

| Validación | Resultado |
| --- | --- |
| Suite Django completa | 172/172 aprobadas |
| Suite `unittest` de infraestructura | 68/68 aprobadas |
| `tests/test_instalador_windows.ps1 -SkipAcl` | Aprobada |
| `django check` con configuración de pruebas | Sin hallazgos |
| `makemigrations --check --dry-run` | Sin cambios pendientes |
| Sintaxis de `ventas/static/ventas/app.js` y `admin.js` | Correcta |
| `git diff --check` | Correcto |
| Detector de Impeccable | Sin hallazgos |

La ejecución con `-SkipAcl` valida el contrato del instalador, pero no sustituye una
aceptación elevada de las ACL sobre el artefacto finalmente instalado.

## Cobertura de los 14 requisitos

| N.º | Comportamiento de dev.3 | Estado |
| ---: | --- | --- |
| 1 | Instalación nueva con clave maestra inicial `0000` almacenada como hash | Cubierto |
| 2 | Administrador elevado con permisos acotados; usuarios/PIN, folios y clave maestra reservados al maestro | Cubierto |
| 3 | Corte diario reimprimible, purga de detalle y consolidación mensual sólo después del acuse del VPS | Cubierto por código y pruebas; VPS real pendiente |
| 4 | Ticket total muestra únicamente el nombre del producto | Cubierto |
| 5 | Pedidos de sucursal organizados en subtabs | Cubierto |
| 6 | Refresco periódico de terminales LAN | Cubierto |
| 7 | Todos los PIN activos pueden acceder a Ventas con atribución de operador | Cubierto |
| 8 | 40 posiciones de Llevar y 40 de Recoger | Cubierto |
| 9 | `pedidos_sucursales` es opcional y no se habilita por defecto | Cubierto |
| 10 | Programados editables, eliminables y desprogramables; Recoger admite programación | Cubierto |
| 11 | Un pedido futuro no inicia el turno antes de su activación | Cubierto |
| 12 | Producto personalizado disponible en todos los canales | Cubierto |
| 13 | Movimientos, control de caja, aplicaciones y sucursales con fórmula de efectivo delimitada | Cubierto |
| 14 | `/tabletas` con teclado numérico y solicitud de pantalla completa | Cubierto automáticamente; validación física Android pendiente |

## Cierre físico y tareas programadas

El corte y la consolidación pueden borrar el detalle lógico dentro de la transacción
sin tener privilegios para eliminar todos los archivos o respaldos protegidos. Para
cerrar esa segunda fase, dev.3 registra solicitudes persistentes bajo
`runtime\purgas-pendientes`.

La tarea `LosTocayosPOS-PurgasFisicas` se ejecuta como SYSTEM cada cinco minutos.
Usa `-OnlyIfPurgePending`, por lo que no crea respaldos si no existe trabajo físico.
Cuando corresponde, genera y verifica un respaldo posterior a la purga, elimina
archivos administrados y tripletes de respaldo que todavía contengan detalle
sensible, y sólo entonces permite que la consolidación quede marcada como purgada.
Los reintentos reutilizan el respaldo ya asignado y evitan proliferar copias.

`LosTocayosPOS-RespaldoSQLite` sigue siendo la tarea diaria. En una instalación
nueva usa por defecto las `03:15` y 30 días de retención; ambos valores son
configurables. Una actualización conserva el horario y la retención existentes.

## Actualizador reversible de laboratorio

`actualizar-laboratorio-desde-release.ps1` está preparado para la prueba real sobre
`C:\LosTocayosPOS`. Su contrato es:

1. Exige elevación y valida que el servicio pertenece a la instalación indicada.
2. Verifica ZIP, manifiesto, SHA-256 y versión antes de detener el servicio.
3. Extrae a un staging externo y rechaza enlaces o junctions.
4. Captura en memoria los XML, o la ausencia, de
   `LosTocayosPOS-RespaldoSQLite` y `LosTocayosPOS-PurgasFisicas`.
5. Detiene el servicio saludable, mueve el árbol anterior a un respaldo completo y
   promueve el staging.
6. Conserva `.env`, `.venv`, `runtime`, `media`, `logs`, `backups` y los archivos
   SQLite de la raíz: `db.sqlite3`, `db.sqlite3-wal`, `db.sqlite3-shm` y
   `db.sqlite3-journal`. `certs` permanece como contenido versionado de la release.
7. Ejecuta `actualizar-servidor.ps1`, comprueba que el servicio apunta al árbol
   promovido y valida salud.
8. Si cualquier fase falla, aparta la candidata fallida, restaura el árbol anterior,
   restaura las dos tareas programadas y vuelve a comprobar servicio y salud.

El respaldo completo anterior no se elimina automáticamente. Esto permite revisar o
revertir el laboratorio, pero exige una política explícita antes de usar un flujo
equivalente en producción.

## Parche explícito de catálogo

La candidata contiene un parche acotado para `ARBOLEDAS`: el producto `AM` pasa a
nombre corto `Topo` y precio vigente `$32.00`. El comando exige la sucursal y valida
el estado anterior antes de escribir. No revela secretos y es idempotente; si
encuentra una personalización distinta, se detiene.

Validación previa:

```powershell
& .\.venv\Scripts\python.exe manage.py `
  aplicar_parche_catalogo_0_4_0_dev_3_topo `
  --sucursal ARBOLEDAS --dry-run
```

Aplicación, sólo después de respaldo y migraciones:

```powershell
& .\.venv\Scripts\python.exe manage.py `
  aplicar_parche_catalogo_0_4_0_dev_3_topo `
  --sucursal ARBOLEDAS
```

Una actualización no debe ejecutar `cargar_datos_iniciales`.

## Construcción reproducible pendiente

La construcción final debe partir de un commit limpio. Se usarán el mismo commit,
`SOURCE_DATE_EPOCH` y wheelhouse CPython 3.13 x64 en dos directorios vacíos. Ambos
ZIP deberán pasar `release_servidor.py verify` y producir el mismo SHA-256.

```powershell
$commit = git rev-parse HEAD
$epoch = git show -s --format=%ct $commit

& .\.venv\Scripts\python.exe .\herramientas\release_servidor.py build `
  --version 0.4.0-dev.3 --source . --output .\release\build-1 `
  --commit $commit --source-date-epoch $epoch `
  --wheelhouse ..\wheelhouse-base-a

& .\.venv\Scripts\python.exe .\herramientas\release_servidor.py build `
  --version 0.4.0-dev.3 --source . --output .\release\build-2 `
  --commit $commit --source-date-epoch $epoch `
  --wheelhouse ..\wheelhouse-base-a
```

No debe usarse `--allow-dirty`. El wheelhouse debe coincidir exactamente con
`requirements-lock.txt` y contener wheels `cp313-win_amd64`.

## Evidencia pendiente antes de promover

1. Dos builds reproducibles con SHA-256 idéntico y verificación de manifiesto.
2. Instalación limpia aislada desde el ZIP, incluyendo salud, bootstrap, clave
   `0000`, 40 posiciones por canal y módulos opcionales vacíos.
3. Actualización real de `C:\LosTocayosPOS` con evidencia antes/después de `.env`,
   identidad, base, usuarios, módulos, impresoras, tareas, horario, retención y salud.
4. Dry-run y aplicación controlada del parche `Topo` en el laboratorio instalado.
5. Aceptación física en tableta Android del teclado y pantalla completa; aceptación
   física de impresión según la configuración elegida.
6. Endpoint, autenticación y operación reales del VPS.
7. HTTPS LAN, enrolamiento de terminales y canal remoto de releases firmado.

## Límites conocidos del updater

- Los hashes prueban integridad, pero no identidad del publicador. No existe todavía
  una firma remota ni un canal de descarga autenticado.
- Hay una ventana TOCTOU local entre verificar el ZIP y consumirlo para el staging.
  El artefacto y su carpeta deben permanecer restringidos hasta cerrar el manejo
  mediante copia inmutable o verificación bajo un identificador bloqueado.
- Si el rollback conserva una candidata fallida, ese árbol no queda acreditado con
  las ACL finales. Debe tratarse como evidencia diagnóstica, mantenerse restringido
  y no arrancarse como servicio.
- El wrapper es exclusivamente de laboratorio, elevado y supervisado. Todavía no es
  un actualizador productivo desatendido.
- Falta el proyecto Android WebView/TWA, la firma del APK y la instalación USB-C.
- El contrato Edge del VPS está probado con respuestas simuladas; el backend real y
  sus credenciales aún no existen.

Hasta cerrar la construcción, la instalación limpia, la actualización y la aceptación
física, `0.4.0-dev.3` sigue siendo candidata de laboratorio.