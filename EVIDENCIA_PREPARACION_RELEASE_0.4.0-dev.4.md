# Evidencia de preparación de release `0.4.0-dev.4`

Fecha de actualización: 2026-09-18.

## Estado de la candidata

`0.4.0-dev.4` es una candidata funcional de laboratorio. **No existe ninguna
sucursal en producción y este documento no autoriza un despliegue productivo.**

La candidata corrige las regresiones detectadas en la prueba de `dev.3`, restaura el
directorio de clientes y la integración de Pedidos Sucursales en la instalación de
laboratorio, y simplifica la interfaz de tabletas. Todavía requiere aceptación física
en Android, impresión real y un recorrido manual de los flujos críticos antes de que
pueda promoverse expresamente como versión base estable.

## Identidad del artefacto

| Campo | Valor |
| --- | --- |
| Versión | `0.4.0-dev.4` |
| Commit funcional | `2739944ee5da28b35fa7b4c6330e76ba83605aab` |
| Rama | `codex/candidata-0.4.0-dev.4` |
| Archivo | `LosTocayosPOS-Servidor-0.4.0-dev.4.zip` |
| SHA-256 del ZIP | `c25a3f8fc276e32ebc83eda3d2bfa5e20157ffd479b6feec784c1170b0830904` |
| Tamaño | 32,339,644 bytes |
| Archivos del manifiesto | 208 |
| SHA-256 del manifiesto | `377a765ab6c868d5bee41101a179fa344bcd0cf2d794b3bc136ca1723f579b21` |
| Reproducibilidad | 2/2 construcciones idénticas desde el mismo commit y epoch |
| Verificación | `status=ok`; versión, commit y 208 archivos coincidentes |

Los dos builds se crearon desde el árbol limpio, sin `--allow-dirty`, y se verificaron
con `herramientas\release_servidor.py verify`.

## Cambios incluidos

- `/tabletas/` muestra inicialmente sólo las mesas de Comedor. No presenta una
  sección de Llevar ni un teclado antes de seleccionar una mesa.
- Al seleccionar una mesa aparece el teclado de PIN; el cuarto dígito inicia una sola
  validación automática y no existe botón `OK`.
- La comanda virtual vuelve a utilizar `nombre_corto` para productos normales.
- El producto personalizable queda al final del catálogo con la misma geometría de
  las demás tarjetas y conserva captura de nombre y precio.
- Movimientos del Administrador usa una distribución responsiva, evita desbordamiento
  y deja vacíos los campos numéricos cuyo valor inicial es cero.
- `importar_clientes_sqlite_legado` migra sólo clientes, teléfonos, domicilios y su
  consecutivo desde una SQLite explícita en modo de sólo lectura. No copia tickets ni
  ventas, es transaccional y comprueba coincidencia/idempotencia.

## Validación automatizada y de instalación

| Validación | Resultado |
| --- | --- |
| Suite Django completa | 182/182 aprobadas |
| Suite `unittest` de infraestructura | 73/73 aprobadas |
| Pruebas dirigidas nuevas | 10/10 aprobadas |
| Contrato completo `tests/test_instalador_windows.ps1` con ACL | Aprobado en Windows PowerShell 5.1 |
| `django check` con configuración de pruebas | Sin hallazgos |
| `makemigrations --check --dry-run` | Sin cambios pendientes |
| Sintaxis de `app.js` y `admin.js` | Correcta |
| `git diff --check` | Correcto |
| Revisión Impeccable | Aplicada; avisos globales/históricos revisados |
| Verificación elevada del paquete instalado | Aprobada |

El instalador no cambió respecto de la instalación limpia acreditada en `dev.3`.
Para `dev.4` se repitieron el contrato completo de instalación/ACL, dos builds
reproducibles, la verificación independiente de ambos ZIP y la actualización real del
laboratorio. Los cambios funcionales no agregan migraciones de esquema.

## Actualización real de `C:\LosTocayosPOS`

La actualización se ejecutó el 2026-09-18 de 09:56:03 a 10:03:03 y terminó con
`status=ok`.

| Comprobación | Resultado |
| --- | --- |
| Versión | `0.4.0-dev.4` |
| Commit y SHA-256 | Coinciden con la identidad del artefacto |
| `.env` durante la sustitución de código | Preservado |
| Salud HTTP | `ok` |
| Servicio | `Running`, inicio `Auto`, cuenta `NT AUTHORITY\LocalService` |
| Tareas | Respaldo SQLite y purgas físicas presentes en estado `Ready` |
| Service worker | Versión presente, `skipWaiting` y `clients.claim` |
| Manifiesto tableta | `/tabletas/`, `fullscreen`, `standalone` |
| Impresión | `PRINT_BACKEND=archivo`; no se envió papel |
| Respaldo previo | `C:\LosTocayosPOS-respaldo-lab-20260918-095626-92cfb8fc` |

