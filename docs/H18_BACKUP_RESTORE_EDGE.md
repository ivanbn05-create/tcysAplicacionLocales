# H18 · respaldo integral y restauración aislada del Edge Windows

Estado: candidato de laboratorio. No cambia la tarea diaria de respaldo SQLite ni autoriza operación comercial. Este procedimiento se aplica sólo a instalaciones Windows con DB_ENGINE=sqlite y SQLITE_PATH=runtime/db.sqlite3.

## Qué contiene el paquete

respaldo-integral.ps1 -Action Backup detiene temporalmente **sólo** el servicio LosTocayosPOS que pertenece a la instalación origen, comparte el mutex del respaldo SQLite existente y crea un paquete h18-... en un volumen externo. herramientas/respaldo_integral.py usa la API de backup de SQLite, exige integrity_check=ok y foreign_key_check vacío, y calcula SHA-256 por archivo. El paquete incluye:

- runtime/db.sqlite3 completo: outbox pendiente, ACK, identidades, módulos, catálogo, terminales, impresoras, ruteo y datos operativos persistidos en SQLite;
- .env: configuración e identidades del Edge, incluidos secretos; su contenido nunca se imprime ni se agrega al manifiesto;
- archivos físicos de runtime distintos de los sidecars SQLite, media y certs;
- copias de las autoridades de confianza configuradas en CENTRAL_API_CA_BUNDLE, PEDIDOS_API_CA_BUNDLE y PEDIDOS_SUCURSALES_DB_SSLROOTCERT, incluso si el archivo original estaba fuera de certs;
- trust store **público** de firmas de release indicado por -TrustStorePath (por defecto C:\ProgramData\LosTocayosPOS\release-trust.json). H18 valida el formato, la identidad SHA-256 y que cada RSAKeyValue sólo tenga Modulus y Exponent; rechaza clave privada y exige el archivo para releases 1.x.

manifest.json enumera rutas, tamaños y hashes, la VERSION exacta del origen y los conteos de las tablas SQLite. El restore exige que VERSION en la release firmada del destino coincida exactamente antes de copiar un byte. Los hashes detectan corrupción accidental; no acreditan autoría frente a alguien que pueda modificar el volumen y el manifiesto. El código de la aplicación y .venv se reconstruyen desde una release firmada y verificada, por separado. El script no emite valores de configuración ni secretos a logs. La clave privada del publicador, los backups anteriores y los secretos de la cuenta Windows no forman parte del paquete; los archivos existentes dentro de runtime, media o certs sí se copian y deben tratarse como sensibles.

## Protección y copia externa

El paquete contiene .env en texto claro **dentro de un volumen cifrado**. Backup requiere -BackupRoot explícito en una unidad Windows distinta de la unidad del Edge, completamente cifrada con BitLocker y con protección activada. Verifica Get-BitLockerVolume antes de escribir. Rechaza UNC, unidad local compartida, junctions y estado de BitLocker no acreditable. La carpeta y cada archivo publicado reciben ACL sin herencia que permiten solamente SYSTEM y Administradores; el servicio no puede leer el paquete. No usar DPAPI ligada al equipo: impediría recuperar el Edge en otra VM.

Conservar la clave de recuperación de BitLocker fuera de la VM y bajo custodia separada. **Una letra distinta no acredita un dispositivo físico distinto**: dos particiones del mismo disco pueden pasar la comprobación del script. Antes de aceptar H18, registrar identificador/serial del disco de respaldo y del disco del Edge, acreditar que son dispositivos independientes y conservar una copia cifrada fuera de la VM y de su almacenamiento con fallo común. No copiar el paquete a correo, una carpeta compartida sin cifrar, una release ZIP o un repositorio. Verificar periódicamente que el volumen externo se puede desbloquear en otra VM.

Ejemplo en Windows PowerShell 5.1 elevado, con la unidad externa BitLocker E: desbloqueada:

~~~powershell
.\respaldo-integral.ps1 -Action Backup -BackupRoot 'E:\LosTocayosPOS-H18' -TrustStorePath 'C:\ProgramData\LosTocayosPOS\release-trust.json'
.\respaldo-integral.ps1 -Action Verify -Bundle 'E:\LosTocayosPOS-H18\h18-AAAAMMDD-HHMMSS-xxxxxxxx'
~~~

La salida JSON muestra sólo estado, ruta, número de archivos y SHA-256 de SQLite. Ni .env ni tokens se imprimen. El wrapper intenta reiniciar el servicio de origen en finally aun cuando falle el backup; confirmar su estado de forma explícita. Una publicación parcial no se anuncia como paquete válido. Backup es una operación de mantenimiento: programarla fuera del horario de venta y confirmar que el servicio regresó a Running.

