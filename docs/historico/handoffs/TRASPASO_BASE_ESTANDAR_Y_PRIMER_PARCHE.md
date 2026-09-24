# Traspaso: base estándar, instalador Windows y primer parche

> Documento histórico. Conserva el estado de aquella etapa; no es una instrucción vigente ni autorización para ejecutar cambios.

> Snapshot informativo del 2026-09-14, zona `America/Mexico_City`. Este archivo
> aporta contexto a un agente nuevo; no reemplaza una solicitud posterior del
> usuario ni constituye autorización permanente para acciones destructivas.

## Estado vigente: candidata `0.4.0-dev.6`

Actualización del 2026-09-18. Las secciones 4 a 8 conservan evidencia histórica del
snapshot `0.4.0-dev.1`; las subsecciones de `dev.3`, `dev.4` y `dev.5` son registros
históricos. Este bloque y el estado comprobado del repositorio tienen precedencia
para continuar el trabajo.

**No existe ninguna sucursal en producción.** Hay cinco contextos distintos en esta
máquina:

| Contexto | Estado |
| --- | --- |
| Repositorio original | `C:\tcysAplicacionLocales` |
| Candidata activa | worktree `codex/candidata-0.4.0-dev.6` bajo `C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tcys-base-work\base-a-src` |
| Servicio instalado de laboratorio | `C:\LosTocayosPOS` actualizado a `dev.6`; salud `ok`, servicio `Running/Auto`, entorno y datos preservados, y `/service-worker.js` íntegramente en `dev.6` |
| Instalación limpia aislada | `LAB_DEV3`, evidencia histórica aprobada y detenida después de validar |
| Perfil de prueba aislada | `127.0.0.1:8001`, base y medios bajo `runtime\prueba`; la conexión TCP real sólo se habilita de forma explícita |

### Corrección vigente en dev.6

El primer intento de actualización `dev.5` se detuvo durante la validación aislada:
`herramientas/validar_despliegue.py` copiaba el código a una carpeta temporal, pero
omitía `VERSION`. El actualizador ejecutó su rollback y restauró la instalación
`dev.4`; la evidencia quedó en `C:\LosTocayosPOS-lab-actualizaciones\evidencia`.

`dev.6` añade `VERSION` al snapshot efímero y una prueba de regresión. El validador
exacto completó 182 pruebas Django, checks HTTPS/HTTP LAN, 13 pruebas de
respaldo y 5 del host Windows. Dos builds idénticos y la actualización real quedaron
acreditados en `../releases/EVIDENCIA_PREPARACION_RELEASE_0.4.0-dev.6.md`.

### Evidencia histórica de dev.5

`0.4.0-dev.5` sustituyó a `0.4.0-dev.4` como candidata para nuevas releases y
actualizaciones. Una comprobación HTTP posterior a la actualización de `dev.4`
demostró que `/service-worker.js` todavía generaba la caché
`tocayos-pos-0.4.0-dev.3` y URLs de recursos con `?v=0.4.0-dev.3`, aunque el archivo
`VERSION` instalado ya declaraba `0.4.0-dev.4`. Reconstruir el mismo artefacto no
podía corregirlo y publicar bytes distintos bajo la misma versión habría roto la
identidad inmutable de la release.

`dev.5` centraliza la identidad en `VERSION`. El runtime, el service worker y el
registro de módulos consumen el mismo valor; las pruebas comparan la caché, las URLs
versionadas y las versiones mínimas de módulos con ese archivo. El worker conserva
`Cache-Control: no-cache`, `skipWaiting`, `clients.claim` y la eliminación de cachés
anteriores. Una tableta que estuviera abierta durante la actualización debe recargar
o volver a abrir la aplicación una vez.

`dev.5` se fijó en `c5731e497d2ea3cfa2f26fdfd40cc96a7eccea4f` y produjo dos
ZIP idénticos, pero su actualización no completó el validador efímero. El artefacto
queda como evidencia histórica y no debe reutilizarse con bytes distintos.

