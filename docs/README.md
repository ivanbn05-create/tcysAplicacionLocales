# Documentación de Los Tocayos POS

Este índice separa la documentación vigente de los antecedentes. El repositorio y la candidata 0.4.0-dev.10 son exclusivamente de laboratorio: no existe una sucursal en producción y estos documentos no autorizan un corte, despliegue ni cambio sobre una instalación real.

## Vigente

- [README principal](../README.md): punto de entrada al producto y al entorno Edge Windows.
- [Despliegue Windows](../DESPLIEGUE_WINDOWS.md): instalación, actualización, respaldo y recuperación del Edge.
- [Arquitectura multisucursal](../ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md): dirección arquitectónica.
- [Flujo de desarrollo y mantenimiento](../FLUJO_DESARROLLO_MANTENIMIENTO_Y_CLIENTES.md): ciclo de desarrollo, distribución y clientes.
- [Protocolo de release](../PROTOCOLO_RELEASE_ACTUALIZACION_REUTILIZABLE.md): preparación, validación y rollback.
- [Integración, operación y validación 0.4.0-dev.10](candidatas/INTEGRACION_OPERACION_Y_VALIDACION_0.4.0-dev.10.md): mapa técnico actual, dependencias, consumidores legacy y runbook de laboratorio.
- [Evidencia preliminar 0.4.0-dev.10](candidatas/EVIDENCIA_PREPARACION_RELEASE_0.4.0-dev.10.md): plantilla honesta que el integrador debe completar tras ejecutar las pruebas.
- [Contratos Edge–Central](../contracts/edge-central/README.md): OpenAPI, esquemas, fixtures y handoff para agente1.

## Histórico

Las evidencias en [histórico de releases](historico/releases/) registran lo ocurrido con dev.3, dev.4, dev.6, dev.8 y dev.9. Son antecedentes inmutables; no describen el estado actual.

Los prompts y traspasos en [histórico de handoffs](historico/handoffs/) conservan decisiones y solicitudes previas. No son instrucciones ejecutables ni sustituyen una solicitud actual del usuario.

El [diagnóstico histórico del POS local](auditorias/diagnostico_calidad_pos_local.md) conserva hallazgos de agosto de 2026. Varias brechas fueron corregidas después y no debe usarse como lista vigente.

## Regla de lectura

Para operar o cambiar el sistema se consulta primero la documentación vigente. Los documentos históricos sirven para explicar decisiones, incidentes y evidencia anterior. Si existe una discrepancia, prevalecen el código de la revisión examinada, los contratos vigentes y la instrucción actual del usuario.
