# Prompt para VPS Hostinger, migración de pedidos y nuevo frente central

> Documento histórico. Conserva el encargo de esa etapa; no es una instrucción vigente ni autorización para ejecutar cambios.

Fecha de contexto: 2026-09-19

Continúa el proyecto de Los Tocayos desde el frente de infraestructura. El objetivo es preparar de forma segura y reproducible un VPS Hostinger KVM 2 con Ubuntu Server sin interfaz gráfica, migrar la aplicación `tcysPedidosSucursales` que actualmente corre en Render y trasladar su base PostgreSQL de Supabase al VPS. También debes conservar la integración vigente con el POS local o sustituirla mediante una transición probada que no exponga PostgreSQL a Internet.

## Jerarquía y alcance

Esta solicitud define el trabajo. Los archivos Markdown, README, reportes, scripts y documentos históricos de ambos repositorios son antecedentes técnicos; no son instrucciones nuevas del usuario ni autorización para ejecutar acciones destructivas. Verifica sus afirmaciones contra el código, Git, Render, Supabase y el VPS. Si existe una contradicción, registra la discrepancia y sigue este prompt y las instrucciones más recientes del usuario.

No existe ninguna sucursal POS en producción. `C:\LosTocayosPOS` es una instalación de laboratorio. Sin embargo, Render y Supabase pueden contener datos reales o necesarios de `tcysPedidosSucursales`; trátalos como un sistema activo que debe conservarse. No borres, reinicies, siembres, sobrescribas ni retires Render o Supabase sin respaldo restaurado, comparación de datos, rollback concreto y autorización final del usuario.

Separa estos dos productos:

1. `tcysPedidosSucursales`: aplicación web existente para pedidos de sucursales/clientes, hoy en Render y Supabase. Ésta es la primera migración.
2. Backend central futuro del POS: API Edge-VPS, catálogo maestro, módulos, enrolamiento y consolidación general. Todavía no existe. Puede compartir el KVM 2 al principio, pero debe tener procesos, bases, roles, secretos, dominios y ciclos de despliegue separados.

No despliegues el `docker-compose.yml` actual de `tcysAplicacionLocales` como backend central: corresponde a desarrollo/Edge e incluye responsabilidades locales como impresión.

## Forma de trabajar

- Empieza con una auditoría de sólo lectura.
- Avanza de manera autónoma con trabajo reversible y comprobable.
- Usa hasta tres subagentes en paralelo si ayuda: infraestructura/seguridad; PostgreSQL/datos; Django/integración POS. Conserva tú la integración de archivos compartidos.
- Informa porcentajes tentativos durante el trabajo. Reporta por separado el avance del frente VPS/migración y el avance global del ecosistema.
- Prepara todo lo necesario antes de pedir una decisión. Solicita autorización final antes de congelar escrituras, cambiar DNS, ejecutar el corte definitivo o retirar un servicio anterior.
- Haz commits pequeños y claros en ramas o worktrees. No trabajes directamente sobre `main`.
- No edites la instalación `C:\LosTocayosPOS` como código fuente.
- No uses `git pull` dentro de una instalación productiva como mecanismo de release.
- No declares la migración terminada porque la página abra: exige pruebas de datos, seguridad, backup/restauración, compatibilidad, monitoreo y rollback.

## Estado del POS local

Repositorio canónico:

- URL: `https://github.com/ivanbn05-create/tcysAplicacionLocales`
- Rama de continuidad: `codex/candidata-0.4.0-dev.8`
- Worktree usado en la sesión anterior:
  `C:\Users\Srv1\.codex\visualizations\2026\09\14\01a0a104-1066-79f1-9e18-9de37c022fa6\tcys-base-work\base-a-src`
- Versión: `0.4.0-dev.8`
- Commit fuente de la release: `e7153ed1916ddf3500380b3443922dd15657ea0b`
- SHA-256 del ZIP:
  `4d29c6f845945fe5d83c44de3a00630c4df3c4fa50ab3ee4b8df6672755f1fe2`
- Commit documental confirmado antes de este traspaso:
  `cfc88c7c4c53de100645352cffdf0075ffd8f679`

Verifica el HEAD remoto al iniciar porque este documento y la confirmación física pueden haberse agregado después de ese commit.

La candidata dev.8 superó:

- 192 pruebas Django;
- 13 pruebas de respaldo SQLite;
- 5 pruebas del host Windows;
- 85 pruebas de infraestructura;
- dos construcciones reproducibles idénticas;
- actualización real del laboratorio preservando `.env` y datos;
- servicio Windows, health check, tareas y ACL;
- configuración TCP y respaldo privado de `.env`;
- una impresión física real.

El usuario confirmó que salió una sola hoja, que la impresión fue perfecta y que el contenido correspondió con lo ingresado en las comandas. La validación funcional de impresión está aprobada. Dev.8 sigue siendo candidata de laboratorio hasta que exista una decisión expresa de promoción; no reconstruyas sus artefactos con el mismo número de versión.

El Edge de cada sucursal debe abrir pedidos, cobrar e imprimir aunque Internet o el VPS fallen. Las terminales Windows/Android hablan con el Edge local; no deben depender del VPS para una venta normal.

El backend central general aún no existe. `EventoOutbox`, el contrato de acuse y las variables `VPS_CONSOLIDACION_*` son una base local, no un servidor central desplegado.

Documentos vigentes que debes leer y contrastar:

- `../../../ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md`
- `../../../FLUJO_DESARROLLO_MANTENIMIENTO_Y_CLIENTES.md`
- `../../../DESPLIEGUE_WINDOWS.md`
- `../../../PROTOCOLO_RELEASE_ACTUALIZACION_REUTILIZABLE.md`
- `../releases/EVIDENCIA_PREPARACION_RELEASE_0.4.0-dev.8.md`
- `../../../seguridad/supabase/REMEDIACION_SUPABASE_2026-08-29.md`

## Estado de `tcysPedidosSucursales`

Copia local conocida:

- Ruta: `C:\Users\Srv1\Downloads\tcysPedidosSucursales-main`
- Repositorio: `https://github.com/ivanbn05-create/tcysPedidosSucursales`
- Rama observada: `main`
- Commit observado: `ba7330bf006417cc1377b20bdcb532a4a27c37b9`
- URL histórica de Render que debe verificarse:
  `https://tcyspedidossucursales.onrender.com/`

El checkout tenía archivos sin seguimiento:

- `docs/contexto_aplicacion_pedidos_sucursales.md`
- `exports/`
- `reporte_soporte_brot_nueva_galicia_2026-09-14.txt`

No los borres, agregues a Git ni publiques sin revisar si contienen datos personales, evidencia o secretos.

Es una aplicación Django para que sucursales y clientes mayoristas capturen pedidos y para que matriz los revise, agrupe e imprima. Actualmente usa:

- Render para la aplicación;
- PostgreSQL de Supabase mediante conexión directa/pooler;
- Gunicorn y WhiteNoise;
- Python `3.13.12` según `runtime.txt`;
- APScheduler para recordatorios en Render;
- zona horaria de negocio `America/Mexico_City`.

En el VPS, `SCHEDULER_ENABLED=False` y `enviar_recordatorios` debe ejecutarse mediante un único timer de `systemd` o cron. Evita que varios workers envíen recordatorios duplicados.

El repositorio no tiene todavía un despliegue VPS reproducible. `requirements.txt` usa rangos amplios; crea un lock verificable y prueba la versión exacta antes de declarar una release. Ubuntu puede no incluir Python 3.13 de serie: decide y documenta si se usará un runtime empaquetado, contenedor o instalación controlada. No añadas un PPA ni cambies Python sin justificarlo y probarlo.

`proyecto/settings.py` conserva un `SECRET_KEY` inseguro como fallback cuando falta la variable. Un perfil VPS debe fallar de forma cerrada si no se proporciona el secreto. Configura `DEBUG=False`, hosts y orígenes CSRF exactos, cabeceras de proxy correctas, cookies seguras y HTTPS.

El README contiene credenciales de demostración en texto y el build de Render documentado ejecuta `seed_demo`. Trátalas como credenciales conocidas: inventaría usuarios, rota o deshabilita lo necesario y retira los valores del documento sin volver a mostrarlos. No ejecutes `seed_demo` sobre la base migrada salvo que antes se audite, se demuestre que es necesario e idempotente y el usuario lo autorice.

Entidades que deben conservar IDs, relaciones, decimales, historial y secuencias:

- `SucursalCliente`
- `Producto`
- `Precio`
- `Pedido`
- `ItemPedido`
- `MacroPedido`
- `Configuracion`
- `SesionActiva`
- `EventoCliente`
- usuarios, grupos, sesiones y `django_migrations`

