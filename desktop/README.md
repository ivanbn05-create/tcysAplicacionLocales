# Aplicación de escritorio

`TocayosPOS.exe` presenta el punto de venta en una ventana independiente de Windows.
No duplica el frontend: carga el mismo Django local que usan las terminales y tabletas,
por lo que una actualización del sistema se aplica a todos los clientes de la sucursal.

## Construcción

Desde PowerShell, en la raíz del proyecto:

```powershell
.\desktop\build.ps1
```

El resultado queda en `desktop\dist\TocayosPOS.exe`. En esta primera versión el
ejecutable usa el motor instalado de Microsoft Edge en modo aplicación; la ventana no
muestra pestañas ni barra de direcciones y no requiere instalar un runtime adicional.

## Ejecución

```powershell
.\desktop\dist\TocayosPOS.exe
```

Si Django no está activo, el programa aplica las migraciones pendientes e inicia el
servidor local en el puerto 8000. La primera instalación de dependencias todavía se hace
una sola vez con `iniciar-local.ps1`.

Para abrir directamente la interfaz táctil a pantalla completa:

```powershell
.\desktop\dist\TocayosPOS.exe --tableta
```

Para iniciar únicamente el servicio de la sucursal:

```powershell
.\desktop\dist\TocayosPOS.exe --solo-servidor
```

`TocayosPOS.exe --comprobar` valida silenciosamente la conexión configurada y devuelve
código cero cuando el servidor responde; se utiliza para diagnosticar los paquetes.

El valor opcional `TOCAYOS_SERVER_URL` permite que el ejecutable sea sólo un cliente de
otro servidor de la red. Por ejemplo:

```powershell
$env:TOCAYOS_SERVER_URL = "http://192.168.0.30:8000"
.\desktop\dist\TocayosPOS.exe --tableta
```

El cliente también lee `servidor.txt` junto al ejecutable. Este archivo es el mecanismo
usado por el paquete distribuible y evita configurar variables de entorno manualmente.

## Paquete para otras computadoras

```powershell
.\desktop\build-package.ps1 -ServerUrl "http://192.168.0.30:8000"
```

Genera en `desktop\release\` un ZIP, un instalador autoextraíble y sus sumas SHA-256.
La instalación se realiza por usuario, no requiere permisos de administrador, crea
accesos directos y registra un desinstalador en Windows. La dirección puede modificarse
desde **Menú Inicio > Los Tocayos POS > Configurar servidor**.

Para la entrega final a sucursales se debe sustituir el servidor de desarrollo por un
servicio de Windows, PostgreSQL, copias de seguridad y un instalador firmado. El shell
de escritorio y toda la aplicación web se conservan sin reescritura.
