# Credenciales, scopes y variables por sucursal

Este documento separa tres planos de confianza. Los nombres marcados como **existentes** ya están reconocidos por el candidato POS; los marcados como **propuestos** requieren implementación o reconciliación. Las banderas POS de catálogo y el perfil ACK Central sólo se habilitan en laboratorio hasta completar el E2E con agente1; ninguna ruta se corta en producción.

## Credenciales

| Credencial | Sujeto obligatorio | Scopes mínimos | Uso permitido | Estado |
| --- | --- | --- | --- | --- |
| `PEDIDOS_API_TOKEN` | Una instalación Edge y una allowlist de `SucursalCliente.id` | `orders:v2:read` limitado a esas identidades | Leer páginas de pedidos y obtener un cursor firmado | El nombre existe en POS. El candidato de Pedidos `8fad56815f856b4286a2f60f488480960e34cde5` todavía usa una allowlist global; el binding y scope por Edge son **propuestos**. |
| `VPS_CONSOLIDACION_TOKEN` | Una sucursal central (`Branch.source_id`) | `sales:v1:write` | Consolidación mensual v1 | Flujo **implementado como candidato privado**. El modelo central enlaza credencial y sucursal, pero el scope explícito es **propuesto**. |
| `CENTRAL_INGEST_TOKEN` | Una instalación Edge + una sucursal | `sales:v2:write`, y sólo si se habilita clientes, `customers:v2:write` | Ventas detalladas y eventos de cliente | Nombres y banderas existen en POS; rutas/scopes centrales **propuestos y desactivados**. Puede emitirse un token separado para cada scope si se desea menor privilegio. |
| `CENTRAL_CATALOG_TOKEN` | Una instalación Edge + una sucursal | `catalog:v3:read`, `catalog:v3:ack`; durante transición también `catalog:v2:read`, `catalog:v2:ack` | Descargar snapshots completos de su sucursal y confirmar aplicación/rechazo | Token separado de Pedidos e ingesta; rutas privadas v3 candidatas. Los ACK v2 pendientes requieren scopes v2 y, si exceden 16 KiB, el perfil Central `mappings-large-1`. |

Un token de catálogo no publica, edita ni aprueba precios. Esas acciones pertenecen al portal humano del central con MFA y step-up. Una credencial de Pedidos nunca se reutiliza contra el central, y `CENTRAL_INGEST_TOKEN` nunca se usa para leer Pedidos. La rotación de una credencial no cambia UUID, cursores, outbox, lotes ni ACK de otra integración.

## Propuesta ejecutable para Pedidos

Agente1 y el responsable de Pedidos deben emitir una credencial por Edge con claims o registro equivalente:

```json
{
  "subject": "pos-instance:14141414-1414-4141-8141-141414141414",
  "audience": "pedidos-api",
  "scopes": ["orders:v2:read"],
  "allowed_sucursal_cliente_ids": [101],
  "expires_at": "2026-12-31T23:59:59Z",
  "credential_id": "22222222-2222-4222-8222-222222222222"
}
```

El servidor autoriza los `SucursalCliente.id` exactos ligados a la credencial Edge; el Edge cruza cada ID con el mapeo aprobado en `contracts/edge-central/fixtures/matriz-identidades-v1.json`. `SucursalCliente` no tiene `codigo_publico`; `Pedido.codigo_publico` identifica pedidos individuales y no amplía el scope de sucursal. La allowlist global actual no satisface este contrato. El rate limit de `LocMemCache` por worker sólo amortigua abuso; autorización por sucursal, límites de cuerpo, paginación y auditoría siguen siendo obligatorios.

## Propuesta ejecutable para credenciales del Central

Agente1 debe materializar dos registros distintos por instalacion. Ejemplo sintetico de la autorizacion de ingesta:

```json
{
  "credential_id": "26262626-2626-4262-8262-262626262621",
  "subject": "pos-instance:23232323-2323-4232-8232-232323232323",
  "branch_source_id": "11111111-1111-4111-8111-111111111111",
  "branch_code": "LAB01",
  "audience": "central-ingest",
  "scopes": ["sales:v2:write", "customers:v2:write"],
  "expires_at": "2026-12-31T23:59:59Z"
}
```

Ejemplo separado para catalogo:

```json
{
  "credential_id": "26262626-2626-4262-8262-262626262622",
  "subject": "pos-instance:23232323-2323-4232-8232-232323232323",
  "branch_source_id": "11111111-1111-4111-8111-111111111111",
  "branch_code": "LAB01",
  "audience": "central-catalog",
  "scopes": ["catalog:v2:read", "catalog:v2:ack", "catalog:v3:read", "catalog:v3:ack"],
  "expires_at": "2026-12-31T23:59:59Z"
}
```

Interfaz CLI propuesta para implementar en el central; estos comandos **no existen aun**:

```bash
python manage.py issue_scoped_edge_token --branch-source-id 11111111-1111-4111-8111-111111111111 --pos-instance-id 23232323-2323-4232-8232-232323232323 --audience central-ingest --scope sales:v2:write --scope customers:v2:write --output /run/secrets/lab01-central-ingest.token
python manage.py issue_scoped_edge_token --branch-source-id 11111111-1111-4111-8111-111111111111 --pos-instance-id 23232323-2323-4232-8232-232323232323 --audience central-catalog --scope catalog:v2:read --scope catalog:v2:ack --scope catalog:v3:read --scope catalog:v3:ack --output /run/secrets/lab01-central-catalog.token
python manage.py rotate_edge_token --credential-id 26262626-2626-4262-8262-262626262621 --output /run/secrets/lab01-central-ingest-next.token
python manage.py revoke_edge_token --credential-id 26262626-2626-4262-8262-262626262621 --reason rotacion_completada
```

`--output` debe crear un archivo nuevo con modo 0600 y fallar si ya existe. La salida estandar muestra solo `credential_id`, prefijo no sensible, branch, scopes y expiracion. Nunca muestra el bearer. El token de mensual v1 puede migrar a la misma tabla/scopes, pero `VPS_CONSOLIDACION_TOKEN` se conserva mientras v1 sea rollback.

## Variables del POS ya reconocidas

### Pedidos v2

| Variable | Regla |
| --- | --- |
| `PEDIDOS_SUCURSALES_FUENTE` | Mantener `desactivada`, `supabase` o `sqlite` mientras se prueba. Usar `api_v2` únicamente tras E2E y corte autorizado. |
| `PEDIDOS_SUCURSALES_AUTO_SYNC` | `false` hasta aprobar identidad y credencial. La venta, cobro e impresión no dependen de esta tarea. |
| `PEDIDOS_API_BASE_URL` | URL HTTPS sin credenciales embebidas. |
| `PEDIDOS_API_ENDPOINT` | Ruta relativa del mismo origen; candidato: `/api/v2/pos/pedidos/`. |
| `PEDIDOS_API_TOKEN` | Secreto exclusivo de lectura de Pedidos, mínimo 32 caracteres. |
| `PEDIDOS_API_CA_BUNDLE` | CA confiable del endpoint privado/público. No se admite desactivar verificación TLS. |
| `PEDIDOS_API_SUCURSAL_IDS` | IDs enteros explícitos de `SucursalCliente`; cada uno debe existir en la matriz verificada. No inferir por nombre. |
| `PEDIDOS_API_PAGE_SIZE` | 1–500; usar 100 como base. |
| `PEDIDOS_API_CONNECT_TIMEOUT_SECONDS` | 1–30; base 5. |
| `PEDIDOS_API_READ_TIMEOUT_SECONDS` | 1–60; base 15. |
| `PEDIDOS_API_MAX_RESPONSE_BYTES` | 4 KiB–4 MiB; base 1 MiB. |
| `PEDIDOS_API_MAX_RETRIES` | 0–5; base 2 con backoff/jitter. |

El POS candidato guarda la confirmación manual del vínculo en `ventas.SucursalPedido.identidad_confirmada_en` y el checkpoint/cursor en `ventas.EstadoSincronizacionPedidos`. Antes de activar `api_v2`, todos los IDs configurados deben estar aprobados. `410 retention_gap` conserva el último cursor confirmado y entra en conciliación.

Las variables legacy `PEDIDOS_SUCURSALES_DATABASE_URL` y `PEDIDOS_SUCURSALES_DB_*` se conservan durante el rollback autorizado. Sus secretos no se copian a `PEDIDOS_API_TOKEN`.

### Central

