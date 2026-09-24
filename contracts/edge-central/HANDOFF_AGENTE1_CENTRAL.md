# Handoff técnico para agente1 — Backend Central

**Fecha de auditoría:** 2026-09-24
**Destino:** agente1, responsable de reconciliar y preparar el Backend Central
**Condición:** laboratorio. No hay sucursales en producción y este handoff no autoriza merge, despliegue, DNS/TLS público, API de clientes ni distribución de catálogo.

## Repositorios y revisiones auditadas

Backend Central, sólo lectura:

```text
C:\Users\Srv1\Documents\ChatGPT\VPS Los Tocayos
rama: master
HEAD: 0528b7aa83a5b8d6b7236934956de77db04511f9
remoto Git: no configurado
estado durante la auditoría: limpio
```

Cadena relevante:

| Commit | Contenido |
| --- | --- |
| `a931c462fefcfed021e035ea6aaf367e2d15b1d1` | Base central, mensual v1, recibos/tombstones, clientes v1 de borrador, retención/exportación y portal inicial. |
| `d3490a173bcfff0e23531cd5ffaa0cb8c51f899a` | Catálogo central de dueño y plan de integración; aún sin distribución al POS. |
| `c24f866c452affda026514a3c7f56001a741acf9` | Desactiva el control socket de Gunicorn en el preview. |
| `367e3d07389d760225a27b3b2f257f217b82fde5` | MFA, step-up, hardening del portal y retiro de staging antiguos de Pedidos. |
| `0528b7aa83a5b8d6b7236934956de77db04511f9` | Sólo documentación adicional de preview/MFA sobre `367e3d0`. |

Candidato de Pedidos entregado por agente2: `8fad56815f856b4286a2f60f488480960e34cde5`, sin merge ni despliegue. Conserva v1; v2 añade `Pedido.codigo_publico`, cursor firmado y `410 retention_gap`, rechaza cursores antiguos de época numérica y corrigió el filtrado de purgas por sucursal. Su allowlist de token continúa global y PostgreSQL real/E2E POS siguen pendientes.

Candidato POS que contiene este paquete:

```text
C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tcys-base-work\base-a-src
rama: codex/candidata-0.4.0-dev.10
base auditada al iniciar el paquete: f230ba06b9464e1541820a931037fde9b4d38dce
```

No se creó commit para estos contratos; agente integrador debe revisar el estado del worktree y conservar los cambios concurrentes de la candidata.

## Diferencias de los servicios privados

| Superficie | Revisión desplegada documentada | Estado comprobado |
| --- | --- | --- |
| `127.0.0.1:8010` central base | `a931c46` | Privada. Mensual v1, clientes draft desactivados y portal inicial. No incluye MFA; la ruta `/admin/` seguía expuesta en esta revisión. No promover. |
| `127.0.0.1:8011` preview catálogo | `367e3d0` | Privada. Catálogo central, MFA/step-up y hardening; `/admin/` responde 404; clientes draft apagados. Paquete documentado: SHA-256 `71abbfc8ac800af8a92ed925e9d80e5fa3da112caf931b02442f1dcd74aa9b2d`. |
| `master`/HEAD local | `0528b7a` | Mismo código ejecutable que `367e3d0`; sólo cambian documentos operativos. No implica despliegue nuevo. |

Las unidades versionadas están en `deploy/tcyscentral-staging.service` (8010) y `deploy/tcyscentral-catalog-preview.service` (8011). Ambas enlazan loopback; 8011 incluye `--no-control-socket`. Los staging antiguos de Pedidos 8000/8001 fueron retirados; Pedidos productivo permanece en 8002 según el resultado obligatorio de agente1.

## Estado real del central

### Implementado como candidato privado

