# Evidencia de preparación de release `0.4.0-dev.3`

Fecha de actualización: 2026-09-18.

## Estado de la candidata

`0.4.0-dev.3` es una candidata validada de laboratorio. **No existe ninguna
sucursal en producción y este documento no autoriza un despliegue productivo.**

Quedaron completos el desarrollo funcional, la validación automatizada, dos builds
reproducibles, una instalación limpia aislada del payload funcional y la actualización
real de `C:\LosTocayosPOS` con el artefacto final. La candidata sigue en desarrollo
hasta completar la aceptación física en Android e impresión, recorrer manualmente los
flujos críticos y registrar una decisión expresa de promoción. El VPS, HTTPS,
enrolamiento y el canal remoto firmado siguen siendo trabajo futuro del producto
multisucursal.

## Identidad del artefacto final

| Campo | Valor |
| --- | --- |
| Versión | `0.4.0-dev.3` |
| Commit | `7ae0431d4d615d4c3df059ce8749179c966c4ab7` |
| Rama | `codex/candidata-0.4.0-dev.3` |
| Archivo | `LosTocayosPOS-Servidor-0.4.0-dev.3.zip` |
| SHA-256 del ZIP | `c3c23e58663e2f110d568f4781a1507b257af4382c74ac67e35820989ff9e890` |
| Tamaño | 32,327,195 bytes |
| Archivos del manifiesto | 204 |
| SHA-256 del manifiesto | `14abf805f2f827b045d84cf2353a155ebb79405f22afc45f7ffc315a30d3257d` |
| SHA-256 del archivo de checksums | `4bf8bb6dce0bfc57b4657826cf94722e49339f316df89b4eb3965a452e1d5956` |
| Árbol fuente | Limpio; `source_dirty=false` |
| Reproducibilidad | 2/2 construcciones idénticas desde el mismo commit y epoch |
| Verificación del paquete | `status=ok`, versión/commit/204 archivos coincidentes |

Los dos builds usaron el wheelhouse CPython 3.13 para Windows x64 y pasaron
`herramientas\release_servidor.py verify`. No se usó `--allow-dirty`.

## Resultado de validación

| Validación | Resultado |
| --- | --- |
| Suite Django completa | 172/172 aprobadas |
| Suite `unittest` de infraestructura | 68/68 aprobadas |
| Contrato `tests/test_instalador_windows.ps1` en Windows PowerShell 5.1 | Aprobado |
| `django check` con configuración de pruebas | Sin hallazgos |
| `makemigrations --check --dry-run` | Sin cambios pendientes |
| Sintaxis de `ventas/static/ventas/app.js` y `admin.js` | Correcta |
| `git diff --check` | Correcto |
| Detector de Impeccable | Sin hallazgos |
| Verificador elevado de la instalación actualizada | Aprobado |
| Auditoría ACL elevada | 11,984 elementos; 0 violaciones |
| Respaldo SQLite real desde la tarea instalada | Resultado 0 |

La suite Django completa volvió a pasar durante la actualización final. Los avisos
de despliegue W004, W008, W012 y W016 son los esperados porque este laboratorio
acepta explícitamente HTTP directo en la LAN; no describen el perfil futuro HTTPS.

## Instalación limpia aislada

La instalación limpia `LAB_DEV3` se completó el 2026-09-17 y se detuvo después de
validar. Acreditó:

- Python 3.13.14 y `pip check` sin conflictos;
- migraciones, estáticos, bootstrap y salud HTTP 200;
- clave maestra inicial `0000` almacenada como hash;
- cuenta administrativa y primer operador separados;
- 24 posiciones de Comedor, 100 de Domicilio, 40 de Llevar y 40 de Recoger;
- módulos `pos`, `catalogo`, `impresion` y `respaldos` habilitados;
- módulos opcionales deshabilitados;
- service worker dev.3, `skipWaiting`, `clients.claim` y manifiesto de tableta;
- vista previa de impresión en modo `archivo`.

