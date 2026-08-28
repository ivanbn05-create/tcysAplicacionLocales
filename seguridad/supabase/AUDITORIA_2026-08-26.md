# Auditoría remota de Supabase — 26 de agosto de 2026

La inspección se ejecutó en modo de sólo lectura, con TLS `verify-full` y una transacción confirmada como `READ ONLY`. No se consultaron filas de negocio ni se ejecutó DDL/DML.

## Resultado

- La alerta es real y crítica: las 20 tablas de `public` tienen RLS desactivado, cero políticas y privilegios de lectura/escritura para `anon` y `authenticated`.
- Las 19 secuencias de la aplicación también conceden privilegios a esas identidades.
- `sensitive_columns_exposed` coincide con `auth_user.password`, `django_session.session_key` y `pedidos_sesionactiva.token`.
- El rol histórico `postgres` conserva `CREATEDB`, `CREATEROLE`, `REPLICATION`, `BYPASSRLS` y membresías privilegiadas.
- `pos_local_reader` todavía no existe en el proyecto remoto.
- No se encontraron vistas públicas ni funciones `SECURITY DEFINER` en `public` durante la inspección.
- La evidencia confirma exposición posible; no demuestra por sí sola que ya haya ocurrido una intrusión.

## Contención y corrección

1. Si ningún consumidor necesita Data API, retirar `public` de **Exposed schemas** de inmediato.
2. Respaldar y ejecutar `00_inventario_previo.sql`; identificar consumidores de `anon`, `authenticated` y `service_role`.
3. Confirmar que las sucursales autorizadas sean exactamente los IDs `1..10` y probar `01_endurecer_rol_pos.sql` en staging.
4. Aplicar el script en una ventana de mantenimiento. El script cierra `PUBLIC`, `anon` y `authenticated`, habilita RLS y concede al lector sólo cuatro tablas y columnas explícitas.
5. Activar `pos_local_reader` con un secreto nuevo, configurar TLS `verify-full` y ejecutar `02_verificar_endurecimiento.sql`.
6. Repetir Security Advisor y rotar la contraseña histórica de `postgres` después de migrar todos sus consumidores.
7. Tratarlo como posible exposición previa: invalidar sesiones/tokens, revisar administradores y cambios inesperados, y decidir si procede restablecer contraseñas.

Los controles de red, MFA, API keys, logs, backups/PITR y el rechazo de conexiones sin TLS deben verificarse desde el Dashboard; no son observables por la conexión SQL usada para esta auditoría.

## Estado de la remediación en el repositorio

Se prepararon scripts fail-closed para inventariar, endurecer, comprobar y cortar
de emergencia el acceso del lector. `02_verificar_endurecimiento.sql` comprueba
sin leer datos de negocio: 20/20 tablas con RLS, cuatro políticas exclusivas de
`pos_local_reader`, ausencia de permisos de `PUBLIC`/`anon`/`authenticated`,
columnas exactas del lector y cierre de secuencias, vistas, rutinas y privilegios
predeterminados.

No se ejecutó DDL remoto como parte de esta preparación. Por tanto, el estado
remoto crítico descrito arriba debe considerarse vigente hasta que exista
evidencia de ejecución satisfactoria del verificador y una nueva revisión de
Security Advisor.
