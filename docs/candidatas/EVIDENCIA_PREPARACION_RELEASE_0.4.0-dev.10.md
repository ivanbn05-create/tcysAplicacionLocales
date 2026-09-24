# Evidencia de preparación de release 0.4.0-dev.10

- **Estado:** candidata POS validada para el siguiente E2E privado; no acredita producción, despliegue ni corte
- **Entorno autorizado:** laboratorio, sin sucursales en producción
- **Fecha de cierre:** 24 de septiembre de 2026
- **Decisión:** NO PROMOVER

Este documento registra resultados reales del árbol candidato. No contiene secretos, PIN, tokens, datos personales ni contenido de archivos .env.

## Identidad de la candidata

| Campo | Valor |
| --- | --- |
| Versión | 0.4.0-dev.10 |
| Rama | codex/candidata-0.4.0-dev.10 |
| Commit base | f230ba06b9464e1541820a931037fde9b4d38dce |
| Commit funcional | c3ba43d9f410ee95bbec060751ed1aba43a16fe6 |
| Commit documental | commit inmediatamente posterior; consultar el historial de la rama |
| Estado al construir | limpio; source_dirty=false |
| SOURCE_DATE_EPOCH | 1790268913 |
| Fecha reproducible UTC | 2026-09-24T16:55:13Z |
| Destino | CPython 3.13 x64 / win_amd64 |
| Decisión | candidata apta para E2E privado; NO PROMOVER |

No se modificó C:\LosTocayosPOS, Pedidos en 8002, Central en 8010/8011, DNS, TLS ni ninguna base remota.

## Alcance entregado

- Pedidos API v2 con esquema estricto, identidad explícita, checkpoint durable, cursor opaco y conciliación ante 410 retention_gap.
- Supabase/SQLite legacy y API v1 conservados como rollback.
- Outbox durable para ventas, clientes y ACK de catálogo, con claim CAS y lease recuperable.
- Catálogo completo con checksum, cadena de versiones, aplicación atómica y conservación de la última versión válida.
- Credenciales separadas para Pedidos, ingesta central, catálogo y consolidación mensual.
- Consolidación mensual sin bloquear venta, cobro o impresión offline.
- Precio de lista capturado y preservado al editar líneas promocionales aunque cambie el catálogo.
- Ruteo de impresión por terminal y configuración administrativa.
- Contratos ejecutables, fixtures, matrices y handoff para agente1.
- Actualizador de laboratorio con copia privada y revalidación de artefactos antes de extraer.

## Validación automatizada

| Comprobación | Resultado real |
| --- | --- |
| Django check | OK; 0 incidencias |
| Migraciones | OK; No changes detected |
| Suite Django completa | 313/313 OK en 175.987 s |
| Preflight oficial sobre snapshot del commit | 313/313 OK en 182.164 s; check HTTPS, SQLite y host Windows aprobados |
| Suite de biblioteca estándar | 105/105 OK en 62.998 s |
| Contrato Edge–Central | 8/8 OK |
| Contrato Pedidos v2 autocontenido | 7/7 OK |
| Pedidos v2 y checkpoints | 37/37 OK |
| Catálogo atómico y precio capturado | 9/9 OK; regresión específica 1/1 OK |
| Empaquetado y actualizador | 22/22 OK |
| Instalador Windows | OK con SkipAcl; preflight, orden, entorno y recuperación de .venv |
| Sintaxis JavaScript | app.js y admin.js OK |
| Barrido TLS | sin verify=False, CERT_NONE ni hostname desactivado en código ejecutable |
| Barrido de logs | sin impresión de bearer/tokens en código ejecutable |
| Whitespace | git diff --check OK |
| Auditoría independiente | sin hallazgos críticos, altos, medios ni accionables conocidos al cierre |

El perfil HTTP LAN del preflight produjo cuatro avisos esperados de HSTS, redirect y cookies seguras. Ese perfil sirve al acceso local del POS; todas las integraciones VPS candidatas exigen HTTPS verificado.

## QA funcional y visual

| Flujo | Resultado | Límite |
| --- | --- | --- |
| Venta, cobro e impresión sin VPS | Aprobado por pruebas automatizadas | No se repitió impresión física con esta candidata |
| Clientes, teléfonos y domicilios | Aprobado por suite Django | API central de clientes permanece apagada |
| Pedidos legacy | Conservado y cubierto | Sigue siendo rollback |
| Pedidos v2 normal, paginado y duplicado | Aprobado por cliente, checkpoint y contrato | Servicio remoto candidato sin merge/deploy |
| 410 retention_gap | Aprobado: entra en conciliación y no avanza cursor/high-water | Resincronización operativa aún debe acordarse |
| Catálogo incompleto, checksum, orden y reinicio | Aprobado por pruebas atómicas | Distribución central real apagada |
| Outbox y ACK | Aprobado para crash/concurrencia SQLite | Restore conjunto con PostgreSQL pendiente |
| Rutas de impresión por terminal | Aprobado por pruebas | Hardware real no probado en dev.10 |
| Administrador, personal y resoluciones | Pruebas UI y revisión responsiva aprobadas | Aceptación manual final pendiente |
| Tableta Android/PWA | Sin cambio en esta candidata | APK y hardware objetivo pendientes |
| Instalación o actualización real | NO EJECUTADA | Prohibida por el alcance de esta tarea |

## Seguridad POS

