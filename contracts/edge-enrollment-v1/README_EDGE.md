# Enrolamiento Edge ↔ Central 1.0

Esta carpeta conserva el contrato ejecutable candidato recibido de Central el 25-09-2026. La ruta Central /api/v1/enrollment/claim/ está implementada detrás de CENTRAL_ENABLE_ENROLLMENT_V1=1 y **no está habilitada en producción**. Los tokens de fixtures son sintéticos.

El operador prepara la sucursal en Central y exporta una tarjeta JSON privada con code, expected_branch_id, expected_edge_id y request_id. El instalador universal lee esa tarjeta desde un archivo con ACL que sólo permita SYSTEM, Administradores y el usuario técnico actual. El código no va en línea de comandos, URL, logs, Git ni recibo persistido. La respuesta 201 se valida estrictamente, incluyendo UUID exactos, versión 1.0, módulos obligatorios, alcance de scopes, vencimiento y credenciales independientes. Si el transporte falla después de enviar la solicitud, **no se reintenta**: se consulta Central, se revoca Edge/credenciales si el código se consumió y se emite otra tarjeta.

La identidad branch.id equivale exactamente a personas.Sucursal.id y CENTRAL_BRANCH_ID. edge.id es CENTRAL_POS_INSTANCE_ID, diferente del UUID de sucursal. pedidos_programados del contrato Central corresponde a la clave interna histórica programados del POS mediante adaptador explícito. pedidos_sucursales sólo puede autorizarse para Arboledas; la emisión de Central permanece bloqueada mientras no se apruebe su SucursalCliente.id. El token de lectura de Pedidos se provisiona por separado; Central no lo emite.

La credencial ingest permite sales:v2:write, customers:v2:write y terminals:v1:write. La credencial catalog permite únicamente catalog:v2:read y catalog:v2:ack. Ninguna terminal recibe credenciales Edge. El Edge registra cada UUID durable de terminal en la ruta candidata /api/v1/edge/terminals/; el registro no participa en el camino crítico de venta/cobro/impresión.

## Validación local

- python -m unittest tests.test_enrolamiento_edge -v valida fixtures, identidad, scopes, módulos, respuesta malformada y ausencia de reintento.
- El E2E real necesita TLS/CA, bandera Central, sucursal de laboratorio activa y tarjeta recién emitida. No se ejecutó contra una sucursal real.

## Reconciliación pendiente

- Agente1 debe confirmar el hash/commit de openapi.json y schemas; las copias locales deben actualizarse juntas si cambian.
- Publicación del catálogo y credenciales de Pedidos requieren pruebas de integración aparte. Una respuesta de enrolamiento nunca activa esos flujos por sí sola.
- Si Central revoca o vence una credencial, rotarla de forma independiente sin cambiar UUID, SQLite, outbox ni cursores.