Evidencia local:

- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\resultado-20260918-095603.json`;
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\actualizacion-20260918-095603.txt`;
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\verificacion-20260918-095603.txt`.

## Directorio de clientes e integración de Sucursales

El directorio se tomó de `C:\tcysAplicacionLocales\runtime\db.sqlite3` mediante el
migrador de sólo lectura. El dry-run, la importación y la segunda ejecución
idempotente pasaron.

| Entidad | Total final |
| --- | ---: |
| Clientes | 1,730 |
| Teléfonos | 1,434 |
| Domicilios | 1,772 |

La configuración de Pedidos Sucursales se incorporó desde el `.env` legado mediante
una lista cerrada de variables `PEDIDOS_SUCURSALES_*`. Se conservaron las demás
variables de la instalación y `PRINT_BACKEND=archivo`; la evidencia sólo registra
nombres de claves, nunca credenciales.

| Comprobación | Resultado |
| --- | --- |
| Fuente | `supabase` |
| Sincronización automática | Activa |
| Módulo `pedidos_sucursales` | Activo para ARBOLEDAS |
| Comando de sincronización | Aprobado |
| Registros importados | 5 |
| Pedidos visibles | 5 |
| Salud posterior | `ok` |

Evidencia local:

- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\datos-configuracion-20260918-101346.json`;
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\datos-configuracion-20260918-101346.txt`.

### Incidencias del arnés de finalización

La actualización del código terminó correctamente en el primer intento. El
finalizador de datos se detuvo inicialmente por tres incompatibilidades del arnés con
Windows PowerShell 5.1: conversión de `stderr` nativo en error terminante, pérdida de
comillas dobles al pasar Python a `shell -c` y rechazo de líneas vacías de `.env` por
el enlazador de parámetros. Los fallos ocurrieron antes de modificar el `.env`; los
clientes ya importados se conservaron y su idempotencia se comprobó por separado.

El finalizador corregido usa el código de salida nativo como autoridad, sanea cualquier
diagnóstico antes de guardarlo, usa comillas compatibles con procesos nativos y acepta
líneas vacías del archivo de entorno. La ejecución final terminó con `status=ok` sin
repetir la actualización del código.

## Validación visual de tabletas

Se levantó una instancia aislada del mismo commit en `127.0.0.1:8001`, con base de
pruebas separada, y se detuvo al finalizar. Chrome se controló mediante CDP local; la
petición de identificación se interceptó dentro del navegador para no abrir tickets ni
modificar datos.

| Comportamiento | Resultado |
| --- | --- |
| Portada | 24 mesas de Comedor; sin canales, Llevar ni teclado visibles |
| Selección de mesa | Muestra panel de identificación y teclado de 12 teclas |
| Botón `OK` | Ausente |
| Tres dígitos | 3 indicadores, 0 solicitudes |
| Cuarto dígito | Exactamente 1 solicitud con los 4 dígitos |
| Error controlado | Mensaje visible y teclado habilitado para reintento |
| Anchura 1024 × 768 | Sin desbordamiento horizontal |

Evidencia local:

- `C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tabletas-dev4-cdp-result.json`;
- `C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tabletas-dev4-cdp-mesas.png`;
- `C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tabletas-dev4-cdp-pin.png`;
- `C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tabletas-dev4-cdp-pin-error.png`.

## Trabajo pendiente para promover la base

1. Ejecutar un recorrido manual completo en la instalación real: Ventas, Domicilios,
   Sucursales, Administrador, programados, caja, corte, reimpresión y recuperación.
2. Validar impresión física aceptada; el laboratorio permanece deliberadamente en
   modo `archivo`.
3. Crear el proyecto envolvente Android (WebView o TWA), definir firma y versionado,
   generar el APK e instalarlo por USB-C en una tableta real.
4. Probar pantalla completa, reconexión, caché y actualización de la PWA/APK en el
   hardware Android objetivo.
5. Registrar una decisión expresa de promoción de la candidata a versión base.
6. Para el producto multisucursal completo: VPS, consolidación real, HTTPS LAN,
   enrolamiento de terminales y canal remoto firmado.

`0.4.0-dev.4` queda instalada y validada como candidata de laboratorio. No debe
etiquetarse todavía como base estable ni desplegarse en una sucursal productiva.