| Variable | Regla |
| --- | --- |
| `VPS_CONSOLIDACION_URL` | Endpoint HTTPS completo de mensual v1; debe configurarse junto con su token. |
| `VPS_CONSOLIDACION_TOKEN` | Secreto exclusivo de mensual v1. |
| `VPS_CONSOLIDACION_TIMEOUT` | 1–60 segundos. |
| `CENTRAL_API_BASE_URL` | Origen HTTPS único de las rutas v2/v3 candidatas. |
| `CENTRAL_BRANCH_ID` | UUID exacto de `personas.Sucursal.id` = `Branch.source_id`. |
| `CENTRAL_BRANCH_CODE` | Código exacto esperado por ambos extremos; es comprobación adicional, no identidad sustituta. |
| `CENTRAL_POS_INSTANCE_ID` | UUID durable creado por instalación; no cambia en una actualización. |
| `CENTRAL_INGEST_TOKEN` | Secreto de escritura ventas/clientes. Distinto de Pedidos y catálogo. |
| `CENTRAL_CATALOG_TOKEN` | Secreto de lectura/ACK catálogo. Distinto de ingestión. |
| `CENTRAL_API_CA_BUNDLE` | CA confiable. No `verify=False`. |
| `CENTRAL_ENABLE_SALES_V2` | Debe permanecer `false` hasta que agente1 implemente y habilite la ruta. |
| `CENTRAL_ENABLE_CUSTOMERS_V2` | Debe permanecer `false`; la API de clientes no está activada. |
| `CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2` | Rollback y drenaje de ACK v2; `false` por defecto. ACK grande sólo si Central anuncia su perfil opt-in. |
| `CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3` | GET/ACK v3 del candidato privado; `false` por defecto hasta E2E. |
| `CENTRAL_CONNECT_TIMEOUT_SECONDS` | 1–30; base 5. |
| `CENTRAL_READ_TIMEOUT_SECONDS` | 1–60; base 15. |
| `CENTRAL_MAX_RESPONSE_BYTES` | 4 KiB–4 MiB; base 1 MiB. |
| `CENTRAL_SYNC_INTERVAL_SECONDS` | 30–86400; base 300. |

## Variables propuestas del central

Los siguientes nombres deben agregarse al backend central con valor seguro por defecto; aún no deben activarse:

```dotenv
CENTRAL_ENABLE_SALES_V2=false
CENTRAL_ENABLE_CUSTOMERS_V2=false
CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2=false
CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3=false
CENTRAL_SALES_V2_MAX_BODY_BYTES=262144
CENTRAL_CUSTOMERS_V2_MAX_BODY_BYTES=65536
CENTRAL_CATALOG_V2_MAX_BODY_BYTES=1048576
CENTRAL_ENABLE_CATALOG_ACK_V2_LARGE=false
CENTRAL_CATALOG_ACK_V3_MAX_BODY_BYTES=1048576
CENTRAL_EDGE_TOKEN_TTL_DAYS=90
CENTRAL_EDGE_TOKEN_HASH_PEPPER_FILE=/run/secrets/central_edge_token_pepper
```

El ACK v2 legado permanece fijo en 16 KiB. El perfil `mappings-large-1` permite 1 MiB sólo con `CENTRAL_ENABLE_CATALOG_ACK_V2_LARGE=1`, OPTIONS autenticado y cabeceras exactas; el antiguo nombre propuesto `CENTRAL_CATALOG_ACK_V2_MAX_BODY_BYTES` no es la señal de capacidad y no debe usarse para ampliar unilateralmente el límite. El perfil debe quedar activo hasta drenar ACK grandes antes de un rollback de Central.

El servidor debe rechazar una credencial sin sucursal, con scope ausente, expirada o revocada antes de procesar el cuerpo. Debe almacenar sólo hash/identificador de token, nunca el bearer completo.

## Almacenamiento y rotación

- Guardar `.env` y CA con las ACL que ya protege la instalación; sólo la cuenta del servicio y administradores pueden leer secretos.
- No imprimir tokens en logs, excepciones, UI, respaldos de diagnóstico ni archivos de contrato.
- Emitir el token nuevo, instalarlo en un único Edge, comprobar autenticación/branch/scope, y revocar el anterior tras un período breve de solapamiento.
- La idempotencia se liga a sucursal + UUID de solicitud y contenido, nunca al ID de credencial. Por eso un reintento tras rotación devuelve el mismo ACK.
- Los respaldos deben incluir outbox, checkpoints, mapeos y ACK; los secretos pueden restaurarse por un canal separado.
- Todos los clientes verifican hostname y cadena TLS. Para laboratorio se instala una CA de prueba en `*_CA_BUNDLE`; no se permite `verify=False` ni certificados aceptados manualmente en cada petición.