- `POST /api/v1/edge/consolidaciones-mensuales/`, registrado en `central/urls.py` y procesado por `ingest/views.py` + `ingest/services.py`.
- Máximo global de carga 1 MiB en `central/settings.py`; el perfil POS de este paquete limita mensual v1 a 256 KiB.
- Bearer almacenado por hash y ligado a `ingest.Branch` mediante `ingest.EdgeCredential`.
- `Idempotency-Key` UUID debe coincidir con `idempotencia` del cuerpo.
- Primera recepción devuelve 201 y repetición idéntica 200 con el mismo acuse. Conflictos devuelven 409.
- `ingest.IngestReceipt` queda permanente; `ingest.MonthlySnapshot` es temporal. La purga elimina el snapshot y conserva recibo, hash, período y marca de purga para impedir rehidratación.
- Portal de exportación y purga, más `purge_transients`, están implementados. Exportar, verificar, confirmar y purgar siguen siendo operaciones humanas/CLI; no hay automatización productiva aprobada.
- Clientes y catálogo central se incluyen en la allowlist de respaldo permanente; snapshots mensuales quedan fuera. La restauración real aún necesita una prueba con PostgreSQL.
- Portal MFA/TOTP, códigos de recuperación, invalidación de sesiones y step-up de cinco minutos están implementados en `security/`, `central/auth_views.py` y decoradores de operaciones críticas.

### Candidato o desactivado

- `POST /api/v1/edge/clientes/` existe en `central/urls.py`, pero `ingest/views.py` devuelve 404 salvo `CENTRAL_ENABLE_DRAFT_CUSTOMER_API=1`. Debe permanecer apagado; no sustituye el contrato v2 de este paquete.
- `ingest.CatalogProduct`, `CatalogBranchPrice`, `CatalogPublication` y `CatalogEvent` permiten preparar y publicar revisiones dentro del central. No existe snapshot completo por sucursal, descarga Edge ni ACK.
- No existen rutas centrales para ventas detalladas v2, clientes v2, distribución de catálogo v2 o ACK.
- `EdgeCredential` liga un token a una sucursal, guarda `active`/`revoked_at` y su hash, pero no modela scopes, expiración, instalación Edge, identificador público de credencial ni auditoría/CLI de rotación.
- `issue_edge_token.py` imprime el token una sola vez en stdout. Antes de operación real debe usar un canal de entrega controlado y evitar históricos/logs de terminal.
- El rate limit Nginx de ejemplo es por IP. El throttle de aplicación usa memoria local por worker; ninguno sustituye autorización por sucursal.
- El receptor mensual acepta hoy propiedades anidadas que el perfil estricto rechaza. El POS emite sólo las propiedades conocidas; el servidor debe endurecerlas sin romper los fixtures válidos.
- Las 42 pruebas observadas cubren lógica local, pero no sustituyen concurrencia/idempotencia sobre PostgreSQL real, rotación de credenciales, restauración ni E2E POS.
- MFA tiene pruebas automatizadas; falta completar una ceremonia E2E con propietario real, TOTP, recuperación y custodia de claves.

## Contratos que debe consumir agente1

Todos son JSON UTF-8 sintéticos y no contienen secretos. Las rutas son relativas a la raíz del repositorio POS:

| Flujo | Contrato y schemas exactos | Fixtures y prueba ejecutable |
| --- | --- | --- |
| Pedidos API v2 → POS | `contracts/pedidos-v2/openapi.json`; `contracts/pedidos-v2/schemas/pagina-pedidos-v2.schema.json`; `contracts/pedidos-v2/schemas/error-pedidos-v2.schema.json` | `contracts/pedidos-v2/fixtures/index.json`; `contracts/pedidos-v2/test_contract.py` |
| POS → Central, consolidación mensual v1 | `contracts/edge-central/openapi.json`; `contracts/edge-central/schemas/consolidacion-mensual-v1-receptor-actual.schema.json`; `contracts/edge-central/schemas/consolidacion-mensual-v1-request.schema.json`; `contracts/edge-central/schemas/consolidacion-mensual-v1-response.schema.json` | `contracts/edge-central/fixtures/index.json`; `tests/test_contracts_vps.py` |
| POS → Central, ventas detalladas v2 | `contracts/edge-central/openapi.json`; `contracts/edge-central/schemas/ventas-lote-v2-request.schema.json`; `contracts/edge-central/schemas/ventas-lote-v2-response.schema.json` | `contracts/edge-central/fixtures/index.json`; `tests/test_contracts_vps.py` |
| POS → Central, clientes v2 | `contracts/edge-central/openapi.json`; `contracts/edge-central/schemas/cliente-evento-v2-request.schema.json`; `contracts/edge-central/schemas/cliente-evento-v2-response.schema.json` | `contracts/edge-central/fixtures/index.json`; `tests/test_contracts_vps.py` |
| Central → POS, catálogo y ACK v2 | `contracts/edge-central/openapi.json`; `contracts/edge-central/schemas/catalogo-publicacion-v2.schema.json`; `contracts/edge-central/schemas/catalogo-ack-v2-request.schema.json`; `contracts/edge-central/schemas/catalogo-ack-v2-response.schema.json` | `contracts/edge-central/fixtures/index.json`; `tests/test_contracts_vps.py` |
| Identidad POS ↔ Pedidos ↔ Central | `contracts/edge-central/schemas/matriz-identidades-v1.schema.json`; `contracts/edge-central/MATRIZ_IDENTIDADES.md` | `contracts/edge-central/fixtures/matriz-identidades-v1.json`; `tests/test_contracts_vps.py` |
| HTTP → acción Edge y credenciales | `contracts/edge-central/MATRIZ_HTTP_ACCIONES.md`; `contracts/edge-central/CREDENCIALES_Y_VARIABLES.md` | Validación documental dentro de ambas pruebas anteriores |

