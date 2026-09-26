# Contratos Edge ↔ servicios VPS

Este paquete contiene contratos ejecutables y datos exclusivamente sintéticos. No activa rutas, no contiene credenciales y no autoriza un corte productivo.

## Estado por flujo

| Flujo | Estado comprobado al 2026-09-26 |
| --- | --- |
| POS → Central, consolidación mensual v1 | **Implementado como candidato privado** en el central. `consolidacion-mensual-v1-receptor-actual.schema.json` reproduce su aceptación observada; `consolidacion-mensual-v1-request.schema.json` es el perfil estricto emitido por el POS. |
| POS → Central, ventas detalladas v2 | **Propuesto y desactivado**. No existe todavía la ruta central ni el productor durable completo en el POS. |
| POS → Central, clientes v2 | **Propuesto y desactivado**. La ruta central v1 de borrador permanece apagada y no debe activarse como sustituto. |
| Central → POS, catálogo y ACK v2 | **Candidato de laboratorio**. Se conserva la compatibilidad v2 y el límite ACK legado de 16 KiB; `mappings-large-1` requiere negociación y flag Central explícito. No hay corte productivo. |
| Central → POS, catálogo y promociones v3 | **Candidato Edge/Central de laboratorio, sin despliegue**. El E2E sintético v3/1→v3/4 por PostgreSQL/TLS pasó; Central aún debe importar sólo el menú real clasificado de Arboledas y publicar el primer snapshot autorizado. |
| Pedidos v2 → POS | **Candidato sin merge ni despliegue** `8fad56815f856b4286a2f60f488480960e34cde5`. Contrato ejecutable separado en `contracts/pedidos-v2/`; v1/Supabase legacy se conservan. |

## Rutas ejecutables por flujo

| Flujo | OpenAPI / schemas | Fixtures / prueba |
| --- | --- | --- |
| Pedidos API v2 → POS | `contracts/pedidos-v2/openapi.json`; `contracts/pedidos-v2/schemas/pagina-pedidos-v2.schema.json`; `contracts/pedidos-v2/schemas/error-pedidos-v2.schema.json` | `contracts/pedidos-v2/fixtures/index.json`; `contracts/pedidos-v2/test_contract.py` |
| POS → Central, mensual v1 | `contracts/edge-central/openapi.json`; `contracts/edge-central/schemas/consolidacion-mensual-v1-request.schema.json`; `contracts/edge-central/schemas/consolidacion-mensual-v1-response.schema.json` | `contracts/edge-central/fixtures/index.json`; `tests/test_contracts_vps.py` |
| POS → Central, ventas detalladas v2 | `contracts/edge-central/openapi.json`; `contracts/edge-central/schemas/ventas-lote-v2-request.schema.json`; `contracts/edge-central/schemas/ventas-lote-v2-response.schema.json` | `contracts/edge-central/fixtures/index.json`; `tests/test_contracts_vps.py` |
| POS → Central, clientes v2 | `contracts/edge-central/openapi.json`; `contracts/edge-central/schemas/cliente-evento-v2-request.schema.json`; `contracts/edge-central/schemas/cliente-evento-v2-response.schema.json` | `contracts/edge-central/fixtures/index.json`; `tests/test_contracts_vps.py` |
| Central → POS, catálogo y ACK v2 | `contracts/edge-central/openapi.json`; `contracts/edge-central/schemas/catalogo-publicacion-v2.schema.json`; `contracts/edge-central/schemas/catalogo-ack-v2-request.schema.json`; `contracts/edge-central/schemas/catalogo-ack-v2-response.schema.json` | `contracts/edge-central/fixtures/index.json`; `tests/test_contracts_vps.py` |
| Central → POS, catálogo/promociones v3 | `contracts/edge-central/schemas/catalogo-publicacion-v3.schema.json`; `contracts/edge-central/CATALOGO_V3_EDGE.md` | `contracts/edge-central/fixtures/catalogo-publicacion-v3-lab01-promocion.json` (sintético, sin metadata HTTP hasta cerrar ruta); `tests/test_contracts_vps.py` |
| Identidad POS ↔ Pedidos ↔ Central | `contracts/edge-central/schemas/matriz-identidades-v1.schema.json`; `contracts/edge-central/MATRIZ_IDENTIDADES.md` | `contracts/edge-central/fixtures/matriz-identidades-v1.json`; `tests/test_contracts_vps.py` |

