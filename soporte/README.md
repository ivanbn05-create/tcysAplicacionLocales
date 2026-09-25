# Soporte técnico local del Edge

La ruta /soporte/ usa una sesión Django de una cuenta técnica separada. La cuenta debe estar activa, tener is_staff y pertenecer al grupo soporte_tecnico_edge; un dueño POS o un superusuario sin ese grupo no recibe acceso. Para prepararla sin poner la contraseña en argumentos ni logs:

~~~powershell
.\.venv\Scripts\python.exe manage.py crear_cuenta_tecnica --username soporte_arboledas
~~~

El comando solicita y confirma la contraseña por terminal. El instalador puede usar --password-stdin desde un canal privado. El panel no ofrece shell, elevación Windows ni cambios al adaptador de red.

## Cierre de red

El panel comprueba que la IP cliente en REMOTE_ADDR pertenezca a loopback, RFC 1918 o ULA IPv6. Si WAITRESS_TRUSTED_PROXY está configurado, exige también X-Forwarded-For saneado y coincidente con REMOTE_ADDR; una cabecera ausente o discordante cierra el acceso. El servicio Windows invoca Waitress con proxy confiable exclusivamente por loopback y trusted-proxy-count=1.

El proxy de despliegue debe negar /soporte/ fuera de las redes LAN autorizadas. Ejemplo orientativo dentro del servidor Nginx local, adaptando las redes reales:

~~~nginx
location ^~ /soporte/ {
    allow 127.0.0.1;
    allow 10.0.0.0/8;
    allow 172.16.0.0/12;
    allow 192.168.0.0/16;
    allow fc00::/7;
    deny all;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Host $host;
    proxy_pass http://127.0.0.1:8000;
}
~~~

El firewall también debe limitar el listener a la LAN necesaria. DJANGO_ALLOWED_HOSTS y el certificado HTTPS deben incluir la dirección anunciada a las terminales. Guardar esa dirección en el panel registra una URL de referencia; no cambia el listener ni la IP de Windows.

## Impresoras y cola

Una Impresora es un recurso físico con nombre, dirección LAN o hostname, puerto y descripción. Una terminal puede asignar una impresora para todos sus trabajos y agregar excepciones por destino lógico. El destino describe el documento; no fija la función física de la impresora. Si no existe asignación nueva, el ruteo heredado de la terminal y después el entorno instalado siguen disponibles. Una asignación explícita a un recurso desactivado no redirige silenciosamente a otra impresora.

Cada trabajo conserva nombre, host y puerto resueltos al entrar en la cola. Cambiar la IP afecta trabajos nuevos. El sondeo abre y cierra el socket TCP sin transmitir bytes. Un trabajo TCP interrumpido por reinicio pasa a error para que soporte revise el papel antes de reintentar; desde el panel se puede reencolar con la misma ruta o con la ruta actual. El worker informa su último latido a la vista de salud.

## Gate de impresión física

El resumen técnico muestra Impresión pendiente de validar hasta que el backend sea TCP, exista al menos un recurso activo, cada terminal activa tenga una ruta general o rutas para caja/cocina/barra, cada recurso activo esté asignado y el técnico registre una prueba de papel. PRINT_BACKEND=archivo, una respuesta correcta de /salud/ o un sondeo TCP exitoso no son evidencia de impresión. El endpoint de confirmación devuelve conflicto mientras falte configuración; el acta registra cuenta técnica, fecha, referencia y huella de la topología. Un cambio de terminal, IP, puerto, estado de recurso o ruta invalida automáticamente la confirmación anterior.

Procedimiento en sitio:

1. Configurar el backend TCP en el instalador, registrar los recursos y conectar las terminales reales.
2. Crear y verificar rutas para cada terminal y destino usado. Sondear conectividad; esto no envía papel.
3. Emitir desde cada PC/tableta real una comanda y una cuenta de prueba, comprobar legibilidad y destino en cada impresora asignada. Verificar concurrencia, recurso inalcanzable, cola, reinicio y reintento únicamente después de revisar si el papel ya salió.
4. Anotar una referencia no sensible de la prueba y confirmar en el panel con la cuenta técnica. Revisar que el estado cambie a Validada. Repetir después de cambios de red, dispositivos o rutas.

La confirmación del panel es una constancia manual auditada, no una prueba automática. La habilitación del corte de Production 1.0 además requiere los demás gates operativos y de respaldo de la instalación; este panel sólo informa el de impresión.

## Validación pendiente en hardware

Antes de promover Production 1.0, validar con dos impresoras físicas y las PC/tabletas reales de Arboledas y Santa Anita: cuatro terminales repartidas entre dos recursos, trabajos simultáneos, impresora inalcanzable, reinicio durante envío, cola y reintento controlado, cambio de IP y reconexión. Las pruebas automatizadas no sustituyen estas verificaciones.

## Contrato Central

Central congeló POST /api/v1/edge/terminals/ con scope terminals:v1:write bajo el token de ingesta. El registro local de terminales de este panel aún no publica cambios a ese contrato. El enlace futuro debe ejecutarse fuera del camino crítico de venta e impresión y mantener identidad por UUID/device_id, sin unión por nombre.
