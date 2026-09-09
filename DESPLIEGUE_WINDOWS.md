# Despliegue Windows por sucursal

## Alcance y limites actuales

Guia basada en la recuperacion y puesta en marcha del 2026-08-30 en
C:\tcysAplicacionLocales. Se verifico Windows PowerShell 5.1 y Python 3.13.14
instalado en C:\Program Files\Python313, con HTTP LAN en el puerto 8000.

**Importante antes de repetir o distribuir el instalador:** aun ejecuta
catalogo/management/commands/cargar_datos_iniciales.py, cuya sucursal esta fijada
a ARBOLEDAS. Esa carga actualiza productos/precios y puede desactivar categorias
personalizadas. No basta cambiar la IP o SUCURSAL_CLAVE para instalar otra
sucursal. Antes de replicarlo hay que separar la inicializacion de catalogos de
las actualizaciones y preparar los datos de cada sucursal. Esto sigue pendiente;
las correcciones del servicio Windows no resuelven esa personalizacion.

El servicio usa los archivos locales, la .venv local y runtime\db.sqlite3. No hace
git pull ni descarga codigo desde GitHub; el instalador si descarga dependencias
con pip cuando hace falta. Un commit o push no actualiza un servidor instalado.
Las correcciones incluyen servicio_windows.py, herramientas/host_servicio_windows.py,
herramientas/validar_despliegue.py y sus pruebas: no distribuir solo el archivo ps1.
Los archivos nuevos deben estar incluidos en la version entregada.

La arquitectura futura y las decisiones vigentes de distribución están en
[ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md](ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md).
Este archivo sigue siendo exclusivamente el runbook del **servidor Edge Windows
actual**; no describe ni instala el backend central de Hostinger KVM 2.

## Estado consolidado del instalador al 2026-09-09

| Área | Ya implementado/probado | Pendiente antes de otras sucursales |
| --- | --- | --- |
| Servicio Windows | Host pywin32 dentro de la venv, DLL locales, `LocalService`, inicio retardado y registro de eventos | Validarlo desde una release completa en Windows limpio |
| Permisos | ACL diferenciadas para código, secretos, runtime, media, logs y respaldos; reparación limitada para Administradores/SYSTEM | Probar matriz de instalación, actualización y recuperación en equipos ajenos al desarrollo |
| Migración | El servicio se detiene antes de migrar y vuelve a comprobarse al arrancar | El preflight pesado aún ocurre con el servicio detenido; falta staging y rollback |
| Respaldo | Copia SQLite consistente, hash, integridad, claves foráneas, restauración de prueba y tarea diaria SYSTEM | Copia externa cifrada, alerta y respaldo de configuración/media |
| Validación | Suite aislada, verificador de servicio, tarea, DB, salud, firewall y ACL | Salud profunda con versión, esquema, sucursal, worker, sincronización y prueba funcional |
| Red y seguridad | Firewall privado, hosts explícitos, opción HTTPS y rechazo de configuraciones inseguras implícitas | Artefactos firmados, manifiesto verificable y canal de actualización |
| Identidad | La aplicación dispone de `Sucursal` con UUID y clave | El instalador no enrola sucursal; `SUCURSAL_CLAVE` aún cae a ARBOLEDAS |
| Catálogo | Modelos por sucursal y precios con vigencia | La semilla Arboledas aún se ejecuta siempre y no puede formar parte de una actualización |
| Módulos | Código común reutilizable | No existe selección, configuración ni validación de módulos por sucursal |
| Release | Paquete del cliente ligero y sumas SHA-256 | No existe paquete completo y firmado del servidor ni dependencias fijadas/offline |

### Backlog obligatorio del instalador y actualizador

1. Crear una release completa, inmutable, versionada y firmada del servidor, con
   dependencias fijadas y sin ejecutar `pip install` contra Internet durante la
   ventana de mantenimiento.
2. Separar físicamente `Instalar-Servidor`, `Aprovisionar-Sucursal`,
   `Actualizar-Servidor` y reparación. `-PrepareOnly` no debe seguir siendo una
   mezcla que migra, siembra y puede dejar detenido el servicio.
3. Hacer obligatoria la identidad de sucursal y enrolarla mediante código de un
   solo uso o archivo firmado; nunca asumir ARBOLEDAS.
4. Convertir la carga inicial de catálogo en un paquete explícito por sucursal.
   Retirar `cargar_datos_iniciales` del arranque y de toda actualización.
