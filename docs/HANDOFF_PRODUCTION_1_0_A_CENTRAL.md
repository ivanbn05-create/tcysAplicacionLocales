# Handoff Production 1.0 al responsable de Backend Central

Fecha: 2026-09-25. Destino: agente1 y el integrador Central/Pedidos. **Candidata de laboratorio**: ninguna sucursal está en producción, esta nota no autoriza despliegue, corte, publicación pública de DNS/TLS, activación de clientes/catálogo ni uso de tokens reales. La rama POS es `codex/candidata-production-1.0-dev.1`, derivada de `e046f2bf74096d76eea50762aa07253f9b073a3d` (0.4.0-dev.10). Al redactar esto, la candidata aún contiene cambios sin commit; el integrador debe tomar el commit final que entregue el responsable POS, no asumir que ese HEAD base contiene Production 1.0.

## Contratos ejecutables que debe reconciliar Central

| Flujo | Fuente POS | Estado al redactar |
| --- | --- | --- |
| Enrolamiento, tarjeta de un uso y registro de terminal | `contracts/edge-enrollment-v1/openapi.json`, `schemas/*.json`, `fixtures/*.json`, `README_EDGE.md`; cliente `herramientas/enrolamiento_edge.py`; prueba `tests/test_enrolamiento_edge.py` | Central lo implementó como candidato privado detrás de `CENTRAL_ENABLE_ENROLLMENT_V1=1`; no está habilitado en producción. |
| Pedidos confirmados v2 | `contracts/pedidos-v2/openapi.json`, esquemas y fixtures; `contracts/pedidos-v2/test_contract.py`; cliente `ventas/pedidos_api_v2.py` | Candidato Pedidos `8fad56815f856b4286a2f60f488480960e34cde5` sin merge/despliegue según handoff previo; v1/Supabase legacy permanece como rollback. |
| Mensual v1, ventas y clientes v2, catálogo y ACK | `contracts/edge-central/openapi.json`, `schemas/*.json`, `fixtures/index.json`, `tests/test_contracts_vps.py` | Mensual v1 es privado; ventas/clientes/catálogo v2 son contratos candidatos. Las banderas POS v2 siguen apagadas. |
| Identidades, credenciales y respuestas | `contracts/edge-central/MATRIZ_IDENTIDADES.md`, `fixtures/matriz-identidades-v1.json`, `CREDENCIALES_Y_VARIABLES.md` y `MATRIZ_HTTP_ACCIONES.md` | La matriz con `SucursalCliente.id` real requiere aprobación explícita. |

Los fixtures de este repositorio son sintéticos. No usar tokens de fixture para emitir credenciales ni copiar una clave privada a Git o al ZIP. Al reconciliar cambios, actualizar OpenAPI, esquemas, fixtures y ambas pruebas en el mismo commit. El esquema Edge candidato fija `branch.name` a 120 caracteres, igual que el validador y la base POS. El contrato Central previamente copiado permitía 180: agente1 debe reconciliar su esquema/serializador a 120 y probar 120/121 antes del E2E.

## Identidad y módulos: valores que no se infieren por nombre

| Concepto | Identificador y regla |
| --- | --- |
| Sucursal POS | `personas.Sucursal.id` UUID = `CENTRAL_BRANCH_ID` = `ingest.Branch.source_id`. `Sucursal.clave` = `CENTRAL_BRANCH_CODE` verifica el código, pero no sustituye el UUID. |
| Instalación Edge | `ConfiguracionSucursal.instalacion_id` = `CENTRAL_POS_INSTANCE_ID` = `edge.id` de la respuesta de enrolamiento. Es otro UUID, estable entre updates. El aprovisionamiento recibe el `expected_edge_id` de la tarjeta, no genera uno nuevo. |
| Terminal | UUID local propio, ligado al Edge; `POST /api/v1/edge/terminals/` usa el token de ingesta con `terminals:v1:write`. Registrar o perder contacto con Central nunca bloquea venta/cobro/impresión. La terminal no recibe credenciales Edge. |
| Sucursal en Pedidos | `SucursalCliente.id` entero, asociado mediante una matriz aprobada al UUID y código POS. **No empatar por nombre.** El archivo `branches-production-1.0.json` tiene `pedidos_sucursal_id: null` en las cinco sucursales: no hay correspondencia real aprobada. |
| Pedido en Pedidos | `Pedido.codigo_publico` UUID identifica de forma durable al pedido; `Pedido.id` entero no reemplaza ese UUID. `SucursalCliente` no tiene `codigo_publico`. |
| Venta/cliente/outbox | La identidad Central incluye sucursal y UUID local. Rotar tokens no cambia UUID, tickets cerrados, cursor, eventos pendientes ni ACK. |
| Catálogo | Producto/categoría Central UUID ↔ UUID POS mediante mapeo persistente por sucursal; publicación = `publicacion_id` + `version_sucursal` + checksum. No unir por código/nombre. |