### Evidencia histórica de dev.4

`0.4.0-dev.4`, commit funcional
`2739944ee5da28b35fa7b4c6330e76ba83605aab`, incorporó las correcciones de
tabletas, comandas, producto personalizable, Movimientos, directorio de clientes y
Pedidos Sucursales. Sus pruebas, sus dos builds reproducibles y la actualización del
laboratorio se conservan en
[EVIDENCIA_PREPARACION_RELEASE_0.4.0-dev.4.md](../releases/EVIDENCIA_PREPARACION_RELEASE_0.4.0-dev.4.md).
El artefacto permanece como evidencia histórica de esos cambios, pero fue sustituido
como candidata promocionable por la discrepancia de versión de la caché PWA.

### Evidencia histórica de dev.3

La candidata dev.3 declara `0.4.0-dev.3`. Su HEAD final es
`7ae0431d4d615d4c3df059ce8749179c966c4ab7`
(`fix: validar multicast en Windows PowerShell 5.1`), está publicado en
`github/codex/candidata-0.4.0-dev.3` y era limpio al construir. Dos builds del mismo
commit y epoch produjeron exactamente el mismo ZIP:

- `LosTocayosPOS-Servidor-0.4.0-dev.3.zip`;
- SHA-256 `c3c23e58663e2f110d568f4781a1507b257af4382c74ac67e35820989ff9e890`;
- 32,327,195 bytes y 204 archivos;
- manifiesto SHA-256
  `14abf805f2f827b045d84cf2353a155ebb79405f22afc45f7ffc315a30d3257d`.

La instalación limpia `LAB_DEV3` acreditó el payload funcional en `b568bb5`.
Validó Python 3.13.14, dependencias, migraciones, salud, cuenta administrativa y
operativa, clave maestra `0000` almacenada como hash, 24 posiciones de Comedor,
100 de Domicilio, 40 de Llevar y 40 de Recoger, y sólo los módulos del núcleo. El
commit final `7ae0431` cambia únicamente el verificador PowerShell y su regresión;
el ZIP final exacto se acreditó mediante la actualización real de
`C:\LosTocayosPOS`.

### Cambios incorporados en dev.3

- Una sucursal nueva recibe la clave maestra inicial `0000`, almacenada como hash.
  Debe rotarse manualmente. La contraseña y el PIN del primer operador se capturan
  aparte durante una instalación si todavía no existen.
- Todos los perfiles POS activos pueden identificarse en **Ventas**. Los usuarios
  con rol **Elevado** operan el Administrador, salvo consulta/gestión de usuarios y
  PIN, reinicio de folios y cambio de la clave maestra, que siguen reservados al
  maestro.
- Los módulos opcionales se eligen por sucursal. La selección vacía instala sólo
  `pos`, `catalogo`, `impresion` y `respaldos`; ni Arboledas ni
  `pedidos_sucursales` son predeterminados.
- El corte diario conserva una instantánea reimprimible y elimina el detalle del
  turno, incluidas cancelaciones, movimientos incorporados y archivos relacionados.
  Los pedidos programados futuros permanecen.
- Al cambiar de mes se bloquea la apertura de ventas mientras exista un periodo
  pendiente. Sólo un HTTP exitoso y JSON con `recibido: true` y `acuse` no vacío
  permiten purgar las instantáneas mensuales y reiniciar folios.
- Se agregaron control de caja por denominaciones, ingresos/gastos/terminales,
  aplicaciones y fondos; edición de programados; Recoger programable; artículos
  personalizados; 40 posiciones Llevar/Recoger; subtotales por sucursal; refresco
  LAN y la interfaz táctil con teclado numérico/pantalla completa.
- El ticket total usa el nombre del producto y omite comentario, modificador y
  término de preparación.