5. Aprovisionar por separado módulos, impresoras, red, respaldo y credenciales.
   La selección de módulos pertenece al servidor Edge, no al instalador del
   cliente ligero.
6. Preparar la nueva release en staging antes de detener el servicio, verificar
   firma/manifiesto, respaldar DB y configuración, migrar, hacer un cambio atómico
   y conservar rollback compatible.
7. Registrar versión de aplicación, esquema, protocolo, catálogo, configuración
   y módulos; añadir una comprobación local protegida que valide el estado real.
8. Producir un informe de instalación/actualización sin secretos y desplegar
   primero por canal piloto.
9. Unificar las versiones del cliente Windows, comprobar la firma dentro del
   bootstrap y propagar el código de salida cuando falle su script de instalación.

### Límite frente al VPS central

El futuro despliegue KVM 2 tendrá configuración propia, preferentemente bajo un
directorio como `deploy/vps/`. No reutilizará este instalador, no ejecutará el
worker de impresión de sucursal y jamás cargará la semilla Arboledas. Alojará
PostgreSQL, la API/panel central, HTTPS, respaldos externos y monitoreo; los Edge
seguirán operando y cobrando localmente cuando el VPS no esté disponible.

## Requisitos

Ejecutar desde **Windows PowerShell como administrador**. Python 3.10 o posterior
debe estar instalado para todos los usuarios; comprobar la ruta con py -0p.
No usar un Python ubicado en C:\Users. Mantener fija la ruta del proyecto
despues de instalar el servicio.

Comprobar la elevacion real y el interprete sin mostrar configuracion privada:

~~~powershell
$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidad)
$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
py -0p
& "C:\Program Files\Python313\python.exe" -I --version
~~~

La primera comprobacion debe devolver True. La ruta mostrada es la usada en este
servidor; en otro equipo verificar la ruta real. No basta que `python` funcione en
la consola del usuario. No ejecutar desde enlaces/junctions ni mover el proyecto
despues de registrar el servicio. No se necesita relajar la politica de ejecucion
global: el comando siguiente limita Bypass a ese proceso de PowerShell.

## Instalacion

Para este servidor, en una instalacion inicial o recuperacion cuyo catalogo se
haya revisado. No usarlo como actualizacion automatica de datos personalizados:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\tcysAplicacionLocales\instalar-servicio-lan.ps1" -AllowedHosts "localhost,127.0.0.1,192.168.0.30" -ListenAddress "0.0.0.0" -AllowInsecureHttpLan -Port 8000
~~~

En otra sucursal, preparar primero su inicializacion; despues definir su IP,
identidad e impresoras. La parte Windows se reutiliza, la carga de Arboledas no.
No copiar .env, credenciales, bases ni certificados privados entre sucursales.
La red de Windows debe ser privada: la regla del instalador solo permite TCP 8000
desde la subred local en ese perfil y apunta al Python de maquina que realmente
escucha, no al lanzador de la .venv. HTTP no cifra contrasenas ni pedidos;
-AllowInsecureHttpLan confirma expresamente ese riesgo.

### Recuperar un intento incompleto

Si una instalacion anterior dejo archivos inaccesibles, agregar
-RepairPermissions al mismo comando. Esta opcion agrega control efectivo a
Administradores y SYSTEM, sin conceder acceso adicional a LocalService ni a otros
usuarios. No usar icacls /reset /T ni borrar .venv para recuperarse.
Si ni siquiera se puede leer el instalador, -RepairPermissions no puede ejecutarse.
Desde una consola realmente elevada, verificar primero la ruta exacta. Para
recuperar solo ese archivo (sin recursion ni permisos para otros usuarios):

~~~powershell
takeown.exe /F "C:\tcysAplicacionLocales\instalar-servicio-lan.ps1" /A
icacls.exe "C:\tcysAplicacionLocales\instalar-servicio-lan.ps1" /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F"
~~~

Despues se puede repetir el comando de instalacion con -RepairPermissions,
respetando la advertencia de catalogos. Si hay denegaciones explicitas o no se
puede leer la ACL, detenerse y diagnosticar esa ruta; no aplicar /reset /T ni
conceder Everyone/Users control total para superar el error.

