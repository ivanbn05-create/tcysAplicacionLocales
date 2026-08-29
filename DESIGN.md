---
name: "Los Tocayos · Punto de venta"
description: "Pase de cocina digital, rápido y táctil, con identidad Los Tocayos y comandas fieles al papel térmico."
colors:
  brand-yellow: "#ffed00"
  brand-yellow-soft: "#fff9b8"
  state-open: "#f2c500"
  state-open-surface: "#fff7c8"
  state-open-ink: "#795a00"
  state-free: "#4aa736"
  state-free-ink: "#2f7a26"
  state-free-surface: "#e9f5e6"
  state-processed: "#e42522"
  state-processed-surface: "#fde9e8"
  charcoal: "#353436"
  charcoal-soft: "#535154"
  rail: "#242629"
  muted: "#625f62"
  paper: "#f8f7f5"
  paper-inactive: "#eeece8"
  canvas: "#e9e7e1"
  focus: "#0066cc"
typography:
  display:
    fontFamily: '"Bebas Neue Tocayos", "Arial Narrow", sans-serif'
    fontSize: "clamp(2.8rem, 5vw, 3.8rem)"
    fontWeight: 400
    lineHeight: 0.82
    letterSpacing: "0.01em"
  brand:
    fontFamily: '"Bebas Neue Tocayos", "Arial Narrow", sans-serif'
    fontSize: "clamp(2.1rem, 3.8vw, 2.7rem)"
    fontWeight: 400
    lineHeight: 0.78
    letterSpacing: "0.01em"
  title:
    fontFamily: '"Bebas Neue Tocayos", "Arial Narrow", sans-serif'
    fontSize: "1.25rem"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "0.02em"
  body:
    fontFamily: '"Montserrat Tocayos", Montserrat, system-ui, sans-serif'
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.35
    letterSpacing: "normal"
  label:
    fontFamily: '"Montserrat Tocayos", Montserrat, system-ui, sans-serif'
    fontSize: "0.75rem"
    fontWeight: 800
    lineHeight: 1.15
    letterSpacing: "0.08em"
rounded:
  none: "0"
  crisp: "2px"
  restrained: "6px"
spacing:
  xxs: "4px"
  xs: "8px"
  sm: "10px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  compact-header:
    backgroundColor: "{colors.brand-yellow}"
    textColor: "{colors.charcoal}"
    height: "62px"
    padding: "5px clamp(14px, 2.4vw, 34px)"
    rounded: "{rounded.none}"
  typed-brand-signature:
    backgroundColor: "transparent"
    textColor: "{colors.charcoal}"
    typography: "{typography.brand}"
    padding: "0"
    rounded: "{rounded.none}"
  channel-ticket:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.charcoal}"
    typography: "{typography.title}"
    height: "70px"
    padding: "10px clamp(14px, 2vw, 24px) 11px"
    rounded: "{rounded.none}"
  position-free:
    backgroundColor: "#ffffff"
    textColor: "{colors.state-free-ink}"
    height: "118px"
    padding: "10px 12px"
    rounded: "{rounded.crisp}"
  position-open:
    backgroundColor: "{colors.state-open-surface}"
    textColor: "{colors.state-open-ink}"
    height: "118px"
    padding: "10px 12px"
    rounded: "{rounded.crisp}"
  position-processed:
    backgroundColor: "#ffffff"
    textColor: "{colors.state-processed}"
    height: "118px"
    padding: "10px 12px"
    rounded: "{rounded.crisp}"
  product-card:
    backgroundColor: "#ffffff"
    textColor: "{colors.charcoal}"
    height: "82px"
    padding: "0"
    rounded: "{rounded.crisp}"
  action-primary:
    backgroundColor: "{colors.brand-yellow}"
    textColor: "{colors.charcoal}"
    typography: "{typography.label}"
    height: "44px"
    padding: "8px 14px"
    rounded: "{rounded.crisp}"
  admin-rail:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.paper}"
    width: "244px"
    padding: "24px 0 18px"
    rounded: "{rounded.none}"
  pending-ticket:
    backgroundColor: "#ffffff"
    textColor: "{colors.charcoal}"
    typography: "{typography.title}"
    padding: "14px"
    rounded: "{rounded.none}"
  admin-tool-track:
    backgroundColor: "#ffffff"
    textColor: "{colors.charcoal}"
    height: "58px"
    padding: "10px 13px"
    rounded: "{rounded.none}"