- Las purgas lógicas generan solicitudes persistentes para cerrar archivos y
  respaldos sensibles. `LosTocayosPOS-PurgasFisicas` las procesa como SYSTEM cada
  cinco minutos y no crea respaldos cuando no hay solicitudes; la tarea diaria
  `LosTocayosPOS-RespaldoSQLite` conserva su horario predeterminado de `03:15` y
  retención predeterminada de 30 días.

Los requisitos funcionales 1 a 14 quedaron cubiertos por implementación y pruebas.
La validación cerró con 172/172 pruebas Django, 68/68 pruebas unitarias de
infraestructura, el contrato PowerShell 5.1, `django check`,
`makemigrations --check --dry-run`, sintaxis JavaScript, `git diff --check` y el
detector de Impeccable sin hallazgos. La construcción reproducible, la instalación
limpia aislada y la actualización real del laboratorio también están completas.
El requisito 14 y la salida impresa aún requieren aceptación física.

### Actualización real de laboratorio

El 2026-09-18, entre 07:57:49 y 08:06:27, el wrapper
`actualizar-laboratorio-desde-release.ps1` instaló el ZIP final en
`C:\LosTocayosPOS` y concluyó con `status=ok`:

- versión `0.4.0-dev.3`, commit `7ae0431` y SHA-256 del artefacto coincidentes;
- `.env` preservado byte por byte, identidad y datos conservados;
- servicio `Running`, inicio automático y cuenta `NT AUTHORITY\LocalService`;
- las dos tareas programadas presentes y en estado `Ready`;
- respaldo real con resultado 0 y archivo `db-20260918-080534.sqlite3`;
- 11,984 elementos auditados y 0 violaciones ACL;
- firewall privado limitado a `LocalSubnet`;
- service worker dev.3 con `skipWaiting` y `clients.claim`;
- manifiesto de tableta con `/tabletas/` y `fullscreen,standalone`.

La instalación anterior quedó respaldada en
`C:\LosTocayosPOS-respaldo-lab-20260918-075832-91d6c18f`. El parche explícito
`Topo` se aplicó después de un dry-run y un respaldo: `AM`/$30 vigente desde
2026-08-14 pasó a `Topo`/$32 vigente desde 2026-09-17. La segunda ejecución informó
`ya_aplicado` y la salud permaneció `ok`.

El primer intento de actualización dejó la aplicación saludable, pero falló en la
verificación posterior porque Windows PowerShell 5.1 no expone
`IPAddress.IsMulticast`. El commit `7ae0431` sustituyó esa dependencia por
inspección de bytes IPv4/IPv6 y añadió una prueba de regresión. Tras reconstruir dos
veces, el reintento terminó correctamente.

### Brechas vigentes

- La instalación conserva deliberadamente `PRINT_BACKEND=archivo`. El sondeo a
  `192.168.0.33:9100` responde por TCP y una prueba aislada acreditó el backend,
  pero todavía no se ha aceptado la salida física de un ticket desde el servicio.
- Falta la aceptación manual en una tableta Android real y el recorrido funcional de
  Ventas, Administrador, programados, corte diario, reimpresión y recuperación.
- Falta configurar `VPS_CONSOLIDACION_URL`, `VPS_CONSOLIDACION_TOKEN` y, si se
  modifica, `VPS_CONSOLIDACION_TIMEOUT`. El backend real del VPS, su autenticación,
  enrolamiento y operación no existen todavía.
- El wrapper sigue limitado al laboratorio: no descarga ni valida una firma del
  publicador, tiene una ventana TOCTOU entre verificar y consumir el ZIP local, y las
  ACL finales del árbol de candidata conservado tras un rollback no quedan
  acreditadas.
- El ejecutable Windows es un cliente ligero del Edge local. La PWA existe, pero el
  repositorio aún no contiene el proyecto Android ni genera un APK firmado.
