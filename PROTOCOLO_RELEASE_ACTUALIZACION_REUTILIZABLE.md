# Protocolo reproducible de release y actualización

Este documento permite preparar, aplicar y acreditar una actualización del servidor
Edge sin depender del historial de una conversación. El flujo vigente es local,
elevado, supervisado y exclusivo del laboratorio. No existe una sucursal en
producción y `actualizar-laboratorio-desde-release.ps1` no debe presentarse como un
actualizador remoto o desatendido.

## 1. Lecciones que fijan el contrato

| Candidata | Resultado | Regla permanente |
| --- | --- | --- |
| `0.4.0-dev.4` | Los dos ZIP fueron reproducibles y la actualización funcionó, pero el service worker todavía publicaba la caché de `dev.3`. | `VERSION`, runtime, módulos, caché PWA y URLs de assets deben coincidir antes de construir. |
| `0.4.0-dev.5` | Centralizó la versión y produjo dos artefactos idénticos. La actualización real falló en la validación aislada porque el snapshot omitía el archivo raíz `VERSION`; el rollback recuperó `dev.4`. | Una build reproducible no sustituye la prueba del snapshot efímero. Una versión ya emitida no se reconstruye ni se reutiliza después de descubrir un defecto. |
| `0.4.0-dev.6` | Incluyó `VERSION` en el snapshot, añadió una regresión y completó dos builds, verificación, actualización, respaldo y comprobación PWA. | La release se identifica por versión, commit fuente y SHA-256 del ZIP. El commit documental posterior se registra aparte. |

Los commits fuente acreditados fueron `2739944ee5da28b35fa7b4c6330e76ba83605aab`
para `dev.4`, `c5731e4` para `dev.5` y
`8beb9a7957bc4f3833868454230519f5dac21392` para `dev.6`. Las evidencias históricas
están en `EVIDENCIA_PREPARACION_RELEASE_0.4.0-dev.4.md` y
`EVIDENCIA_PREPARACION_RELEASE_0.4.0-dev.6.md`.

## 2. Datos que deben definirse antes de empezar

Registrar en una hoja de trabajo, sin secretos:

- ruta absoluta del checkout limpio;
- versión objetivo y rama candidata;
- commit fuente que se construirá;
- ruta del wheelhouse plano para CPython 3.13/Windows x64;
- raíz de salida externa al repositorio;
- instalación objetivo y workspace externo de actualización;
- versión instalada, sucursal y estado de salud actuales;
- módulos habilitados antes de actualizar;
- motor y ruta administrada de la base;
- modo de impresión esperado: `archivo` o `tcp`;
- invariantes de datos que deben conservarse, por ejemplo cantidades de clientes,
  teléfonos, domicilios y pedidos importados;
- cambios de datos posteriores, si existen, cada uno con su propio dry-run y
  criterio de idempotencia.

Detener el procedimiento si falta cualquiera de esos datos, si el servicio actual no
está saludable, si no hay respaldo verificable o si el artefacto no corresponde a un
commit limpio. Nunca obtener credenciales copiando `.env` a la evidencia.

La elección de módulos e impresión pertenece a cada sucursal y debe decidirse,
configurarse y probarse antes de liberar. El paquete y la actualización nunca cambian
`.env`; cualquier ajuste de impresión o módulos es una operación separada, con
respaldo y evidencia propios.

## 3. Congelar una única identidad de versión

1. Cambiar `VERSION` una sola vez durante el desarrollo de la candidata.
2. Confirmar que contiene exactamente una línea y que el runtime sigue leyendo
   `pos/version.py`.
3. Ejecutar pruebas y correcciones antes del commit fuente.
4. Crear el commit funcional final. La evidencia y otros documentos posteriores
   pueden vivir en otro commit, pero no cambian la identidad del ZIP ya construido.
5. No volver a construir bajo una versión ya emitida. Si se descubre un defecto,
   incrementar la versión, como ocurrió de `dev.5` a `dev.6`.

Desde la raíz del checkout ya confirmado y limpio:

```powershell
$repo = (Get-Location).Path
$version = (Get-Content -LiteralPath (Join-Path $repo 'VERSION') -Raw).Trim()
$commit = (git rev-parse --verify HEAD).Trim()

& .\.venv\Scripts\python.exe .\herramientas\preflight_release.py `
    --source $repo `
    --expected-version $version `
    --expected-commit $commit
```

El resultado debe ser JSON con `status: ok`. Guardar `version`, `commit` y
`source_date_epoch`: los dos builds deben usar exactamente esos valores. El preflight
rechaza cambios rastreados o sin rastrear, una segunda fuente `VERSION`, una versión
distinta en `HEAD`, la pérdida de propagación hacia PWA/módulos y un snapshot que no
copie `VERSION`.

## 4. Validación previa del commit fuente

Ejecutar, como mínimo:

```powershell
& .\.venv\Scripts\python.exe .\herramientas\validar_despliegue.py
& .\.venv\Scripts\python.exe manage.py test --noinput
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File .\tests\test_instalador_windows.ps1
& .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
node --check .\ventas\static\ventas\app.js
node --check .\ventas\static\ventas\admin.js
git diff --check
git status --short
```

`validar_despliegue.py` crea un snapshot sin `.env` ni bases reales e incluye
obligatoriamente `VERSION`. Ejecuta migraciones declaradas, suite Django, check HTTPS,
respaldo SQLite y host Windows; luego prueba el perfil HTTP LAN, donde sólo se aceptan
los avisos de TLS/cookies ya documentados. Si hubo cambios después de estas pruebas,
crear otro commit y repetir desde el preflight. Para cambios visuales, añadir la
revisión Impeccable y aceptación en las resoluciones objetivo.

Ejecutar las pruebas funcionales en una base aislada con datos representativos, nunca
sobre la base operativa: comandas vacías y ocupadas, múltiples domicilios, pedidos
programados, módulos opcionales activados y desactivados, movimientos de caja y
trabajos de impresión en cola. Si cambió la interfaz, la revisión Impeccable y el QA
visual deben cubrir escritorio, 1024 × 768 y la tableta objetivo, incluidos estados
vacío, largo, error, carga y desbordamiento.

## 5. Dos construcciones reproducibles

Usar un wheelhouse fijo y directorios nuevos fuera del checkout. No usar
`--allow-dirty`, `--force`, `--source-only` ni descargas en línea para una release
instalable.

```powershell
$epoch = [Int64](git show -s --format=%ct $commit)
$parent = Split-Path -Parent $repo
$releaseRoot = Join-Path $parent ("release-$version-" + $commit.Substring(0, 7))
$wheelhouse = 'C:\ruta\wheelhouse-win-py313'
$build1 = Join-Path $releaseRoot 'build-1'
$build2 = Join-Path $releaseRoot 'build-2'