Los subtotales usan `cantidad_por_precio`; no los reemplaces por `cantidad * precio_unitario`. El POS depende de identificadores remotos y valida nombres, así que no renumeres productos o sucursales.

## Dependencia crítica de Supabase

El POS local importa idempotentemente pedidos confirmados recientes mediante `ventas/integracion_sucursales.py`. La ruta actual:

- usa PostgreSQL directo;
- permite sólo el rol lector `pos_local_reader`;
- exige `sslmode=verify-full` y una CA local;
- consulta únicamente `pedidos_pedido`, `pedidos_sucursalcliente`, `pedidos_itempedido` y `pedidos_producto`;
- limita las sucursales remotas a IDs `1..10`;
- sólo recibe pedidos confirmados, no eliminados y dentro de su ventana temporal;
- nunca escribe en la base remota.

La remediación de Supabase dejó 20/20 tablas con RLS y cuatro políticas `pos_local_reader_select`. El esquema `public` fue retirado de Data API. El lector quedó sólo lectura, sin secuencias ni tablas sensibles.

Existe un bloqueo de migración: el código del POS rechaza expresamente hosts PostgreSQL que no terminen en `.supabase.com` o `.supabase.co`. Cambiar sólo `DATABASE_URL` o el host romperá la integración.

No publiques el puerto `5432` del VPS para eludir esta protección. La opción preferida es agregar a `tcysPedidosSucursales` una API HTTPS de sólo lectura, versionada, paginada y autenticada, con respuesta mínima, cursor estable, límites, auditoría e idempotencia; después se adapta el POS en una versión nueva. Una red privada o túnel puede servir como puente temporal si queda restringido y con fecha de retiro, pero no debe convertirse en una conexión pública permanente.

No retires Supabase hasta que la ruta sustituta se pruebe de extremo a extremo desde una instalación Edge y se compruebe la repetición idempotente. Si la API aún no está lista al mover Render, mantén Supabase como base durante esa primera etapa.

La documentación indica que un `SERVICE_ROLE` de Supabase apareció anteriormente en salida de terminal y debe tratarse como expuesto. Verifica su rotación sin imprimir su valor. También está pendiente rotar la contraseña histórica de `postgres.<project-ref>` después de coordinar todos sus consumidores.

## Acceso y manejo de secretos

El usuario proporcionará IP/host, puerto, usuario SSH y una ruta local segura a la llave cuando el VPS esté listo. No le pidas que pegue en mensajes o Git:

- llave privada;
- contraseña de Hostinger;
- códigos MFA o recuperación;
- `DATABASE_URL`;
- `SECRET_KEY`;
- contraseña SMTP;
- JWT, service role o tokens;
- contraseñas PostgreSQL.

Usa una llave SSH dedicada y coloca sólo la parte pública en el servidor. Verifica la huella del host por un canal independiente antes de aceptar el primer acceso.

No deshabilites root/contraseña, cierres la sesión original ni elimines la vía de rescate hasta crear un usuario no root, probar su llave en una segunda sesión, comprobar `sudo` y confirmar acceso de emergencia desde hPanel.

Guarda secretos en archivos de entorno protegidos, credenciales de `systemd` o un gestor apropiado. Versiona sólo `.env.example` con nombres y placeholders. La evidencia debe indicar presencia, propietario y permisos, nunca valores.

## Primer entregable: inventario de sólo lectura

Antes de instalar o cambiar nada:

1. Revisa ambos repositorios, remotos, ramas, commits, estado Git, archivos sin seguimiento, dependencias, migraciones y pruebas.
2. Inventaría el VPS:
   - versión exacta de Ubuntu;
   - CPU, RAM, swap, disco y particiones;
   - IPv4/IPv6;
   - usuarios, grupos y `sudo`;
   - actualizaciones pendientes;
   - hora, NTP y zona horaria;
   - servicios y puertos;
   - SSH;
   - firewall;
   - dominio y DNS previstos.
3. Comprueba que los recursos reales coinciden con el KVM 2 contratado; la referencia arquitectónica esperaba aproximadamente 2 vCPU, 8 GB RAM y 100 GB NVMe, pero no lo asumas.
4. Inventaría Render sin copiar secretos:
   - commit desplegado;
   - build/start command;
   - variables presentes sólo por nombre;
   - dominio/DNS;
   - instancias y workers;
   - health, logs, tareas y rollback.