Ejecutar:

```powershell
python -m unittest tests.test_contracts_vps -v
python contracts/pedidos-v2/test_contract.py -v
```

Variables Edge que deben conservar nombre y separacion: `PEDIDOS_API_TOKEN` para lectura de Pedidos, `CENTRAL_INGEST_TOKEN` para escritura de ventas/clientes y `CENTRAL_CATALOG_TOKEN` para lectura/ACK de catalogo. No compartir ni acoplar su rotacion.

Estados normativos en `openapi.json`:

| Método y ruta | Estado | Scope |
| --- | --- | --- |
| `GET /api/v2/pos/pedidos/` | `candidato-sin-merge-sin-deploy` en Pedidos, no en el central | `orders:v2:read` propuesto; candidato aún con allowlist global |
| `POST /api/v1/edge/consolidaciones-mensuales/` | `implementado-candidato-privado` | `sales:v1:write` propuesto sobre el binding actual |
| `POST /api/v2/edge/ventas/lotes/` | `propuesto-desactivado` | `sales:v2:write` |
| `POST /api/v2/edge/clientes/eventos/` | `propuesto-desactivado` | `customers:v2:write` |
| `GET /api/v2/edge/catalogo/publicaciones/actual/` | `propuesto-desactivado` | `catalog:v2:read` |
| `GET /api/v2/edge/catalogo/publicaciones/{publicacion_id}/` | `propuesto-desactivado` | `catalog:v2:read` |
| `POST /api/v2/edge/catalogo/publicaciones/{publicacion_id}/acuse/` | `propuesto-desactivado` | `catalog:v2:ack` |

Los payloads exactos están en el índice y fixtures; no reconstruirlos desde ejemplos de documentación. Para mensual v1, el OpenAPI referencia `consolidacion-mensual-v1-receptor-actual.schema.json`; la extensión `x-pos-emitter-profile` señala el subconjunto estricto que produce el POS. El fixture `consolidacion-mensual-v1-receptor-actual-compat.json` debe aceptarse en `a931c46` y rechazarse por el perfil emisor. Reglas esenciales:

- Pedidos v2: bearer opaco, `SucursalCliente.id` exacto, `Pedido.codigo_publico` durable, cursor opaco y `410 retention_gap` como conciliación;
- mensual v1: `Idempotency-Key == body.idempotencia`;
- ventas v2: `Idempotency-Key == lote_id`, 1–100 eventos, hash canónico de `eventos`;
- clientes v2: `Idempotency-Key == evento_id`, hash canónico de `cliente`, identidad `(sucursal.id, cliente_id)`;
- catálogo v2: siempre snapshot completo por sucursal, hash canónico de `contenido`, sin binarios de imágenes y cadena exacta (`v1` sin predecesora sólo sobre Edge vacío; después `version_sucursal = activa + 1` y `publicacion_anterior_id = publicacion_id` activa);
- ACK catálogo: `Idempotency-Key == ack_id` y repite sucursal, release, publicación, versión y checksum aplicados/rechazados.

## Cambios exactos solicitados al central

