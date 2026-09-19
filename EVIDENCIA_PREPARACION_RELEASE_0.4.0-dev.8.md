# Evidencia de preparacion de release 0.4.0-dev.8

Fecha de la validacion: 2026-09-19  
Estado: candidata de laboratorio; no promovida a version base  
Entorno: instalacion local de pruebas en `C:\LosTocayosPOS`  
Produccion: no existe ninguna sucursal en produccion en este momento

## Resultado ejecutivo

La candidata `0.4.0-dev.8` se construyo de forma reproducible y se instalo correctamente sobre el laboratorio local. La actualizacion termino con codigo de salida `0`, preservo la configuracion y los datos de negocio, y dejo operativo el servicio de Windows y sus tareas auxiliares.

Al terminar la actualizacion se observo un `TimeoutError` contra `192.168.0.33:9100`; un escaneo de la red local `/24` tampoco encontro inicialmente un equipo con ese puerto abierto. El hallazgo quedo resuelto al encender o reconectar la impresora. Entre las 07:31 y las 07:32, caja, cocina y barra respondieron correctamente en `192.168.0.33:9100`. El diagnostico fue seguro: `envio_de_datos=false`.

Despues se ejecuto una unica prueba controlada. El trabajo de caja termino tecnicamente en estado `impreso`, con un intento y archivo generado; el ticket temporal quedo cancelado. Falta que una persona confirme visualmente si la hoja salio y si su contenido fue legible. Hasta registrar esa confirmacion y la decision expresa de promocion, `0.4.0-dev.8` permanece como candidata de laboratorio.

## Identidad de la candidata

| Concepto | Valor |
| --- | --- |
| Version | `0.4.0-dev.8` |
| Commit fuente | `e7153ed1916ddf3500380b3443922dd15657ea0b` |
| SHA-256 del artefacto ZIP | `4d29c6f845945fe5d83c44de3a00630c4df3c4fa50ab3ee4b8df6672755f1fe2` |
| Archivos incluidos | `215` |
| Construcciones reproducibles | `2/2` identicas |
| SHA-256 del manifiesto | `fcc6003482c6d2dba8c5b332d625b41c142d766173f294e2ca600b7a896e8d6d` |
| SHA-256 del archivo de sumas | `0dfdcf237462e9f4a753411c2fab6f69a818ccdfc5c843fc9368c1b4436425ff` |

Las dos construcciones independientes produjeron el mismo ZIP, manifiesto y archivo de sumas. La evidencia de construccion se encuentra en:

- `C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tcys-base-work\release-0.4.0-dev.8-e7153ed`

## Motivo de la revision dev.8

La version `0.4.0-dev.7` habia sido emitida y, por la regla de inmutabilidad de releases, no se reconstruyo con cambios bajo el mismo numero. Durante su comprobacion se detecto que el configurador de impresion dejaba una copia de respaldo de `.env` junto al archivo original y con una ACL que no cumplia el contrato de seguridad de la instalacion.

`0.4.0-dev.8` corrige ese defecto: el respaldo se guarda en `C:\LosTocayosPOS\backups` y se protege para `SYSTEM` y `Administradores`. La reparacion puntual aplicada al laboratorio antes de emitir dev.8 quedo registrada en:

- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\reparacion-respaldo-impresion-dev7.json`

## Validaciones automatizadas

| Conjunto | Resultado |
| --- | ---: |
| Pruebas Django | `192/192` |
| Pruebas de respaldo SQLite | `13/13` |
| Pruebas del host Windows | `5/5` |
| Suite de infraestructura | `85/85` |
| Preflight de release | Aprobado |
| Reproducibilidad del artefacto | `2/2` |

La validacion de despliegue completo comprobo ademas el servicio, HTTPS, tareas programadas y el contrato de archivos de la instalacion. Las unicas advertencias observadas correspondieron al acceso HTTP en LAN frente a las politicas esperadas de TLS y cookies; no invalidaron la ruta HTTPS comprobada.

## Actualizacion real del laboratorio

La actualizacion local de `C:\LosTocayosPOS` se ejecuto el 2026-09-19 y termino con codigo de salida `0`.

| Comprobacion posterior | Resultado |
| --- | --- |
| Version instalada | `0.4.0-dev.8` |
| Commit instalado | `e7153ed1916ddf3500380b3443922dd15657ea0b` |
| Servicio de Windows | `Running` |
| Inicio del servicio | `Automatic` |
| Cuenta del servicio | `NT AUTHORITY\LocalService` |
| Health check | Correcto |
| Tareas auxiliares | `Ready` |
| Errores de ACL tras configurar impresion | `0` de `12029` elementos revisados |
| `.env` | Preservado |
| Backend de impresion | `tcp` preservado |
| Modo de impresion | Asincrono preservado |

Los resultados, transcripciones y verificaciones de la actualizacion se encuentran en:

- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\resultado-20260919-071919.json`
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\actualizacion-20260919-071919.txt`
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\verificacion-20260919-071919.txt`

