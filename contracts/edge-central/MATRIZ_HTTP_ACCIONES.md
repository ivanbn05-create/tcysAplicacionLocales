# Matriz HTTP → acción local

El objetivo es conservar datos y evitar bucles. Una respuesta HTTP nunca modifica ventas, cursor o catálogo si su cuerpo no cumple el contrato esperado.

## Pedidos v2 → POS

| Resultado | Estado local y acción |
| --- | --- |
| 200 página válida | Validar sucursal y esquema. Persistir pedidos, deduplicación y `next_cursor` en **una misma transacción**. Sólo después avanzar el cursor. |
| 200 página vacía válida | Persistir el cursor firmado recibido. Es el único caso que significa “sin pedidos nuevos”. |
| 401 | Suspender sólo el flujo Pedidos; marcar `credencial_invalida`; no tocar el cursor. |
| 403 | Marcar `scope_o_sucursal_incorrectos`; requiere configuración, no reintento rápido. |
| 404 | Versión/ruta incorrecta; conservar v1/Supabase legacy como rollback y alertar. |
| 409 | Conciliación de identidad o cursor; no inventar un cursor. |
| **410 `retention_gap`** | Entrar en `REQUIERE_CONCILIACION`; conservar último cursor y pedidos importados; detener avance automático. Nunca tratarlo como lista vacía. |
| 410 para cursor antiguo con época numérica | Mismo estado `REQUIERE_CONCILIACION`; ejecutar resincronización completa y auditada antes de adoptar un cursor firmado nuevo. |
| 422 | Cuarentena de respuesta/solicitud incompatible; intervención. |
| 429 | Respetar `Retry-After`, usar backoff con jitter y conservar cursor. |
| 5xx, timeout o TLS | Reintento acotado en segundo plano; el POS sigue vendiendo e imprimiendo. |

La salida de `REQUIERE_CONCILIACION` necesita una instantánea/exportación autorizada de Pedidos, comparación por `id`/`codigo_publico`, persistencia atómica y una acción explícita que registre el cursor basal nuevo.

## POS → Central: mensual, ventas y clientes

| Resultado | Acción del Edge |
| --- | --- |
| 200/201 con IDs, estado y checksum válidos | Persistir ACK y `publicado_en` dentro de una transacción. |
| 200 mensual o ventas con `estado=purgado` | Entrega definitiva. No reenviar ni intentar rehidratar. |
| 204 o 2xx sin ACK válido | Error de contrato; evento continúa pendiente. |
| 400 | Cuarentena; el cuerpo es defectuoso. No regenerar UUID. |
| 401 | Suspender esa credencial; rotarla sin cambiar evento, lote ni cuerpo. |
| 403 | Suspender el flujo; revisar scope y sucursal. |
| 404 clientes/ventas v2 | Feature no activado; mantener pendiente con backoff largo. |
| 409 | `REQUIERE_CONCILIACION`; no cambiar UUID, versión ni contenido para forzar éxito. |
| 413 | Error permanente de empaquetado. No dividir silenciosamente un lote ya emitido; una migración explícita debe registrar su reemplazo. |
| 415/422 | Contrato no compatible; cuarentena y alerta. |
| 429 | Respetar `Retry-After`; reintento con jitter. |
| 5xx, timeout o TLS | Reintento acotado. La operación local nunca espera este envío. |

## Central → POS: catálogo

| Resultado | Acción del Edge |
| --- | --- |
| 200 snapshot | Guardar como candidato; validar tamaño, sucursal, esquema, conteos, checksum y cadena exacta. Sólo aplicar `v1` sin predecesora cuando no exista publicación local, o `versión activa + 1` cuyo `publicacion_anterior_id` sea la publicación activa. Saltos, replay obsoleto y predecesora ajena conservan menú/ACK vigentes. |
| 204 | Aún no hay publicación. Conservar la última versión válida. |
| 304 | No hay cambios. No modificar estado local. |
| 401/403 | Conservar versión activa; suspender sincronización y alertar. |
| 404 | Ruta desactivada o ID ajeno. Conservar versión activa. |
| 410 publicación histórica | Solicitar `/actual/` como snapshot completo. No vaciar el menú. |
| 409 al enviar ACK | Conciliación de release/checksum; conservar el ACK durable. |
| 413/415/422 | Rechazar de forma permanente, conservar versión activa y encolar ACK `rechazado`. |
| 429/5xx/timeout/TLS | Reintento en segundo plano; conservar versión activa. |

Un checksum incorrecto, un esquema desconocido, una sucursal distinta, una cadena discontinua o una referencia incompleta nunca se convierten en menú activo. El ACK `aplicado` se crea en la misma transacción SQLite que la activación y sobrevive reinicio, actualización y restauración. Al activar un snapshot completo, el POS desactiva todas las filas de precio `central_v2` anteriores del producto antes de activar el precio publicado; así un precio futuro obsoleto no reaparece.

Para catálogo v3 privado, el Edge usa `GET /api/v3/edge/catalogo/publicaciones/actual/` y `POST /api/v3/edge/catalogo/publicaciones/{uuid}/acuse/` sólo con su flag v3 activo. El ACK v3 aplicado/rechazado lleva `version_contrato=3`, mappings exhaustivos y límite de 1 MiB. Cada evento v2 ya persistido conserva su payload, hash y ruta v2; requiere el flag/scope v2 para drenarse. Ambas banderas siguen apagadas por defecto.
