# Acta de certificación de catálogo v3 · candidata dev.2

Estado: **abierta, no certificada**. Rama `codex/production-1.0-catalogo-promociones-dev.2`. Autoridad: `docs/autoridad/ESPECIFICACION_PRODUCTION_1_0_LOS_TOCAYOS.md` y decisiones posteriores del dueño. Uso exclusivo de laboratorio; no instalar ni actualizar sucursales.

## Evidencia congelada

- POS código `1489708342838ea6482e1150ff7b4acc5f9e0af5`: [CI Edge catálogo v3 #36186496189](https://github.com/ivanbn05-create/tcysAplicacionLocales/actions/runs/36186496189) verde el 2026-09-25: 23 pruebas de contratos/enrolamiento/settings y 360 Django en SQLite. Tres commits documentales posteriores no cambiaron ese código. Las pruebas nuevas de caracterización de restore y ACK llevan otros SHA y requieren su propia corrida.
- Central candidato local `bd0e0b8`: agente1 reportó E2E sintético Central↔POS con SQLite aislada, v3/1→v3/2, PB/PL/P4/PK, precio histórico y ACK; además 147 pruebas Central y 35 enfocadas en PostgreSQL. El reporte no acredita todavía HTTP/TLS/CA ni import del catálogo real.
- Arboledas: agente1 tiene autorización de **sólo lectura/dry-run**. La importación real a Central no está autorizada todavía. No tratar la semilla histórica ni LAB01 como catálogo vigente.
- Matriz física: `docs/CERTIFICACION_HARDWARE_PRODUCTION_1_0.md`; preparada, no ejecutada.

## Matriz de catálogo v3

| Caso | Evidencia actual | Estado de certificación |
| --- | --- | --- |
| Edge vacío, enrolamiento y publicación inicial | Tests Django y E2E sintético SQLite v3/1 | Pendiente Central HTTP/TLS/CA PostgreSQL |
| Edge previo v2/1→v2/2→v3/1; ACK v2 durable | Tests Django y E2E sintético SQLite | Pendiente laboratorio PostgreSQL/TLS y restore |
| PB/PL/P4/PK migradas, promoción nueva, elección manual e historia | Tests Django y E2E sintético SQLite | Pendiente catálogo representativo/real |
| ACK perdido, duplicado y reintento | Tests unitarios de outbox/ACK; nueva prueba en candidata | Pendiente transporte real y concurrencia |
| Checksum incorrecto, payload parcial, rollback a última válida | Tests Django de validación/transacción | Pendiente HTTP real y corte/crash |
| Catálogo representativo/grande, límite 1 MiB, dos Edge/terminales | LAB01 sólo 3 productos (~4 KiB) | No ejecutado |
| Reinicio, offline, restore desde backup antiguo | Reinicio/offline unitarios; restore v3/1 con Central en v3/3 revela falta de catch-up | Bloqueante hasta corrección y E2E |
| Precios/tickets cerrados históricos | Tests Django y E2E sintético SQLite | Pendiente restore y publicación real |
| Hardware Windows/EXE/APK/PWA/impresoras/soporte/backup | Protocolo H01–H16 | No ejecutado |

## Hallazgos bloqueantes

1. **Restore v3 atrasado.** El sincronizador sólo solicita `/publicaciones/actual/`, mientras la aplicación local exige `version_sucursal` consecutiva y predecesora exacta. Un Edge restaurado en v3/1 frente a Central v3/3 vuelve a rechazar v3/3 y no solicita v3/2. Prueba de caracterización en `ventas/test_catalogo_v3_aprovisionamiento.py` (commit `ebdce4f8a2b879351e291f8b2257c1c2cc5cd6ba`), pendiente resultado CI. Cambio mínimo propuesto para acordar con agente1: recuperar cadena histórica por UUID, validar todos los eslabones y aplicarlos en orden con ACK durable, sin aceptar saltos ni cambiar v2.
2. **Infraestructura E2E.** Falta ejecución conjunta sobre Central real de laboratorio, PostgreSQL, HTTPS con CA verificada y catálogo representativo. Registrar SHAs de POS/Central, certificado/CA pública, conteos, tamaño JSON canonizado y HTTP, IDs de publicaciones, logs saneados y resultado de cada caso.
3. **Fuente inicial.** La lectura del catálogo real Arboledas para dry-run está bloqueada por ACL; agente1 no lo ha importado. Ningún Edge vacío debe marcarse listo con fixture sintético como si fuera catálogo real.
4. **Retención de Pedidos.** POS conserva cursor y estado `RECONCILIACION` ante `410 retention_gap`. El recovery-v1 de Pedidos `28cc53ac11ce4628516ef1947e048ef58412c2cd` no está congelado con Central/POS; faltan baseline/ACK Ed25519/checkpoint POS, Linux/PostgreSQL/POSIX y mapping real `SucursalCliente.id`. No activar purga ni avanzar cursor.
5. **Restore operacional.** El respaldo SQLite actual verifica su propia restaurabilidad, pero no hay comando público de restauración integral de una instalación. El gate H16 exige procedimiento probado en clon y auditoría de credenciales/outbox.

## Criterio de cierre

Cada caso debe tener SHA exacto de los tres repositorios, fixture/manifiesto hash, topología y CA de laboratorio, comando ejecutado, salida, estado anterior/posterior de Edge/Central, ACK/outbox y evidencia privada sin secretos. Un caso unitario no sustituye E2E HTTP/TLS ni hardware. No cambiar flags de producción ni instalar esta candidata hasta cerrar los bloqueos y obtener un corte separado.