- Siguen pendientes HTTPS LAN, canal remoto de releases, catálogo versionado por
  sucursal, CI atestada y la decisión expresa de promover `dev.6` a base estándar.

## 1. Lectura obligatoria y precedencia

Leer completamente, en este orden:

1. Este traspaso.
2. `../../../DESPLIEGUE_WINDOWS.md`: contrato operativo del servidor Edge Windows actual.
3. `../../../ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md`: arquitectura
   futura y decisiones sobre distribución, módulos y VPS.
4. `../../../README.md`: uso general y puntos de entrada públicos.

Si el estado real diverge de este snapshot, manda el estado comprobado. No
convertir frases de este documento en instrucciones que contradigan al usuario.

## 2. Objetivo acordado

La **Base A** fue el checkpoint histórico para ensayar la instalación limpia. El
**Parche B** se materializó primero como `0.4.0-dev.3`; las correcciones encontradas
durante su aceptación formaron `0.4.0-dev.4`. Ambos ciclos conservan evidencia de
implementación, construcción reproducible y actualización del laboratorio. La
candidata vigente es `0.4.0-dev.6`, que hereda ese alcance funcional, la corrección de
identidad de `dev.5` y el contrato del snapshot antes de una posible promoción.

La candidata aún no es la base estable porque faltan la aceptación física Android e
impresión, el recorrido manual de los flujos críticos y la decisión expresa de
promoción. El VPS real y el canal firmado son brechas del producto multisucursal y no
deben presentarse como capacidades ya disponibles.

Después de promover una base, todas las sucursales deben recibir el mismo paquete.
Los módulos extras se activan mediante configuración o futuros `entitlements` por
sucursal; no mediante ramas, copias manuales ni instaladores divergentes. Una
instalación en sucursal se actualiza desde una release validada y nunca con
`git pull`.

## 3. Contexto operativo y límites de autorización

- El negocio todavía **no utiliza activamente** esta instalación. Sólo se han
  realizado simulaciones.
- El usuario permite detener y levantar el servicio y contempla reiniciar el
  estado simulado desde cero.
- El repositorio original, el worktree de la candidata y `C:\LosTocayosPOS` son
  raíces distintas. La carpeta instalada es estado de laboratorio, no fuente.
- Nunca borrar recursivamente ninguna de esas raíces. Antes de borrar, mover o
  reemplazar datos, enumerar destinos exactos
  (`.env`, base, runtime, media, logs, backups, `.venv`, servicio, tarea y
  firewall), decidir qué se conserva en cuarentena y confirmar el alcance.
- No revelar `.env`, claves, certificados privados, bases ni logs completos.

## 4. Estado histórico del repositorio en el snapshot dev.1

| Dato | Valor comprobado |
| --- | --- |
| Raíz | `C:\tcysAplicacionLocales` |
| Rama | `main` |
| Upstream antes del nuevo checkpoint | `origin/main` |
| HEAD anterior | `faf52da` — `feat: completar operación POS y endurecer despliegue Windows` |
| Versión | `0.4.0-dev.1` |
| Diferencia previa al nuevo checkpoint | 29 archivos tracked modificados, 14 nuevos; aproximadamente 4,127 inserciones y 580 eliminaciones |
| `git diff --check` | Correcto |

El commit que contiene este documento será el checkpoint nuevo y es la referencia
autoritaria. Al abrir el proyecto, ejecutar `git log -1 --oneline` y
`git status --short --branch`; no asumir que `faf52da` sigue siendo HEAD.

Cambios del checkpoint, agrupados:

- Instalación/operación Windows: `instalar-servicio-lan.ps1`,
  `instalar-servidor.ps1`, `actualizar-servidor.ps1`,
  `aprovisionar-sucursal.ps1`, `reparar-permisos-servidor.ps1`,
  `iniciar-servicio-lan.ps1`, `iniciar-local.ps1`,
  `verificar-servicio-lan.ps1` y `servicio_windows.py`.
