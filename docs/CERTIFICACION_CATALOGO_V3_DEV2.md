# Acta de certificación de catálogo v3 · candidata dev.2

Estado: **abierta, no certificada**. Rama `codex/production-1.0-catalogo-promociones-dev.2`. Autoridad: `docs/autoridad/ESPECIFICACION_PRODUCTION_1_0_LOS_TOCAYOS.md` y decisiones posteriores del dueño. Exclusivo laboratorio; no instalar, actualizar ni publicar en una sucursal operativa.

## Evidencia ejecutada

- POS código `02a5232a`: [CI Edge catálogo v3 #36247588447](https://github.com/ivanbn05-create/tcysAplicacionLocales/actions/runs/36247588447) aprobó **23 pruebas de contratos/enrolamiento/settings y 375 Django** en SQLite. Los commits posteriores de esta acta/contrato son documentales.
- Los tests nuevos prueban catch-up Edge vacío ante Central v3/3, restore v3/1→v3/3, cadena ausente/corrupta/cíclica, fecha futura, publicación obsoleta, ACK perdido/repetido, ACK 409 durable, UUIDv5 estable para altas v3, colisiones y v2 UUID4 intacto. El transporte Central de esas pruebas está simulado; no acredita HTTP/TLS/PostgreSQL.
- Central candidato local `570719a390b66c5f9e658f3bee0f248ad5de0e0b`: agente1 implementó segundo ACK tras restore sólo con Edge/publicación/checksum/mapeos idénticos; reportó 22/22 pruebas de importación, además del E2E sintético SQLite previo (v3/1→v3/2, PB/PL/P4/PK, precio histórico y ACK), 147 pruebas Central y 35 enfocadas en PostgreSQL de la base anterior. Falta E2E del par nuevo sobre PostgreSQL/TLS.
- Agente1 completó dry-run **sólo lectura** de Arboledas con `quick_check`, respaldo previo verificable y manifiesto fuera de Git. Conteos preliminares: **8 categorías, 275 productos, 47 activos con precio, 228 inactivos sin precio y 48 registros de precio**. PB/PL/P4/PK todavía se extraen del código instalado. No se importó ni publicó nada en Central.
- Agente1 preparó un plan E2E privado fuera de Git (SHA-256 `590d3023c4d250ab34f9a694cc58aadc6cdd35d398cfd7245c0e02724ad41a36`) para el par Central `570719a`/POS `02a5232a`, PostgreSQL/TLS/CA, 275 productos y escenarios de restore/ACK/concurrencia. Su ejecución en VPS está pendiente de autorización específica para transferir el tar fuente de Central al directorio privado del laboratorio; el control automático rechazó esa transferencia porque la autorización previa no nombraba payload y destino. No se subió el archivo.
- [Matriz hardware H01–H16](https://github.com/ivanbn05-create/tcysAplicacionLocales/blob/codex/production-1.0-catalogo-promociones-dev.2/docs/CERTIFICACION_HARDWARE_PRODUCTION_1_0.md): preparada; **0 de 16 casos ejecutados**.

## Matriz E2E por cerrar

| Caso | Estado verificable | Evidencia que falta |
| --- | --- | --- |
| Central laboratorio PostgreSQL + TLS/CA; Edge vacío y v2 previo | Sólo unitarios y E2E sintético SQLite previo | Corrida HTTP real con SHA de ambos repos, CA, publicaciones y ACK |
| Catálogo representativo/grande | Fixture LAB01 tiene 3 productos; dry-run real tiene 275 | Snapshot saneado de forma real y uno grande cercano a 1 MiB; conteos, bytes canonizados/HTTP y checksum |
| PB/PL/P4/PK y promoción nueva | Equivalencia local y E2E sintético previo | Import/promoción como datos en Central y venta E2E con comprobante histórico |
| ACK perdido/repetido | Test POS con transporte simulado | HTTP real, retry con mismo `ack_id`, respuesta 200/201 estable |
| Checksum incorrecto y publicación parcial | Validación/transacción local | Respuesta HTTP corrupta/parcial, rollback y última válida persistente |
| Concurrencia, reinicio y offline | Casos unitarios parciales | Dos polls/terminales reales y reinicio del Edge con Central disponible/no disponible; el test intercalado `53d5158` ya evita ACK falso y pasa CI |
| Restore v3 atrasado | Catch-up local + UUID estable y ACK 409 seguro probados en CI | Central `570719a` acepta nuevo ACK sólo con identidad/checksum/mapeos idénticos en test; falta E2E de backup v3/1 frente a v3/3 |
| Precios/promociones/tickets cerrados | Tests POS y E2E sintético previo | Repetir en PostgreSQL/TLS tras actualización/restauración |
| Instalación/update/rollback/EXE/APK/PWA/impresoras/soporte/backup | Protocolo H01–H16 | Pruebas físicas posteriores, sin sucursal real |

## Bloqueos y cambios indispensables

1. **Importación del catálogo real.** El importador Central actual rechaza los 228 maestros inactivos sin precio. Ajustarlo para conservarlos en Central sin inventar importes, exigir precio a todo vendible y excluir del snapshot v3 los inactivos sin precio. Probar conteos importados/publicados y ausencia de producto activo sin precio. La lectura dry-run no autoriza aún la importación real.
2. **ACK tras restore.** POS `02a5232a` ya recupera la cadena v3 por GET histórico y reproduce mapeos de altas v3 con UUIDv5. Central `570719a` corrigió el `409 ack_state_conflict` para un nuevo `ack_id` sólo cuando Edge/sucursal, publicación, checksum y mapeos exhaustivos coinciden exactamente; diferencias siguen en 409. Falta E2E HTTP/TLS/PostgreSQL del par nuevo; no tratar el menú recuperado como restore integral certificado.
3. **Catálogo grande/infraestructura.** Falta prueba conjunta con PostgreSQL, HTTP HTTPS con CA validada y tamaño representativo/cercano a 1 MiB. El límite Edge de 64 publicaciones por catch-up exige conciliación asistida después, sin salto silencioso. La CI reprodujo y corrigió una carrera de dos polls (`53d5158`); un prefijo divergente no crea ACK falso. Una prueba roja de serialización `JsonResponse` (`5bd2307`) justificó margen HTTP predeterminado de 2 MiB sin elevar el límite semántico canónico de 1 MiB; Central candidato envía bytes canónicos.
4. **Retention gap de Pedidos.** POS conserva cursor y estado `RECONCILIACION` ante `410`. Recovery-v1 Pedidos `28cc53ac11ce4628516ef1947e048ef58412c2cd` no está congelado con Central/POS; faltan baseline/ACK Ed25519/checkpoint POS, Linux/PostgreSQL/POSIX y mapping real `SucursalCliente.id`; agente1 verificó que actualmente no hay fila `SucursalCliente` ni vínculo POS de Arboledas. No activar purga ni avanzar cursor.
5. **Hardware y restore operacional.** La matriz está lista, pero no hay ensayos físicos ni comando público de restauración integral; `herramientas/respaldo_sqlite.py` sólo crea/verifica respaldo. Ejecutar H01–H16 en clon desechable y fijar runbook de restore antes de sucursal.

## Evidencia requerida por corrida

Registrar SHA exacto POS/Central/Pedidos, fixture/manifiesto hash, topología de laboratorio, CA pública y certificado, conteos/bytes, comando, salida saneada, estado Edge/Central antes/después, ACK/outbox, ticket/precio histórico, fecha y operador. Un test unitario no sustituye E2E HTTP/TLS ni hardware. Mantener flags de distribución y purga apagados hasta gates cerrados y corte autorizado por separado.