---

# Design System: Los Tocayos · Punto de venta

## Overview

**Creative North Star: “El pase de cocina digital”**

La interfaz traduce el trabajo físico del restaurante —pase, tickets, clips, papel térmico y marcadores de estado— a una superficie operativa de alta densidad. Debe sentirse propia de Los Tocayos: enérgica, franca y táctil; la marca vive en la estructura amarilla, la firma tipográfica bicolor, la tipografía condensada, las reglas de carbón y las decisiones de color, no en efectos decorativos.

La referencia de composición aprobada del POS es `.impeccable/mocks/pos-pase-cocina-b-hibrida.png`; la del Administrador local es `.impeccable/mocks/decision/admin-bandeja-pendientes.png`. El Administrador no reemplaza ni convierte el POS en un dashboard: extiende el mismo mundo con una estación separada de bandejas, tickets y herramientas. La comanda virtual conserva la topología y la sobriedad de la comanda impresa; el resto de cada superficie puede evolucionar dentro de este sistema y de su brief.

**Key Characteristics:**

- Pase de cocina, no dashboard genérico.
- Cabecera amarilla compacta y riel carbón con etiquetas de canal tipo ticket/clipboard.
- Administración separada como bandeja de pendientes, con riel propio y papel operativo.
- Densidad táctil, lectura inmediata y objetivos principales de al menos 44 CSS px.
- Estado siempre expresado con color, texto y marca visual.
- Recursos, fuentes y miniaturas disponibles sin internet.

## Colors

La paleta combina identidad cálida con papel y carbón; los colores de estado son semánticos y no se intercambian por preferencias decorativas.

### Primary

- **Amarillo de marca:** barra de identidad, selección primaria y acentos de clip.
- **Carbón de trabajo:** texto, reglas, estructura y controles oscuros.

### Secondary

- **Verde libre:** disponibilidad y confirmación; el tono oscuro se usa para texto pequeño.
- **Amarillo orden abierta:** superficie, marca superior y número de toda orden aún abierta; siempre lleva tinta oscura legible.
- **Rojo procesado:** pedido ya procesado, cancelación o urgencia explícita. No identifica órdenes abiertas.

### Neutral

- **Papel térmico:** superficies operativas y comanda.
- **Riel oscuro:** fondo de navegación y barra de estado.
- **Lienzo cálido:** fondo detrás de las superficies de trabajo.
- **Tinta secundaria:** ayuda y metadatos que conservan contraste AA.

**The Three-State Rule.** Libre es verde, Orden abierta es amarilla y Procesada es roja. Cada estado conserva etiqueta visible y nombre accesible; el color nunca actúa solo.

**The Red Means Resolved-or-Urgent Rule.** El rojo queda reservado para procesado, cancelación, error o acción destructiva; no se usa como decoración abundante.

## Typography

**Display Font:** Bebas Neue Tocayos (con Arial Narrow y sans-serif como respaldo)

**Body Font:** Montserrat Tocayos (con Montserrat, system-ui y sans-serif como respaldo)

**Character:** Bebas Neue aporta la voz frontal de letrero y ticket; Montserrat sostiene lectura funcional rápida. Ambas se sirven localmente y no dependen de CDN.

### Hierarchy

- **Display:** números de mesa o posición; cifras tabulares, gran contraste y línea compacta.
- **Title:** nombres breves de canal, zona y panel; preferentemente en mayúsculas y nunca para instrucciones largas.
- **Body:** nombres de producto, formularios, mensajes y datos de cliente; longitud controlada y lectura natural.
- **Label:** estados, abreviaturas y metadatos; peso alto y espaciado amplio sólo en frases breves.

