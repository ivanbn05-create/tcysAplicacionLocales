# Candidata Production 1.0 · catálogo y promociones · 1.0.0-dev.2

Estado: **laboratorio, sin instalación ni actualización de sucursal**. Rama de desarrollo: `codex/production-1.0-catalogo-promociones-dev.2`. Esta nota complementa `docs/PRODUCTION_1_0_CANDIDATA_DEV1.md`; la autoridad funcional es `docs/autoridad/ESPECIFICACION_PRODUCTION_1_0_LOS_TOCAYOS.md` y las decisiones posteriores del dueño. No hay sucursales en producción.

## Decisión de origen

El catálogo maestro inicial será el **catálogo real vigente de Arboledas**, importado con control y reconciliación a Central. Central se convierte en autoridad de categorías, productos, precios, promociones y disponibilidad por sucursal después de esa importación. No usar la semilla de laboratorio de Arboledas como catálogo de Santa Anita ni presentar el fixture sintético LAB01 como dato real. Agente1 importó el menú real clasificado en **Central de laboratorio** (`tcyscentral_arboledas_import_e2e`): 7 categorías, 47 productos activos, 48 periodos de precio y PB/PL/P4/PK. Cotejó identidades/atributos y restauró un respaldo posterior en otra base. La publicación E2E al Edge sigue pendiente y la distribución apagada; Arboledas y producción no se modificaron.

Un producto adicional de una sucursal es un producto maestro Central con `disponible_sucursal` en el snapshot de esa sucursal. Los UUID Central se mapean explícitamente a los productos locales. El Edge retiene mapping e historia de productos retirados, pero filtra el menú y la API de venta por el último snapshot válido de su sucursal.

## Venta con promociones dinámicas

La operación normal ya no lee `PROMOCIONES = {...}` ni decide reglas por los códigos PB/PL/P4/PK. Cada publicación v3 almacena definiciones, grupos, cantidades y productos permitidos mediante identidades Central. La migración 0026 representa las cuatro promociones legadas como datos para preservar equivalencia; una publicación v3 sustituye esas reglas para nuevas capturas. Una publicación v2 todavía conserva el backfill legado durante la transición.

El operador elige la tarjeta de promoción de manera expresa y selecciona componentes dentro de los grupos mostrados. Las partidas normales nunca se absorben automáticamente. Cada componente tiene una FK a una sola partida principal y a un solo grupo; un producto maestro puede estar permitido en varias promociones. El ticket admite varias promociones independientes. Al procesar, se exige la cantidad exacta de cada grupo; al editar, se impide exceder cupos o mezclar raíces. Al quitar la principal se restauran en los componentes sus precios de lista capturados.

La principal guarda la definición/publicación con que nació y el precio efectivo y de lista capturados. Cada componente conserva nombre abreviado, texto y precio de lista capturado aunque su precio efectivo sea cero. Reimpresión y vista histórica leen las partidas/definiciones capturadas, incluso si Central cambia precio, nombre, vigencia o composición. La promoción ofrece vigencia por días de semana y fechas inclusivas opcionales, sin horarios. Quesaking queda fuera de esta candidata; el catálogo conserva identidades y grupos extensibles para módulos especializados futuros.

## Aprovisionamiento Edge

Una instalación Central nueva expone estados durables `enrolado`, `esperando_catalogo_inicial`, `catalogo_aplicado`, `listo`. Salud técnica del servicio no implica permiso para vender. Abrir ticket o agregar un producto, incluso personalizado, falla mientras el Edge enrolado no está listo. La interfaz explica el estado y ofrece comprobación de nuevo. La primera publicación v3 se aplica dentro de una única transacción SQLite que incluye catálogo, promociones, estado y ACK outbox; no marca `listo` por sí sola.

