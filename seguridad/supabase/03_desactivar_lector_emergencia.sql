-- Corte de emergencia. Primero confirma el bloqueo y la revocación; después
-- intenta terminar sesiones. Así, un fallo al terminar una conexión no revierte
-- la contención ya aplicada ni restaura grants públicos inseguros.
BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SELECT pg_advisory_xact_lock(hashtext('tocayos_supabase_endurecimiento_v2'));

ALTER ROLE pos_local_reader NOLOGIN;

REVOKE SELECT (
    id, sucursal_cliente_id, codigo_publico, estado, fecha_confirmacion, eliminado
) ON TABLE public.pedidos_pedido FROM pos_local_reader;
REVOKE SELECT (id, nombre)
    ON TABLE public.pedidos_sucursalcliente FROM pos_local_reader;
REVOKE SELECT (id, pedido_id, producto_id, cantidad, precio_unitario)
    ON TABLE public.pedidos_itempedido FROM pos_local_reader;
REVOKE SELECT (id, nombre, nombre_ticket)
    ON TABLE public.pedidos_producto FROM pos_local_reader;

REVOKE USAGE ON SCHEMA public FROM pos_local_reader;

DO $revocar_conexion$
BEGIN
    EXECUTE format(
        'REVOKE CONNECT ON DATABASE %I FROM pos_local_reader',
        current_database()
    );
END
$revocar_conexion$;

COMMIT;

-- Segunda fase deliberadamente fuera de la transacción anterior. Si el
-- ejecutor no puede terminar alguna sesión, NOLOGIN y los REVOKE permanecen.
SELECT pid,
       pg_terminate_backend(pid) AS terminada
  FROM pg_stat_activity
 WHERE usename = 'pos_local_reader'
   AND pid <> pg_backend_pid();

-- Después del incidente: vuelve a ejecutar 01_endurecer_rol_pos.sql, rota la
-- contraseña mientras el rol sigue en NOLOGIN, habilita LOGIN por un canal
-- seguro y ejecuta inmediatamente 02_verificar_endurecimiento.sql. Si falla,
-- aplica otra vez este corte.
