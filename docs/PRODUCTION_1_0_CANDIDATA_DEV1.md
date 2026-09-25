# Candidata técnica Production 1.0 · 1.0.0-dev.1

Estado: desarrollo de laboratorio, no aprobada para corte. La línea 0.4.0-dev.10 permanece congelada; esta candidata usa una rama y un número nuevos. El código se trabaja en un worktree Git separado, no en C:\tcysAplicacionLocales ni en C:\LosTocayosPOS. No hay sucursal en producción. Ninguna instalación real se ha modificado.

## Producto único

La release Edge, el cliente Windows y la aplicación Android/PWA son comunes a las cinco sucursales. Arboledas y Santa Anita son el piloto previsto. Los siete módulos núcleo (POS, catálogo, impresión, respaldos, domicilios, pedidos programados y reparto) siempre están presentes. Pedidos de sucursales es opcional y sólo autorizable inicialmente en Arboledas. Menú, precios, impresoras y cantidad de terminales son datos/configuración, no builds distintos.

La sucursal y el Edge se reciben de Central por HTTPS y una tarjeta privada de un uso. El instalador universal no ofrece selector de sucursal. Un UUID Edge no es UUID de sucursal ni UUID de terminal. La credencial de Pedidos sigue separada de las dos credenciales Central. Enrolar no activa por sí mismo ventas/clientes v2 ni distribución de catálogo: las banderas siguen apagadas hasta el corte autorizado y E2E conjunto.

## Cadena de confianza y firma propia

