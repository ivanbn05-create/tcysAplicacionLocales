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

Si la dirección configurada es local y el servidor no responde, el programa solicita a
Windows iniciar el servicio `LosTocayosPOS`. No ejecuta migraciones ni crea un proceso
`runserver`/Waitress fuera del servicio. La instalación inicial del servidor se hace
una vez, como administrador, con `instalar-servicio-lan.ps1` desde la raíz del proyecto.

Para abrir directamente la interfaz táctil a pantalla completa:

```powershell
.\desktop\dist\TocayosPOS.exe --tableta
```

Para solicitar el inicio del servicio y comprobarlo sin abrir la interfaz:

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
Por seguridad sólo se acepta un origen `http://host:puerto` o `https://host:puerto`, sin
credenciales, ruta, consulta ni fragmento.

## Paquete para otras computadoras

```powershell
.\desktop\build-package.ps1 -ServerUrl "https://pos.tocayos.local"
```

Genera en `desktop\release\` un ZIP, un instalador autoextraíble y sus sumas SHA-256.
La instalación se realiza por usuario, no requiere permisos de administrador, crea
accesos directos y registra un desinstalador en Windows. La dirección puede modificarse
desde **Menú Inicio > Los Tocayos POS > Configurar servidor**.

Si durante una transición se empaqueta una URL HTTP, debe declararse de forma
consciente con `-AllowInsecureHttp`. Además, el instalador y el configurador exigirán
escribir `HTTP LAN` antes de guardar una URL sin cifrar; no basta con aceptar el valor
predeterminado.

En la computadora principal, el servicio usa Waitress, cuenta `LocalService`, ACL
restringidas y recuperación automática. El paquete de clientes no contiene la base ni
credenciales del servidor. Sigue siendo recomendable firmar el instalador y usar un
origen HTTPS antes de distribuirlo fuera de una LAN controlada.