foreach ($output in @($build1, $build2)) {
    & .\.venv\Scripts\python.exe .\herramientas\release_servidor.py build `
        --source $repo `
        --version $version `
        --commit $commit `
        --source-date-epoch $epoch `
        --output $output `
        --wheelhouse $wheelhouse
    if ($LASTEXITCODE -ne 0) { throw "Fallo el build en $output" }
}
```

Cada directorio debe contener exactamente el ZIP, el manifiesto externo y el archivo
de sumas con el nombre `LosTocayosPOS-Servidor-$version`. Verificar ambos de forma
independiente:

```powershell
foreach ($output in @($build1, $build2)) {
    $base = Join-Path $output "LosTocayosPOS-Servidor-$version"
    & .\.venv\Scripts\python.exe .\herramientas\release_servidor.py verify `
        --archive "$base.zip" `
        --manifest "$base.manifest.json" `
        --checksum "$base.sha256"
    if ($LASTEXITCODE -ne 0) { throw "Fallo verify en $output" }
}
```

Comparar bytes o SHA-256 de los tres archivos entre `build-1` y `build-2`. Deben ser
idénticos. El manifiesto debe declarar la versión y commit congelados,
`source_dirty=false`, el destino `cp313/win_amd64`, wheelhouse y el mismo número de
archivos. Registrar hashes, tamaños y conteo. Los hashes prueban integridad, pero aún
no prueban identidad del publicador porque falta firma digital.

## 6. Preparación sin conmutar la instalación

Copiar el trío de artefactos como unidad y mantenerlo fuera de
`C:\LosTocayosPOS`. Con el servicio instalado todavía `Running` y saludable, ejecutar
primero el actualizador con `-PrepareOnly` desde PowerShell elevado:

```powershell
$base = Join-Path $build1 "LosTocayosPOS-Servidor-$version"
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File .\actualizar-laboratorio-desde-release.ps1 `
    -ArchivePath "$base.zip" `
    -ManifestPath "$base.manifest.json" `
    -ChecksumPath "$base.sha256" `
    -ExpectedVersion $version `
    -InstallationRoot 'C:\LosTocayosPOS' `
    -WorkspaceRoot 'C:\LosTocayosPOS-lab-actualizaciones' `
    -PrepareOnly
```

Esta fase verifica el ZIP antes de detener el servicio, extrae a staging, rechaza
reparse points y exige `VERSION`, `actualizar-servidor.ps1` y el manifiesto embebido.
Conservar la ruta de staging en la evidencia. Un fallo aquí invalida la candidata y
no autoriza ejecutar la conmutación.

## 7. Snapshot operativo y respaldo previo

Antes de elevar la actualización real, capturar de forma redactada:

- servicio, cuenta, modo de inicio, ejecutable y `/salud/`;
- versión instalada y commit del manifiesto instalado;
- SHA-256 de `.env`, nunca su contenido;
- motor, ruta, `PRAGMA integrity_check` y conteos de negocio acordados;
- módulos efectivos y configuración opcional, sin secretos;
- estado de las tareas de respaldo y purga;
- firewall, ACL y espacio disponible;
- `PRINT_BACKEND`, estado de la cola y disponibilidad de la impresora;
- respuesta del service worker, nombre de caché y URLs versionadas.

Ejecutar `verificar-servicio-lan.ps1 -RunBackup` como administrador y exigir un
respaldo SQLite nuevo, íntegro y restaurable. Conservar también la ruta del respaldo
completo que creará el actualizador. Si `SQLITE_PATH` apunta fuera del árbol
administrado, detenerse: el rollback por intercambio de árboles no basta y requiere
un plan explícito de restauración de esa base.

El modo de impresión es una precondición operativa:

- `archivo`: preservar byte por byte y comprobar que la actualización no envíe papel;
- `tcp`: preservar host/puerto, revisar trabajos pendientes y autorizar por separado
  una impresión física controlada. Nunca cambiar de `archivo` a `tcp` como efecto
  implícito de una actualización.

Un socket alcanzable sólo demuestra conectividad a una IP y puerto. No demuestra que
`PRINT_BACKEND=tcp` esté activo, que sea la impresora correcta ni que acepte
ESC/POS. Los diagnósticos antes y después de actualizar deben ejecutarse sin papel:
leer configuración redactada, inspeccionar cola/estado y probar únicamente
conectividad. Un ticket físico de prueba requiere autorización operativa explícita.

Antes de gastar papel se aplica esta puerta obligatoria:

1. Identificar la IP autorizada de la impresora y confirmar que pertenece a la
   subred IPv4 `/24` esperada para la sucursal. No explorar la red ni adoptar como
   impresora un host desconocido sólo porque tenga un puerto abierto.
2. Ejecutar primero `diagnosticar_impresoras` y conservar su JSON redactado. La
   evidencia debe mostrar el destino, el puerto `9100`, `alcanzable=true` y
   `envio_de_datos=false`; esta prueba abre y cierra el socket sin transmitir bytes.
3. Si ningún host autorizado de esa `/24` responde en TCP `9100`, considerar la
   impresora fuera de línea o inalcanzable y no crear, reintentar ni reimprimir un
   trabajo físico. Revisar alimentación, cable, dirección IP y puerto, y repetir sólo
   el diagnóstico sin datos.
4. Sólo después de aprobar esa puerta, enviar un único ticket controlado y registrar
   el identificador del trabajo, el resultado técnico y la confirmación visual del
   papel.

Una impresora fuera de línea no autoriza a cambiar `.env`, vaciar la cola ni restaurar
otra base. Se conserva la configuración y todos los datos; el incidente queda en la
evidencia y la aceptación física permanece pendiente. Si falla la activación TCP antes
de esa prueba, `configurar_impresion_instalada.ps1` restaura `.env` byte por byte y
recupera el servicio. Si el fallo pertenece a la actualización, se usa el rollback del
árbol descrito en la sección 8 y se conservan tanto el respaldo como la candidata
fallida para diagnóstico.

### Incidencia dev.7: activación de impresión TCP

La actualización preserva `.env` y no activa TCP. Cambiar una instalación de
`archivo` a `tcp` es una operación explícita y separada para esa sucursal, con
respaldo del entorno, evidencia redactada y reversión propia. El comando
`manage.py diagnosticar_impresoras` sólo abre y cierra el socket configurado: no
envía papel ni acredita por sí solo la cola, el modelo o ESC/POS. La aceptación se
completa después con una impresión física controlada y autorizada.

El script validado `herramientas/configurar_impresion_instalada.ps1` realiza esa
operación de forma transaccional: respalda `.env` dentro de `backups` con ACL privada,
detiene el servicio, acredita que
no existan trabajos `PENDIENTE` ni `PROCESANDO`, cambia únicamente las claves de
impresión, reinicia, comprueba salud y ejecuta el diagnóstico sin papel. Si la cola no
está vacía, restaura el servicio sin modificar `.env`; si una fase posterior falla,
restaura sus bytes originales. Se ejecuta elevado después de instalar la candidata:

```powershell
& .\herramientas\configurar_impresion_instalada.ps1 `
  -HostCaja 192.168.0.33 -HostCocina 192.168.0.33 -HostBarra 192.168.0.33
```

Su evidencia no se mezcla con la instalación del artefacto.

### Incidencia dev.8: ubicación privada del respaldo de impresión

La ejecución real de `dev.7` detectó que guardar el respaldo junto a `.env` conservaba
permisos válidos para el servicio sobre un archivo que el auditor trataba como código.
`dev.8` mueve ese respaldo a `backups`, le asigna únicamente `SYSTEM` y Administradores
y exige que el verificador oficial conserve cero infracciones ACL. Una candidata ya
emitida no se reconstruye: esta corrección usa una versión y un commit nuevos.

## 8. Actualización real y preservación

Repetir el comando anterior sin `-PrepareOnly` en una única sesión elevada. El
actualizador adquiere los mutex de mantenimiento y respaldo, vuelve a comprobar salud,
captura las tareas administradas, detiene el servicio y mueve la instalación anterior
a un respaldo. Después promueve staging y copia desde el respaldo:

- `.env` y `.venv`;
- `runtime`, `media`, `logs` y `backups`;
- SQLite y sidecars de la raíz si existieran.

La actualización nunca edita, regenera ni combina `.env`: copia el archivo anterior
y el motor comprueba que su hash permanezca idéntico. Un hash inesperado invalida la
actualización. Los cambios de configuración autorizados se realizan en una operación
separada; nunca se esconden dentro del ZIP ni en pasos posteriores a la actualización.

`actualizar-servidor.ps1` aplica dependencias fijadas, respaldo premigración,
migraciones, estáticos, servicio, tareas, ACL y salud. La carga histórica, parches de
catálogo e importaciones de clientes no forman parte de este paso.

Si algo falla, el wrapper mueve la candidata fallida, restaura el árbol anterior,
restaura las tareas y exige que el servicio anterior recupere salud. No borrar ni el
árbol fallido ni el respaldo hasta terminar el diagnóstico. Una migración con datos
externos al árbol administrado o efectos en servicios remotos necesita además su
propio rollback ensayado.

## 9. Verificación posterior obligatoria

No aceptar sólo que el proceso terminó con código cero. Verificar y registrar:

1. versión, commit y hash instalados iguales al artefacto;
2. servicio `Running`, automático retardado y bajo `LocalService`;
3. `/salud/` en estado `ok`;
4. `.env` con el mismo SHA-256 y la misma lista de claves;
5. base íntegra, migraciones completas y conteos/invariantes conservados;
6. módulos núcleo y opcionales exactamente iguales al snapshot previo;
7. tareas de respaldo/purga, respaldo real, firewall y ACL sin infracciones;
8. modo de impresión sin cambios y cola en estado esperado;
9. service worker con `Cache-Control: no-cache`, caché
   `tocayos-pos-$version`, assets `?v=$version`, ausencia de versiones anteriores,
   `skipWaiting()` y `clients.claim()`;
10. manifiesto de tableta con `/tabletas/` y modo `fullscreen,standalone`;
11. recarga o reapertura de cada tableta una vez para adoptar la nueva caché;
12. recorrido manual de los flujos afectados y de los flujos críticos acordados.

Los cambios de datos post-update se ejecutan después de acreditar el código. Cada uno
requiere respaldo, dry-run, comando explícito, conteos antes/después, segunda ejecución
idempotente y salud posterior. Si el destino contiene datos parciales o conflictivos,
el procedimiento debe abortar; no se corrige silenciosamente durante el update.

## 10. Evidencia mínima por candidata

Crear un archivo nuevo `EVIDENCIA_PREPARACION_RELEASE_<version>.md` y un directorio
local de evidencia. Incluir:

- fecha, entorno y declaración de laboratorio/producción;
- rama, commit fuente y, por separado, commit documental;
- versión, nombres, tamaños y SHA-256 de ZIP/manifiesto/sumas;
- `SOURCE_DATE_EPOCH`, número de archivos y comparación 2/2;
- resultados y cantidades de pruebas;
- resultado de `-PrepareOnly` y ruta de staging;
- snapshot previo redactado y respaldo verificable;
- transcript, JSON de resultado, verificador oficial y datos posteriores;
- estado de `.env`, módulos, impresión, PWA, tareas, ACL y firewall;
- cualquier incidente, momento exacto del fallo y resultado del rollback;
- pruebas manuales/físicas pendientes;
- decisión de promoción o rechazo.

No registrar contraseñas, PIN, tokens, cadenas de conexión ni el contenido de `.env`.
Un intento fallido sigue siendo evidencia y no se borra ni se reescribe.

## 11. Promoción e inmutabilidad

Una candidata puede promoverse sólo cuando:

- todas las validaciones automatizadas y ambos builds pasan;
- el artefacto exacto completa preparación, actualización y verificación posterior;
- los datos, módulos y configuración quedan preservados;
- PWA/caché corresponde a la versión;
- el recorrido manual crítico termina sin regresiones;
- la tableta Android y la impresión física se aceptan si forman parte del alcance;
- se revisan los límites conocidos: firma, HTTPS, enrolamiento, VPS y canal remoto;
- existe una decisión humana expresa de promoción.

Después de promover, conservar inmutables el trío de artefactos, evidencia y commit.
Publicar una corrección mediante una versión nueva. No aplicar la misma candidata a
todas las sucursales a la vez: usar una instalación piloto, observarla y desplegar
después sólo a las sucursales cuyos módulos y configuración correspondan.

## 12. Matriz rápida de decisión

| Hallazgo | Acción |
| --- | --- |
| Árbol sucio, `VERSION` duplicada o commit distinto | Detener, corregir y crear un commit nuevo. |
| Builds diferentes | Rechazar ambos; revisar epoch, wheelhouse y fuente. |
| `verify` o `-PrepareOnly` falla | No detener el servicio ni aplicar la candidata. |
| Servicio previo no saludable o respaldo no verificable | Resolver primero el estado anterior. |
| Falla después de conmutar | Dejar actuar rollback, verificar versión anterior y conservar candidata fallida. |
| `.env`, módulos, impresión o datos difieren sin autorización | Rechazar actualización y restaurar/investigar. |
| PWA publica otra versión | Incrementar versión y reconstruir desde un commit limpio. |
| Sólo faltan datos post-update explícitos | Acreditar primero el código; luego ejecutar su procedimiento separado. |
| Todo pasa pero falta aceptación física | Mantener como candidata de laboratorio. |