**The Two-Voices Rule.** Bebas Neue nombra y numera; Montserrat explica y permite operar. No se introducen nuevas familias tipográficas sin actualizar este sistema.

## Layout

La aplicación sigue una secuencia vertical: cabecera de marca compacta, riel de canales y estación de posiciones. No usa un pie de página operativo: cada posición comunica su estado dentro de la propia tarjeta, con texto, símbolo y color. La cabecera no aloja telemetría pasiva de conexión o impresora. Entre la sucursal y los controles muestra al mesero realmente identificado por PIN; el bloque se oculta al cerrar su modo de operación y nunca sustituye ese nombre por la cuenta técnica de Django. En la vista de posiciones reserva las acciones de ventana y `Salir`; al entrar a captura, `Salir` desaparece y la navegación vuelve a depender de la acción contextual `Volver`.

Las etiquetas Comedor, Domicilio y Sucursales son tickets/clipboards horizontales de papel con esquinas superiores cortadas, agrupados a la derecha del riel y sin ocupar todo su ancho en escritorio. Cada uno usa un icono SVG Tabler a la izquierda y un clip centrado; el seleccionado vuelve rojo el clip y añade una regla inferior roja cuya muesca triangular apunta hacia el contenido. La estación usa una rejilla de siete columnas entre 701 px y escritorio: la zona principal ocupa cinco y Recoger/Llevar dos. Ambas heredan el mismo `subgrid`, por lo que toda tarjeta principal y auxiliar tiene exactamente el mismo ancho, alto, separación y padding. Bajo 700 px ambas zonas ocupan dos columnas y se apilan sin deformar las tarjetas.

Las sucursales se organizan como columnas desplazables; sus encabezados y textos internos usan una escala compacta para evitar saturación. El flujo de captura mantiene tres zonas funcionales —comensal, catálogo y comanda—: el riel de comensales conserva 62 px y, en disposición horizontal, el espacio restante se reparte aproximadamente 60 % para catálogo y 40 % para comanda. La comanda puede compactar sus pistas, pero nunca alterar el orden ni el lenguaje de la impresión térmica. Bajo 700 px las tres zonas se apilan sin ocultar funciones.

El catálogo usa tarjetas horizontales de dos mitades exactas. La izquierda muestra un WebP local con `object-fit: cover`; la derecha contiene abreviatura y nombre. En PC de operación hasta 1180 px y en `/tabletas` se muestran dos productos por fila. El encabezado del menú completo ofrece siete atajos numerados, en el mismo orden del catálogo, que desplazan el panel a Taco, Promoción, Consomé y Barbacoa, Lonches, Gringas y Quesadillas, Bebidas y Postre. La imagen administrada en `Producto.imagen` tiene prioridad. Si no existe, se consulta el mapa estático de 25 WebP en `ventas/static/ventas/menu/`; un código sin asignación usa `mainlogo.webp`. Los 25 recursos forman parte del precache del service worker para operación offline.

El Administrador local mantiene la cabecera de marca y abre una estación independiente: riel carbón fijo de 244 px y contenido de papel. Su inicio es una bandeja 2:1, con Domicilios sin repartidor y Programados en la columna principal, Cierres pendientes en la secundaria y una pista de herramientas no urgentes después de la bandeja. La pista pertenece al contenido de Inicio; no es un footer operativo persistente. Los totales monetarios del turno aparecen únicamente en Reportes y corte de caja, nunca en Inicio, el riel o los tickets de pendiente.

A 1040 px o menos el riel administrativo se compacta a iconos y etiquetas breves. A 760 px o menos se vuelve una pista horizontal desplazable y la bandeja se apila en el orden de decisión **Domicilios → Cierres → Programados**; la pista inferior pasa de cuatro a dos columnas. Este orden de lectura se conserva con zoom y en toda presentación de una sola columna.

## Elevation & Depth