- Respaldo/release: `respaldar-db-sqlite.ps1`,
  `herramientas/respaldo_sqlite.py`,
  `herramientas/release_servidor.py`, `requirements-lock.txt`, `VERSION`,
  `.gitattributes` y `.dockerignore`.
- Identidad/configuración: `personas/identidad.py`, comandos de
  aprovisionamiento/verificación, settings, Docker y plantillas de entorno.
- Bootstrap de escritorio: `desktop/InstallerBootstrap.cs`,
  `desktop/package/Instalar-LosTocayosPOS.ps1` y `desktop/README.md`.
- Contratos y pruebas: `tests/test_instalador_windows.ps1`,
  `tests/test_respaldo_sqlite.py`, `tests/test_release_servidor.py`,
  `tests/test_contratos_despliegue.py`, `tests/test_servicio_windows.py` y
  pruebas funcionales de `ventas`/`personas`.
- Documentación: `../../../README.md`, `../../../DESPLIEGUE_WINDOWS.md`,
  `../../../ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md` y este archivo.

## 5. Estado histórico de la instalación simulada

| Componente | Estado comprobado |
| --- | --- |
| Servicio `LosTocayosPOS` | `Running`, inicio automático, `NT AUTHORITY\LocalService` |
| Listener conocido | `0.0.0.0:8000`; revalidar PID y ascendencia antes de actuar |
| Tarea `LosTocayosPOS-RespaldoSQLite` | `Ready`, cuenta `SYSTEM` |
| Datos | Simulados; no confundir con datos de operación real |

El verificador elevado y no mutante encontró todo conforme salvo **22
desviaciones ACL en artefactos de respaldo heredados**. El wrapper nuevo ya
demostró en una raíz aislada que repara ese estado. Se decidió no mutar los
respaldos de esta instalación provisional porque la siguiente etapa será una
instalación limpia.

`verificar-servicio-lan.ps1 -RunBackup` **no es sólo diagnóstico**: crea un
triplete y puede eliminar tripletes completos que superen la retención. No se
ejecutó contra el estado real en este cierre. Debe acreditarse sobre la
instalación limpia.

## 6. Trabajo implementado hasta el snapshot dev.1

- Puntos de entrada separados para instalar, adoptar, actualizar y reparar. El
  operador no debe invocar directamente `instalar-servicio-lan.ps1`.
- Python 3.13 x64 instalado para todos los usuarios; se rechazan 3.12, 3.14 y
  runtimes ligados a un perfil personal.
- Servicio bajo `LocalService`, inicio retardado, recuperación, firewall
  restringido y tarea de respaldo bajo `SYSTEM`.
- Identidad de sucursal explícita y sin fallback de producción.
- Actualización que preserva `.env` byte por byte, identidad, catálogo, cuentas,
  firewall y tarea.
- Manifiesto de release v2, dependencias fijadas, wheelhouse offline, ZIP
  reproducible y rechazo de secretos, estado local o fuente sucia para una
  distribución normal.
- Respaldo SQLite consistente con hash, tamaño, integridad, claves foráneas y
  restauración de prueba.
- Base, `.sha256` y `.json` publicados mediante reemplazo atómico **por
  archivo**. No afirmar que los tres archivos forman una sola transacción.
- La retención empieza después de completar el triplete nuevo y elimina cada
  triplete como unidad.
- ACL exacta antes y después del respaldo, incluido endurecimiento de archivos
  antiguos, temporales o huérfanos.
- Mutex independientes de mantenimiento y respaldo, orden
  mantenimiento → respaldo y liberación inversa.
- Cesión segura del mutex al wrapper hijo y readquisición obligatoria antes de
  continuar o revertir. El aviso usa `-WarningAction Continue` para que una
  preferencia heredada no interrumpa el bucle.
- No existe bypass público `ParentHoldsBackupMutex`. Su texto sólo debe aparecer
  en una aserción negativa o en el rechazo del verificador.