## Restauración en otra VM Windows

1. Desbloquear el volumen BitLocker externo en la VM de recuperación. Preparar en una ruta nueva, por ejemplo C:\tcys-pos-restaurado, los archivos de **la misma release firmada** y sus dependencias .venv. No registrar ni iniciar allí el servicio; runtime\db.sqlite3 y .env deben estar ausentes. Mantener una copia separada de las herramientas/release en otra ruta, por ejemplo C:\h18-herramientas, desde donde se ejecutará el script.
2. Crear en la raíz nueva la marca exacta .h18-restauracion-aislada con contenido H18:ISOLATED seguido de LF. El script compara rutas y además rechaza el destino si LosTocayosPOS apunta a esa raíz.
3. Ejecutar Verify sobre el paquete externo antes del restore. Después ejecutar Restore con la raíz nueva y -RestoreTrustStorePath absoluto, nuevo y **fuera** de la instalación y del paquete; por ejemplo C:\ProgramData\LosTocayosPOS\h18-recovered\release-trust.json. La carpeta de destino del trust store debe ser nueva o tener ACL privadas SYSTEM/Administradores. El restore verifica todo el paquete, incluidas ACL de cada archivo/carpeta, rechaza archivos ajenos, hashes distintos, SQLite corrupta, enlaces, colisiones y destinos con datos previos; prepara archivos antes de publicarlos. Reescribe solamente SQLITE_PATH y las tres rutas de CA en .env para que apunten a certs\h18 de la nueva instalación. Conserva los demás valores y secretos. Aplica ACL privadas a .env, runtime, media, certs y trust store de release.
4. Pasar la ruta restaurada al actualizador en toda ejecución posterior: -TrustStorePath 'C:\ProgramData\LosTocayosPOS\h18-recovered\release-trust.json'. El actualizador H17 rechaza el trust store dentro de la instalación. En la instalación aislada, comprobar manage.py check, identidad de sucursal/Edge, módulos, conteos de outbox/ACK, impresoras/terminales, CA TLS y salud local sin enviar datos al Central. Registrar una prueba de venta e impresión sólo con dispositivos de laboratorio. No arrancar dos Edge con la misma identidad y tokens simultáneamente: mantener el origen desconectado o revocar/rotar las credenciales antes de poner la copia en línea.
5. Sólo después de la prueba aislada, preparar el procedimiento de recuperación operativa del servicio/tareas/firewall con el dueño. Este paquete por sí solo no registra un servicio nuevo ni resuelve credenciales expiradas o rutas absolutas de infraestructura ajena a los tres CA capturados.

Ejemplo de marca y restore, desde C:\h18-herramientas:

~~~powershell
[IO.File]::WriteAllText('C:\tcys-pos-restaurado\.h18-restauracion-aislada', "H18:ISOLATED" + [char]10, (New-Object Text.UTF8Encoding($false)))
.\respaldo-integral.ps1 -Action Restore -Python 'C:\h18-herramientas\.venv\Scripts\python.exe' -Bundle 'E:\LosTocayosPOS-H18\h18-AAAAMMDD-HHMMSS-xxxxxxxx' -TargetRoot 'C:\tcys-pos-restaurado' -RestoreTrustStorePath 'C:\ProgramData\LosTocayosPOS\h18-recovered\release-trust.json'
~~~

## Pruebas

python -m unittest tests.test_respaldo_integral realiza ida/vuelta con SQLite sintética, outbox pendiente, identidad, configuración, impresora, terminal, CA, trust store público de release y archivos operativos; también comprueba pérdida total de la raíz origen, VERSION discordante, trust store ausente o con clave privada, corrupción, colisiones y symlinks. Ejecutarla en el runner Windows junto con los tests de respaldo SQLite existentes. La prueba tests/test_respaldo_integral_windows.ps1 de PowerShell, BitLocker y ACL recursivas necesita Windows PowerShell 5.1 elevado y un volumen BitLocker externo; sin ese entorno se registra como pendiente, no como aprobada. La validación integral exige además una restauración aislada en Windows con una release real, manage.py check y comprobación operacional sin tocar la instalación de sucursal. Para ejecutar el harness en un runner elevado con E: externo completamente cifrado: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\test_respaldo_integral_windows.ps1 -EncryptedRoot 'E:\' -Python 'C:\h18-herramientas\.venv\Scripts\python.exe'`.

Nunca probar el restore contra C:\tcysAplicacionLocales ni contra una raíz con servicio LosTocayosPOS registrado.
