# Rearme auditado de ACK v2 grande · sólo laboratorio

Este procedimiento sirve exclusivamente para el evento de catálogo v2 que quedó en `CUARENTENA` por el preflight antiguo de 16 KiB antes de cualquier HTTP. No convierte cualquier cuarentena en pendiente. No ejecutar en `C:\LosTocayosPOS` ni en una sucursal operativa.

## Precondiciones

1. Congelar el SHA del código POS (`087d5ac00955a76a12c5ed2af8ac87dc526bad1e`) y del Central candidato usado. Conservar intacto el checkout `.e2e-pos` anterior; preparar un checkout y una copia de SQLite/configuración/recibo/CA nuevos en laboratorio. Detener sus sincronizadores y comprobar que sólo un proceso usa la identidad Edge.
2. Tomar backup SQLite mediante su API de backup, comprobar `integrity_check=ok` y `foreign_key_check` vacío. Guardar de forma privada hashes de `.env`, base y recibo; nunca imprimir tokens. Verificar identidad Edge, sucursal, publicación activa, número de productos y UUID del evento.
3. En Central de laboratorio, consultar por el `ack_id` exacto, Edge, sucursal, publicación y checksum: el conteo remoto debe ser **cero**. Guardar evidencia de esa consulta con fecha y SHA-256. Si Central ya recibió el ACK, no rearmar: pasar a conciliación idempotente.
4. Probar primero con otro evento sintético que `OPTIONS` legado 405/HTML deja pendiente sin POST; el perfil activo devuelve 204/cabeceras exactas; 413/503 conservan pendiente; pérdida de 201 y replay devuelve 200 con el mismo `ack_id`. Mantener `CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2` activo mientras drenan ACK v2.

## Verificación y transición en el clon

Ejecutar las siguientes sentencias en `manage.py shell` **apuntando sólo a la copia aislada**, sustituyendo tres marcadores por el UUID, hash local y SHA de evidencia remota registrados. Guardar el transcript sin secretos. El operador debe comprobar antes que no haya otros ACK v2 elegibles para envío.

```python
from uuid import UUID
from django.db import transaction
from ventas.models import EventoOutbox
from ventas.sincronizacion_central import (
    _ruta_evento, _validar_identidad_evento, hash_payload, json_canonico,
)

EVENT_ID = UUID('REEMPLAZAR_UUID')
EXPECTED_HASH = 'REEMPLAZAR_HASH_SHA256_64_HEX'
EVIDENCE_SHA = 'REEMPLAZAR_SHA256_EVIDENCIA_CENTRAL_CERO'
assert len(EXPECTED_HASH) == len(EVIDENCE_SHA) == 64
with transaction.atomic():
    e = EventoOutbox.objects.select_for_update().select_related('sucursal').get(pk=EVENT_ID)
    assert e.destino == EventoOutbox.Destino.CENTRAL_CATALOGO_ACK
    assert e.version_contrato == 2
    assert e.estado_entrega == EventoOutbox.EstadoEntrega.CUARENTENA
    assert e.ultimo_error == 'El payload excede el limite contractual del flujo.'
    assert e.ultima_respuesta_http is None
    assert not e.acuse_remoto and not e.estado_remoto and e.publicado_en is None
    assert e.payload_hash == EXPECTED_HASH == hash_payload(e.datos)
    assert 16 * 1024 < len(json_canonico(e.datos)) <= 1024 * 1024
    _validar_identidad_evento(e)
    assert _ruta_evento(e).startswith('/api/v2/edge/catalogo/publicaciones/')
    cambiado = EventoOutbox.objects.filter(
        pk=e.pk, estado_entrega=EventoOutbox.EstadoEntrega.CUARENTENA,
        intentos=e.intentos, payload_hash=EXPECTED_HASH,
    ).update(
        estado_entrega=EventoOutbox.EstadoEntrega.PENDIENTE,
        proximo_intento_en=None,
        ultimo_error='Rearme lab de preflight v2 antiguo; evidencia=' + EVIDENCE_SHA,
    )
    assert cambiado == 1
```

El cambio conserva `id`, `datos`, `payload_hash`, `intentos`, identidad y versión. Si falla cualquier condición, abortar sin editar la fila. El campo `ultimo_error` puede cambiar en el siguiente intento, por lo que el transcript y la evidencia remota deben quedar en el acta privada. El script no prueba por sí mismo el conteo remoto cero.

Después, ejecutar el sincronizador sólo con el perfil anunciado en Central de laboratorio. Exigir `OPTIONS` 204 sin cuerpo, `Cache-Control: no-store`, `X-Catalog-Ack-Profile: mappings-large-1` y `X-Catalog-Ack-Max-Body-Bytes: 1048576`; comprobar POST idéntico al payload/hash original y `Idempotency-Key=ack_id`. Con respuesta perdida, repetir y exigir 200/201 válido del mismo `ack_id`. Registrar estado remoto/local y conteo Central antes/después. Un ACK diferente, identidad distinta o 409 queda en `CONCILIACION`; no regenerar el evento.

## Criterio de parada

No tocar el evento original si faltan backup, consulta Central cero, identidad, hash o motivo de cuarentena exactos. No ampliar 16 KiB legado ni borrar outbox. Si Central revierte o apaga el perfil después de aceptar el POST y antes de responder, el evento queda pendiente hasta reactivar el perfil o conciliarlo; drenar ACK grandes antes del rollback. Repetir v2→v3 con los flags separados y comprobar que tickets y precios históricos permanecen intactos. Documentar SHA POS/Central, CA/TLS, DB, evento y fixture en `docs/CERTIFICACION_CATALOGO_V3_DEV2.md`.
