# Integración, operación y validación de 0.4.0-dev.10

- **Fecha de corte documental:** 24 de septiembre de 2026
- **Rama de trabajo:** codex/candidata-0.4.0-dev.10
- **Base al iniciar la candidata:** f230ba06b9464e1541820a931037fde9b4d38dce
- **Commit funcional de la candidata:** c3ba43d9f410ee95bbec060751ed1aba43a16fe6
- **Entorno:** laboratorio, sin sucursales en producción

Esta guía describe el estado candidato del POS. No acredita una release, un E2E con PostgreSQL ni un despliegue. Tampoco autoriza modificar C:\LosTocayosPOS, Pedidos en 8002, los previews centrales en 8010/8011, DNS, TLS o bases remotas.

## Mapa de integración

| Área | Existente y reutilizable | Incorporado en la candidata | Falta o depende de otro frente |
| --- | --- | --- | --- |
| Núcleo POS | Venta, cobro, clientes, domicilios, SQLite local, catálogo local, impresión, respaldos y actualización supervisada. | Actor administrativo protegido, mejoras de administración y ruteo de impresora por terminal con fallback local. | Aceptación final en resoluciones y hardware objetivo; no debe depender del VPS. |
| Pedidos de sucursales | Fuente Supabase explícita y lector SQLite sólo en DEBUG. Pedidos API v1 permanece en el servicio remoto como rollback. | Cliente API v2 estricto, identidad por ID, cursor firmado, checkpoint durable y estado explícito ante 410 retention_gap. | Token y scopes por Edge/sucursal, matriz real POS ↔ SucursalCliente, candidato remoto integrado y E2E. |
| Consolidación mensual v1 | Emisor Edge, idempotencia, ACK y purga local tras confirmación. Receptor central privado candidato. | Payload local inmutable, hash, estado remoto recibido o purgado y conciliación en conflictos. | PostgreSQL real, concurrencia, restore y operación humana de exportar, confirmar y purgar. |
| Ventas detalladas v2 | Tickets y precios históricos locales. | Outbox durable, cliente HTTPS estricto y contratos ejecutables. La bandera inicia apagada. | Ruta, recibos, tombstones, scopes y pruebas PostgreSQL en el central. |
| Clientes v2 | Base local de clientes, teléfonos y domicilios. | Snapshot versionado y outbox durable. La bandera inicia apagada. | API central v2; la API draft v1 debe seguir apagada. |
| Catálogo v2 | Menú local y última versión utilizable. | Cliente, validación de snapshot completo, checksum, aplicación atómica, mapeo UUID explícito y ACK durable. La bandera inicia apagada. | Release/publicación completa por sucursal, rutas de descarga/ACK y categorías con UUID estable en el central. |
| Seguridad de transporte | ACL del Edge, secretos fuera de Git y configuración local preservada por el actualizador. | HTTPS obligatorio, CA configurable, sin verify=False, límites, esquema estricto, rechazo de redirects y credenciales separadas. | DNS/TLS aprobado, custodia y rotación de secretos, scopes en servidor y ceremonia E2E. |

El detalle normativo está en [contratos Edge–Central](../../contracts/edge-central/README.md). El trabajo que debe ejecutar agente1 está en [HANDOFF_AGENTE1_CENTRAL.md](../../contracts/edge-central/HANDOFF_AGENTE1_CENTRAL.md).

## Estado externo recibido como entrada obligatoria

- Los staging antiguos de Pedidos en 8000/8001 fueron retirados; el servicio productivo de Pedidos continúa en 8002 y no forma parte de este cambio.
- El central base continúa privado en 8010 y el preview endurecido de central/catálogo está en 8011. Ninguno es un central productivo.
- El portal candidato incorpora MFA y step-up.
- No existen todavía DNS/TLS público aprobado, API de clientes activada ni purga automática.
- El candidato de Pedidos es 8fad56815f856b4286a2f60f488480960e34cde5, sin merge ni despliegue. Conserva v1; v2 añade `Pedido.codigo_publico`, cursor firmado y 410 retention_gap, y corrige el aislamiento de purgas por sucursal.
- Exportación, confirmación y purga de retención continúan como comandos CLI. PostgreSQL real del VPS y el E2E POS siguen pendientes.

## Identidad, credenciales y límites de confianza

La identidad se resuelve por identificadores, nunca por nombres visibles:

- la sucursal local usa su UUID y código canónico;
- la instalación Edge conserva un UUID durable independiente;
- Pedidos debe mapear esos valores con el `SucursalCliente.id` entero exacto; `SucursalCliente` no tiene `codigo_publico` y su nombre nunca es identidad;
- el central debe ligar cada credencial a una sucursal y, para v2, a la instalación Edge;
- el archivo de referencia es [MATRIZ_IDENTIDADES.md](../../contracts/edge-central/MATRIZ_IDENTIDADES.md).