## 7. Evidencia histórica de validación del snapshot dev.1

Ejecutada el 2026-09-14 sobre el árbol que se va a guardar:

| Validación | Resultado |
| --- | --- |
| Hash byte a byte de los 9 últimos archivos aceptados contra el repositorio | 9/9 |
| Parseo de todos los `.ps1` con Windows PowerShell 5.1 | Correcto |
| `tests.test_respaldo_sqlite` | 4/4 |
| `tests.test_release_servidor` | 37/37 |
| `tests.test_contratos_despliegue` | 10/10 |
| `tests.test_servicio_windows` | 2/2 |
| Suite Django aislada | 118/118 |
| Host Windows aislado | 5/5 |
| Check deploy HTTPS | Sin avisos |
| Check deploy HTTP LAN | Correcto; 4 avisos de TLS/cookies esperados |
| Suite ACL elevada, wrapper real aislado, error e idempotencia | Correcto |
| `git diff --check` | Correcto |
| Verificador de instalación real, sin ejecutar respaldo | Sólo falla por las 22 desviaciones heredadas descritas arriba |

Repetir desde el commit y desde cada ZIP que vaya a instalarse. Un test sobre el
checkout no acredita por sí solo el artefacto distribuible.

## 8. Límites registrados en el snapshot dev.1

Este bloque describe el estado histórico de dev.1. Ya no debe interpretarse como el
estado del wrapper de laboratorio vigente; la sección inicial conserva la evidencia
de `dev.3`/`dev.4`, la identidad de `dev.5` y el snapshot de `dev.6`. Permanecen pendientes la
firma, el canal remoto, el cierre TOCTOU y una política acreditada para las ACL del
árbol fallido.

- En dev.1 el actualizador era exclusivamente `in-place` y carecía de staging,
  intercambio de árboles y rollback transaccional.
- Volver al código anterior no deshace una migración ya iniciada.
- Falta construir y verificar una release limpia del checkpoint; no usar
  `--allow-dirty` para la Base A que se instalará.
- Falta un desinstalador canónico probado. No improvisar un borrado amplio.
- Edge Windows admite sólo SQLite. PostgreSQL corresponde al VPS.
- Faltan enrolamiento autorizado, catálogo inicial versionado por sucursal,
  canal firmado y CI atestada. La selección local de módulos ya existe en dev.3.
- Falta aceptación LAN desde otro dispositivo y comprobación física de
  impresoras según la configuración elegida.

## 9. Arquitectura futura aceptada

- Un repositorio y un paquete común; datos y configuración separados por
  sucursal.
- Módulos activados por configuración local; la instalación presencial identifica
  sucursal y conjunto mínimo. Un `entitlement` remoto sigue siendo futuro.
- Hostinger KVM2 separado, idealmente bajo `deploy/vps/`: PostgreSQL, API/panel
  del administrador general, HTTPS, respaldos externos y monitoreo.
- El contrato actual del Edge envía una consolidación mensual y exige acuse antes
  de purgar; el VPS real sigue pendiente. La publicación central de catálogo,
  precios y módulos continúa como arquitectura futura.
- El Edge sigue cobrando localmente si el VPS no está disponible.
- Supabase no reemplaza la decisión KVM2. La integración actual
  `tcysPedidosSucursales` es distinta y de sólo lectura.

## 10. Matriz funcional histórica que debe revalidarse

No reimplementar estos puntos a ciegas: `dev.4` cubre buena parte de esta matriz
histórica y `dev.6` hereda ese alcance. Primero relacionar cada requisito con el código vigente, una prueba
automática y el resultado manual; cualquier divergencia comprobada manda sobre
este listado. Los puntos conocidos son:

1. Importar automáticamente pedidos confirmados desde
   `tcysPedidosSucursales` y reflejar sus totales en reportes/corte.
