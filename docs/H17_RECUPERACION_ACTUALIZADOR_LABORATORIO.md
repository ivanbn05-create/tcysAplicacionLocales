# H17 — recuperación del actualizador Windows de laboratorio

Estado: candidata técnica. **No se ha probado con corte eléctrico real, VM ni instalación de sucursal.** El workflow Windows ejecuta sólo fixtures temporales y no toca el servicio instalado.

## Journal y exclusión

El actualizador conserva `actualizacion-pendiente.json` en el workspace privado adyacente a la instalación y fuera del árbol que se reemplaza. Lo escribe con `FileStream.Flush(true)` y publica cada cambio mediante movimiento o reemplazo atómico en el mismo volumen. El workspace exige ACL exclusiva SYSTEM/Administradores. El mutex global de mantenimiento cubre recuperación, verificación, staging y conmutación; el mutex de respaldo cubre la copia del estado SQLite hasta antes de invocar el motor oficial. Se bloquean las tareas administradas durante la conmutación y se restablecen desde XML guardado en el journal cuando se restaura la versión anterior.

El respaldo anterior conserva el árbol completo: `VERSION`, `.env`, `runtime/db.sqlite3` y sus WAL/SHM, outbox persistente, `.venv`, medios y backups. No se borra el respaldo. La recuperación valida que el journal pertenezca a la instalación, que sus rutas sean las previstas, que no haya enlaces de directorio, y que `VERSION` y el hash de `.env` anterior coincidan antes de renombrar un árbol.

## Recuperación

Ejecutar una copia **confiable del script fuera de la instalación**, desde PowerShell elevado. El mismo comando original recupera el journal pendiente antes de leer la release; para recuperar sin iniciar otra actualización:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Releases\actualizar-laboratorio-desde-release.ps1 -RecoverOnly -InstallationRoot C:\LosTocayosPOS
```

Si se usó `-WorkspaceRoot` personalizado, pasar exactamente la misma ruta. Revisar el archivo de journal y los árboles respaldados antes de cualquier acción manual. No borrar ni mover esos árboles para desbloquear un intento.

- **Antes de invocar el motor de migraciones:** si el respaldo existe, se restaura y la candidata parcial pasa a `fallida-...`; se recuperan tareas, modo de inicio y salud anterior. Un reintento es idempotente.
- **Motor en ejecución al caer:** si se verifica la identidad del servicio, se intenta detenerlo, poner inicio manual y deshabilitar tareas administradas independientemente entre sí. El journal y ambos árboles permanecen. Puede haber una migración parcial o nuevas ventas; se requiere conciliación asistida del SQLite/outbox. No hay rollback automático.
- **Motor terminado, health externo pendiente:** si la candidata, `.env` y `/salud/` pasan, se conserva como activa y se cierra el journal. Si falla, se detiene el servicio y se conservan ambos árboles. Reintentar `-RecoverOnly` sólo tras resolver la causa; no sobreescribir bases.
- **Identidad del servicio no verificable:** no se detiene un servicio que podría pertenecer a otra instalación; se informa que la candidata podría seguir activa y se exige intervención manual inmediata.
- **Después de cerrar journal:** no hay acción pendiente. Los journals cerrados quedan archivados en el workspace privado.

Si se corta la energía justo entre el retorno del motor y el registro `engine_complete`, el estado observable será `engine_running` y se tratará como ambiguo. Esto es deliberado: prioriza conservar ventas y ACK/outbox sobre disponibilidad automática.

## Validación restante

El workflow `H17 actualizador Windows aislado` analiza PowerShell y simula, bajo `RUNNER_TEMP`, el corte antes del stop, durante la conmutación, antes del motor, durante el motor, tras el motor y un health fallido/reintentado. Los fixtures contienen SQLite real con venta y outbox UUID; se comprueba `PRAGMA integrity_check` y la identidad de ambos registros después de recuperar. Cinco casos crean journal y árboles en un PowerShell hijo, matan el proceso y reanudan en otro PowerShell; también se prueba que un fallo de tareas no impida intentar detener el servicio. La certificación requiere una VM Windows desechable con servicio y SQLite reales, cortes de proceso y energía en los mismos puntos, verificación de tareas/ACL, ventas/outbox antes y después, rollback, servicio y dos terminales. Hasta esa ejecución H17 no debe marcarse verde ni usarse para producción.