El sistema es plano por defecto. La separación proviene de campos tonales, reglas de uno o dos píxeles, cortes de ticket y contraste entre papel y riel. Las sombras sólo aparecen en capas que realmente se elevan —diálogos, avisos persistentes y la hoja de comanda sobre su bandeja— y nunca convierten cada elemento en una tarjeta flotante.

### Shadow Vocabulary

- **Capa modal:** sombra ambiental amplia para separar un diálogo del flujo bloqueado.
- **Aviso persistente:** sombra media para que un error accionable permanezca encontrable.
- **Hoja térmica:** sombra corta y suave, equivalente a papel colocado sobre una mesa.

**The Flat-by-Default Rule.** Una superficie en reposo no usa sombra si una regla, un campo tonal o la propia geometría ya comunica su límite.

## Shapes

Predominan esquinas rectas o apenas suavizadas. Campos y tarjetas operativas usan radios discretos; la comanda y los tickets administrativos permanecen rectangulares. Los canales incorporan dos cortes superiores de papel, un clip centrado construido con CSS y una muesca roja únicamente en el activo; los tickets de pendiente usan borde carbón y pequeños cortes inferiores. Reglas de carbón, subrayados y bordes discontinuos evocan impresión y papelería sin usar texturas raster.

La firma visible se compone con la fuente Bebas Neue local: `LOS` verde, `TOCAYOS` rojo, la `T` inicial un 13 % mayor y `®` al final. Es texto de marca controlado —sin imagen raster, contorno, relieve ni sombra— y conserva un nombre accesible único para lectores de pantalla. La misma firma aparece en POS y Administrador. Los iconos de navegación proceden de Tabler Icons, son SVG de línea de 24 px y trazo uniforme de 2 px, se incrustan localmente y mantienen texto visible junto a cada símbolo.

## Components

### Buttons

- **Shape:** rectos y táctiles, con radio mínimo y altura operativa no menor a 44 CSS px.
- **Primary:** amarillo de marca con tinta carbón; la selección activa puede usar rojo sólo cuando no represente estado de orden.
- **Hover / Focus:** cambio tonal breve, sin salto; foco azul de tres píxeles con separación exterior.
- **Loading / Disabled:** toda acción asíncrona conserva el control que la inició, cambia a texto de espera explícito, se deshabilita y expone `aria-busy="true"`; el contenedor puede reflejar actividad agregada sin sustituir ese estado local. Al terminar restaura etiqueta y disponibilidad, con contraste suficiente; nunca aparenta estar congelada.

### Cards / Containers

- **Position cards:** misma geometría para Comedor/Domicilio y Recoger/Llevar. Libre usa número verde; Orden abierta, amarillo con tinta oscura; Procesada, rojo con marca de verificación.
- **Branch cards:** conservan la misma gramática pero reducen título, estado, número y metadatos para que nombres largos no saturen la columna.
- **Product cards:** 50 % fotografía y 50 % texto, altura compacta y nombre real de hasta tres líneas; no inventan precio ni descriptor.
- **Pending tickets:** papel blanco, borde carbón de dos píxeles, cortes inferiores y folio prominente; presentan la decisión y su acción sin mezclar métricas de venta.
- **Containers:** papel plano, borde carbón y sin elevación salvo las excepciones definidas arriba.

### Inputs / Fields

- **Style:** fondo blanco o papel, borde visible y radio mínimo.
- **Focus:** contorno azul exterior de tres píxeles, también en radios y switches cuyo input nativo está visualmente oculto.
- **Error / Disabled:** el error accionable persiste hasta descartar o reintentar; el estado deshabilitado sigue siendo legible y no simula disponibilidad.

### Navigation

El riel de canales del POS usa tres tickets tipo clipboard agrupados y alineados a la derecha. El seleccionado expone `aria-pressed`, papel blanco, clip rojo y una única regla roja inferior con muesca central. Los siete atajos del catálogo son botones con nombre accesible y destino explícito; el desplazamiento respeta movimiento reducido y nunca cambia el orden de categorías. `Salir` vive en la cabecera sólo mientras se muestran posiciones; dentro de una orden se oculta para evitar abandonar accidentalmente la captura.

