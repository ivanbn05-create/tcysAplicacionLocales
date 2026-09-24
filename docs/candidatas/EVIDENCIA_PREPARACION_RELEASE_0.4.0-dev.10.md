# Evidencia preliminar de preparación de release 0.4.0-dev.10

- **Estado:** abierta; no acredita release, E2E, actualización ni despliegue
- **Entorno autorizado:** laboratorio, sin sucursales en producción
- **Fecha de apertura:** 24 de septiembre de 2026

Este archivo es una hoja de evidencia para el integrador. Los campos pendientes deben completarse con salida real. No se deben copiar secretos, PIN, tokens, datos personales ni el contenido de .env.

## Identidad de la candidata

| Campo | Valor |
| --- | --- |
| Versión del worktree | 0.4.0-dev.10 |
| Rama | codex/candidata-0.4.0-dev.10 |
| Commit base | f230ba06b9464e1541820a931037fde9b4d38dce |
| Commit funcional final | PENDIENTE |
| Commit documental | PENDIENTE |
| Estado del árbol al validar | PENDIENTE |
| SOURCE_DATE_EPOCH | PENDIENTE |
| Decisión | NO PROMOVER mientras esta evidencia permanezca incompleta |

No se modificó ni debe modificarse C:\LosTocayosPOS como parte de la preparación de esta candidata.

## Alcance esperado para revisión

- Cliente Pedidos API v2 con identidad explícita, cursor firmado y conciliación ante 410 retention_gap.
- Conservación de Supabase/SQLite legacy y de API v1 remota como rollback.
- Outbox durable para ventas, clientes y ACK de catálogo.
- Aplicación atómica de catálogo completo, checksum y última versión válida.
- Credenciales separadas para Pedidos, ingesta central y catálogo.
- Consolidación mensual con payload inmutable, ACK estricto y conciliación.
- Ruteo de impresión por terminal con fallback.
- Actor administrador protegido y ajustes de interfaz.
- Contratos, fixtures y handoff para agente1.

La lista describe el alcance a probar; no afirma que cada punto esté aprobado.

## Validación automatizada

| Comprobación | Comando o evidencia | Resultado real |
| --- | --- | --- |
| Django check | manage.py check | PENDIENTE |
| Migraciones sin cambios | manage.py makemigrations --check --dry-run | PENDIENTE |
| Suite Django completa | manage.py test --noinput | PENDIENTE |
| Suite stdlib | python -m unittest discover -s tests -v | PENDIENTE |
| Pruebas de contratos | tests/test_contracts_vps.py | PENDIENTE |
| Instalador Windows | tests/test_instalador_windows.ps1 | PENDIENTE |
| Sintaxis JavaScript | node --check de app.js y admin.js | PENDIENTE |
| Validación de despliegue | herramientas/validar_despliegue.py | PENDIENTE |
| Whitespace | git diff --check | PENDIENTE |
| Estado Git final | git status --short | PENDIENTE |

Registrar cantidades exactas, fecha, entorno, código de salida y fallos. Si una prueba se corrige, conservar el incidente y repetir el conjunto afectado.

## QA funcional y visual

| Flujo | Resultado | Evidencia |
| --- | --- | --- |
| Venta, cobro e impresión sin red/VPS | PENDIENTE | PENDIENTE |
| Clientes, teléfonos y domicilios | PENDIENTE | PENDIENTE |
| Pedidos legacy explícitos | PENDIENTE | PENDIENTE |
| Pedidos v2 normal, paginado y duplicado | PENDIENTE | PENDIENTE |
| 410 retention_gap sin avance de cursor | PENDIENTE | PENDIENTE |
| Catálogo incompleto/checksum/rollback/reinicio | PENDIENTE | PENDIENTE |
| Outbox tras crash, actualización y restore | PENDIENTE | PENDIENTE |
| Rutas de impresión por terminal | PENDIENTE | PENDIENTE |
| Administrador y personal | PENDIENTE | PENDIENTE |
| Resoluciones objetivo y 200 % | PENDIENTE | PENDIENTE |
| Tableta Android/PWA | PENDIENTE | PENDIENTE |
| Impresión física autorizada | PENDIENTE | PENDIENTE |

## Seguridad POS

| Control | Resultado real |
| --- | --- |
| URLs HTTPS y CA válida; ausencia de verify=False | PENDIENTE |
| Tokens ausentes de logs y evidencia | PENDIENTE |
| ACL de .env, CA, runtime, backups y staging | PENDIENTE |
| Pedidos e ingesta/catálogo con credenciales distintas | PENDIENTE |
| Scopes e identidad por sucursal | PENDIENTE; depende de agente1/Pedidos |
| Esquema, tamaño, duplicados JSON y redirects | PENDIENTE |
| Aplicación remota dentro de transacción SQLite | PENDIENTE |
| Rechazo seguro de versión/checksum desconocidos | PENDIENTE |
| LocMemCache tratado sólo como defensa parcial | PENDIENTE |

## Contratos y handoff

| Entrega | Estado |
| --- | --- |
| OpenAPI, schemas y fixtures Edge–Central | Disponible para revisión en ../../contracts/edge-central/ |
| Matriz de identidades | Disponible; valores reales pendientes de aprobación |
| Matriz HTTP → acción | Disponible para revisión |
| Variables y scopes | Disponible; implementación central pendiente |
| Handoff para agente1 | Disponible en ../../contracts/edge-central/HANDOFF_AGENTE1_CENTRAL.md |
| Commit POS que contiene los contratos | PENDIENTE |
| Commit central reconciliado | PENDIENTE |

Clientes v2 y distribución real de catálogo deben permanecer desactivados. El candidato de Pedidos, PostgreSQL real y el E2E POS–VPS siguen pendientes.

## E2E conjunto

| Campo | Evidencia |
| --- | --- |
| Revisión exacta de Pedidos v2 | PENDIENTE |
| Revisión exacta del Central | PENDIENTE |
| PostgreSQL y base sintética | PENDIENTE |
| Host TLS/CA de laboratorio | PENDIENTE |
| Matriz de identidades aprobada | PENDIENTE |
| Credenciales y rotaciones separadas | PENDIENTE |
| Resultado de casos normales y fallos | PENDIENTE |
| Restore de permanentes/tombstones/ACK | PENDIENTE |
| Banderas desactivadas al terminar | PENDIENTE |

No hay E2E acreditado mientras esta tabla permanezca pendiente.

## Artefactos y actualización

| Campo | Build 1 | Build 2 |
| --- | --- | --- |
| ZIP | PENDIENTE | PENDIENTE |
| SHA-256 ZIP | PENDIENTE | PENDIENTE |
| Manifiesto | PENDIENTE | PENDIENTE |
| SHA-256 manifiesto | PENDIENTE | PENDIENTE |
| Archivo de sumas | PENDIENTE | PENDIENTE |
| Número de archivos | PENDIENTE | PENDIENTE |
| Comparación byte a byte | PENDIENTE | PENDIENTE |

PrepareOnly, snapshot previo, respaldo restaurable, actualización supervisada, verificación posterior y rollback permanecen PENDIENTES. Esta candidata no se ha aplicado a una instalación real.

## Incidentes y divergencias

PENDIENTE DE COMPLETAR POR EL INTEGRADOR. Registrar aquí cualquier fallo, corrección, diferencia de contrato, decisión de no probar y limitación del entorno.

## Decisión de promoción

**NO PROMOVER.** Esta es evidencia preliminar. La promoción requiere resultados completos, E2E privado con PostgreSQL, aceptación manual y una decisión humana expresa.