La implementación central v1 acepta actualmente propiedades adicionales dentro de `sucursal` y `totales`; el perfil normativo de este paquete las rechaza. El emisor POS existente sólo genera las propiedades declaradas. Agente1 debe endurecer el receptor central y conservar compatibilidad con estos fixtures. El fixture `consolidacion-mensual-v1-receptor-actual-compat.json` fija esa diferencia: es válido para el receptor `a931c46` y deliberadamente inválido para el perfil emisor POS.

## Reglas comunes

- JSON UTF-8, claves únicas y `Content-Type: application/json`.
- UUID persistentes; un reintento nunca genera otro identificador.
- Importes y cantidades viajan como cadenas decimales. No se admiten `float`, `NaN` ni infinitos.
- Timestamps usan RFC 3339 con zona. Fechas de negocio usan `YYYY-MM-DD`.
- El contrato objetivo liga cada token a una sucursal e incluye scopes explícitos. Pedidos todavía usa una allowlist global; esa brecha no se toma como autorización suficiente.
- El servidor confirma sólo después del commit. El Edge persiste el ACK antes de cerrar su outbox.
- Los hashes de `eventos`, `cliente` o `contenido` se calculan sobre JSON canónico UTF-8: claves ordenadas, sin espacios, Unicode sin escapar y sin valores no finitos.
- `Authorization`, tokens y payloads de clientes no se registran.
- La verificación TLS es obligatoria. Las pruebas privadas usan una CA de laboratorio confiada, nunca `verify=False`.

## Límites propuestos

| Cuerpo | Máximo |
| --- | ---: |
| Consolidación mensual v1 | 256 KiB |
| Lote de ventas v2 | 256 KiB y 100 eventos |
| Evento de cliente v2 | 64 KiB, 10 teléfonos y 10 domicilios |
| ACK de catálogo v2 legado | 16 KiB sin perfil |
| ACK de catálogo v2 `mappings-large-1` | 1 MiB, sólo tras `OPTIONS` autenticado a la misma ruta de acuse con 204, `X-Catalog-Ack-Profile: mappings-large-1`, `X-Catalog-Ack-Max-Body-Bytes: 1048576` y `Cache-Control: no-store`; POST agrega el encabezado del perfil y conserva cuerpo, `ack_id` e idempotencia |
| Snapshot completo de catálogo v2 | 1 MiB sin imágenes binarias |
| Snapshot completo de catálogo/promociones v3 candidato | 1 MiB sin imágenes binarias |

El perfil grande v2 es candidato de laboratorio y requiere activación explícita en Central. Si un servidor viejo no anuncia la capacidad, el ACK local válido queda pendiente con reintento; no se envía un cuerpo mayor al límite legado ni se descarta el outbox. Un 413 después de anunciar el perfil también conserva el evento para revisar proxy/worker. La forma JSON y `version_contrato=2` no cambian. Si Central desactiva el perfil o revierte a un binario anterior después de recibir el ACK pero antes de que Edge guarde su respuesta, el evento local permanece pendiente hasta restaurar la capacidad o conciliarlo; drenar ACK grandes antes del rollback evita ese bloqueo operativo. La reversión no autoriza borrar ni regenerar eventos.

## Índices y matrices

- `contracts/edge-central/fixtures/index.json`: metadatos HTTP reproducibles de los flujos con el central.
- `contracts/pedidos-v2/fixtures/index.json`: metadatos HTTP reproducibles de Pedidos v2, incluyendo cursor legado y `410 retention_gap`.
- `contracts/edge-central/MATRIZ_IDENTIDADES.md`: correspondencias explícitas; la forma ejecutable está en `contracts/edge-central/fixtures/matriz-identidades-v1.json`.
- `contracts/edge-central/MATRIZ_HTTP_ACCIONES.md`: estado local que corresponde a cada respuesta.
- `contracts/edge-central/CREDENCIALES_Y_VARIABLES.md`: scopes y nombres de configuración sin valores.
- `contracts/edge-central/HANDOFF_AGENTE1_CENTRAL.md`: cambios exactos para reconciliar el backend central.

Validación autocontenida:

```powershell
python -m unittest tests.test_contracts_vps -v
python contracts/pedidos-v2/test_contract.py -v
```

Las pruebas usan sólo la biblioteca estándar. Validan JSON Schema, referencias OpenAPI, hashes, relaciones entre fixtures y, para Pedidos, las excepciones reales del cliente POS.