El Administrador usa un riel carbón separado con Inicio, Personal, Domicilios, Programados, Movimientos, Reportes y Sucursales. El destino activo cambia a amarillo y expone `aria-current="page"`; en móvil conserva el mismo orden como pista horizontal. La pista inferior de Inicio ofrece herramientas secundarias después de los pendientes y no suplanta el riel ni eleva Reportes por encima de una urgencia operativa.

### Protected Administrative Actions

Entrar al Administrador exige una clave de cuatro dígitos, pero no crea una autorización persistente. Cada mutación o impresión protegida vuelve a pedir el PIN en un diálogo modal, devuelve el foco al flujo y presenta errores accionables sin revelar la clave. Mientras la petición corre, el control iniciador se bloquea con etiqueta de progreso y `aria-busy`; no se permiten dos mutaciones simultáneas.

### Administrative Page Change

El cambio de sección usa un único gesto de hoja horizontal, breve y sin rebote: el panel entra desde la derecha con 22 px de desplazamiento y 200 ms. Con `prefers-reduced-motion: reduce`, no hay animación y el desplazamiento al inicio es inmediato.

### Virtual Comanda

La comanda virtual conserva deliberadamente el aspecto y el orden de la impresión térmica: encabezado, folio/contexto, comensales, preparación, productos, contacto, comentarios, bebidas, salsas y terminal. Sus celdas siguen siendo controles accesibles, pero no se rediseñan como tarjetas de aplicación.

### Product Image Resolver

La resolución sigue una sola prioridad: `Producto.imagen` mediante endpoint autenticado; después el mapa por código; finalmente `mainlogo.webp`. Lonches sin queso comparten la foto de su variante normal, todas las aguas frescas comparten `aguasfrescas.webp` y todos los refrescos comparten `refresco.webp`. La UI no depende de imágenes remotas.

## Do's and Don'ts

### Do:

- **Do** revisar `PRODUCT.md`, este archivo y el brief de superficie antes de crear o modificar una pantalla.
- **Do** preservar la igualdad geométrica entre posiciones principales y auxiliares mediante la misma retícula.
- **Do** mantener el reparto 5+2 de posiciones y 60/40 de catálogo/comanda en el formato horizontal de operación.
- **Do** mantener color, etiqueta y símbolo en todos los estados operativos.
- **Do** conservar en móvil la prioridad administrativa Domicilios → Cierres → Programados y mantener las herramientas secundarias después de la bandeja.
- **Do** pedir el PIN de cuatro dígitos en cada acción administrativa protegida y marcar el control iniciador con texto de espera, deshabilitado y `aria-busy`.
- **Do** conservar la comanda virtual alineada con `impresion/render.py::render_comanda` y con una impresión real.
- **Do** servir tipografías, iconos e imágenes desde el proyecto y registrar la procedencia de todo raster nuevo.
- **Do** mantener el Administrador local dentro de este lenguaje, con navegación separada y funciones confirmadas por su brief.

### Don't:

- **Don't** devolver Orden abierta al rojo; amarillo es su semántica aprobada.
- **Don't** reintroducir un footer de estado: esa información debe vivir en las posiciones y acciones que representa.
- **Don't** convertir la bandeja administrativa en un dashboard de métricas ni mostrar totales fuera de Reportes y corte de caja.
- **Don't** conservar el PIN como autorización de sesión para mutaciones posteriores.
- **Don't** mostrar `Salir` durante la captura ni reintroducir estados pasivos de conexión o impresora en la cabecera.
- **Don't** crear tarjetas flotantes genéricas, glassmorphism, gradientes decorativos, halos, texturas de papel o sombras duras.
- **Don't** deformar el logotipo ni usar Bebas Neue para instrucciones, datos de cliente o errores extensos.
- **Don't** sustituir la comanda por una maqueta raster o reorganizar su contenido como dashboard.
- **Don't** cargar fotografías de terceros o depender de URLs remotas para productos.