5. Inventaría Supabase:
   - versión PostgreSQL y extensiones;
   - esquemas, tablas, secuencias, índices, restricciones y triggers;
   - `django_migrations`;
   - roles, grants y RLS;
   - conteos, tamaños, rangos de fechas y consumidores;
   - backups y conexiones activas;
   - servicios Supabase adicionales usados, si existen.
6. Determina la fuente de verdad. No migres `db.sqlite3` local como si fuera la base remota.
7. Inventaría media/uploads, estáticos, exportaciones y cualquier dato persistente fuera de PostgreSQL.
8. Ejecuta una línea base local de pruebas sin mutar datos remotos.
9. Entrega un informe con riesgos, supuestos, decisiones pendientes y arquitectura propuesta.

## Arquitectura inicial del VPS

Prepara configuración reproducible en Git, preferentemente bajo `deploy/vps/`, sin secretos. Decide entre contenedores y servicios `systemd` después de auditar Ubuntu y el runtime. Para esta app sencilla, `systemd` + entorno virtual + Gunicorn + proxy inverso + PostgreSQL es una opción válida; un contenedor reproducible puede ser preferible si resuelve Python 3.13 con menos variación. Documenta la decisión.

La línea base debe incluir:

- usuario de despliegue no root;
- usuario de servicio sin login;
- SSH por llave y permisos mínimos;
- firewall con SSH controlado y sólo HTTP/HTTPS públicos;
- PostgreSQL ligado a loopback o red privada, nunca a `0.0.0.0`;
- base y roles separados, SCRAM y privilegios mínimos;
- Gunicorn mediante socket Unix o loopback;
- Nginx o Caddy con TLS válido y renovación automática;
- redirección HTTPS y cabeceras proxy correctas;
- servicios con reinicio controlado y health checks;
- releases por commit/versión y rollback;
- logs en `journald` o archivos con rotación;
- parches de seguridad con política de reinicio;
- separación de `tcysPedidosSucursales` y el futuro backend central.

No instales Redis, Celery, Kubernetes u otra capa si las métricas y necesidades actuales no la justifican.

Planea DNS y TLS antes del corte. Usa primero un hostname de staging. Reduce el TTL con anticipación cuando ya exista una fecha de migración.

## Orden recomendado para reducir riesgo

Trabaja en carriles separados:

### Carril A: sacar la aplicación de Render

Despliega primero `tcysPedidosSucursales` en el VPS usando un commit fijo y, durante el piloto, conserva Supabase como base. Esto permite validar Ubuntu, Gunicorn, proxy, TLS, estáticos, sesiones y scheduler sin cambiar simultáneamente la base.

### Carril B: ensayar PostgreSQL local del VPS

Crea una base staging, restaura una copia de Supabase y compara los datos. No expongas esa base a Internet. Corrige el proceso de dump/restore, roles, secuencias y backups hasta que sea repetible.

No restaures ciegamente todos los esquemas, roles o extensiones administrados por Supabase. Tras el inventario, migra sólo los objetos de la aplicación necesarios y recrea roles mínimos propios del VPS.

### Carril C: reemplazar el lector directo del POS

Diseña la API HTTPS de pedidos y el adaptador del POS, con pruebas de contrato, autenticación, paginación/cursor, repetición idempotente y operación cuando el VPS esté temporalmente fuera de línea. Este cambio debe salir como una versión nueva del POS; no alteres dev.8.

Sólo cuando los tres carriles estén validados se programa el corte final de la base y la retirada posterior de Supabase.

## Backups y observabilidad

Implementa y prueba:

- `pg_dump` diario consistente;
- cifrado;
- copia fuera del mismo VPS;
- retención acordada;
- hash e inventario;
- alerta por backup ausente o antiguo;
- restauración completa en otra base;
- backup previo a migraciones y despliegues;
- monitoreo de HTTPS, certificado, disco, memoria, carga, PostgreSQL, servicios y antigüedad del respaldo;
- logs sin secretos ni datos personales innecesarios;
- alertas a un canal que el usuario elija.

Un backup no está validado hasta restaurarlo y comprobar datos. Las copias o snapshots de Hostinger no sustituyen una copia externa independiente.

## Staging y validación

Crea ramas o worktrees para ambos repositorios. Antes del corte:

1. Genera un dump de Supabase sin modificar el origen.
2. Restaura en PostgreSQL staging del VPS conservando PK, secuencias, timestamps y migraciones.
3. Ejecuta `migrate --plan` y luego las migraciones necesarias bajo respaldo.
4. Fija dependencias y registra versiones exactas.
5. Ejecuta:
   - `python manage.py check`
   - `python manage.py check --deploy`
   - `python manage.py test pedidos`
   - `python manage.py collectstatic --noinput`
6. Valida manualmente:
   - login de sucursal;
   - login admin;
   - operador sólo impresión;
   - captura, edición y confirmación;
   - restricciones horarias y máximo diario;
   - historial y macropedidos;
   - impresión;
   - `/api/horarios/`;
   - heartbeat y sesión única;
   - dashboard/configuración;
   - recordatorios con `--test`;
   - estáticos, responsive, HTTPS y CSRF.
7. Compara origen/destino:
   - conteos por tabla;
   - PK mínimas y máximas;
   - secuencias;
   - claves foráneas;
   - agregados monetarios;
   - estados y fechas;
   - muestras deterministas;
   - migraciones aplicadas.
8. Valida la API sustituta desde un POS de laboratorio con pedidos de prueba y repetición idempotente. No imprimas ni cobres datos reales durante esa prueba.

## Corte reversible

Redacta y revisa un runbook antes del corte. Debe contener:

1. ventana de mantenimiento;
2. comprobación de salud de Render, Supabase y VPS;
3. respaldo final y restauración acreditada;
4. suspensión temporal de escrituras para evitar split-brain;
5. dump final;
6. restore;
7. migraciones;
8. comparación de datos;
9. cambio de DNS/enrutamiento;
10. pruebas funcionales;
11. observación;
12. criterios y tiempos de rollback;
13. responsables.

Prepara todo primero y solicita autorización final justo antes de congelar escrituras, hacer el dump definitivo y cambiar tráfico.

Mantén Render y Supabase intactos durante una ventana de rollback acordada. No elimines proyectos, roles, bases, backups o DNS anteriores al terminar el primer corte. Si falla la validación, devuelve el tráfico al sistema anterior y conserva la evidencia. No permitas que dos bases acepten escrituras independientes.

La migración de Supabase no termina hasta que el POS use la ruta sustituta o exista un puente temporal seguro y explícito.

## Entregables

Genera, según corresponda:

- inventario inicial fechado;
- diagrama de arquitectura;
- registro de decisiones;
- configuración reproducible bajo `deploy/vps/`;
- `.env.example` sin secretos;
- lock de dependencias;
- runbook de aprovisionamiento;
- runbook de despliegue y rollback;
- política SSH/firewall;
- procedimiento de backup/restauración;
- evidencia de restauración;
- plan y evidencia de migración;
- comparación de datos;
- smoke tests;
- matriz Render/Supabase/VPS antes/después;
- contrato y plan de la API para el POS;
- observabilidad y alertas;
- lista de secretos a rotar, sólo por nombre;
- commits, ramas y riesgos pendientes.

No incluyas dumps, SQLite, exportaciones con datos, `.env`, certificados privados, llaves o logs sensibles en Git.

## Avance global inicial

El avance global tentativo del ecosistema completo es **58%**, con un rango razonable de **55% a 60%** según el alcance final del backend central y los módulos.

Referencia por frente:

- actualización dev.8 en su alcance: 100%;
- base local Windows/Edge en laboratorio: aproximadamente 90%;
- UX y funcionalidad base: aproximadamente 85%;
- instalación y actualización: aproximadamente 85%;
- impresión física: 100%;
- clientes `.exe`/PWA/APK: aproximadamente 40%;
- módulos opcionales por sucursal: aproximadamente 30–35%;
- VPS, consolidación y esta migración: aproximadamente 5–10%;
- pruebas, operación y seguridad del producto completo: aproximadamente 50–55%.

Los principales bloques restantes son el APK firmado y probado, el cliente Windows final, validaciones en otros equipos, modularidad completa, VPS endurecido, migración de pedidos, API Edge-VPS, catálogo y enrolamiento centrales, consolidación idempotente, canal firmado de actualizaciones, backups externos y observabilidad.

Al informar avances, no confundas haber terminado la migración de `tcysPedidosSucursales` con haber terminado el backend central del POS ni el producto completo.