Los UUID de sucursal candidatos en `contracts/edge-enrollment-v1/fixtures/branches-production-1.0.json` son Arboledas `e1d9a253-7b96-4c32-8376-00fb8f6d8bdb`, Águilas `c5eb4066-a82b-4d9e-abaa-968c8bde06ae`, Estancia `4a76b526-8081-4c68-8101-796a3b246451`, Plaza del Sol `e3228ca5-77b3-4643-9d8b-1e81e89bea98` y Santa Anita `22c91fd6-21dc-402d-ac68-3fcf6c6d9a47`. Reconciliar estos UUID con los registros Central antes de emitir una tarjeta. Arboledas y Santa Anita son pilotos; los siete módulos núcleo (`pos`, `catalogo`, `impresion`, `respaldos`, `domicilios`, `pedidos_programados`, `reparto`) se entregan siempre. `pedidos_sucursales` sólo se autoriza a Arboledas y debe permanecer bloqueado mientras `SucursalCliente.id` no esté verificado. `pedidos_programados` de Central se adapta explícitamente a `programados` interno del POS.

## Enrolamiento y credenciales

El portal Central debe exportar una tarjeta privada JSON exacta `{code, expected_branch_id, expected_edge_id, request_id}`. `POST /api/v1/enrollment/claim/` recibe esa tarjeta por HTTPS y devuelve una vez HTTP 201 con `version=1.0`, `request_id`, `branch`, `edge`, `modules`, `credentials.ingest` y `credentials.catalog`. El código de 256 bits dura 15 minutos; uso previo/identidad discordante da 409, caducidad 410. No permitir replay de secretos. El Edge limita tarjeta a 4 KiB, respuesta a 64 KiB, comprueba JSON estricto sin claves repetidas, UUID exactos, scopes y expiración, CA/hostname TLS y ausencia de redirect. Ante timeout después del POST, **no reintentar** el mismo código: consultar Central, revocar Edge/credenciales si se consumió y emitir otra tarjeta; no registrar el código.

| Credencial | Variables Edge | Alcance exigido |
| --- | --- | --- |
| Pedidos, provisionada aparte | `PEDIDOS_API_TOKEN`, `PEDIDOS_API_BASE_URL`, `PEDIDOS_API_CA_BUNDLE`, `PEDIDOS_API_SUCURSAL_IDS` | `orders:v2:read` para una instalación Edge y sólo los `SucursalCliente.id` aprobados. El candidato Pedidos aún tiene allowlist global; esto requiere cambio servidor. `LocMemCache` por worker no es control de autorización. |
| Central ingesta, del claim | `CENTRAL_INGEST_TOKEN`, `CENTRAL_INGEST_CREDENTIAL_ID` | `sales:v2:write`, `customers:v2:write` y `terminals:v1:write`, ligados al Edge y branch exactos. Mantener clientes v2 apagado aunque se entregue el scope. |
| Central catálogo, del claim | `CENTRAL_CATALOG_TOKEN`, `CENTRAL_CATALOG_CREDENTIAL_ID` | Sólo `catalog:v2:read` y `catalog:v2:ack` para la misma sucursal/Edge. No publica precios. |
| Mensual v1 de rollback | `VPS_CONSOLIDACION_TOKEN` | Mantener v1 aislado hasta E2E/corte autorizado. |

Central debe soportar rotación/revocación por `credential_id` y audience, sin cambiar identidad ni idempotencia. Los secretos del claim se entregan una sola vez por HTTPS al Edge y se persisten en su recibo privado; cualquier emisión manual usa archivo/descriptor protegido. Jamás en stdout, logs, URL o reportes. Variables de identidad nuevas reconocidas por el instalador POS: `CENTRAL_API_BASE_URL`, `CENTRAL_BRANCH_ID`, `CENTRAL_BRANCH_CODE`, `CENTRAL_POS_INSTANCE_ID`, `CENTRAL_EDGE_LABEL` y `CENTRAL_ENROLLMENT_REQUEST_ID`. `CENTRAL_API_CA_BUNDLE` señala una CA persistente y protegida. La política POS mantiene `CENTRAL_ENABLE_SALES_V2=false`, `CENTRAL_ENABLE_CUSTOMERS_V2=false` y `CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=false`; Pedidos usa `PEDIDOS_SUCURSALES_FUENTE=desactivada` y `PEDIDOS_SUCURSALES_AUTO_SYNC=false` hasta su propio corte. Ninguna rotación Central debe alterar la credencial Pedidos, y viceversa.

