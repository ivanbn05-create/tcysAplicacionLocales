(() => {
  "use strict";

  const productos = JSON.parse(document.getElementById("datos-productos").textContent);
  const posiciones = JSON.parse(document.getElementById("datos-posiciones").textContent);
  const nombresCanal = { comedor: "Comedor", domicilio: "Domicilio", sucursales: "Sucursales" };
  const terminosPreparacion = ["dorado", "medio", "blando"];
  const modificadores = [
    { codigo: "C/T", nombre: "CON TODO" },
    { codigo: "S/N", nombre: "SIN NADA" },
    { codigo: "CEB", nombre: "CEBOLLA" },
    { codigo: "CH G", nombre: "CHILE GÜERO" },
    { codigo: "CH V", nombre: "CHILE VERDE" },
    { codigo: "LLEVAR", nombre: "LLEVAR" },
  ];
  const estado = {
    canal: "comedor",
    persona: 1,
    modoMenu: "productos",
    objetivoModificador: { tipo: "persona", persona: 1 },
    edicion: null,
    colaEdicion: Promise.resolve(),
    errorEdicion: null,
    tickets: {},
    ticket: null,
    operando: false,
    resultadosClientes: [],
    temporizadorCliente: null,
    tokenBusquedaCliente: 0,
    clienteEditando: null,
    ultimoTerminoPorProducto: new Map(),
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const csrf = () => document.cookie.split("; ").find(v => v.startsWith("csrftoken="))?.split("=")[1] || "";
  const dinero = (valor) => new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(Number(valor || 0));
  const cantidad = (valor) => Number(valor).toLocaleString("es-MX", { maximumFractionDigits: 3 });
  const escapar = (valor) => String(valor ?? "").replace(/[&<>'"]/g, caracter => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[caracter]);

  async function api(url, opciones = {}) {
    const respuesta = await fetch(url, {
      ...opciones,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf(),
        ...(opciones.headers || {}),
      },
    });
    let datos;
    try { datos = await respuesta.json(); } catch { datos = {}; }
    if (!respuesta.ok) {
      const error = new Error(datos.error || "No fue posible completar la operación.");
      error.datos = datos;
      error.status = respuesta.status;
      throw error;
    }
    return datos;
  }

  function toast(mensaje, error = false) {
    const nodo = $("#toast");
    nodo.textContent = mensaje;
    nodo.classList.toggle("error", error);
    nodo.classList.add("visible");
    clearTimeout(toast.temporizador);
    toast.temporizador = setTimeout(() => nodo.classList.remove("visible"), 3300);
  }

  function bloquear(valor) {
    estado.operando = valor;
    $$("button").forEach(boton => boton.disabled = valor);
    $(".perfil-acceso.administrador")?.setAttribute("disabled", "");
    if (!valor && estado.ticket) {
      renderMenu();
      renderComanda();
      renderAcciones();
    }
  }

  async function cargarEstado() {
    const conexion = $("#conexion");
    try {
      const datos = await api("/api/estado/");
      estado.tickets = datos.tickets;
      conexion?.classList.remove("error");
      renderPosiciones();
    } catch (error) {
      conexion?.classList.add("error");
      toast(error.message, true);
    }
  }

  async function cargarEstadoImpresion() {
    const nodo = $("#impresora-estado");
    if (!nodo) return;
    nodo.className = "impresora-estado comprobando";
    nodo.querySelector("span").textContent = "Comprobando impresora";
    try {
      const datos = await api("/api/impresion/estado/");
      nodo.title = `${datos.mensaje} ${datos.host}:${datos.puerto}`;
      if (datos.backend === "archivo") {
        nodo.className = "impresora-estado vista-previa";
        nodo.querySelector("span").textContent = "Sólo vista previa";
      } else if (datos.disponible) {
        nodo.className = "impresora-estado lista";
        nodo.querySelector("span").textContent = "Impresora lista";
      } else {
        nodo.className = "impresora-estado error";
        nodo.querySelector("span").textContent = "Impresora sin conexión";
      }
    } catch (error) {
      nodo.className = "impresora-estado error";
      nodo.querySelector("span").textContent = "Error de impresora";
      nodo.title = error.message;
    }
  }

  function renderPosiciones() {
    const contenedor = $("#rejilla-posiciones");
    const filtradas = posiciones.filter(p => p.canal === estado.canal).sort((a, b) => a.orden - b.orden);
    contenedor.innerHTML = filtradas.map(posicion => {
      const ticket = estado.tickets[posicion.id];
      const clase = ticket ? (ticket.estado === "abierto" ? "ocupada" : "procesada") : "";
      const detalle = ticket ? `Ticket ${ticket.folio} · ${dinero(ticket.total)}` : "Disponible";
      return `<button class="posicion ${clase}" data-id="${posicion.id}" type="button"><strong>${posicion.nombre}</strong><small>${detalle}</small></button>`;
    }).join("");
    if (!filtradas.length) contenedor.innerHTML = '<p class="vacio">No hay posiciones configuradas.</p>';
  }

  function cambiarCanal(canal) {
    estado.canal = canal;
    $$(".canal").forEach(b => b.classList.toggle("activo", b.dataset.canal === canal));
    renderPosiciones();
  }

  async function abrirPosicion(mesaId) {
    if (estado.operando) return;
    bloquear(true);
    try {
      const datos = await api("/api/tickets/abrir/", { method: "POST", body: JSON.stringify({ mesa_id: mesaId }) });
      estado.ticket = datos.ticket;
      mostrarTicket();
    } catch (error) {
      toast(error.message, true);
    } finally {
      bloquear(false);
    }
  }

  function mostrarTicket() {
    const ticket = estado.ticket;
    $("#vista-posiciones").classList.add("oculto");
    $("#vista-ticket").classList.remove("oculto");
    $("#ticket-mesa").textContent = ticket.mesa;
    $("#ticket-folio").textContent = ticket.folio;
    $("#entrega").value = ticket.entrega_aproximada || "";
    $("#comentario").value = ticket.comentario_general || "";
    const esDomicilio = ticket.canal === "domicilio";
    $(".panel-orden").classList.toggle("con-domicilio", esDomicilio);
    $("#datos-cliente").classList.toggle("oculto", !esDomicilio);
    if (esDomicilio) {
      $("#buscar-cliente").value = "";
      renderClienteDomicilio();
      if (!ticket.cliente.id) buscarClientes("");
    }
    renderPersonas();
    estado.modoMenu = "productos";
    estado.objetivoModificador = { tipo: "persona", persona: estado.persona };
    estado.edicion = null;
    estado.colaEdicion = Promise.resolve();
    estado.errorEdicion = null;
    estado.ultimoTerminoPorProducto.clear();
    renderMenu();
    renderComanda();
    renderAcciones();
  }

  function renderPersonas() {
    $("#personas").innerHTML = Array.from({ length: 24 }, (_, i) => i + 1).map(numero =>
      `<button class="persona ${estado.persona === numero ? "activa" : ""}" data-persona="${numero}" type="button">${numero}</button>`
    ).join("");
  }

  function esBebida(partidaOProducto) {
    return String(partidaOProducto?.categoria || "").toLocaleLowerCase("es-MX") === "bebidas";
  }

  function bloqueComensales() {
    const inicio = Math.floor((estado.persona - 1) / 6) * 6 + 1;
    return { inicio, personas: Array.from({ length: 6 }, (_, indice) => inicio + indice) };
  }

  async function cambiarModoMenu(modo, objetivo = null) {
    if (modo !== "calculadora" && !(await finalizarEdicion())) return;
    estado.modoMenu = modo;
    if (objetivo) estado.objetivoModificador = objetivo;
    renderMenu();
    renderComanda();
  }

  function renderMenu() {
    const abierto = estado.ticket?.estado === "abierto";
    $(".panel-productos")?.classList.toggle("modo-calculadora", estado.modoMenu === "calculadora");
    $("#productos")?.classList.toggle("modo-calculadora", estado.modoMenu === "calculadora");
    const volverProductos = $("#menu-productos");
    volverProductos.classList.toggle("oculto", ["productos", "calculadora"].includes(estado.modoMenu));
    if (estado.modoMenu === "calculadora" && estado.edicion) {
      const producto = productos.find(item => item.id === estado.edicion.productoId);
      if (!producto) return;
      const abreviatura = producto.permite_termino
        ? producto.abreviaturas_termino[estado.edicion.termino]
        : producto.corto;
      $("#menu-contexto").textContent = "Cantidad y término";
      $("#menu-indicacion").textContent = `Comensal ${estado.edicion.persona} · ${producto.nombre}`;
      const terminos = `
        <div class="selector-termino" aria-label="Término del producto">
          ${terminosPreparacion.map(termino =>
            `<button class="termino ${estado.edicion.termino === termino ? "activo" : ""}" data-termino="${termino}" type="button" ${!abierto || !producto.permite_termino ? "disabled" : ""}>${termino[0].toUpperCase()}${termino.slice(1)}</button>`
          ).join("")}
        </div>`;
      $("#productos").innerHTML = `
        <section class="calculadora-cantidad">
          ${terminos}
          <div class="calculadora-producto"><strong>${escapar(producto.nombre)}</strong><span>${escapar(abreviatura || producto.corto)}</span></div>
          <output class="pantalla-cantidad" aria-label="Cantidad">${estado.edicion.cantidadPantalla}</output>
          <div class="teclado-cantidad">
            ${[7, 8, 9, 4, 5, 6, 1, 2, 3].map(numero => `<button data-tecla="${numero}" type="button">${numero}</button>`).join("")}
            <button class="borrar" data-tecla="borrar" type="button" aria-label="Borrar un dígito">←</button>
            <button data-tecla="0" type="button">0</button>
            <button class="eliminar" data-tecla="eliminar" type="button" aria-label="Eliminar producto">×</button>
          </div>
          <button class="confirmar-edicion" data-confirmar-edicion type="button" aria-label="Confirmar y salir" title="Confirmar y salir">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>
          </button>
        </section>`;
      return;
    }
    if (estado.modoMenu === "modificadores") {
      const objetivo = estado.objetivoModificador.tipo === "grupo"
        ? `comensales ${estado.objetivoModificador.inicio}–${estado.objetivoModificador.inicio + 5}`
        : `comensal ${estado.objetivoModificador.persona}`;
      $("#menu-contexto").textContent = "Preparación";
      $("#menu-indicacion").textContent = `Aplicar a ${objetivo}`;
      $("#productos").innerHTML = modificadores.map(modificador =>
        `<button class="producto opcion-preparacion" data-codigo="${modificador.codigo}" data-nombre="${modificador.nombre}" type="button" ${!abierto ? "disabled" : ""}>
          <small>${modificador.codigo}</small><strong>${modificador.nombre}</strong><b>Agregar</b>
        </button>`
      ).join("");
      return;
    }
    const soloBebidas = estado.modoMenu === "bebidas";
    const disponibles = soloBebidas ? productos.filter(esBebida) : productos;
    $("#menu-contexto").textContent = soloBebidas ? "Bebidas" : "Menú completo";
    $("#menu-indicacion").textContent = soloBebidas ? "Se acumulan al final de la comanda" : "Todos los productos, en el orden del menú";
    $("#productos").innerHTML = disponibles.map(producto =>
      `<button class="producto producto-menu" data-id="${producto.id}" type="button" ${!abierto ? "disabled" : ""}>
        <small>${escapar(producto.categoria)} · ${escapar(producto.corto)}</small><strong>${escapar(producto.nombre)}</strong>
      </button>`
    ).join("") || '<div class="vacio">No hay opciones disponibles.</div>';
  }

  function formatoFechaComanda(valor) {
    const fecha = new Date(valor);
    if (Number.isNaN(fecha.getTime())) return "";
    return `${fecha.toLocaleDateString("es-MX", { day: "2-digit", month: "2-digit", year: "numeric" })} · ${fecha.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" })}`;
  }

  function renderComanda() {
    const ticket = estado.ticket;
    const contenedor = $("#comanda-papel-preview");
    if (!ticket || !contenedor) return;
    const { inicio, personas } = bloqueComensales();
    const partidasComida = ticket.partidas.filter(partida => !esBebida(partida));
    const filas = new Map();
    for (const partida of partidasComida) {
      const clave = `${partida.producto_id}:${partida.termino || "unico"}`;
      if (!filas.has(clave)) {
        filas.set(clave, { clave, productoId: partida.producto_id, termino: partida.termino || "", nombre: partida.nombre_corto, cantidades: new Map() });
      }
      const fila = filas.get(clave);
      if (!fila.cantidades.has(partida.comensal)) fila.cantidades.set(partida.comensal, { cantidad: 0, ids: [] });
      const celda = fila.cantidades.get(partida.comensal);
      celda.cantidad += Number(partida.cantidad);
      celda.ids.push(partida.id);
    }
    const preparacion = new Map(personas.map(persona => [persona, ticket.modificadores.filter(mod => mod.comensal === persona)]));
    const bebidas = new Map();
    for (const partida of ticket.partidas.filter(esBebida)) {
      if (!bebidas.has(partida.producto_id)) bebidas.set(partida.producto_id, { nombre: partida.nombre_corto, cantidad: 0 });
      bebidas.get(partida.producto_id).cantidad += Number(partida.cantidad);
    }
    const filasHtml = [...filas.values()].map(fila => {
      const celdas = personas.map(persona => {
        const celda = fila.cantidades.get(persona);
        const seleccionada = estado.edicion?.clave === fila.clave && estado.edicion?.persona === persona;
        const valor = seleccionada ? estado.edicion.cantidadPantalla : (celda?.cantidad || 0);
        const texto = valor || seleccionada ? cantidad(valor) : "";
        return `<button class="comanda-celda cantidad-celda ${seleccionada ? "seleccionada" : ""}" data-celda-producto="${fila.productoId}" data-celda-persona="${persona}" data-celda-termino="${fila.termino}" data-celda-clave="${fila.clave}" type="button">${texto}</button>`;
      }).join("");
      return `<button class="comanda-etiqueta producto-zona" data-modo-menu="productos" type="button">${escapar(fila.nombre)}</button>${celdas}`;
    }).join("") || `<button class="comanda-vacio producto-zona" data-modo-menu="productos" type="button">Toca aquí para mostrar productos y comenzar la orden</button>`;
    const bebidasHtml = [...bebidas.values()].map(bebida =>
      `${bebida.cantidad >= 2 ? `<strong>( ${cantidad(bebida.cantidad)} )</strong> ` : ""}${escapar(bebida.nombre)}`
    ).join('<b class="separador-bebida">* </b>');
    const comentarioActual = $("#comentario")?.value || ticket.comentario_general || "";
    $("#orden-resumen").textContent = `${ticket.partidas.length} ${ticket.partidas.length === 1 ? "partida" : "partidas"}`;
    contenedor.innerHTML = `
      <section class="comanda-papel">
        <header class="comanda-papel-encabezado">
          <em>Los Tocayos Tacos de Barbacoa</em>
          <div><span>${formatoFechaComanda(ticket.creado_en)}</span><strong>Ticket: ${ticket.folio}</strong></div>
          <div><span>Ent. Aprox: ${escapar(ticket.entrega_aproximada || "—")}</span><strong>${escapar(ticket.mesa)}</strong></div>
          <b>${dinero(ticket.total)}</b>
        </header>
        <div class="comanda-matriz">
          <span class="comanda-etiqueta encabezado">Comensal</span>
          ${personas.map(persona => `<button class="comanda-numero ${estado.persona === persona ? "activo" : ""}" data-seleccionar-persona="${persona}" type="button">${persona}</button>`).join("")}
          <button class="comanda-global" data-objetivo-modificador="grupo" data-inicio="${inicio}" type="button">Comentario General</button>
          <span class="comanda-etiqueta encabezado">Prep.</span>
          ${personas.map(persona => {
            const texto = preparacion.get(persona).map(mod => mod.codigo).join(" ") || "+";
            return `<button class="comanda-comentario ${estado.persona === persona ? "activo" : ""}" data-objetivo-modificador="persona" data-persona="${persona}" type="button">${escapar(texto)}</button>`;
          }).join("")}
          ${filasHtml}
        </div>
        <button class="comanda-bebidas" data-modo-menu="bebidas" type="button">
          <strong>Bebidas</strong>
          <span>${bebidasHtml || "Toca aquí para elegir bebidas"}</span>
        </button>
        ${comentarioActual ? `<p class="comanda-comentario-general">${escapar(comentarioActual)}</p>` : ""}
      </section>`;
  }

  function renderClienteDomicilio() {
    const cliente = estado.ticket?.cliente || {};
    const seleccionado = Boolean(cliente.id);
    $("#cliente-buscador").classList.toggle("oculto", seleccionado);
    $("#resultados-clientes").classList.toggle("oculto", seleccionado);
    $("#cliente-seleccionado").classList.toggle("oculto", !seleccionado);
    if (!seleccionado) {
      renderResultadosClientes();
      return;
    }
    $("#cliente-seleccionado").innerHTML = `
      <div class="cliente-resultado-cabecera">
        <strong>${escapar(cliente.nombre)}</strong>
        <span class="cliente-clave">${escapar(cliente.clave_corta)}</span>
      </div>
      <p>${cliente.telefono ? `<b>${escapar(cliente.telefono)}</b> · ` : ""}${escapar(cliente.domicilio)}</p>
      ${cliente.referencia ? `<p>Referencia: ${escapar(cliente.referencia)}</p>` : ""}
      ${cliente.notas ? `<p class="cliente-nota-interna"><b>Nota interna:</b> ${escapar(cliente.notas)}</p>` : ""}
      ${cliente.comentarios_multiples ? `
        <div class="contacto-pedido">
          <p class="eyebrow">Contacto para este pedido</p>
          <label>Nombre<input id="contacto-pedido-nombre" value="${escapar(cliente.contacto_pedido_nombre || "")}" autocomplete="off" required></label>
          <label>Celular<input id="contacto-pedido-telefono" value="${escapar(cliente.contacto_pedido_telefono || "")}" inputmode="tel" autocomplete="off" required></label>
          <small>Se solicita de nuevo al levantar cada orden para esta empresa.</small>
        </div>` : ""}
      <div class="cliente-seleccionado-acciones">
        <button class="boton secundario" data-editar-cliente type="button">Editar datos</button>
        <button class="boton enlace" data-cambiar-cliente type="button">Cambiar cliente</button>
      </div>`;
  }

  function renderResultadosClientes() {
    const contenedor = $("#resultados-clientes");
    const consulta = $("#buscar-cliente")?.value.trim() || "";
    if (!estado.resultadosClientes.length) {
      contenedor.innerHTML = `<p class="cliente-sin-resultados">${consulta ? "No se encontraron coincidencias. Puedes registrar un cliente nuevo." : "Aún no hay clientes recientes."}</p>`;
      return;
    }
    contenedor.innerHTML = estado.resultadosClientes.map((resultado, indice) => {
      const telefono = resultado.telefono?.numero || "Sin teléfono";
      const domicilio = resultado.domicilio?.texto || "Sin domicilio";
      const incompleto = !resultado.domicilio || (!resultado.telefono && !resultado.comentarios_multiples);
      return `<button class="cliente-resultado" data-seleccionar-cliente="${indice}" type="button" ${incompleto ? "disabled" : ""}>
        <span class="cliente-resultado-cabecera"><strong>${escapar(resultado.nombre)}</strong><span class="cliente-clave">${escapar(resultado.clave_corta)}</span></span>
        <p>${escapar(telefono)} · ${escapar(domicilio)}</p>
        <small>${escapar(incompleto ? "Registro incompleto: edítalo antes de usarlo" : (resultado.comentarios_multiples ? `${resultado.motivo} · solicita contacto` : resultado.motivo))}</small>
      </button>`;
    }).join("");
  }

  async function buscarClientes(consulta) {
    const token = ++estado.tokenBusquedaCliente;
    try {
      const datos = await api(`/api/clientes/buscar/?q=${encodeURIComponent(consulta)}&limite=12`);
      if (token !== estado.tokenBusquedaCliente || estado.ticket?.canal !== "domicilio") return;
      estado.resultadosClientes = datos.resultados;
      renderResultadosClientes();
    } catch (error) {
      if (token === estado.tokenBusquedaCliente) {
        estado.resultadosClientes = [];
        renderResultadosClientes();
        toast(error.message, true);
      }
    }
  }

  async function asignarCliente(clienteId, telefonoId, domicilioId) {
    const datos = await api(`/api/tickets/${estado.ticket.id}/cliente/`, {
      method: "POST",
      body: JSON.stringify({ cliente_id: clienteId, telefono_id: telefonoId, domicilio_id: domicilioId }),
    });
    estado.ticket = datos.ticket;
    renderClienteDomicilio();
    renderAcciones();
  }

  async function quitarCliente() {
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/cliente/`, { method: "DELETE" });
      estado.ticket = datos.ticket;
      estado.resultadosClientes = [];
      $("#buscar-cliente").value = "";
      renderClienteDomicilio();
      buscarClientes("");
    } catch (error) { toast(error.message, true); }
  }

  function filaTelefono(telefono = {}) {
    return `<div class="fila-telefono" data-registro-id="${escapar(telefono.id || "")}">
      <label>Etiqueta<input class="telefono-etiqueta" value="${escapar(telefono.etiqueta || "Principal")}" maxlength="30"></label>
      <label>Teléfono<input class="telefono-numero" value="${escapar(telefono.numero || "")}" inputmode="tel" autocomplete="tel"></label>
      <button class="quitar-fila" data-quitar-fila type="button" aria-label="Quitar teléfono">×</button>
    </div>`;
  }

  function filaDomicilio(domicilio = {}) {
    return `<div class="fila-domicilio" data-registro-id="${escapar(domicilio.id || "")}">
      <label>Etiqueta<input class="domicilio-etiqueta" value="${escapar(domicilio.etiqueta || "Principal")}" maxlength="30"></label>
      <label>Calle<input class="domicilio-calle" value="${escapar(domicilio.calle || "")}" autocomplete="address-line1" required></label>
      <label>Núm. exterior<input class="domicilio-exterior" value="${escapar(domicilio.numero_exterior || "")}"></label>
      <label>Núm. interior<input class="domicilio-interior" value="${escapar(domicilio.numero_interior || "")}"></label>
      <label class="domicilio-colonia">Colonia<input class="domicilio-colonia-valor" value="${escapar(domicilio.colonia || "")}"></label>
      <label>CP<input class="domicilio-cp" value="${escapar(domicilio.codigo_postal || "")}" inputmode="numeric"></label>
      <label class="domicilio-municipio">Municipio<input class="domicilio-municipio-valor" value="${escapar(domicilio.municipio || "")}"></label>
      <label class="domicilio-referencia">Referencia<textarea class="domicilio-referencia-valor" rows="2">${escapar(domicilio.referencia || "")}</textarea></label>
      <button class="quitar-fila" data-quitar-fila type="button" aria-label="Quitar domicilio">×</button>
    </div>`;
  }

  function abrirFormularioCliente(cliente = null) {
    estado.clienteEditando = cliente;
    $("#titulo-form-cliente").textContent = cliente ? `Editar ${cliente.nombre}` : "Nuevo cliente";
    $("#cliente-form-id").value = cliente?.id || "";
    $("#cliente-form-nombre").value = cliente?.nombre || "";
    $("#cliente-form-notas").value = cliente?.notas || "";
    $("#cliente-form-multiples").checked = Boolean(cliente?.comentarios_multiples);
    $("#telefonos-form").innerHTML = (cliente?.telefonos?.length ? cliente.telefonos : [{}]).map(filaTelefono).join("");
    $("#domicilios-form").innerHTML = (cliente?.domicilios?.length ? cliente.domicilios : [{}]).map(filaDomicilio).join("");
    $("#aviso-duplicados").classList.add("oculto");
    $("#aviso-duplicados").innerHTML = "";
    $("#form-cliente").dataset.confirmarDuplicado = "false";
    $("#guardar-cliente").textContent = "Guardar y seleccionar";
    $("#dialogo-cliente").showModal();
    $("#cliente-form-nombre").focus();
  }

  function datosFormularioCliente(confirmarDuplicado = false) {
    const telefonos = $$("#telefonos-form .fila-telefono").map(fila => ({
      id: fila.dataset.registroId || null,
      etiqueta: fila.querySelector(".telefono-etiqueta").value,
      numero: fila.querySelector(".telefono-numero").value,
    }));
    const domicilios = $$("#domicilios-form .fila-domicilio").map(fila => ({
      id: fila.dataset.registroId || null,
      etiqueta: fila.querySelector(".domicilio-etiqueta").value,
      calle: fila.querySelector(".domicilio-calle").value,
      numero_exterior: fila.querySelector(".domicilio-exterior").value,
      numero_interior: fila.querySelector(".domicilio-interior").value,
      colonia: fila.querySelector(".domicilio-colonia-valor").value,
      codigo_postal: fila.querySelector(".domicilio-cp").value,
      municipio: fila.querySelector(".domicilio-municipio-valor").value,
      referencia: fila.querySelector(".domicilio-referencia-valor").value,
    }));
    return {
      nombre: $("#cliente-form-nombre").value,
      notas: $("#cliente-form-notas").value,
      comentarios_multiples: $("#cliente-form-multiples").checked,
      telefonos,
      domicilios,
      confirmar_duplicado: confirmarDuplicado,
    };
  }

  function mostrarDuplicados(duplicados) {
    const aviso = $("#aviso-duplicados");
    aviso.innerHTML = `<p><strong>Ya existe un cliente con este nombre.</strong> El teléfono y el domicilio pueden compartirse; esta advertencia se basa únicamente en el nombre.</p>
      <ul>${duplicados.map(cliente => `<li>${escapar(cliente.nombre)} · clave ${escapar(cliente.clave_corta)}</li>`).join("")}</ul>`;
    aviso.classList.remove("oculto");
    $("#form-cliente").dataset.confirmarDuplicado = "true";
    $("#guardar-cliente").textContent = "Guardar de todos modos";
    aviso.scrollIntoView({ block: "nearest" });
  }

  async function guardarFormularioCliente(confirmarDuplicado = false) {
    const id = $("#cliente-form-id").value;
    try {
      const datos = await api(id ? `/api/clientes/${id}/` : "/api/clientes/", {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify(datosFormularioCliente(confirmarDuplicado)),
      });
      const cliente = datos.cliente;
      const telefonoActual = cliente.telefonos.find(item => item.id === estado.ticket.cliente.telefono_id) || cliente.telefonos[0];
      const domicilioActual = cliente.domicilios.find(item => item.id === estado.ticket.cliente.domicilio_id) || cliente.domicilios[0];
      await asignarCliente(cliente.id, telefonoActual?.id || "", domicilioActual.id);
      $("#dialogo-cliente").close();
      toast(`Cliente ${cliente.nombre} guardado y seleccionado.`);
    } catch (error) {
      if (error.status === 409 && error.datos?.duplicados) mostrarDuplicados(error.datos.duplicados);
      else toast(error.message, true);
    }
  }

  async function editarClienteSeleccionado() {
    try {
      const datos = await api(`/api/clientes/${estado.ticket.cliente.id}/`);
      abrirFormularioCliente(datos.cliente);
    } catch (error) { toast(error.message, true); }
  }

  function renderAcciones() {
    const abierto = estado.ticket.estado === "abierto";
    const cobrable = ["procesado", "cobrar"].includes(estado.ticket.estado);
    $("#procesar").classList.toggle("oculto", !abierto);
    $("#cancelar-orden").classList.toggle("oculto", !abierto);
    $("#cobrar").classList.toggle("oculto", !cobrable);
    $("#reimprimir").classList.toggle("oculto", abierto);
    $$(".persona, .opcion-preparacion, .comanda-papel button, .producto").forEach(b => b.disabled = !abierto);
    $$("#datos-cliente button, #datos-cliente input, #datos-cliente textarea").forEach(control => control.disabled = !abierto);
  }

  function partidasDeEdicion(productoId, persona, termino) {
    return estado.ticket.partidas.filter(partida =>
      partida.producto_id === productoId && partida.comensal === persona && (partida.termino || "") === (termino || "")
    );
  }

  function claveTerminoProducto(productoId, persona) {
    return `${productoId}:${persona}`;
  }

  function recordarTermino(productoId, persona, termino) {
    if (termino) estado.ultimoTerminoPorProducto.set(claveTerminoProducto(productoId, persona), termino);
  }

  function siguienteTerminoProducto(producto) {
    if (!producto.permite_termino) return "";
    const existentes = new Set(
      estado.ticket.partidas
        .filter(partida => partida.producto_id === producto.id && partida.comensal === estado.persona)
        .map(partida => partida.termino)
        .filter(Boolean),
    );
    if (!existentes.size) return producto.termino_predeterminado;
    const clave = claveTerminoProducto(producto.id, estado.persona);
    const ultimo = estado.ultimoTerminoPorProducto.get(clave) || producto.termino_predeterminado;
    const indice = Math.max(0, terminosPreparacion.indexOf(ultimo));
    for (let desplazamiento = 1; desplazamiento <= terminosPreparacion.length; desplazamiento += 1) {
      const candidato = terminosPreparacion[(indice + desplazamiento) % terminosPreparacion.length];
      if (!existentes.has(candidato)) return candidato;
    }
    return terminosPreparacion[(indice + 1) % terminosPreparacion.length];
  }

  function abrirCalculadora(producto, partidas, termino) {
    const cantidadActual = partidas.reduce((total, partida) => total + Number(partida.cantidad), 0) || 1;
    estado.edicion = {
      productoId: producto.id,
      persona: estado.persona,
      termino: termino || "",
      clave: `${producto.id}:${termino || "unico"}`,
      ids: partidas.map(partida => partida.id),
      cantidadPantalla: Math.min(99, Math.max(1, Math.trunc(cantidadActual))),
      reemplazar: true,
    };
    recordarTermino(producto.id, estado.persona, termino);
    estado.modoMenu = "calculadora";
    renderMenu();
    renderComanda();
  }

  async function seleccionarProducto(productoId) {
    if (!(await finalizarEdicion())) return;
    const producto = productos.find(item => item.id === productoId);
    if (!producto) return;
    const termino = siguienteTerminoProducto(producto);
    let partidas = partidasDeEdicion(producto.id, estado.persona, termino);
    if (!partidas.length) {
      try {
        const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/`, {
          method: "POST",
          body: JSON.stringify({ producto_id: productoId, comensal: estado.persona, cantidad: 1, termino }),
        });
        estado.ticket = datos.ticket;
        partidas = partidasDeEdicion(producto.id, estado.persona, termino);
      } catch (error) {
        toast(error.message, true);
        return;
      }
    }
    abrirCalculadora(producto, partidas, termino);
  }

  function persistirEdicion(eliminar = false, sincronizarCantidad = false) {
    if (!estado.edicion || (!eliminar && estado.edicion.cantidadPantalla === 0)) return Promise.resolve();
    const captura = {
      ticketId: estado.ticket.id,
      productoId: estado.edicion.productoId,
      persona: estado.edicion.persona,
      termino: estado.edicion.termino,
      clave: estado.edicion.clave,
      ids: [...estado.edicion.ids],
      cantidad: estado.edicion.cantidadPantalla,
      eliminar,
      sincronizarCantidad,
    };
    estado.colaEdicion = estado.colaEdicion.then(async () => {
      const datos = await api(`/api/tickets/${captura.ticketId}/partidas/ajustar/`, {
        method: "POST",
        body: JSON.stringify({
          partida_ids: captura.ids,
          cantidad: captura.cantidad || 1,
          termino: captura.termino,
          eliminar: captura.eliminar,
        }),
      });
      estado.ticket = datos.ticket;
      estado.errorEdicion = null;
      if (estado.edicion?.clave === captura.clave && estado.edicion?.persona === captura.persona) {
        const partidasActuales = partidasDeEdicion(captura.productoId, captura.persona, captura.termino);
        estado.edicion.ids = partidasActuales.map(partida => partida.id);
        if (captura.sincronizarCantidad && estado.edicion.cantidadPantalla === captura.cantidad) {
          estado.edicion.cantidadPantalla = partidasActuales.reduce(
            (total, partida) => total + Number(partida.cantidad),
            0,
          );
          renderMenu();
        }
      }
      renderComanda();
    }).catch(error => {
      estado.errorEdicion = error;
      toast(error.message, true);
    });
    return estado.colaEdicion;
  }

  async function finalizarEdicion() {
    if (!estado.edicion) return true;
    const edicion = estado.edicion;
    await estado.colaEdicion;
    if (estado.errorEdicion) return false;
    if (estado.edicion !== edicion) return true;
    if (edicion.cantidadPantalla === 0) {
      edicion.cantidadPantalla = 1;
      await persistirEdicion();
      if (estado.errorEdicion) return false;
    }
    if (estado.edicion === edicion) estado.edicion = null;
    return true;
  }

  async function manejarTeclaCantidad(tecla) {
    if (!estado.edicion) return;
    if (tecla === "eliminar") {
      await persistirEdicion(true);
      if (estado.errorEdicion) return;
      estado.edicion = null;
      estado.modoMenu = "productos";
      renderMenu();
      renderComanda();
      return;
    }
    let texto = String(estado.edicion.cantidadPantalla);
    if (tecla === "borrar") {
      texto = texto.slice(0, -1) || "0";
      estado.edicion.reemplazar = false;
    } else {
      texto = estado.edicion.reemplazar || texto === "0" ? String(tecla) : `${texto}${tecla}`;
      texto = texto.slice(0, 2);
      estado.edicion.reemplazar = false;
    }
    estado.edicion.cantidadPantalla = Math.min(99, Number(texto) || 0);
    renderMenu();
    renderComanda();
    if (estado.edicion.cantidadPantalla > 0) persistirEdicion();
  }

  async function confirmarEdicion() {
    if (!(await finalizarEdicion())) return;
    estado.modoMenu = "productos";
    renderMenu();
    renderComanda();
  }

  function seleccionarTermino(termino) {
    if (!estado.edicion) return;
    const producto = productos.find(item => item.id === estado.edicion.productoId);
    if (!producto?.permite_termino || !producto.abreviaturas_termino[termino]) return;
    estado.edicion.termino = termino;
    estado.edicion.clave = `${producto.id}:${termino}`;
    recordarTermino(producto.id, estado.edicion.persona, termino);
    renderMenu();
    renderComanda();
    persistirEdicion(false, true);
  }

  async function seleccionarCelda(productoId, persona, termino) {
    if (!(await finalizarEdicion())) return;
    estado.persona = persona;
    estado.objetivoModificador = { tipo: "persona", persona };
    const producto = productos.find(item => item.id === productoId);
    if (!producto) return;
    let partidas = partidasDeEdicion(productoId, persona, termino);
    if (!partidas.length) {
      try {
        const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/`, {
          method: "POST",
          body: JSON.stringify({ producto_id: productoId, comensal: persona, cantidad: 1, termino }),
        });
        estado.ticket = datos.ticket;
        partidas = partidasDeEdicion(productoId, persona, termino);
      } catch (error) {
        toast(error.message, true);
        return;
      }
    }
    renderPersonas();
    recordarTermino(producto.id, persona, termino);
    abrirCalculadora(producto, partidas, termino);
  }

  async function seleccionarPersona(persona) {
    if (!(await finalizarEdicion())) return;
    estado.persona = persona;
    estado.objetivoModificador = { tipo: "persona", persona };
    estado.modoMenu = "productos";
    renderPersonas();
    renderMenu();
    renderComanda();
  }

  async function aplicarPreparacion(codigo, nombre) {
    try {
      const objetivo = estado.objetivoModificador;
      let cuerpo;
      if (objetivo.tipo === "grupo") {
        const fin = objetivo.inicio + 5;
        const comensales = [...new Set(
          estado.ticket.partidas
            .filter(partida => !esBebida(partida) && partida.comensal >= objetivo.inicio && partida.comensal <= fin)
            .map(partida => partida.comensal)
        )];
        if (!comensales.length) {
          toast("Primero agrega productos a este bloque de comensales.", true);
          return;
        }
        cuerpo = { comensales, codigo, nombre };
      } else {
        cuerpo = { comensal: objetivo.persona, codigo, nombre };
      }
      const datos = await api(`/api/tickets/${estado.ticket.id}/modificadores/`, {
        method: "POST", body: JSON.stringify(cuerpo),
      });
      estado.ticket = datos.ticket;
      renderPersonas();
      renderComanda();
      renderMenu();
    } catch (error) { toast(error.message, true); }
  }

  async function guardarDatos() {
    const cuerpo = {
      comentario_general: $("#comentario").value,
      entrega_aproximada: $("#entrega").value,
      contacto_pedido_nombre: $("#contacto-pedido-nombre")?.value || "",
      contacto_pedido_telefono: $("#contacto-pedido-telefono")?.value || "",
    };
    const datos = await api(`/api/tickets/${estado.ticket.id}/`, { method: "PATCH", body: JSON.stringify(cuerpo) });
    estado.ticket = datos.ticket;
  }

  async function procesar() {
    if (!(await finalizarEdicion())) return;
    bloquear(true);
    try {
      await guardarDatos();
      const datos = await api(`/api/tickets/${estado.ticket.id}/procesar/`, { method: "POST", body: "{}" });
      estado.ticket = datos.ticket;
      mostrarTicket();
      resumirImpresiones(datos.impresiones, "Orden procesada.");
    } catch (error) { toast(error.message, true); }
    finally { bloquear(false); }
  }

  async function cobrar(formaPago, importeRecibido, imprimirTicket) {
    bloquear(true);
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/cobrar/`, {
        method: "POST", body: JSON.stringify({
          forma_pago: formaPago,
          importe_recibido: importeRecibido,
          imprimir_ticket: imprimirTicket,
        }),
      });
      estado.ticket = datos.ticket;
      $("#dialogo-cobro").close();
      resumirImpresiones(datos.impresiones, imprimirTicket ? "Cobro registrado." : "Cobro registrado sin imprimir ticket.");
      await volver(true);
    } catch (error) { toast(error.message, true); }
    finally { bloquear(false); }
  }

  async function reimprimir() {
    const formato = estado.ticket.estado === "pagado" ? "cuenta" : "comanda";
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/imprimir/`, { method: "POST", body: JSON.stringify({ formato }) });
      resumirImpresiones(datos.impresiones, "Reimpresión solicitada.");
    } catch (error) { toast(error.message, true); }
  }

  function resumirImpresiones(impresiones, mensajeBase) {
    const errores = impresiones.filter(impresion => impresion.estado === "error").length;
    const previas = impresiones.filter(impresion => impresion.estado === "generado").length;
    const impresas = impresiones.filter(impresion => impresion.estado === "impreso").length;
    if (errores) {
      const detalle = impresiones.find(impresion => impresion.estado === "error")?.error;
      toast(`${mensajeBase} No se pudo imprimir${detalle ? `: ${detalle}` : "."}`, true);
    } else if (previas) {
      toast(`${mensajeBase} Vista previa generada; no se envió papel.`);
    } else if (impresas) {
      toast(`${mensajeBase} Impresión enviada correctamente.`);
    } else if (!impresiones.length) {
      toast(mensajeBase);
    } else {
      toast(`${mensajeBase} Impresión en cola.`);
    }
    cargarEstadoImpresion();
  }

  async function cancelarOrden() {
    if (!estado.ticket || !window.confirm("¿Cancelar esta orden? Se borrarán todos los productos y la posición quedará disponible.")) return;
    bloquear(true);
    try {
      await api(`/api/tickets/${estado.ticket.id}/cancelar/`, { method: "POST", body: "{}" });
      toast("Orden cancelada; la posición quedó disponible.");
      await volver(true);
    } catch (error) {
      toast(error.message, true);
    } finally {
      bloquear(false);
    }
  }

  async function volver(forzar = false) {
    if (!forzar && !(await finalizarEdicion())) return;
    estado.ticket = null;
    $("#vista-ticket").classList.add("oculto");
    $("#vista-posiciones").classList.remove("oculto");
    cargarEstado();
  }

  async function salirModoMesero() {
    if (!(await finalizarEdicion())) return;
    estado.ticket = null;
    $("#vista-ticket").classList.add("oculto");
    $("#vista-posiciones").classList.remove("oculto");
    $("main").classList.add("oculto");
    $("#pantalla-acceso")?.classList.remove("oculto");
    document.body.classList.remove("en-operacion");
  }

  async function alternarPantallaCompleta() {
    try {
      if (document.fullscreenElement || document.webkitFullscreenElement) {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      } else if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen({ navigationUI: "hide" });
      } else if (document.documentElement.webkitRequestFullscreen) {
        document.documentElement.webkitRequestFullscreen();
      } else {
        toast("Este navegador no permite activar pantalla completa desde la página.", true);
      }
    } catch {
      toast("No fue posible activar la pantalla completa. Revisa los permisos del navegador.", true);
    }
  }

  $$(".canal").forEach(boton => boton.addEventListener("click", () => cambiarCanal(boton.dataset.canal)));
  $("#entrar-mesero")?.addEventListener("click", () => {
    $("#pantalla-acceso").classList.add("oculto");
    $("main").classList.remove("oculto");
    document.body.classList.add("en-operacion");
    cargarEstado();
  });
  $("#impresora-estado")?.addEventListener("click", cargarEstadoImpresion);
  $$('[data-salir-mesero]').forEach(boton => boton.addEventListener("click", salirModoMesero));
  $("#pantalla-completa")?.addEventListener("click", alternarPantallaCompleta);
  $("#rejilla-posiciones").addEventListener("click", evento => {
    const boton = evento.target.closest(".posicion");
    if (boton) abrirPosicion(boton.dataset.id);
  });
  $("#volver").addEventListener("click", () => volver());
  $("#personas").addEventListener("click", async evento => {
    const boton = evento.target.closest(".persona");
    if (boton) await seleccionarPersona(Number(boton.dataset.persona));
  });
  $("#productos").addEventListener("click", async evento => {
    const confirmar = evento.target.closest("[data-confirmar-edicion]");
    if (confirmar) {
      await confirmarEdicion();
      return;
    }
    const termino = evento.target.closest("[data-termino]");
    if (termino) {
      seleccionarTermino(termino.dataset.termino);
      return;
    }
    const tecla = evento.target.closest("[data-tecla]");
    if (tecla) {
      await manejarTeclaCantidad(tecla.dataset.tecla);
      return;
    }
    const preparacion = evento.target.closest(".opcion-preparacion");
    if (preparacion) {
      await aplicarPreparacion(preparacion.dataset.codigo, preparacion.dataset.nombre);
      return;
    }
    const boton = evento.target.closest(".producto");
    if (boton) await seleccionarProducto(boton.dataset.id);
  });
  $("#menu-productos").addEventListener("click", async () => cambiarModoMenu("productos"));
  $("#comanda-preview").addEventListener("click", async evento => {
    const persona = evento.target.closest("[data-seleccionar-persona]");
    if (persona) {
      await seleccionarPersona(Number(persona.dataset.seleccionarPersona));
      return;
    }
    const objetivo = evento.target.closest("[data-objetivo-modificador]");
    if (objetivo) {
      if (objetivo.dataset.objetivoModificador === "grupo") {
        estado.objetivoModificador = { tipo: "grupo", inicio: Number(objetivo.dataset.inicio) };
      } else {
        estado.persona = Number(objetivo.dataset.persona);
        estado.objetivoModificador = { tipo: "persona", persona: estado.persona };
        renderPersonas();
      }
      await cambiarModoMenu("modificadores");
      return;
    }
    const celda = evento.target.closest("[data-celda-producto]");
    if (celda) {
      await seleccionarCelda(
        celda.dataset.celdaProducto,
        Number(celda.dataset.celdaPersona),
        celda.dataset.celdaTermino,
      );
      return;
    }
    const zonaMenu = evento.target.closest("[data-modo-menu]");
    if (zonaMenu) await cambiarModoMenu(zonaMenu.dataset.modoMenu);
  });
  $("#buscar-cliente").addEventListener("input", evento => {
    clearTimeout(estado.temporizadorCliente);
    const consulta = evento.currentTarget.value;
    estado.temporizadorCliente = setTimeout(() => buscarClientes(consulta), 250);
  });
  $("#datos-cliente").addEventListener("click", async evento => {
    const resultado = evento.target.closest("[data-seleccionar-cliente]");
    if (resultado) {
      const cliente = estado.resultadosClientes[Number(resultado.dataset.seleccionarCliente)];
      if (!cliente?.domicilio || (!cliente?.telefono && !cliente?.comentarios_multiples)) return;
      try {
        await asignarCliente(cliente.cliente_id, cliente.telefono?.id || "", cliente.domicilio.id);
        toast(`${cliente.nombre} seleccionado.`);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (evento.target.closest("[data-cambiar-cliente]")) {
      await quitarCliente();
      return;
    }
    if (evento.target.closest("[data-editar-cliente]")) await editarClienteSeleccionado();
  });
  $("#nuevo-cliente").addEventListener("click", () => abrirFormularioCliente());
  $("#agregar-telefono").addEventListener("click", () => $("#telefonos-form").insertAdjacentHTML("beforeend", filaTelefono()));
  $("#agregar-domicilio").addEventListener("click", () => $("#domicilios-form").insertAdjacentHTML("beforeend", filaDomicilio()));
  $("#form-cliente").addEventListener("click", evento => {
    const quitar = evento.target.closest("[data-quitar-fila]");
    if (quitar) quitar.parentElement.remove();
  });
  $("#form-cliente").addEventListener("submit", evento => {
    evento.preventDefault();
    guardarFormularioCliente(evento.currentTarget.dataset.confirmarDuplicado === "true");
  });
  $("#cancelar-cliente").addEventListener("click", () => $("#dialogo-cliente").close());
  $("#descartar-cliente").addEventListener("click", () => $("#dialogo-cliente").close());
  $("#comentario").addEventListener("input", renderComanda);
  $("#procesar").addEventListener("click", procesar);
  $("#cancelar-orden").addEventListener("click", cancelarOrden);
  $("#cobrar").addEventListener("click", () => {
    $("#cobro-total").textContent = dinero(estado.ticket.total);
    $("#importe-recibido").value = estado.ticket.total;
    $("#dialogo-cobro").showModal();
  });
  $("#form-cobro").addEventListener("submit", evento => {
    evento.preventDefault();
    if (evento.submitter?.value === "cancel") { $("#dialogo-cobro").close(); return; }
    const formulario = new FormData(evento.currentTarget);
    cobrar(
      formulario.get("forma_pago"),
      $("#importe-recibido").value,
      formulario.get("imprimir_ticket") === "si",
    );
  });
  $("#reimprimir").addEventListener("click", reimprimir);

  window.addEventListener("online", cargarEstado);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  renderPersonas();
  renderMenu();
  cargarEstado();
  cargarEstadoImpresion();
})();
