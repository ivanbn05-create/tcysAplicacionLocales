# Producto

<!-- impeccable:product-schema 1 -->

## Plataforma

web

## Usuarios

- Personal de caja y meseros que capturan pedidos con rapidez durante horas de servicio. *(Inferido del flujo y pendiente de confirmación explícita.)*
- Encargados que supervisan cobros, cancelaciones, reimpresiones y sincronización de sucursales. *(Inferido de los permisos existentes.)*
- Administradores que más adelante gestionarán catálogo, personal, sucursales y reportes desde una superficie separada pero coherente. *(Confirmado por la solicitud; alcance funcional aún abierto.)*

## Propósito del producto

Operar localmente el punto de venta de Los Tocayos: abrir pedidos de comedor, domicilio y sucursales; capturar productos y preparación por comensal; procesar, cobrar e imprimir comandas y cuentas sin depender de internet para la operación principal.

El éxito operativo significa que una persona pueda identificar mesa/canal, capturar la orden correcta, revisar la comanda y cerrar el cobro con el mínimo de duda y retrabajo.

## Posicionamiento

El producto adapta el trabajo real de Los Tocayos -mesas, comensales, preparación, entrega, clientes recurrentes, pedidos de sucursales e impresión térmica- a una interfaz táctil local; no es un panel administrativo genérico ni un comercio electrónico de propósito general.

## Contexto de operación

- Restaurante con ritmo alto, ruido ambiental y decisiones repetitivas de corta duración. *(Inferido; pendiente de confirmación.)*
- Uso principal en PC de caja y tabletas, con teclado, ratón y entrada táctil.
- La continuidad local y la lectura rápida importan más que la ornamentación.
- La comanda térmica es el artefacto que conecta captura, cocina/barra y cobro.

## Capacidades y restricciones

- Conservar los flujos actuales de comedor, domicilio, sucursales, clientes, preparación, cobro, impresión y permisos.
- Funcionar como PWA y dentro del contenedor de escritorio de Windows.
- Mantener objetivos táctiles amplios, navegación por teclado, zoom del navegador y adaptación a escritorio/tableta.
- No inventar precios, productos, promociones, testimonios ni capacidades.
- Mantener la operación diaria separada del futuro módulo de administración.
- No depender de fuentes, iconos ni recursos remotos durante la operación.

## Compromisos de marca

- Nombre: Los Tocayos - Tacos de Barbacoa.
- Manual vinculante: `C:\Users\Srv1\Downloads\MANUAL DE IDENTIDAD TOCAYOS.pdf`.
- Valores comunicados: tradición, energía, cercanía, frescura, higiene, calidad, servicio y precio justo.
- Logotipo tipográfico sin deformación, rotación, recoloración, sombras, relieve ni contornos ajenos a sus variantes autorizadas.
- Amarillo corporativo `#FFED00`, verde `#4AA736`, rojo `#E42522` y carbón `#353436`.
- Bebas Neue para voz de marca/display y Montserrat Semibold para descriptor y lectura funcional.
- Los recursos deben permanecer empaquetados localmente.

## Evidencia disponible

- Manual de identidad de 18 páginas con logotipo, variantes, área de seguridad, tipografías, colores, restricciones y aplicaciones.
- Logotipo actual en `ventas/static/ventas/brand/logoactual.jpeg`.
- Montserrat Medium y Semibold en `ventas/static/ventas/fonts/`.
- Flujos implementados y pruebas funcionales en `ventas/`.
- No hay aún una especificación funcional confirmada del módulo administrador; futuros agentes no deben inventarla.

## Principios de producto

1. La siguiente acción operativa debe distinguirse en menos de un vistazo.
2. La marca debe sentirse en la estructura, tipografía y color, no en efectos decorativos.
3. El sistema debe prevenir errores de pedido y cobro antes de pedir confirmaciones.
4. La densidad debe servir a la velocidad sin sacrificar objetivos táctiles ni legibilidad.
5. Operación y administración comparten lenguaje visual, pero mantienen navegación y jerarquías separadas.

## Accesibilidad e inclusión

Objetivo mínimo: WCAG 2.2 AA en contraste, foco visible, semántica, teclado y estados no comunicados sólo por color. La interfaz debe seguir siendo operable con zoom al 200 %, movimiento reducido y objetivos táctiles de al menos 44 x 44 CSS px en los flujos principales.