1. Crear una rama desde `0528b7a`; conservar v1 y la bandera draft de clientes apagada.
2. Versionar/copy exacto de `contracts/edge-central/` en el central o generar validadores desde esos archivos. Consumir `contracts/pedidos-v2/` para coordinar con agente2; esa ruta pertenece a Pedidos y no debe implementarse dentro del central. Hacer que CI ejecute los mismos fixtures válidos e inválidos.
3. Evolucionar `EdgeCredential` con scopes explícitos, `credential_id`, instalación Edge opcional, `expires_at`, `last_used_at` y registro auditable de rotación; conservar y usar correctamente `active`/`revoked_at`. Mantener el vínculo obligatorio a `Branch`.
4. Cambiar idempotencia para que la identidad durable sea sucursal + UUID de solicitud + hash/contenido, no el token concreto. Una rotación debe devolver el mismo ACK a un reintento legítimo.
5. Implementar ventas v2 tras bandera `false`: recibo/tombstone permanente del lote y contenido temporal detallado. Validar lote, sucursal autenticada, IDs, decimales, timestamps, conteo, checksum y 256 KiB en una transacción. No recalcular precios históricos.
6. Implementar clientes v2 tras bandera `false`: recibo permanente por evento, upsert por `(branch, cliente_id)`, versión monotónica, teléfonos/domicilios por UUID, y límite 64 KiB. No deduplicar por nombre/teléfono y excluir estas tablas de purga.
7. Separar el catálogo editable actual de una `CatalogRelease` inmutable y una `CatalogBranchPublication` inmutable. Agregar UUID estable a categoría. Materializar un snapshot **completo por sucursal** con precio efectivo; una publicación parcial no es descargable. Serializar por sucursal: emitir exactamente la siguiente versión y señalar como predecesora la publicación activa confirmada por el Edge. Una corrección reemplaza la secuencia de precios del producto; los precios `central_v2` previos, incluso futuros, quedan inactivos.
8. Implementar descarga de catálogo y `CatalogAck` durable tras bandera `false`. El ACK se valida contra branch autenticada, release, publicación, versión y checksum. Reintentos idénticos devuelven el mismo resultado; conflicto 409.
9. No exponer directamente la `CatalogPublication` actual: cada publicación de producto contiene alcance/precios de varias sucursales y podría filtrar datos cruzados. Construir y autorizar la representación por branch antes de responder.
10. Añadir límites por ruta antes de decodificar JSON, claves JSON únicas, `Content-Type`, esquema estricto, límites de listas/cadenas, decimales como texto, UUID/RFC3339, y respuestas de error estables. Nunca registrar bearer, payload de cliente ni datos sensibles.
11. Mantener TLS y hostname obligatorios. Configurar scopes + branch como control primario; rate limit por proxy y aplicación sólo como defensa adicional.
12. Actualizar `scripts/backup_permanent.sh`: incluir scopes/credenciales revocadas, recibos/tombstones v2, clientes, releases/publicaciones/mapeos/ACK; excluir contenido temporal de ventas. Probar restauración aislada y actualizar allowlist en cada migración.
13. Agregar comandos de issue/rotate/revoke que no dejen el token en journal, logs, historial o reportes. La salida única debe ir a un descriptor/archivo 0600 explícito o a un gestor de secretos.
14. Agregar pruebas PostgreSQL para dos POST simultáneos, repetición tras rotación, UUID igual/cuerpo distinto, misma venta/evento entre branches, branch ajena, purga+reintento, ACK perdido, restauración y separación de publicaciones por sucursal.
15. Ejecutar `manage.py check --deploy`, suite completa, análisis de migraciones, respaldo/restauración y E2E privado antes de proponer cualquier activación.
16. Mantener `CENTRAL_ENABLE_CUSTOMERS_V2=false` y `CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=false` al entregar. No desplegar, no cambiar DNS/Nginx público y no retirar v1 hasta autorización posterior.

## Matriz mínima de pruebas para agente1