Arboledas todavía no tiene un mapeo SucursalCliente aprobado. Esa fila debe permanecer pendiente; no se permite inferirla por nombre.

Se mantienen credenciales independientes:

| Plano | Variable Edge | Alcance mínimo |
| --- | --- | --- |
| Lectura de Pedidos | PEDIDOS_API_TOKEN | orders:v2:read para los `SucursalCliente.id` asignados a un único Edge; `Pedido.codigo_publico` sólo deduplica pedidos |
| Consolidación mensual v1 | VPS_CONSOLIDACION_TOKEN | sales:v1:write para una sucursal |
| Ventas y clientes v2 | CENTRAL_INGEST_TOKEN | sales:v2:write y, sólo cuando se autorice, customers:v2:write |
| Catálogo v2 | CENTRAL_CATALOG_TOKEN | catalog:v2:read y catalog:v2:ack para una sucursal |

Rotar una credencial no debe cambiar cursores, lotes, ACK ni las otras credenciales. La allowlist global actual de Pedidos no satisface el aislamiento por sucursal. El rate limit con LocMemCache es por worker y sólo aporta defensa parcial; autorización, esquema, tamaño, TLS y auditoría siguen siendo controles obligatorios.

## Retention gap y conciliación

HTTP 410 con retention_gap indica que el cursor ya no puede probar continuidad. No significa que no existan pedidos.

El Edge conserva el cursor, la ventana, los pedidos ya importados y el último request confirmado; marca la sincronización como conciliación y detiene el avance. La salida requiere una resincronización o snapshot completo auditado y una decisión explícita. No se borra el checkpoint, no se reemplaza el cursor por uno nuevo y no se comunica “sin pedidos”.

Los cursores antiguos cuya época era numérica también deben recibir 410. La [matriz HTTP → acción](../../contracts/edge-central/MATRIZ_HTTP_ACCIONES.md) fija el comportamiento para errores de autenticación, alcance, contrato, límites y fallos transitorios.

## Operación offline y durabilidad

Venta, cobro e impresión se resuelven en el Edge y funcionan sin VPS. Las tareas de sincronización son posteriores y no participan en la transacción interactiva de caja.

Los precios y nombres de partidas cerradas se toman del ticket almacenado; una publicación de catálogo no recalcula ventas históricas. Un catálogo remoto sólo puede activarse si el snapshot completo corresponde a la sucursal, cumple el esquema, su checksum coincide y toda la aplicación termina dentro de una transacción SQLite. Un fallo conserva la última publicación válida, incluso después de reiniciar sin red.

El outbox de ventas, clientes y ACK de catálogo vive en SQLite. Conserva identidad, cuerpo/hash, intentos y estado de entrega ante crash, actualización o restauración. Los reintentos usan la misma identidad idempotente. Una respuesta desconocida o un conflicto pasa a conciliación o cuarentena; no se marca como entregado de manera silenciosa.

## ACK mensual, purga y libro durable

La consolidación mensual acepta sólo un ACK JSON exacto con recibido igual a true, acuse no vacío y estado recibido o purgado. El payload y su hash quedan congelados antes de salir a red; un reintento conserva la misma idempotencia.

Después del ACK válido, la purga lógica local elimina para el mes y sucursal:

- tickets no programados y sus relaciones dependientes;
- solicitudes de repetición y eventos de auditoría cuyo destino sea local para esos tickets;
- cortes de caja, liquidaciones, cortes de sucursal y reportes administrativos asociados;
- movimientos y controles diarios de efectivo;
- detalle de impresión asociado, mediante solicitudes durables para la fase física;
- los folios operativos se reinician según el flujo vigente.

Permanecen:

- la fila local ConsolidacionMensual, su payload inmutable, hash, idempotencia, acuse, estado remoto y fechas;
- los eventos del outbox destinados al central, porque la purga filtra únicamente destino local;
- clientes, teléfonos, domicilios, catálogo, precios, identidades, módulos y configuración de sucursal;
- tickets programados excluidos del período purgado;
- en el central, el recibo/tombstone permanente, su hash y período; el snapshot mensual detallado es transitorio y puede purgarse después de exportación y confirmación.

El estado local continúa como confirmada y purgado_en marca que terminó la eliminación lógica. La tarea privilegiada posterior crea el respaldo post-purga y retira los archivos físicos. Un ACK perdido se reintenta con la misma identidad; 409 o una divergencia de hash exige conciliación.

## Consumidores legacy que permanecen