2. Liberar casillas de domicilio, incluidas 1, 2 y 3, tras cierre/cobro/corte.
3. Cliente válido sólo con nombre; permitir editarlo y eliminar el punto muerto
   de “registro incompleto”.
4. Reimprimir ticket total en domicilio y separar Ticket de Cobrar.
5. Cancelar pedidos agregados cuando corresponda al estado permitido.
6. Primera impresión sin “Comanda 1”; numerar desde la segunda.
7. Navegar comandas 1/1, 1/2… y agregar una nueva comanda sin editar la anterior.
8. Registrar efectivo/terminal; Cobrar en rojo Tocayos y Agregar en verde.
9. Contraer tipos de pedido y desplegar sus casillas al pulsar. Aclarar si
   “liberar” significa mostrar casillas o desocupar pedidos.
10. Barra inferior de selección con Mover y Ver detalles, sin sección duplicada.
11. Edición/eliminación de entradas y salidas; pedidos programados con hora.
12. Reasignación visual, cobro múltiple y asignación múltiple a repartidor sin
    solicitar nuevamente el PIN dentro del administrador.
13. Pantalla completa en administración; sesión/salida en la pantalla correcta.
14. Comensal 1 por defecto; comentario “Individual”; comentarios y 50 minutos
    predeterminados sólo para domicilio.
15. Consolidación mensual con acuse explícito del VPS antes de purgar y reiniciar
    folios. El contrato Edge ya existe; faltan endpoint, credenciales y aceptación
    contra el VPS real.

Crear una tabla de aceptación con columnas: requisito, evidencia de código, test
automático, prueba manual y estado. Los cambios nuevos que indique el usuario
constituyen el parche B.

## 11. Secuencia vigente para cerrar la candidata

1. **Histórico completado:** `dev.3` acreditó instalación limpia, actualización,
   release reproducible y el primer conjunto funcional del Parche B.
2. **Histórico completado:** `dev.4` corrigió las regresiones encontradas en pruebas,
   aprobó 182/182 pruebas Django y 73/73 de infraestructura, produjo dos ZIP
   idénticos y actualizó el laboratorio preservando configuración y datos.
3. **Hallazgo posterior:** el endpoint real `/service-worker.js` de `dev.4` todavía
   publicaba caché y recursos `dev.3`; por ello `dev.4` no debe promoverse ni
   reutilizarse como identidad de un paquete corregido.
4. **Histórico con rollback seguro:** `dev.5` centralizó `VERSION` y produjo dos
   builds idénticos, pero su primer intento real detectó que el snapshot temporal
   omitía ese archivo; la instalación anterior fue restaurada.
5. **Candidata vigente:** `dev.6` corrige el snapshot, produjo dos builds
   idénticos y actualizó `C:\LosTocayosPOS`. Salud, manifiesto, SHA-256, datos y el
   contenido HTTP exacto de `0.4.0-dev.6` quedaron acreditados.
6. **Pendiente para promoción:** aceptación manual de Ventas, Domicilios, Sucursales,
   Administrador, programados, caja, corte, reimpresión y recuperación, además de
   Android e impresión física.
7. **Pendiente para la arquitectura completa:** endpoint VPS real, credenciales,
   HTTPS LAN, enrolamiento, APK firmado y canal remoto de releases.
8. Promover la base sólo después de cerrar la aceptación física, conservar la
   evidencia exacta del artefacto `dev.6` y registrar expresamente la decisión.

## 12. Comandos iniciales

Ejecutar primero sin mutar:

```powershell
git status --short --branch
git log -1 --oneline
Get-Content .\VERSION
Get-Service LosTocayosPOS
Get-ScheduledTask LosTocayosPOS-RespaldoSQLite
```

Pruebas principales:

```powershell
.\.venv\Scripts\python -m unittest tests.test_respaldo_sqlite -v
.\.venv\Scripts\python -m unittest tests.test_release_servidor -v
.\.venv\Scripts\python -m unittest tests.test_contratos_despliegue -v
.\.venv\Scripts\python -m unittest tests.test_servicio_windows -v
.\.venv\Scripts\python .\herramientas\validar_despliegue.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\test_instalador_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\verificar-servicio-lan.ps1
```

El último comando es diagnóstico si no lleva `-RunBackup`. Con `-RunBackup` es
mutante. `instalar-servidor.ps1` y `actualizar-servidor.ps1` también son
mutantes, requieren elevación y deben ejecutarse desde una release completa.

Construcción, una vez limpio el árbol y localizado un wheelhouse íntegro:

```powershell
$version = (Get-Content .\VERSION -Raw).Trim()
.\.venv\Scripts\python .\herramientas\release_servidor.py build `
    --version $version `
    --source . `
    --output .\release\servidor `
    --wheelhouse RUTA_WHEELHOUSE_VERIFICADO
```

Verificar el ZIP con `release_servidor.py verify` y los tres archivos generados.
No conservar en este documento rutas temporales de aceptación.

## 13. Modelo recomendado para el nuevo agente

Usar **`gpt-6-astra` con razonamiento `medium`** como configuración inicial.

- Bajar a `low` para cambios visuales pequeños y bien delimitados.
- Subir temporalmente a `high` para concurrencia, migraciones, seguridad,
  diagnóstico difícil o revisión final.
- Usar `gpt-5.6-terra` en `medium` para tareas mecánicas, documentación o
  ejecuciones repetitivas si se desea conservar cuota.
- No usar `gpt-5.6-sol` en `ultra` como valor permanente: fue excesivo para
  muchas fases rutinarias.
- Evitar modo rápido salvo necesidad concreta, porque aumenta el consumo.

La documentación oficial describe Astra como el modelo más capaz para trabajo
complejo de extremo a extremo, pero en Plus su capacidad estimada es menor que
la de Sol/Terra. Una mayor calidad por turno no garantiza menor consumo de cuota.
Fuentes:

- https://developers.openai.com/api/docs/guides/latest-model
- https://learn.chatgpt.com/es-419/docs/pricing
- https://learn.chatgpt.com/es-419/use-cases/make-granular-ui-changes

## 14. No asumir

- Un commit o `push` no despliega el servicio.
- No usar `git pull` como mecanismo de actualización en sucursal.
- No copiar scripts sueltos sobre una instalación; usar el paquete completo.
- No cargar la semilla Arboledas durante una actualización.
- No asumir sucursal, IP, HTTP/HTTPS, impresoras, módulos ni datos a borrar.
- No debilitar ACL ni tomar posesión masiva de la raíz.
- La actualización final auditó 11,984 elementos y terminó con 0 violaciones ACL;
  cada release futura debe repetir el verificador elevado.
- Este archivo no forma parte de la allowlist del paquete del servidor.
- La estimación anterior de “98 %” se refería únicamente al hardening del
  instalador, no al producto completo.

## 15. Primer mensaje sugerido

> Lee completamente `docs/historico/handoffs/TRASPASO_BASE_ESTANDAR_Y_PRIMER_PARCHE.md`,
> `../../../README.md`, `../../../DESPLIEGUE_WINDOWS.md` y
> `../../../ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md`. Continúa sobre
> `codex/candidata-0.4.0-dev.6`, verifica primero el estado real y conserva separados
> el worktree, el perfil de prueba `8001` y `C:\LosTocayosPOS`. `dev.3`, `dev.4` y
> `dev.5` conservan evidencia histórica; para `dev.6` comprueba que `VERSION`,
> runtime, módulos y `/service-worker.js` coincidan con el mismo artefacto y que el
> validador efímero incluya `VERSION`. Continúa con la aceptación física
> Android/impresión, el recorrido manual y la evaluación de promoción. No configures
> una sucursal como producción sin esa decisión.