| Prueba | Resultado requerido |
| --- | --- |
| Fixture válido por endpoint | 201 primera vez; 200 con ACK estable al repetir. |
| Mismo UUID y cuerpo distinto | 409 sin mutación. |
| Token de LAB01 con payload/URL LAB02 | 403 antes de persistir. |
| Token sin scope o expirado/revocado | 403/401 estable; sin detalles sensibles. |
| Lote/cliente/snapshot mayor al límite | 413 antes de parseo costoso. |
| Clave duplicada, campo extra, UUID/decimal/fecha inválida | 400/422 según contrato; sin inserción parcial. |
| Dos requests concurrentes en PostgreSQL | Un solo recibo/ACK; sin 500 ni duplicados. |
| Purga y reintento | 200 con tombstone/estado `purgado`; no rehidrata. |
| Cliente con mismo UUID/nombre/teléfono en otra branch | Permanece separado. |
| Catálogo incompleto/checksum errado/branch ajena | Nunca se entrega como publicación válida. |
| Salto de versión/predecesora ajena/replay obsoleto | Edge rechaza con `version_fuera_de_orden`; conserva menú y no crea ACK `aplicado` nuevo. |
| Precio futuro de v1 sustituido por precio vigente de v2 | La fila previa queda inactiva y no reaparece al llegar su fecha. |
| ACK perdido y reenviado | Mismo ACK central y una sola fila durable. |
| Restauración | Permanentes, mapeos y ACK sobreviven; temporales purgados no reaparecen. |
| Logs | No contienen bearer, teléfonos, domicilios ni cuerpos de cliente. |

## E2E seguro posible antes del corte

1. Crear una base PostgreSQL y usuario exclusivos de laboratorio. Cargar sólo fixtures sintéticos.
2. Levantar un servicio central nuevo en loopback con todas las banderas v2 inicialmente `false`; no reutilizar 8010/8011 ni tocar 8002.
3. Exponerlo sólo por un virtual host TLS de laboratorio. Si se accede por SSH, usar port-forward al Nginx TLS y un hostname cuyo certificado/SAN coincida; instalar su CA en `CENTRAL_API_CA_BUNDLE`/`PEDIDOS_API_CA_BUNDLE`. No usar `verify=False`.
4. Emitir tres credenciales sintéticas por Edge: Pedidos lectura, central ingestión y central catálogo. Probar rotación individual y confirmar que los otros dos flujos siguen funcionando.
5. Cargar `contracts/edge-central/fixtures/matriz-identidades-v1.json`, verificar `Sucursal.id` UUID + `clave` → `SucursalCliente.id` entero y marcar el mapa como aprobado. `SucursalCliente` no tiene `codigo_publico`; el UUID `Pedido.codigo_publico` pertenece a cada pedido. No usar nombres.
6. Con el POS y su base de laboratorio, ejecutar mensual v1 y después cada flujo v2 por separado. Simular timeout tras commit, reinicio, pérdida de ACK, respuesta sobredimensionada, TLS inválido y corte de red.
7. Para catálogo, interrumpir la aplicación entre descarga, staging y activación. El menú anterior debe seguir activo; al reiniciar, el ACK durable se reintenta sin duplicar mapeos.
8. Para Pedidos, forzar `410 retention_gap` y cursor de época numérica. El POS debe quedar en conciliación, conservar cursor/pedidos y no informar “sin pedidos”. La salida requiere snapshot completo auditado y una acción explícita.
9. Exportar/purgar datos temporales, restaurar el backup permanente en otra base y repetir consultas/ACK. Documentar hash de artefactos y resultados.
10. Sólo después del E2E conjunto, proponer en otro cambio la activación gradual de una bandera en una instalación laboratorio. Este handoff no la autoriza.

## Brechas que bloquean integración conjunta

- Scopes/rotación central y credencial Pedidos por Edge/sucursal aún no existen de extremo a extremo.
- Falta reconciliar el modelo de identidad de `SucursalCliente` con la matriz real; la allowlist global no basta.
- Falta implementar y probar PostgreSQL para ventas/clientes v2 y catálogo/ACK.
- Falta definir operación de corrección de ventas; no debe sobrescribir un ticket histórico.
- Falta un procedimiento firmado de resincronización después de `retention_gap`.
- Falta comprobar restore real, MFA de propietario real y custodia de secretos/CA.
- No existe todavía DNS/TLS público aprobado para central, ni una decisión de exposición. Mantener privado durante esta fase.

Al devolver el trabajo, agente1 debe reportar commits exactos, migraciones, rutas/flags que permanecen desactivadas, scopes emitibles, prueba PostgreSQL, resultado E2E, hash de artefactos y cualquier divergencia respecto de los schemas/fixtures. Cualquier divergencia requiere una versión nueva del contrato; no se corrige silenciosamente sólo en uno de los extremos.