Los artefactos Edge son ZIP, manifiesto JSON, SHA-256 y un descriptor .signature.json. herramientas/release_firma.ps1 firma los hashes de los tres primeros mediante RSA-3072/SHA-256 (PKCS#1 v1.5). El verificador exige producto, versión, key id derivado de la clave pública, clave confiable no revocada y coincidencia de todos los hashes. La misma política permite el paquete cliente Windows con producto distinto. No se usa una clave privada del repositorio.

Ceremonia de clave productiva:

1. Generar el par RSA de producción en equipo controlado, idealmente fuera de línea. GenerateLabKey sólo sirve para ensayos; no copiar su privada a sucursales.
2. Guardar la privada cifrada, con acceso mínimo y dos respaldos controlados; registrar custodios, huella key id y procedimiento de recuperación. Nunca subir .xml privada, .pfx, .jks o secretos a Git ni incluirlos en ZIP/APK.
3. Distribuir el trust store público y los dos scripts bootstrap/verificador por un canal independiente del ZIP; cotejar su SHA-256 con un valor firmado/publicado fuera de banda. No aceptar un trust store que venga únicamente dentro del artefacto aún no verificado.
4. Para rotar, agregar la clave pública nueva al trust store confiable antes de emitir releases con ella; retirar la anterior sólo tras verificar instalaciones y rollback. Para revocar, cambiar status a revoked y distribuir ese trust store por el mismo canal seguro. El actualizador no modifica silenciosamente el trust store.

La firma propia autentica artefactos dentro de este flujo, pero no equivale a Authenticode de Microsoft ni evita avisos de SmartScreen. Un certificado comercial no es requisito de esta ronda.

## Instalación nueva supervisada

Prerequisitos: Windows con permisos de administrador, Python 3.13 de 64 bits instalado para todos los usuarios en ruta protegida, wheelhouse completo CPython 3.13/Windows x64 incluido en el ZIP firmado, CA/TLS válido para Central y Edge, tarjeta JSON privada emitida por Central, destino vacío y servicio LosTocayosPOS ausente. La instalación firmada rechaza dependencias online y comprueba el wheelhouse antes de consumir el código. El origen Central está implementado detrás de bandera apagada; por ello no se debe intentar una sucursal real todavía.

El bootstrap instalar-universal-desde-release.ps1 recibe rutas absolutas a ZIP, manifiesto, SHA-256, firma, trust store externo, tarjeta privada, destino y versión. Primero verifica firma y hashes, inspecciona límites/rutas ZIP, contrasta manifiesto embebido y VERSION; crea staging privado con ACL desde el primer instante, persiste el trust store y la CA opcional bajo ProgramData con ACL y sólo entonces ejecuta instalar-universal.ps1. El Python de enrolamiento se resuelve desde HKLM y se rechazan rutas escribibles por usuarios comunes. Éste obtiene el recibo por HTTPS validado, exige escribir la clave de sucursal mostrada y pasa la identidad autorizada al motor. El motor almacena secretos en .env con ACL para SYSTEM, Administradores y lectura de Local Service, migra SQLite, aprovisiona los UUID distintos de sucursal y Edge, crea una cuenta dueña explícita y registra el servicio, luego comprueba salud. El código de enrolamiento nunca queda en .env. Salud del servicio no implica catálogo disponible, respaldo verificado ni impresión física probada.

Si la respuesta se pierde o la instalación se detiene tras reclamar el código, no reintentar el mismo código: inspeccionar Central, revocar Edge y ambas credenciales, auditar y emitir tarjeta nueva. No borrar una instalación parcial ni reenrolar a ciegas. El bootstrap no sobrescribe instalaciones existentes.

## Actualización supervisada

actualizar-laboratorio-desde-release.ps1 exige firma/trust store para versión 1.x, verifica antes de detener el servicio, sella los artefactos en staging privado, vuelve a verificar firma y contenido, respalda .env/SQLite, conmuta directorios en la misma unidad, ejecuta migraciones y health check, y restaura código anterior si falla. Conserva runtime, identidad, tokens, módulos, terminales, impresoras, cola y outbox. La firma se verifica con el script de la instalación 1.x confiable anterior. Para migrar una instalación 0.x de laboratorio, el actualizador exige un verificador externo entregado por canal independiente con SHA-256 fijado: todavía falta un ensayo de servicio completo de esa transición. El trust store que deja el bootstrap fuera del árbol reemplazable sirve para la primera actualización 1.x. La opción PrepareOnly aún exige instalación anterior sana, pero no la detiene.

El cambio de código es una secuencia de renombrados en la misma unidad, no una transacción única Windows. El respaldo previo y el rollback cubren fallos detectados. Cortes de energía y hardware defectuoso requieren prueba manual de restauración con SQLite WAL y colas. Ningún sync externo puede bloquear ventas/cobro/impresión.

## Gates operativos y validaciones pendientes antes de declarar Production 1.0

- E2E privado del nuevo enrolamiento con PostgreSQL Central real, tarjeta recién emitida, TLS/CA y revocación; Central mantiene la bandera apagada.
- Catálogo inicial específico de cada sucursal: una instalación limpia hoy queda sin productos. La semilla histórica Arboledas no sirve para Santa Anita y el distribuidor de catálogo Central sigue desactivado. Requiere contrato de snapshot completo, aplicación atómica, prueba offline/reinicio y E2E; es bloqueo de corte, no una mejora opcional.
- Correspondencia explícita Arboledas ↔ SucursalCliente.id y credencial Pedidos limitada por sucursal antes de habilitar ese módulo.
- Prueba de instalación nueva con Windows limpio, Python/wheelhouse, proxy TLS y dos impresoras físicas; no usar una instalación activa. El backend archivo y la omisión de tarea de respaldo son estados de laboratorio que impiden declarar listo el corte.
- Actualización representativa de 1.0.0-dev.1 a una candidata posterior en servicio aislado, incluyendo corte/crash, rollback, SQLite/cola/outbox, sin usar una sucursal real.
- Registro de terminales Central, reconexión Windows/Android y validación USB de APK firmado con clave productiva externa.
- Prueba visual/pantallas táctiles, concurrencia de dos operadores, impresora apagada/cambio de IP y restauración real de respaldo. El panel /soporte/ muestra la impresión pendiente hasta que haya topología TCP completa y un técnico registre una prueba de papel real; cambios de topología invalidan el acta. Falta prueba con hardware y respaldo restaurable.

## Evidencia de laboratorio y límites

El ensayo aislado sobre SQLite validó tanto migraciones limpias como migración de datos desde el commit congelado dev.10: preservó UUID de sucursal, evento outbox pendiente y módulos, sin arrancar ni modificar servicios. Las pruebas unitarias cubren firma/tamper/revocación, contrato de enrolamiento, roles, rutas de soporte e impresión y el actualizador. Se generó un wheelhouse CPython 3.13/Windows x64 y su resolución con pip --no-index --dry-run pasó. Aún no se ha ejecutado un instalador completo contra Windows limpio ni un E2E con Central PostgreSQL, impresoras y tabletas reales. No atribuir a esas pruebas una aprobación de Production 1.0.

El handoff de contratos y cambios requeridos del lado Central está en docs/HANDOFF_PRODUCTION_1_0_A_CENTRAL.md. La API POS v1 y Supabase legacy continúan como rollback hasta una decisión explícita de corte.