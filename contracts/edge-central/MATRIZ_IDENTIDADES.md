# Matriz de identidades

Esta matriz es obligatoria antes del primer E2E. Ninguna correspondencia se infiere por nombre.

| Entidad | Identidad fuente | Identidad destino | Regla |
| --- | --- | --- | --- |
| Sucursal POS → Central | `personas.Sucursal.id` UUID | `ingest.Branch.source_id` UUID | Igualdad exacta. `clave`/`code` se confirma, pero no reemplaza el UUID. |
| Sucursal POS → Pedidos | `Sucursal.id` UUID y `clave` | `SucursalCliente.id` entero | Registro explícito aprobado. `SucursalCliente` actual no tiene `codigo_publico`; su nombre es sólo display. |
| Pedido remoto → importación POS | `Pedido.id` entero + `Pedido.codigo_publico` UUID | `PedidoSucursalImportado.origen_id` + `codigo_publico` | `Pedido.codigo_publico` es la identidad durable del pedido. Mismo UUID con ID distinto es idempotente; mismo ID con UUID distinto exige conciliación. |
| Instalación Edge | UUID generado al instalar | Sujeto de credenciales y ACK | Persiste entre actualizaciones. Una reinstalación crea otra identidad y exige conciliación. |
| Cliente POS → Central | `(Sucursal.id, Cliente.id)` | `(Branch.source_id, Customer.source_id)` | Dos sucursales con el mismo UUID, nombre o teléfono siguen siendo registros distintos. |
| Venta POS → Central | `(Sucursal.id, Ticket.id)` | Agregado temporal de venta | Cada cambio exportable usa `EventoOutbox.id`; no se regenera al reintentar. |
| Producto central → POS | `CatalogProduct.id` UUID | `catalogo.Producto.id` UUID local | Tabla persistente de mapeo por sucursal. Nunca unir sólo por código o nombre. |
| Categoría central → POS | UUID central nuevo y estable | `catalogo.Categoria.id` UUID local | Tabla persistente de mapeo por sucursal. |
| Publicación central → POS | `publicacion_id` + `version_sucursal` + checksum | Estado local aplicado | El ACK debe repetir los tres valores y la sucursal autenticada. |

## Registro mínimo de mapeo POS ↔ Pedidos

El fixture [`contracts/edge-central/fixtures/matriz-identidades-v1.json`](fixtures/matriz-identidades-v1.json) muestra la forma ejecutable. Para cada sucursal se conservan conjuntamente:

- `pos.sucursal_id` y `pos.sucursal_clave`;
- `pedidos.sucursal_cliente_id`;
- `central.branch_source_id` y `central.branch_code`;
- estado `pendiente_verificacion`, `verificado` o `deshabilitado`.

El integrador debe verificar `SucursalCliente.id` contra la base de Pedidos y el UUID/código contra el POS y central. `SucursalCliente.nombre` no participa en la identidad. `Pedido.codigo_publico` aparece en cada pedido importado y no forma parte del mapeo de sucursal.

Un token global de Pedidos no convierte esa relación en verdadera ni limita por sí mismo lo que el Edge puede consultar. El contrato objetivo liga una credencial Edge a la allowlist exacta de `SucursalCliente.id`. Si agente2 incorpora en el futuro un UUID durable de `SucursalCliente`, deberá usar un nombre distinto, una migración y una nueva versión de esta matriz; no puede reutilizar `Pedido.codigo_publico`.

## Reglas de bootstrap de catálogo

Antes de aceptar la primera publicación:

1. Inventariar categorías, productos, códigos, vigencias e imágenes locales.
2. Proponer una correspondencia `central UUID → local UUID` revisable.
3. Marcar colisiones de código, categoría faltante o producto incompatible como conflicto; no crear duplicados silenciosos.
4. Guardar el mapa y la versión inicial en la misma transacción SQLite que activa el snapshot.
5. Mantener UUID locales usados por tickets y promociones.
6. Para un producto central nuevo, crear un UUID local una sola vez y devolverlo en el ACK aplicado.

Los productos mayoristas `ventas.ProductoSucursal` y `PrecioProductoSucursal` quedan fuera de este mapa. Pertenecen al flujo de pedidos de sucursales, no al menú POS.

## Cambio de identidad

- Cambiar sólo un nombre visible no cambia la identidad.
- Cambiar `SucursalPedido.origen_id`/`SucursalCliente.id` o `Sucursal.clave` exige conciliación; no crea otra sucursal automáticamente.
- Cambiar el UUID POS nunca se resuelve con una búsqueda por nombre; requiere migración explícita.
- `Pedido.codigo_publico` sigue al pedido aunque cambie su ID entero; reutilizar un ID para otro UUID exige conciliación.
- Rotar cualquiera de los tokens no modifica sucursal, instalación, UUID de pedidos/eventos, cursor, cuerpos del outbox ni ACK previos.
