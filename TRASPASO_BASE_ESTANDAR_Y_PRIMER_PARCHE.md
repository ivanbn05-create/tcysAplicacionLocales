# Traspaso: base estándar, instalador Windows y primer parche

> Snapshot informativo del 2026-09-14, zona `America/Mexico_City`. Este archivo
> aporta contexto a un agente nuevo; no reemplaza una solicitud posterior del
> usuario ni constituye autorización permanente para acciones destructivas.

## Estado vigente: candidata `0.4.0-dev.3`

Actualización del 2026-09-17. Las secciones 4 a 7 conservan evidencia histórica del
snapshot `0.4.0-dev.1`; este bloque y el estado comprobado del repositorio tienen
precedencia para continuar el trabajo.

**No existe ninguna sucursal en producción.** Hay cuatro contextos distintos en esta
máquina:

| Contexto | Estado |
| --- | --- |
| Repositorio original | `C:\tcysAplicacionLocales` |
| Candidata activa | worktree `codex/candidata-0.4.0-dev.3` bajo `C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tcys-base-work\base-a-src` |
| Servicio instalado de laboratorio | `C:\LosTocayosPOS`, `LosTocayosPOS` activo en `0.0.0.0:8000` |
| Prueba aislada | `127.0.0.1:8001`, base y medios bajo `runtime\prueba`; impresora TCP real habilitada sólo de forma explícita |

La candidata declara `0.4.0-dev.3`. Su HEAD base conocido es `927c5dc`
(`feat: habilitar prueba aislada con impresora real`); la integración funcional se
encuentra en el worktree de candidata y todavía debe quedar en un commit limpio
antes de construir el artefacto reproducible.

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
infraestructura, `test_instalador_windows.ps1 -SkipAcl`, `django check`,
`makemigrations --check --dry-run`, comprobación de sintaxis JavaScript,
`git diff --check` y el detector de Impeccable sin hallazgos. El requisito 14 aún
requiere aceptación física en una tableta Android real. La construcción reproducible,
la instalación limpia y la actualización del laboratorio siguen pendientes, por lo
que esta versión todavía no es la base estable.

### Brechas vigentes

- Falta configurar `VPS_CONSOLIDACION_URL`, `VPS_CONSOLIDACION_TOKEN` y, si se
  modifica, `VPS_CONSOLIDACION_TIMEOUT`. El backend real del VPS, su autenticación,
  enrolamiento y operación no existen todavía; una respuesta simulada sólo valida el
  contrato.
- El motor `actualizar-servidor.ps1` continúa siendo in-place cuando se ejecuta solo,
  pero ya existe `actualizar-laboratorio-desde-release.ps1` para la prueba A→B. El
  wrapper verifica release y versión, extrae a staging externo, intercambia árboles,
  conserva el estado operativo y ejecuta el motor oficial; ante fallo restaura el
  árbol anterior, su salud y los XML —o ausencia— de ambas tareas programadas.
- El wrapper preserva exactamente `.env`, `.venv`, `runtime`, `media`, `logs`,
  `backups` y los archivos SQLite de la raíz. También conserva horario y retención
  existentes, y mantiene el respaldo completo anterior. Falta ejecutar la prueba real
  contra `C:\LosTocayosPOS`.
- El wrapper sigue limitado al laboratorio: no descarga ni valida una firma del
  publicador, tiene una ventana TOCTOU entre verificar y consumir el ZIP local, y las
  ACL finales del árbol de candidata conservado tras un rollback no quedan
  acreditadas. Ese árbol es sólo diagnóstico y debe permanecer restringido.
- Falta completar la aceptación equivalente de instalación limpia y actualización,
  incluida interfaz, acceso LAN, respaldo, ACL, impresión física y el recorrido del
  requisito 14 en Android.
- El ejecutable Windows es un cliente ligero del Edge local. La PWA existe, pero el
  repositorio aún no contiene un proyecto Android ni genera un APK firmado; falta
  construir el envolvente WebView/TWA en Android Studio y probar la instalación
  USB-C.
- Siguen pendientes HTTPS LAN, canal remoto de releases, catálogo versionado por
  sucursal y CI atestada.

## 1. Lectura obligatoria y precedencia

Leer completamente, en este orden:

1. Este traspaso.
2. `DESPLIEGUE_WINDOWS.md`: contrato operativo del servidor Edge Windows actual.
3. `ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md`: arquitectura
   futura y decisiones sobre distribución, módulos y VPS.
4. `README.md`: uso general y puntos de entrada públicos.

Si el estado real diverge de este snapshot, manda el estado comprobado. No
convertir frases de este documento en instrucciones que contradigan al usuario.

## 2. Objetivo acordado

La siguiente etapa debe usar dos artefactos internos:

- **Base A:** estado actual del instalador, usado sólo para comprobar una
  instalación limpia.
- **Parche B:** cambios funcionales y de interfaz que aún solicite el usuario.

El parche B se materializó como la candidata `0.4.0-dev.3`. No es todavía una base
estable: falta cerrar la secuencia de validación y comparar instalación limpia con
actualización.

Secuencia aprobada conceptualmente:

1. Congelar y empaquetar Base A desde un commit limpio.
2. Retirar de forma controlada la instalación simulada e instalar Base A.
3. Implementar y empaquetar el parche B.
4. Probar la actualización Base A → B.
5. Limpiar otra vez e instalar B desde cero.
6. Declarar B versión mínima común sólo si la actualización y la instalación
   limpia producen un estado equivalente y pasan aceptación.
7. Incorporar después módulos extras mediante configuración o `entitlements`
   por sucursal, no mediante ramas, copias manuales o instaladores divergentes.

Base A no debe distribuirse a sucursales. Es únicamente un banco de prueba.

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
- Documentación: `README.md`, `DESPLIEGUE_WINDOWS.md`,
  `ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md` y este archivo.

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
estado del wrapper de laboratorio en dev.3; la sección vigente al inicio documenta su
staging, swap y rollback. Permanecen pendientes la firma, el canal remoto, el cierre
TOCTOU y una política acreditada para las ACL del árbol fallido.

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

No reimplementar estos puntos a ciegas: dev.3 cubre buena parte de esta matriz
histórica. Primero relacionar cada requisito con el código vigente, una prueba
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

1. **Completado:** integrar dev.3 y cerrar suite, checks de Django, migraciones,
   JavaScript, `git diff --check` e Impeccable.
2. **Operativo para desarrollo:** mantener la prueba aislada en
   `127.0.0.1:8001` con `runtime\prueba\db.sqlite3`, separada del servicio
   instalado en `8000`. La aceptación física Android del requisito 14 sigue
   pendiente.
3. **Pendiente:** construir dos releases desde el mismo commit y epoch, comprobar
   SHA-256 idéntico y validar una de ellas en una instalación limpia. Confirmar clave
   maestra `0000`, rotación manual, alta de operador y módulos opcionales vacíos.
4. **Pendiente:** aplicar el mismo artefacto a `C:\LosTocayosPOS` mediante
   `actualizar-laboratorio-desde-release.ps1`. Verificar preservación de `.env`,
   identidad, datos, impresoras, módulos, ambas tareas, horario, retención y ACL.
5. Ejecutar aceptación manual de Ventas, Administrador, tabletas, programados,
   corte diario, reimpresión y salida física de los tickets.
6. Configurar y aceptar el endpoint real antes de depender del cierre mensual en una
   sucursal; las pruebas actuales sólo acreditan el contrato y respuestas simuladas.
7. Comparar instalación limpia y actualización. Sólo si ambas son equivalentes,
   documentar el artefacto, etiquetar la versión y promoverla como base.

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
- Los 22 fallos heredados reaparecerán si `backups\` se conserva dentro de la
  misma raíz. Archivarlos fuera o eliminarlos sólo con autorización.
- Este archivo no forma parte de la allowlist del paquete del servidor.
- La estimación anterior de “98 %” se refería únicamente al hardening del
  instalador, no al producto completo.

## 15. Primer mensaje sugerido

> Lee completamente `TRASPASO_BASE_ESTANDAR_Y_PRIMER_PARCHE.md`,
> `README.md`, `DESPLIEGUE_WINDOWS.md` y
> `ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md`. Continúa sobre
> `codex/candidata-0.4.0-dev.3`, verifica primero el estado real y conserva separados
> el worktree, la prueba `8001` y `C:\LosTocayosPOS`. La suite ya está cerrada;
> completa build reproducible, instalación limpia, actualización de laboratorio y
> aceptación física Android. No declares la base ni configures una sucursal como
> producción sin esa evidencia.
