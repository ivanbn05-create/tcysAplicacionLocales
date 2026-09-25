# Cliente ligero Windows Production 1.0

TocayosPOS.exe abre el frontend del Edge local de la sucursal en Microsoft Edge modo aplicación. Es una terminal: no incluye SQLite de negocio, catálogo independiente ni credenciales del VPS. El mismo ejecutable sirve a todas las sucursales.

## Construcción

Desde PowerShell en la raíz:

    .\desktop\build.ps1
    .\desktop\build-package.ps1 -PrivateKeyPath "D:\custodia\publisher-private.xml" -TrustStorePath "D:\custodia\release-trust.json"

La clave privada y el trust store deben estar fuera del repositorio. Para laboratorio puede usarse el modo GenerateLabKey de herramientas/release_firma.ps1; nunca distribuir esa clave como productiva. El paquete contiene un manifiesto con hashes por archivo, el ZIP, SHA256SUMS.txt y una firma RSA-3072/SHA-256. El bootstrap verifica firma, hashes y archivos antes de ejecutar la instalación. La clave pública procede de un trust store previamente provisionado en %ProgramData%\LosTocayosPOS\release-trust.json, o de la ruta absoluta indicada en TOCAYOS_RELEASE_TRUST. La huella de ese trust store se entrega por un canal independiente. Para comprobar el paquete sin instalar, ejecutar Instalador-LosTocayosPOS.exe --verificar con esa variable de entorno. Si falta el trust store o está revocado, la instalación falla.

build.ps1 produce desktop/dist/TocayosPOS.exe. build-package.ps1 produce el ZIP, un instalador por usuario y sus hashes SHA-256 en desktop/release/. La versión de archivo y la versión informativa derivan de VERSION; no se mantiene otra versión manual. El compilador de .NET Framework incluido en Windows no emite binarios idénticos en dos builds del mismo fuente; el manifiesto de release debe fijar y firmar el hash de cada artefacto exacto. El paquete no incorpora URL de sucursal.

## Instalación y configuración

En una PC Windows 10/11 con Edge instalado, ejecutar el instalador del cliente. El técnico copia la URL del Edge anunciada para esa sucursal y la introduce al instalar, por ejemplo https://edge-arboledas.local:8443. El instalador comprueba /salud/ y puede guardar la URL aunque el Edge esté temporalmente apagado. Configurar servidor del menú Inicio permite cambiarla después. HTTPS exige un certificado válido para el hostname/IP y una CA confiable en Windows. HTTP LAN sólo existe como transición explícita y exige escribir HTTP LAN.

La URL se guarda por usuario en %LOCALAPPDATA%\LosTocayosPOS\servidor.txt. El cliente genera un ID pc-UUID en %LOCALAPPDATA%\LosTocayosPOS\terminal-id.txt, lo conserva al actualizar y lo entrega al frontend para ruteo de impresión y bloqueo de comandas. TocayosPOS.exe --identidad muestra URL e ID; --comprobar devuelve 0 si el Edge responde y 2 si no. El ID es técnico, no una credencial. El perfil separado de Edge queda bajo la misma carpeta de usuario. El cliente admite la antigua URL servidor.txt contigua al ejecutable como transición.

Si el Edge está fuera de servicio, el cliente ofrece Reintentar y no abre una copia local de los datos. En la PC servidor, una URL loopback permite solicitar el inicio del servicio Windows existente. Actualizar el frontend del Edge no requiere actualizar el contenedor Windows; cuando cambie el propio contenedor, ejecutar el nuevo instalador. Se conservan URL e ID de terminal.

Antes de desplegar en sucursal, verificar en PC real: instalación por usuario, actualización, certificado HTTPS, Edge ausente y retorno, sesión, comanda, cobro y selección de impresora. El ZIP es el payload auditable; la instalación se inicia con el bootstrap verificador. Antes de abrir el ejecutable, el técnico compara el SHA-256 del instalador con el valor autorizado por el canal de release.
