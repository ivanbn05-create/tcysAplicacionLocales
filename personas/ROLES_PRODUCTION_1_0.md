# Migración local de roles a Production 1.0

La migración `0007_production_capacidades_modulos` conserva el tipo histórico
`encargado` y sus cuatro permisos operativos registrados (`puede_cobrar`,
`puede_reimprimir`, `puede_cancelar`, `puede_sincronizar`). No lo convierte en
dueño ni le concede Administración, gestión de usuarios, cambio de PIN ajeno o
reinicio de folios. Los perfiles `elevado`, `mesero` y `repartidor` reciben su
matriz de capacidades Production 1.0. El actor protegido `es_sistema` no
autoriza una sesión humana.

En una instalación nueva, después de aprovisionar la identidad de sucursal,
ejecutar `crear_operador_pos --crear-perfil-inicial` para crear una cuenta y un
perfil de tipo `dueno` explícito. El comando solicita la contraseña de la
cuenta, comprueba el PIN y rechaza duplicados o coincidencia con la clave
maestra. No crea un segundo dueño si ya existe uno vinculado.

Antes de actualizar una base con usuarios, revisar los perfiles `encargado`,
sus cuatro flags operativos y quién será el dueño humano autorizado. Ejecutar
el mismo comando con `--crear-perfil-inicial` para crear el perfil `dueno`
explícito; puede coexistir con encargados legados no vinculados. Comprobar con
una sesión de cada rol que dueño accede a usuarios y folios, elevado opera el
panel cotidiano sin esos permisos, mesero no cobra ni cancela, repartidor no
entra a Ventas y la cuenta técnica sólo accede a `/soporte/` si pertenece al
grupo `soporte_tecnico_edge` y tiene `is_staff`.

Una sucursal actualizada sin perfil `dueno` conserva ventas operativas según
las capacidades migradas, pero no tendrá un dueño funcional local hasta que
se provisionen explícitamente su perfil y cuenta. Esta validación forma parte
del gate de actualización antes de promoción productiva.