Para preparar dependencias, configuracion y migraciones sin solicitar cuentas ni
registrar el servicio, usar -PrepareOnly. Al terminar, repetir el comando sin esa
opcion desde una consola interactiva para crear las cuentas e instalar.
**No es un dry-run:** -PrepareOnly puede detener el servicio existente, modifica
.env, ejecuta migraciones y tambien la carga inicial de catalogos.

Una .venv incompleta, no ejecutable o dependiente de Python por usuario se
conserva como .venv-roto-FECHA-ID y se crea otra con Python de maquina. No se
eliminan esos directorios automaticamente y Git los ignora.

### Cuentas de acceso

Solo se solicitan cuentas si faltan. La administrativa permite mantener Django
en /admin/; la operativa inicia sesion en el POS y se vincula a un perfil local.
Sus contrasenas son distintas del PIN de cuatro numeros del personal. Elegir
contrasenas diferentes y largas (al menos 12 caracteres recomendado); escribirlas
solo en el prompt oculto, no en comandos, tickets ni mensajes.

En el modelo de usuario actual, el correo de createsuperuser es opcional:
se puede dejar en blanco pulsando Enter. No es una cuenta de GitHub ni un requisito
de conexion a internet. LocalService y la tarea SYSTEM no usan esas contrasenas.
Si las cuentas ya se crearon y fallo Start-Service, no hay que recrearlas.

## Orden y permisos

El instalador valida Python antes de cambiar ACL o detener un servicio existente.
Luego instala dependencias, ejecuta la suite en una copia temporal sin .env ni
datos reales, valida configuraciones efimeras HTTPS/HTTP LAN y prueba el respaldo.
El host pywin32 queda en .venv\Scripts\pythonservice.exe. Esa ubicacion permite
que Python 3.13 encuentre correctamente el entorno virtual; no usar el host
antiguo en la raiz de .venv. Recibe junto a su ejecutable las DLL de Python,
pywintypes y Visual C++ del runtime instalado. Se comprueba con un PATH minimo
de Windows: no depende del PATH heredado por services.exe al arrancar Windows.
El origen de eventos se registra durante install/update con elevacion.
Despues configura produccion, respalda SQLite antes de migrar, migra, recopila
estaticos y comprueba las cuentas administrativa y operativa. Si faltan, solicita
crearlas en la consola, sin mostrar contrasenas.

El endurecimiento completo solo ocurre al final. .env, certs y backups
reciben sus restricciones antes, sin afectar la lectura del codigo. Cada ACL
instalada conserva control total efectivo para Administradores y SYSTEM; los
archivos nunca reciben permisos que solo se aplican por herencia a carpetas.
Los enlaces y junctions se rechazan antes de modificarlos.

| Ruta | LocalService |
| --- | --- |
| Codigo, .venv, estaticos, certificados | Lectura/ejecucion |
| .env | Lectura |
| runtime, logs, media | Modificacion |
| backups, .git, tmp, .venv-roto-*, base antigua en la raiz | Sin acceso |

El mantenimiento de produccion requiere una consola elevada; un usuario
administrador sin elevar no recibe permisos directos sobre el proyecto.
Si falla una fase, resolver el mensaje y revisar la carga de catalogos antes de
repetir el comando. El servicio previo
puede quedar detenido y no se revierten migraciones automaticamente. Los
respaldos anteriores y la clave existente se conservan.

## Diagnostico del arranque

| Sintoma | Comprobacion y correccion incorporada |
| --- | --- |
| Acceso denegado a requirements.txt/manage.py | Elevacion real; reparar Administradores/SYSTEM. La ACL de archivos debe tener permisos efectivos, no solo heredables. |
| .venv incompleta o ligada a Python del usuario | Se valida Python/pip y se conserva como .venv-roto-* antes de recrearla con Python de maquina. |
| Servicio instalado pero Start-Service falla | Consultar estado y eventos. En este servidor el host terminaba con 0xC0000135 por DLL no localizables; el timeout 1053 por si solo no demuestra esa causa. |
| Python/servicemanager no se encuentra en el servicio | Host en .venv/Scripts y DLL locales; el preflight comprueba el cargador nativo con un entorno minimo. |
| Funciona en el servidor, pero no desde otro equipo | Comprobar IP, perfil privado y firewall. La regla debe apuntar a sys._base_executable, no al lanzador .venv/Scripts/python.exe. |

Consultar sin imprimir .env ni registros completos:

~~~powershell
Get-CimInstance Win32_Service -Filter "Name='LosTocayosPOS'" |
    Select-Object Name, State, StartName, StartMode, PathName