- PEDIDOS_SUCURSALES_FUENTE=supabase conserva la integración histórica cuando se elige de forma explícita.
- PEDIDOS_SUCURSALES_FUENTE=sqlite permanece limitado a DEBUG para pruebas locales.
- La API v1 de Pedidos no se modifica ni retira en el servicio remoto; sigue siendo rollback hasta aprobar E2E v2 y un corte autorizado.
- La consolidación mensual v1 continúa disponible mediante VPS_CONSOLIDACION_URL y VPS_CONSOLIDACION_TOKEN.
- CENTRAL_ENABLE_SALES_V2, CENTRAL_ENABLE_CUSTOMERS_V2 y CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2 permanecen en false.
- La API draft de clientes del central permanece desactivada.
- El actualizador conserva el archivo .env y la base instalada; una release no cambia fuentes, tokens ni banderas.

No se deben retirar estos caminos durante dev.10.

## Runbook de laboratorio

### 1. Validación local sin servicios externos

1. Trabajar en un checkout o worktree separado de la instalación.
2. Usar una base y medios de prueba. No copiar una base real al repositorio.
3. Mantener las tres banderas CENTRAL_ENABLE en false y Pedidos desactivado para la primera pasada.
4. Ejecutar check, migraciones pendientes, suites Django/stdlib, validadores de despliegue, comprobación de JavaScript y git diff --check según el [protocolo de release](../../PROTOCOLO_RELEASE_ACTUALIZACION_REUTILIZABLE.md).
5. Recorrer venta, cobro, impresión a archivo, clientes, domicilios, pedidos programados, movimientos, reinicio y restauración.
6. Confirmar que cortar toda salida de red no bloquea ninguna operación del POS.
7. Registrar comandos, cantidades y resultados en la evidencia dev.10. Un resultado pendiente no se marca como aprobado.

El arranque aislado descrito en el README usa runtime\prueba y no debe apuntar a C:\LosTocayosPOS. La impresión física requiere una autorización operativa separada y una ruta de terminal explícita.

### 2. E2E privado con agente1

1. Crear PostgreSQL, usuarios, host TLS y datos sintéticos exclusivos de laboratorio en un puerto nuevo; no reutilizar Pedidos 8002 ni los centrales 8010/8011.
2. Instalar la CA de laboratorio en los bundles configurados. No usar verify=False ni HTTP.
3. Emitir tres credenciales separadas por Edge: Pedidos lectura, central ingesta y central catálogo. Probar cada rotación sin afectar las otras.
4. Aprobar la matriz UUID/código/SucursalCliente antes de habilitar Pedidos v2.
5. Probar paginación, duplicados, timeout tras commit, cursor numérico y 410 retention_gap. El cursor no debe avanzar en conciliación.
6. Habilitar un flujo central a la vez. Verificar idempotencia, tamaño, esquema, sucursal ajena, 401/403/409/413/422/429, reinicio, ACK perdido y respuesta sobredimensionada.
7. Para catálogo, interrumpir descarga/aplicación/ACK y reiniciar. Nunca debe quedar activo un menú parcial.
8. Ejecutar concurrencia y restore sobre PostgreSQL real, exportar y purgar transitorios, restaurar en otra base y comprobar recibos, tombstones, clientes, publicaciones, mapeos y ACK.
9. Dejar clientes y distribución real de catálogo apagados al finalizar. El E2E genera evidencia; no autoriza producción.

### 3. Rollback del laboratorio

1. Detener sólo el proceso candidato y conservar logs, base y outbox para diagnóstico.
2. Restaurar la copia verificada de la base de laboratorio o volver al árbol/commit anterior del worktree.
3. Dejar las banderas centrales en false y seleccionar la fuente legacy acordada si Pedidos v2 fue la causa.
4. No alterar manualmente cursores, estados de conciliación, ACK ni eventos pendientes.
5. Comprobar venta, cobro, impresión y salud local antes de cerrar el incidente.
6. Registrar versión, hash, momento de fallo y datos restaurados. No borrar el intento fallido.

## Bloqueos reales antes de integración conjunta

- No existe un central productivo ni DNS/TLS público aprobado.
- Ventas/clientes v2 y distribución/ACK de catálogo no existen todavía en el central.
- La API de clientes debe permanecer apagada.
- Pedidos v2 candidato no está integrado ni desplegado y su allowlist sigue siendo global.
- Faltan scopes y rotación por instalación/sucursal.
- Falta aprobar la identidad entre POS y SucursalCliente.
- Faltan concurrencia, restore y E2E sobre PostgreSQL real.
- Exportación, confirmación y purga de retención siguen siendo operaciones CLI.
- Falta un procedimiento firmado para salir de retention_gap.
- El commit funcional y dos artefactos byte a byte reproducibles están acreditados; faltan E2E PostgreSQL/TLS, firma criptográfica, aceptación manual y decisión humana de promoción.

Hasta resolverlos, dev.10 es una candidata de laboratorio.