| Control | Resultado |
| --- | --- |
| HTTPS, CA válida y redirects | Implementado y probado; sin bypass TLS |
| Tokens en logs/evidencia | Barrido aprobado; fixtures usan variables simbólicas |
| ACL de secretos y staging | Contrato de instalador aprobado; ACL real no se reejecutó por no instalar |
| Credenciales distintas | Configuración rechaza tokens reutilizados entre flujos |
| Scope por sucursal | Contrato entregado; servidor Pedidos aún usa allowlist global |
| Identidad POS ↔ Pedidos | UUID + clave POS hacia SucursalCliente.id entero; nunca por nombre |
| Esquema, tamaños y claves duplicadas | Implementado y probado |
| Datos remotos no confiables | Validación estricta antes de persistir |
| Transacciones SQLite | Catálogo y checkpoints se aplican atómicamente |
| Versión/checksum desconocidos | Rechazo seguro y conservación del catálogo activo |
| Rate limit | Documentado sólo como defensa parcial por worker |
| Firma del release | PENDIENTE; hoy hay SHA-256, manifiesto y ACL, sin firma criptográfica |

## Contratos y handoff

| Entrega | Estado |
| --- | --- |
| OpenAPI, schemas y fixtures Edge–Central | Disponible en contracts/edge-central |
| OpenAPI, schemas y fixtures Pedidos v2 | Disponible en contracts/pedidos-v2 |
| Matriz de identidades | Disponible; IDs reales pendientes de aprobación |
| Matriz HTTP → acción | Disponible |
| Variables y scopes | Disponible; implementación servidor pendiente |
| Handoff agente1 | Disponible en contracts/edge-central/HANDOFF_AGENTE1_CENTRAL.md |
| Commit POS de contratos | c3ba43d9f410ee95bbec060751ed1aba43a16fe6 |
| Commit Pedidos candidato | 8fad56815f856b4286a2f60f488480960e34cde5; sin merge/deploy |
| Commit Central reconciliado | PENDIENTE DE AGENTE1 |

Clientes v2 y distribución real de catálogo permanecen desactivados. API v1 y Supabase legacy no se retiraron.

## Artefactos reproducibles

Raíz de laboratorio: C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tcys-base-work

| Campo | Build 1 | Build 2 |
| --- | --- | --- |
| Directorio | release-dev10-c3ba43d-build1 | release-dev10-c3ba43d-build2 |
| ZIP | LosTocayosPOS-Servidor-0.4.0-dev.10.zip | mismo nombre |
| SHA-256 ZIP | 1a875b45f2787d01bb3d644bfd6c329265b47ea30246677e34623eb5fea51752 | idéntico |
| Bytes ZIP | 32,554,004 | 32,554,004 |
| SHA-256 manifiesto | aa338bf188d6ea6850c9ee5a4d8d0eb2d1748493f8ab0fa6d8736da2da3ca1af | idéntico |
| SHA-256 archivo de sumas | 97ee2a5810683ca8ca3382d47ff3a6c1338fb9c96a9149e5bd9327074d0e224a | idéntico |
| Archivos en payload | 303 | 303 |
| Tamaño payload | 35,794,301 bytes | 35,794,301 bytes |
| Contratos incluidos | Edge–Central y Pedidos v2 | Edge–Central y Pedidos v2 |
| Verificador oficial | OK | OK |
| Comparación byte a byte | ZIP, manifiesto y sumas idénticos | ZIP, manifiesto y sumas idénticos |

Los artefactos son de laboratorio, no están firmados y no autorizan instalación. No se ejecutaron PrepareOnly contra C:\LosTocayosPOS, snapshot de una instalación real, actualización supervisada ni rollback real.

## Incidentes corregidos durante la validación

- Se corrigió una prueba de empaquetado que había quedado unida a la declaración de clase.
- La conciliación mensual dejó de bloquear abrir, activar o cobrar tickets cuando el VPS no está disponible.
- La purga mensual conserva eventos destinados al Central.
- El transporte mensual rechaza redirects y conserva origen/TLS.
- El outbox obtuvo claim CAS/lease para evitar doble envío y degradación de ACK.
- Pedidos obtuvo high-water durable, identidad por codigo_publico de pedido y conciliación ante discontinuidad.
- El catálogo exige secuencia/predecesora exactas y desactiva precios central_v2 obsoletos, incluso futuros.
- La edición de componentes promocionales conserva el precio capturado.
- El actualizador sella y revalida los artefactos para reducir TOCTOU.
- El paquete de release ahora incluye ambos directorios de contratos.

## E2E conjunto pendiente

No hay E2E POS–VPS acreditado. Antes de cualquier activación, agente1 debe entregar:

- PostgreSQL y datos sintéticos exclusivos de laboratorio;
- host TLS/CA de laboratorio;
- scopes reales por instalación y sucursal;
- matriz de identidades aprobada;
- rotación independiente de las tres credenciales;
- concurrencia, ACK perdido, purga, restore y retention_gap;
- rutas de clientes y catálogo aún desactivadas al terminar;
- commit Central exacto y evidencia del E2E.

## Decisión

**APROBADA COMO CANDIDATA POS PARA EL SIGUIENTE E2E PRIVADO. NO PROMOVER, NO INSTALAR Y NO ACTIVAR INTEGRACIONES.** La promoción requiere PostgreSQL/TLS, scopes reales por sucursal, firma de artefactos, aceptación manual y autorización humana expresa.