## Matriz HTTP → acción Edge

| Ruta/resultado | Acción obligatoria |
| --- | --- |
| Claim 201 válido | Persistir recibo privado sólo tras validar identidad, módulos y scopes; confirmar código de sucursal en el instalador. |
| Claim 401/409/410/426/429 o timeout ambiguo | Detener instalación y resolver en Central; no reutilizar tarjeta ni continuar con identidad parcial. |
| Pedidos v2 200 válido | Persistir pedidos, deduplicación y `next_cursor` en una transacción SQLite. Una página vacía válida puede significar “sin nuevos”. |
| Pedidos v2 **410 `retention_gap`** (también cursor legado con época numérica) | `REQUIERE_CONCILIACION`; conservar cursor, high-water mark y pedidos locales; detener avance automático. **Nunca** interpretar como “no hay pedidos”. Recuperar mediante snapshot/exportación auditada y decisión explícita sobre nuevo cursor. |
| Pedidos 401/403/404/409/422 | Detener sólo ese flujo o conciliar contrato/identidad; v1/Supabase sigue como rollback. No inventar cursor. |
| Pedidos 429/5xx/timeout/TLS | Backoff acotado en segundo plano, sin bloquear POS. |
| Ventas/clientes 200/201 con ACK válido | Confirmar evento outbox durable en transacción. `purgado` es entrega definitiva; no rehidratar. |
| Ventas/clientes 2xx sin ACK, 400/409/413/415/422 | Conservar outbox/cuerpo/UUID; cuarentena o conciliación según `contracts/edge-central/MATRIZ_HTTP_ACCIONES.md`. |
| Ventas/clientes 401/403/404/429/5xx/timeout/TLS | Suspender credencial/flujo o reintentar con backoff; nunca bloquear venta local. |
| Catálogo 200 snapshot completo válido | Verificar branch, esquema, checksum, conteos y cadena exacta: v1 sin predecesora sólo sobre Edge vacío; luego versión activa+1 y predecesora activa. Aplicar snapshot, mapeos y ACK durable de forma atómica. |
| Catálogo 204/304/401/403/404/410/429/5xx/TLS | Conservar último menú válido. Un 410 histórico consulta `/actual/`; jamás borra el menú. |
| Catálogo snapshot parcial/inválido, o ACK 409 | Rechazar o conciliar; conservar menú y ACK previo. Una publicación parcial nunca es menú activo. |

El detalle por status, incluidos 413, 415 y 422, está en `contracts/edge-central/MATRIZ_HTTP_ACCIONES.md`. TLS verificado y límites por payload son obligatorios; un rate limit por worker no es garantía de aislamiento.

## Bloqueo crítico: instalación limpia sin menú

`instalar-universal.ps1` y `instalar-servicio-lan.ps1` dejan `CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=false`; el inicializador histórico `-InicializarDatosArboledas` es opt-in y sólo acepta Arboledas. Una instalación limpia universal en Santa Anita puede terminar con módulos/servicio correctos, pero **sin productos vendibles**. La ruta POS `sincronizar_catalogo_central` y la aplicación SQLite atómica ya son candidatas, pero Central aún no publica el snapshot completo por sucursal con descarga y ACK reales. Así no se puede declarar Production 1.0 ni usar el ensayo de base limpia como prueba de operación comercial.

Trabajo imprescindible en Central antes del primer piloto operable:

1. Aprobar datos iniciales de categorías, productos, precios efectivos y disponibilidad para **cada sucursal**, con UUID estables y revisión humana. No copiar el menú/precios Arboledas a Santa Anita por conveniencia.
2. Separar catálogo editable de release inmutable y materializar una publicación **completa por sucursal**. La versión 1 debe tener `publicacion_anterior_id=null`; versiones siguientes deben formar cadena sin saltos. Resolver excepciones de precio por sucursal, vigencias, categoría y checksum canónico antes de publicar. No exponer una publicación global multisucursal en una respuesta de una branch.
3. Implementar las rutas candidatas `GET /api/v2/edge/catalogo/publicaciones/actual/`, `GET /api/v2/edge/catalogo/publicaciones/{publicacion_id}/` y `POST /api/v2/edge/catalogo/publicaciones/{publicacion_id}/acuse/` con `catalog:v2:read/ack`, branch exacta, tamaño y esquema estrictos, idempotencia de ACK y flag apagado por defecto.
4. Dar al instalador un procedimiento verificable para activar **la primera versión válida** antes de abrir caja: descarga HTTPS/TLS por el Edge enrolado, comprobación de identidad/checksum, aplicación SQLite en una transacción y ACK durable. Si el bootstrap de catálogo falla, la sucursal no queda “lista para vender”; un update posterior conserva la última versión válida incluso offline.
5. Probar rollback, reinicio/corte entre descarga y activación, error de checksum, publicación parcial, branch ajena, salto de versión, predecesora errónea y precio futuro obsoleto. Tickets ya cerrados conservan nombres/precios capturados. No habilitar `CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2` en una sucursal real hasta E2E aprobado.

Un paquete firmado de bootstrap offline con el mismo contrato de snapshot podría ser alternativa para una primera instalación sin conexión, pero necesita diseño, firma/verificación, aprobación por sucursal y pruebas propias; no existe todavía. No tratar el seed Arboledas como sustituto.

## Pruebas y E2E de integración propuestos

Comprobaciones locales existentes, sin red:

`python -m unittest tests.test_enrolamiento_edge -v`
`python -m unittest tests.test_contracts_vps -v`
`python contracts/pedidos-v2/test_contract.py -v`
`python manage.py test ventas.test_pedidos_api_v2 ventas.test_dev10_integraciones ventas.test_dev10_outbox_concurrencia -v 1`

Ejecución local el 2026-09-25: enrolamiento 6/6, contratos Central 8/8 y contrato Pedidos 7/7. Son pruebas sin red; la suite Django indicada arriba queda sujeta al informe de regresión integral de la candidata.

El ensayo `python herramientas/ensayar_candidata_1_0.py` valida SQLite limpia y migración desde dev.10 de forma aislada; **no** demuestra enrolamiento Central real, impresión física, catálogo inicial o servicio Windows limpio. Las pruebas de contrato usan fixtures sintéticos, no prueban PostgreSQL ni autorización remota.

E2E privado mínimo, sin corte productivo:

1. Congelar commits de Central, POS y Pedidos, hashes de contratos/fixtures y una base PostgreSQL exclusiva de laboratorio. Mantener 8010/8011 privados y Pedidos 8002 sin alterar; habilitar rutas sólo en servicios aislados y TLS/CA con hostname comprobado. No usar `verify=False`.
2. Emitir tarjetas nuevas para Edge de laboratorio Arboledas y Santa Anita. Confirmar 201 una sola vez, 409 al repetir, 410 por caducidad, identidad ajena, scopes, CA errónea y timeout ambiguo. Registrar/repetir una terminal sin darle bearer.
3. Aprobar en la matriz el `SucursalCliente.id` de Arboledas y emitir un token Pedidos por Edge limitado a ese ID. Probar que no puede consultar otra sucursal; un token Pedidos rotado no interrumpe ingesta/catálogo. Forzar `410 retention_gap` y validar el flujo de conciliación/exportación antes de adoptar cursor nuevo.
4. Publicar versión 1 completa para cada sucursal de laboratorio y demostrar menú vendible tras instalación limpia; luego versión 2 por excepción de precio, rechazo seguro de parcial/checksum incorrecto, corte/reinicio y ACK perdido. Comprobar que Arboledas no ve precios Santa Anita y viceversa.
5. Probar outbox de venta/cliente con ACK perdido, mismo UUID/cuerpo repetido, cuerpo distinto 409, rotación de token, 401/403, 413 y fallo TLS. Restaurar backup SQLite y PostgreSQL aislados y verificar que cursor, ACK, mapeos y outbox sobreviven.
6. Probar operación local offline: venta, cobro e impresión sin VPS, sin que sincronización o registro de terminal participen en esa ruta. Documentar resultado, limitaciones y aprobación de corte posterior por separado.

Quedan además por implementar/probar en Central las rutas v2 detalladas, scopes ligados a branch/Edge, idempotencia que sobreviva rotación/purga, respaldo permanente de credenciales revocadas, recibos/ACK y mapeos, y restauración real PostgreSQL. La API de clientes y la distribución de catálogo deben permanecer desactivadas hasta reconciliar contratos y completar este E2E.