La carpeta `C:\LosTocayosPOS-lab-actualizaciones\evidencia` es evidencia externa a la instalacion. Debe conservarse junto con el artefacto y sus sumas para permitir una auditoria posterior.

## Integridad de datos

La comparacion antes y despues de la actualizacion produjo los mismos conteos:

| Entidad | Antes | Despues |
| --- | ---: | ---: |
| Clientes | `1730` | `1730` |
| Telefonos | `1434` | `1434` |
| Domicilios | `1772` | `1772` |
| Pedidos de sucursales importados | `5` | `5` |
| Pedidos de sucursales activos | `0` | `0` |

Tambien quedaron activos y sin cambios:

- la fuente Supabase;
- la sincronizacion automatica;
- el modulo de sucursales.

Las comparaciones estan registradas en:

- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\negocio-antes-20260919-071919.json`
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\negocio-despues-20260919-071919.json`

## Configuracion de impresion y respaldo privado

La ejecucion real del configurador de dev.8 termino con `status=ok_printer_online` entre las 07:31:10 y las 07:32:12. Los tres destinos configurados respondieron:

| Destino | Direccion | Resultado del diagnostico |
| --- | --- | --- |
| Caja | `192.168.0.33:9100` | Alcanzable |
| Cocina | `192.168.0.33:9100` | Alcanzable |
| Barra | `192.168.0.33:9100` | Alcanzable |

El diagnostico solo comprobo el socket y no envio datos: `envio_de_datos=false`.

El hash SHA-256 de `.env` antes y despues fue identico:

`7138DB61E320BE48DE57456CB9E1E2DD6EC717FC9E5A91BCAB2847DB7D541A37`

El configurador genero este respaldo:

- `C:\LosTocayosPOS\backups\env-impresion-backup-20260919-073111-341cc597.bak`

El respaldo tiene herencia ACL deshabilitada y exactamente dos reglas privadas, ambas con `FullControl`: `SYSTEM` y `Administradores`. Despues de la configuracion, el servicio quedo `Running`, con inicio `Auto` y cuenta `NT AUTHORITY\LocalService`; el verificador oficial encontro `0` violaciones en `12029` elementos.

La evidencia se encuentra en:

- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\validacion-respaldo-impresion-dev8.json`
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\validacion-respaldo-impresion-dev8.txt`

## Prueba controlada de impresion

Se ejecuto una unica prueba dirigida a caja. El sistema registro:

| Campo | Resultado |
| --- | --- |
| Trabajo | `3e1ad9b1-cff7-49f4-a60e-09435044a005` |
| Destino | `caja` |
| Formato | `cuenta` |
| Estado | `impreso` |
| Intentos | `1` |
| Archivo generado | Si |
| Ticket temporal | Folio `13`, id `5c8cad75-293f-49f6-b16f-bc3caa09f94f` |
| Estado final del ticket | `cancelado` |

La evidencia de esta ejecucion esta en:

- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\prueba-fisica-dev8.json`
- `C:\LosTocayosPOS-lab-actualizaciones\evidencia\prueba-fisica-dev8.txt`

El runner externo devolvio codigo `1` solamente por una comprobacion HTTP simplificada posterior a la prueba. Ese resultado fue un falso negativo del runner y no el estado del trabajo de impresion. Una comprobacion independiente posterior confirmo:

- servicio `Running`, inicio `Auto`, cuenta `NT AUTHORITY\LocalService`;
- `/salud/` respondio HTTP `200` con `{"estado":"ok"}`;
- el socket `192.168.0.33:9100` acepto conexion sin que la comprobacion enviara datos.

El registro tecnico demuestra que el trabajo paso por el backend y termino en `impreso`. Todavia no existe confirmacion visual humana de que la hoja haya salido ni de que su contenido sea correcto.

## Criterio de promocion a version base

Para promover esta candidata como version base de las sucursales falta:

1. confirmar visualmente si salio una sola hoja y si su contenido fue legible;
2. registrar esa confirmacion o, si no salio correctamente, abrir una correccion en una version posterior;
3. documentar la decision explicita de promocion sin modificar los artefactos ya emitidos.

Tambien conviene corregir la comprobacion HTTP simplificada del runner de prueba para evitar que futuras ejecuciones correctas terminen con un falso codigo de error.

Hasta completar esos pasos, `0.4.0-dev.8` permanece como candidata funcional de laboratorio. Cualquier correccion posterior debe producir una nueva version; los artefactos, sumas y manifiestos de dev.8 deben permanecer inmutables.