Get-NetConnectionProfile | Select-Object Name, NetworkCategory
Get-NetFirewallRule -DisplayName "Los Tocayos POS - LAN privada" |
    Get-NetFirewallApplicationFilter | Select-Object Program
~~~

El preflight `servicio_windows.py --check-host` prepara/copia el host y sus DLL;
no es una consulta de solo lectura. El instalador lo ejecuta con el servicio
detenido. No lanzarlo sobre una instancia en ejecucion ni sustituir DLL a mano.
No aumentar el timeout de Windows ni ampliar el firewall para ocultar un fallo
del cargador. Revisar logs/eventos localmente y compartir solo errores depurados.

## Comprobacion

Desde una consola elevada en C:\tcysAplicacionLocales, para este perfil HTTP LAN:

~~~powershell
Set-Location -LiteralPath "C:\tcysAplicacionLocales"
.\.venv\Scripts\python.exe herramientas\validar_despliegue.py
powershell -NoProfile -ExecutionPolicy Bypass -File tests\test_instalador_windows.ps1
Get-Service LosTocayosPOS
Get-ScheduledTask LosTocayosPOS-RespaldoSQLite
Get-ScheduledTaskInfo LosTocayosPOS-RespaldoSQLite
Test-Path .\runtime\db.sqlite3
Invoke-RestMethod http://127.0.0.1:8000/salud/
powershell -NoProfile -ExecutionPolicy Bypass -File verificar-servicio-lan.ps1 -RunBackup
~~~

La validacion efimera HTTPS debe terminar sin avisos. En HTTP LAN son esperados
los avisos Django de HSTS, redireccion HTTPS y cookies seguras; no se silencian
en produccion.
La suite usa una copia temporal sin .env ni datos reales. Las pruebas de ACL
tambien usan un directorio temporal. La auditoria completa puede tardar varios
minutos; -RunBackup ejecuta ademas la tarea real y debe terminar con resultado 0.
El verificador acepta LocalSubnet como texto o lista, segun lo devuelva Windows.

Resultado esperado: servicio Running como NT AUTHORITY\LocalService, inicio
automatico retardado; tarea LosTocayosPOS-RespaldoSQLite como SYSTEM; base runtime
presente; /salud/ con estado ok; firewall Private/LocalSubnet/TCP 8000 y ninguna
desviacion de ACL. Probar http://192.168.0.30:8000/salud/ tambien desde otro equipo:
una prueba desde el propio servidor no demuestra conectividad entre dispositivos.

La tarea de respaldo se ejecuta como SYSTEM, diariamente a las 03:15, con
retencion de 30 dias por defecto. Cada copia se crea con la API de SQLite,
verifica integridad, claves foraneas y restauracion, y guarda hash SHA-256.
Se puede ejecutar sin esperar al horario:

~~~powershell
Start-ScheduledTask LosTocayosPOS-RespaldoSQLite
~~~

No publicar .env, archivos de respaldo, certificados privados ni logs completos
en tickets o commits. Para diagnosticar, compartir solo nombres de fase,
codigos de error y metadatos de permisos.

## Cambios y siguientes sucursales

No editar el codigo de produccion mientras se esta cobrando. Probar cambios en
una copia de desarrollo sin secretos ni datos operativos. Los cambios de Python
y .env requieren reiniciar el servicio; los de estaticos requieren collectstatic
y recarga del navegador, y los de estructura de datos requieren migraciones.
Cambiar productos/precios desde la aplicacion modifica la base local, no Git.

La estrategia prevista es un repositorio comun, versiones publicadas y datos y
configuracion propios por sucursal. Menus distintos no requieren ramas permanentes.
El instalador aun no descarga releases ni dispone de un modo de actualizacion que
omita la carga fija de catalogos. No se ha implementado la seleccion de modulos
por sucursal ni la consolidacion diaria en el futuro VPS como parte de este trabajo.

Antes de distribuir a otra sucursal: separar instalacion/actualizacion, preparar
su catalogo sin sobrescrituras, incluir todos los archivos de la version aprobada
y probarla. Antes de actualizar una sucursal operativa: respaldo verificado,
ventana de mantenimiento, migraciones revisadas y comprobacion posterior. Volver
al codigo anterior no revierte automaticamente una migracion ni recupera ventas;
la recuperacion debe considerar la compatibilidad de la base y los datos nuevos.