Esta prueba usó el payload funcional del commit `b568bb5`. El commit final
`7ae0431` sólo cambia `verificar-servicio-lan.ps1` y su prueba de regresión para
Windows PowerShell 5.1. El ZIP final exacto quedó acreditado mediante la actualización
real descrita a continuación.

## Actualización real del laboratorio

El reintento elevado se ejecutó el 2026-09-18 de 07:57:49 a 08:06:27 mediante
`actualizar-laboratorio-desde-release.ps1`. Aplicó el ZIP final a
`C:\LosTocayosPOS` y terminó con `status=ok`.

| Comprobación | Resultado |
| --- | --- |
| Versión/commit/SHA del origen | Coinciden con la tabla de identidad |
| `.env` | Preservado byte por byte |
| Identidad, base, usuarios y módulos | Conservados |
| Salud | `ok` |
| Servicio | `Running`, `Auto`, `NT AUTHORITY\LocalService` |
| Ejecutable | `C:\LosTocayosPOS\.venv\Scripts\pythonservice.exe` |
| Tarea de respaldo | Presente, `Ready` |
| Tarea de purgas físicas | Presente, `Ready` |
| Respaldo real | `db-20260918-080534.sqlite3`, resultado 0 |
| ACL | 11,984 elementos auditados; 0 violaciones |
| Firewall | Perfil privado y `LocalSubnet` |
| Service worker | Versión dev.3, `skipWaiting`, `clients.claim` |
| Manifiesto tableta | `/tabletas/`, `fullscreen`, `standalone` |
| Respaldo del árbol anterior | `C:\LosTocayosPOS-respaldo-lab-20260918-075832-91d6c18f` |

Evidencia local:

- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\resultado-20260918-075749.json`;
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\actualizacion-20260918-075749.txt`;
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\verificacion-20260918-075749.txt`.

### Incidente de compatibilidad corregido

El primer intento actualizó el servicio y lo dejó saludable, pero la verificación
posterior falló porque Windows PowerShell 5.1 no expone
`System.Net.IPAddress.IsMulticast`. No fue un fallo de Django, de la migración ni
del servicio.

El commit `7ae0431` sustituyó esa propiedad por una clasificación portable de
multicast IPv4 `224.0.0.0/4` e IPv6 `ff00::/8` basada en bytes y agregó una
regresión al contrato del instalador. Se revisaron los 18 scripts PowerShell en 5.1,
se reconstruyó dos veces el paquete y el segundo intento pasó completo. Como mejora
futura, los scripts de instalación y arranque también pueden rechazar multicast antes
de llegar al verificador final.

## Parche explícito de catálogo

Después de la actualización se creó un respaldo, se ejecutó el dry-run y se aplicó
el parche acotado de `ARBOLEDAS`:

| Campo | Antes | Después |
| --- | --- | --- |
| Código | `AM` | `AM` |
| Nombre corto | `AM` | `Topo` |
| Precio | `$30.00` | `$32.00` |
| Vigencia | 2026-08-14 | 2026-09-17 |

La aplicación informó `estado=aplicado`; una segunda ejecución informó
`estado=ya_aplicado` sin cambios y la salud siguió en `ok`. La evidencia está en
`C:\LosTocayosPOS-lab-actualizaciones\evidencia\parche-topo-20260918-081130.json`.
Una actualización normal no ejecuta este parche ni `cargar_datos_iniciales`
automáticamente.

## Impresión y tableta

La actualización preservó la configuración existente de impresión. El servicio
instalado continúa deliberadamente con `PRINT_BACKEND=archivo`, por lo que reporta
modo vista previa y no envía papel. Un sondeo separado confirmó conectividad TCP a
`192.168.0.33:9100`, y la prueba aislada en 8001 validó el backend TCP. Esto
acredita conectividad, pero no una impresión física aceptada.

La ruta `/tabletas/`, el teclado numérico, la solicitud de pantalla completa y el
manifiesto PWA están cubiertos por código y pruebas. Falta aceptar la experiencia en
una tableta Android real. El repositorio todavía no contiene el proyecto envolvente
WebView/TWA, no genera un APK firmado y no ha probado su instalación USB-C.

## Cobertura de los 14 requisitos

| N.º | Comportamiento de dev.3 | Estado |
| ---: | --- | --- |
| 1 | Instalación nueva con clave maestra inicial `0000` almacenada como hash | Cubierto |
| 2 | Administrador elevado con permisos acotados; usuarios/PIN, folios y clave maestra reservados al maestro | Cubierto |
| 3 | Corte diario reimprimible, purga de detalle y consolidación mensual sólo después del acuse del VPS | Código y pruebas completos; VPS real pendiente |
| 4 | Ticket total muestra únicamente el nombre del producto | Cubierto |
| 5 | Pedidos de sucursal organizados en subtabs | Cubierto |
| 6 | Refresco periódico de terminales LAN | Cubierto |
| 7 | Todos los PIN activos pueden acceder a Ventas con atribución de operador | Cubierto |
| 8 | 40 posiciones de Llevar y 40 de Recoger | Cubierto |
| 9 | `pedidos_sucursales` es opcional y no se habilita por defecto | Cubierto |
| 10 | Programados editables, eliminables y desprogramables; Recoger admite programación | Cubierto |
| 11 | Un pedido futuro no inicia el turno antes de su activación | Cubierto |
| 12 | Producto personalizado disponible en todos los canales | Cubierto |
| 13 | Movimientos, control de caja, aplicaciones y sucursales con fórmula de efectivo delimitada | Cubierto |
| 14 | `/tabletas` con teclado numérico y solicitud de pantalla completa | Automatizado; aceptación física Android pendiente |

## Cierre físico y tareas programadas

El corte y la consolidación pueden borrar el detalle lógico dentro de la transacción
sin privilegios para eliminar todos los archivos o respaldos protegidos. Dev.3
registra solicitudes persistentes bajo `runtime\purgas-pendientes`.

`LosTocayosPOS-PurgasFisicas` se ejecuta como SYSTEM cada cinco minutos con
`-OnlyIfPurgePending`. Cuando existe trabajo, genera y verifica un respaldo posterior
a la purga, elimina los artefactos administrados que aún contienen detalle sensible y
permite marcar la consolidación como purgada. `LosTocayosPOS-RespaldoSQLite` conserva
el respaldo diario, de forma predeterminada a las 03:15 con 30 días de retención. La
actualización conservó ambas tareas y su configuración.

## Límites conocidos del actualizador

- El wrapper es elevado, local, supervisado y exclusivo del laboratorio.
- Los hashes prueban integridad, pero no identidad del publicador; falta firma y
  descarga autenticada.
- Persiste una ventana TOCTOU local entre verificar el ZIP y consumirlo.
- Una candidata fallida conservada para diagnóstico no recibe acreditación final de
  ACL y no debe arrancarse como servicio.
- Volver al código anterior no revierte por sí solo una migración de datos ya aplicada.
- Falta un desinstalador canónico probado.

## Evidencia pendiente antes de promover

1. **Completado:** dos builds reproducibles, manifiesto y SHA-256 idénticos.
2. **Completado:** instalación limpia aislada del payload funcional.
3. **Completado:** actualización real con el ZIP final, preservación de estado,
   respaldo, tareas, firewall, salud y ACL.
4. **Completado:** dry-run, aplicación e idempotencia del parche `Topo`.
5. **Pendiente:** recorrido manual de Ventas, Administrador, programados, corte,
   reimpresión y recuperación.
6. **Pendiente:** aceptación física en una tableta Android y salida real de
   impresoras.
7. **Pendiente para el producto completo:** endpoint y credenciales del VPS, HTTPS
   LAN, enrolamiento de terminales, APK firmado y canal remoto firmado.

La construcción, la instalación limpia y la actualización quedaron cerradas en el
laboratorio. `0.4.0-dev.3` permanece como candidata hasta completar la aceptación
física y decidir expresamente su promoción; ninguna parte de esta evidencia implica
que exista una sucursal en producción.
