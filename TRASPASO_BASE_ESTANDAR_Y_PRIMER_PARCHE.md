# Traspaso: base estándar, instalador Windows y primer parche

> Snapshot informativo del 2026-09-14, zona `America/Mexico_City`. Este archivo
> aporta contexto a un agente nuevo; no reemplaza una solicitud posterior del
> usuario ni constituye autorización permanente para acciones destructivas.

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
- El repositorio y la instalación comparten `C:\tcysAplicacionLocales`. Nunca
  borrar recursivamente esa raíz: contiene código Git y estado instalado.
- Antes de borrar, mover o reemplazar datos, enumerar destinos exactos
  (`.env`, base, runtime, media, logs, backups, `.venv`, servicio, tarea y
  firewall), decidir qué se conserva en cuarentena y confirmar el alcance.
- No revelar `.env`, claves, certificados privados, bases ni logs completos.

## 4. Estado del repositorio al redactar este archivo

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

## 5. Estado de la instalación simulada

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

## 6. Trabajo implementado

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

## 7. Evidencia de validación más reciente

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

## 8. Límites todavía abiertos

- El actualizador sigue siendo `in-place`: faltan staging, intercambio atómico,
  firma, diario reanudable y rollback transaccional.
- Volver al código anterior no deshace una migración ya iniciada.
- Falta construir y verificar una release limpia del checkpoint; no usar
  `--allow-dirty` para la Base A que se instalará.
- Falta un desinstalador canónico probado. No improvisar un borrado amplio.
- Edge Windows admite sólo SQLite. PostgreSQL corresponde al VPS.
- Faltan enrolamiento autorizado, catálogo inicial versionado por sucursal,
  selección/validación de módulos, canal firmado y CI atestada.
- Falta aceptación LAN desde otro dispositivo y comprobación física de
  impresoras según la configuración elegida.

## 9. Arquitectura futura aceptada

- Un repositorio y un paquete común; datos y configuración separados por
  sucursal.
- Módulos activados por configuración/`entitlement`. La instalación presencial
  identifica sucursal y conjunto mínimo.
- Hostinger KVM2 separado, idealmente bajo `deploy/vps/`: PostgreSQL, API/panel
  del administrador general, HTTPS, respaldos externos y monitoreo.
- El VPS recibe consolidación diaria y publica cambios versionados de catálogo,
  precios y módulos. Cada Edge consulta y aplica cambios idempotentemente al
  iniciar/sincronizar.
- El Edge sigue cobrando localmente si el VPS no está disponible.
- Supabase no reemplaza la decisión KVM2. La integración actual
  `tcysPedidosSucursales` es distinta y de sólo lectura.

## 10. Matriz funcional y de interfaz que debe revalidarse

No reimplementar estos puntos a ciegas: parte de ellos ya está cubierta por
`faf52da` y por el cambio sin publicar anterior. Primero relacionar solicitud,
código, prueba automática y resultado manual. Los puntos conocidos son:

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
15. Reinicio mensual de folios y posterior integración explícita con la
    sincronización VPS. No ocultar una mutación remota futura detrás de un botón
    sin contrato auditable.

Crear una tabla de aceptación con columnas: requisito, evidencia de código, test
automático, prueba manual y estado. Los cambios nuevos que indique el usuario
constituyen el parche B.

## 11. Secuencia de la siguiente etapa

### A0 — Confirmar el checkpoint

1. Leer este archivo y los dos documentos canónicos.
2. Inventariar repositorio, servicio y tarea sin mutar.
3. Confirmar HEAD, árbol limpio y versión.
4. Repetir pruebas críticas.
5. Construir Base A sin `--allow-dirty`; guardar commit, versión, ZIP,
   manifiesto y SHA-256.

### A1 — Diseñar una limpieza recuperable

1. Inventariar por separado código Git y estado instalado.
2. Definir exactamente qué se conserva fuera de la raíz y qué se elimina.
3. Confirmar los destinos con el usuario antes de actuar.
4. No usar `git reset --hard`, `git checkout --` ni borrado recursivo de la raíz.

### A2 — Instalar Base A

1. Retirar sólo servicio, tarea, firewall y estado autorizados.
2. Instalar el ZIP completo con identidad, red e impresoras confirmadas.
3. Aceptar migraciones, servicio, salud, ACL, firewall, tarea, respaldo fresco
   y acceso LAN externo.

### B1/B2 — Implementar y actualizar

1. Revalidar la matriz y aplicar únicamente fallos vigentes.
2. Añadir pruebas y revisar migraciones.
3. Construir B limpio y actualizar A mediante `actualizar-servidor.ps1`.
4. Acreditar preservación de `.env`, identidad y datos, además de salud,
   ACL, tarea, respaldo y UI.

### B3/B4 — Instalar B y declarar la base mínima

1. Repetir la limpieza controlada.
2. Instalar B desde cero.
3. Comparar evidencia con A → B.
4. Etiquetar/publicar B sólo con ambos recorridos verdes.

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
> `DESPLIEGUE_WINDOWS.md` y
> `ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md`. Empieza con un
> inventario de sólo lectura y compara el estado real con el snapshot. No borres
> ni reinstales hasta enumerar y confirmar destinos exactos. Después cierra Base
> A y sigue la secuencia instalación limpia A → actualización A→B → instalación
> limpia B.