`marcar_listo` exige catálogo v3 con productos vendibles y una acción operativa explícita después de verificar dueño, impresión y respaldo. Una publicación posterior válida conserva el alistamiento. Un error de esquema/checksum, salto de versión o fallo tardío revierte íntegramente la nueva publicación y conserva la última válida para operar sin VPS. Un Edge sin identidad Central conserva temporalmente el flujo legado durante migración.

## Contrato y pruebas

El contrato candidato ejecutable está en `contracts/edge-central/CATALOGO_V3_EDGE.md`, su JSON Schema y fixture LAB01. Incluye `contenido:{categorias,productos,promociones}`, availability obligatoria, precio, grupos y UUID, límite 1 MiB y checksum SHA-256 canónico. Central y Edge acordaron para el E2E privado rutas v3 de descarga/acuse, ACK `version_contrato=3`, scopes `catalog:v3:read`/`catalog:v3:ack` ligados a Edge/sucursal y límite de ACK v3 de 1 MiB. El cliente conserva rutas, payloads y acuses v2 durables para rollback; la selección de ruta v3 requiere `CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3=true`. Ambas banderas de distribución permanecen apagadas por defecto.

Pruebas locales a repetir tras cada cambio:

```powershell
.\.venv\Scripts\python.exe manage.py test ventas.test_promociones_dinamicas ventas.test_catalogo_v3_aprovisionamiento ventas.test_production_promos_integracion
.\.venv\Scripts\python.exe manage.py test
python -m unittest tests.test_contracts_vps -v
node --check ventas/static/ventas/app.js
```

Estas pruebas usan SQLite de test y fixture sintético. Cubren equivalencia PB/PL/P4/PK, grupos con varios permitidos, cupo insuficiente/excedido, producto ajeno, elección expresa, días/fechas/estado, publicación nueva con ticket abierto/cerrado, precio histórico, disponibilidad, intento de API directa, publicación corrupta, conservación de última válida y transición pendiente→aplicado→listo. El agente1 reportó E2E sintético Central↔Edge en SQLite aislada el 2026-09-25, repetido con POS `231513a` y Central candidato local `bd0e0b8`: instalación limpia con v3/1→v3/2, PB/PL/P4/PK, venta/ACK y precio histórico $35→$37; transición v2/1→v2/2→v3/1 con UUID de producto y ACK v2/v3 conservados. Script `scripts/cross_repo_catalog_v3_e2e.py` en el repositorio Central. Central reportó 147 pruebas propias y 35 enfocadas PostgreSQL en verde. La importación del menú real a Central de laboratorio pasó; el E2E HTTP/TLS privado del snapshot real/grande y el corte productivo siguen pendientes.

## Gates restantes

1. La clasificación sólo lectura de Arboledas identificó 47 `catalogo.Producto` activos/preciados como menú, 228 registros POS de `legado_incompleto` inactivos/sin precio y, por separado, 38 `ventas.ProductoSucursal` activos con 372 precios mayoristas. La importación Central de laboratorio de 47 productos, 7 categorías, 48 precios y cuatro promociones pasó y restauró; los 228 legados y mayoristas quedaron fuera. Falta publicar/aplicar el snapshot real en Edge vacío de laboratorio, comprobar UUID/mappings, availability, fotos y excepciones sin empatar por nombre y aprobar el acta antes de operar.
2. Ejecutar E2E completo con Central PostgreSQL y TLS operativo: descarga, ACK, reintento, falla parcial, reinicio, offline y restauración en Edge limpio y con historial v2. El E2E sintético SQLite de v2/2→v3/1 pasó; falta reconciliar UUID maestros reales de Arboledas y repetir sobre infraestructura de laboratorio. Mantener v2/legado como rollback hasta el corte.
3. Ensayar instalación nueva y actualización 1.x en Windows aislado, con impresoras físicas, backup y restore, colas/outbox, dos terminales y APK. La confirmación de `listo` sólo sigue a esa verificación.
4. Verificar UI táctil con operador real y pruebas de varias promociones simultáneas. No habilitar venta ni declarar versión base Production 1.0 por pasar sólo pruebas sintéticas.
