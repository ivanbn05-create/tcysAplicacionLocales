(() => {
  "use strict";

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const csrf = () => document.cookie.split("; ").find(valor => valor.startsWith("csrftoken="))?.split("=")[1] || "";
  const dinero = valor => new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(Number(valor || 0));
  const escapar = valor => String(valor ?? "").replace(/[&<>'"]/g, caracter => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[caracter]);
  const estado = {
    administrador: null,
    resolucionClave: null,
    temporizadorToast: null,
    promesaResumen: null,
    operacionEnCurso: false,
    peticionesPendientes: 0,
  };

  class ErrorAPI extends Error {
    constructor(mensaje, status, datos) {
      super(mensaje);
      this.status = status;
      this.datos = datos;
    }
  }

  async function api(url, opciones = {}) {
    const respuesta = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      ...opciones,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf(),
        ...(opciones.headers || {}),
      },
    });
    let datos = {};
    try { datos = await respuesta.json(); } catch { /* La respuesta vacía se trata como objeto. */ }
    if (!respuesta.ok) throw new ErrorAPI(datos.error || "No fue posible completar la operación.", respuesta.status, datos);
    return datos;
  }

  function toast(mensaje, error = false) {
    const nodo = $("#admin-toast");
    nodo.textContent = mensaje;
    nodo.classList.toggle("error", error);
    nodo.classList.add("visible");
    clearTimeout(estado.temporizadorToast);
    estado.temporizadorToast = setTimeout(() => nodo.classList.remove("visible"), error ? 6500 : 3500);
  }

  function ajustarEstadoOcupado(cambio) {
    estado.peticionesPendientes = Math.max(0, estado.peticionesPendientes + cambio);
    $("#administrador-app").setAttribute("aria-busy", estado.peticionesPendientes ? "true" : "false");
  }

  function controlQueDisparoLaAccion() {
    const activo = document.activeElement;
    if (activo instanceof HTMLButtonElement) return activo;
    return activo?.closest?.("form")?.querySelector('button[type="submit"]') || null;
  }

  function marcarControlPendiente(control, pendiente, etiqueta = "Procesando…") {
    if (!control) return;
    if (pendiente) {
      control.dataset.textoAntesDeEspera = control.textContent;
      control.dataset.deshabilitadoAntesDeEspera = control.disabled ? "1" : "0";
      control.textContent = etiqueta;
      control.disabled = true;
      control.setAttribute("aria-busy", "true");
      return;
    }
    if (!control.isConnected) return;
    control.textContent = control.dataset.textoAntesDeEspera || control.textContent;
    control.disabled = control.dataset.deshabilitadoAntesDeEspera === "1";
    control.removeAttribute("aria-busy");
    delete control.dataset.textoAntesDeEspera;
    delete control.dataset.deshabilitadoAntesDeEspera;
  }

  function fechaCorta(valor) {
    if (!valor) return "Sin fecha";
    const [fecha] = String(valor).split("T");
    const [anio, mes, dia] = fecha.split("-").map(Number);
    if (!anio || !mes || !dia) return valor;
    return new Intl.DateTimeFormat("es-MX", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(anio, mes - 1, dia));
  }

  function fechaHora(valor) {
    if (!valor) return "—";
    return new Intl.DateTimeFormat("es-MX", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(valor));
  }

  function mananaISO() {
    const fecha = new Date();
    fecha.setDate(fecha.getDate() + 1);
    const anio = fecha.getFullYear();
    const mes = String(fecha.getMonth() + 1).padStart(2, "0");
    const dia = String(fecha.getDate()).padStart(2, "0");
    return `${anio}-${mes}-${dia}`;
  }

  function mostrarPanel(nombre) {
    $$("[data-admin-panel]").forEach(panel => {
      const activo = panel.dataset.adminPanel === nombre;
      panel.hidden = !activo;
      panel.classList.toggle("activo", activo);
    });
    $$(".rail-item").forEach(boton => {
      const activo = boton.dataset.panel === nombre;
      boton.classList.toggle("activo", activo);
      if (activo) boton.setAttribute("aria-current", "page");
      else boton.removeAttribute("aria-current");
    });
    history.replaceState(null, "", `#${nombre}`);
    const movimientoReducido = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: movimientoReducido ? "auto" : "smooth" });
  }

  function pedirClave(titulo, ayuda) {
    const dialogo = $("#dialogo-clave");
    $("#dialogo-clave-titulo").textContent = titulo;
    $("#dialogo-clave-ayuda").textContent = ayuda || "Ingresa la clave de administrador para continuar.";
    $("#error-clave").hidden = true;
    $("#clave-administrador").value = "";
    dialogo.showModal();
    setTimeout(() => $("#clave-administrador").focus(), 40);
    return new Promise(resolve => { estado.resolucionClave = resolve; });
  }

  function resolverClave(valor) {
    const dialogo = $("#dialogo-clave");
    if (dialogo.open) dialogo.close();
    const resolver = estado.resolucionClave;
    estado.resolucionClave = null;
    resolver?.(valor);
  }

  async function autorizarEntrada() {
    while (true) {
      const clave = await pedirClave("Abrir administrador", "Esta pantalla administra el turno de la sucursal. Clave inicial: 1212.");
      if (!clave) {
        window.location.assign("/");
        return false;
      }
      try {
        await api("/api/administrador/acceso/", {
          method: "POST",
          body: JSON.stringify({ clave_administrador: clave }),
        });
        return true;
      } catch (error) {
        toast(error.message, true);
      }
    }
  }

  async function cargarResumen() {
    if (estado.promesaResumen) return estado.promesaResumen;
    const control = $("#actualizar-resumen");
    const promesa = (async () => {
      ajustarEstadoOcupado(1);
      marcarControlPendiente(control, true, "Actualizando…");
      try {
        let datos;
        try {
          datos = await api("/api/administrador/resumen/");
        } catch (error) {
          if (error.status !== 401 || !(await autorizarEntrada())) throw error;
          datos = await api("/api/administrador/resumen/");
        }
        estado.administrador = datos.administrador;
        renderTodo();
        return true;
      } catch (error) {
        toast(error.message, true);
        return false;
      } finally {
        marcarControlPendiente(control, false);
        ajustarEstadoOcupado(-1);
      }
    })();
    estado.promesaResumen = promesa;
    try {
      return await promesa;
    } finally {
      if (estado.promesaResumen === promesa) estado.promesaResumen = null;
    }
  }

  async function ejecutarConClave({ titulo, ayuda, url, method = "POST", cuerpo = {}, mensaje, refrescar = true }) {
    if (estado.operacionEnCurso) {
      toast("Espera a que termine la acción en curso.", true);
      return null;
    }
    const control = controlQueDisparoLaAccion();
    const clave = await pedirClave(titulo, ayuda);
    if (!clave) return null;
    if (estado.operacionEnCurso) {
      toast("Espera a que termine la acción en curso.", true);
      return null;
    }
    estado.operacionEnCurso = true;
    ajustarEstadoOcupado(1);
    marcarControlPendiente(control, true);
    const opciones = { method, body: JSON.stringify({ ...cuerpo, clave_administrador: clave }) };
    try {
      let datos;
      try {
        datos = await api(url, opciones);
      } catch (error) {
        if (error.status !== 401) throw error;
        await api("/api/administrador/acceso/", {
          method: "POST",
          body: JSON.stringify({ clave_administrador: clave }),
        });
        datos = await api(url, opciones);
      }
      if (mensaje) toast(mensaje);
      registrarImpresion(datos.impresiones);
      if (refrescar) await cargarResumen();
      return datos;
    } catch (error) {
      toast(error.message, true);
      return null;
    } finally {
      marcarControlPendiente(control, false);
      ajustarEstadoOcupado(-1);
      estado.operacionEnCurso = false;
    }
  }

  function registrarImpresion(impresiones = []) {
    const trabajo = impresiones.find(item => item.url);
    if (!trabajo) return;
    const enlace = $("#ultima-impresion");
    enlace.href = trabajo.url;
    enlace.hidden = false;
    toast("Reporte generado. Puedes abrir la vista previa desde Reportes y corte.");
  }

  function opcionesRepartidores(seleccionado = "") {
    const repartidores = estado.administrador?.repartidores || [];
    return `<option value="">Seleccionar repartidor</option>${repartidores.map(item => `<option value="${item.id}" ${item.id === seleccionado ? "selected" : ""}>${escapar(item.nombre)}</option>`).join("")}`;
  }

  function opcionesPosiciones() {
    const posiciones = estado.administrador?.posiciones_disponibles || [];
    return `<option value="">Posición libre</option>${posiciones.map(item => `<option value="${item.id}">${escapar(item.canal_etiqueta)} · ${escapar(item.nombre)}</option>`).join("")}`;
  }

  function tarjetaTicket(ticket, { asignar = false, programar = false } = {}) {
    const controles = asignar
      ? `<div class="ticket-controles"><select aria-label="Repartidor para ticket ${ticket.folio}">${opcionesRepartidores(ticket.repartidor_id)}</select><button class="boton mini primario" data-accion-ticket="asignar" type="button">Asignar</button></div>`
      : programar
        ? `<div class="ticket-controles"><input type="date" min="${mananaISO()}" value="${mananaISO()}" aria-label="Fecha para ticket ${ticket.folio}"><button class="boton mini primario" data-accion-ticket="programar" type="button">Programar</button></div>`
        : "";
    return `<article class="ticket-pendiente ${ticket.estado === "programado" ? "programado" : ""}" data-ticket-id="${ticket.id}">
      <div class="ticket-cabecera"><strong>${escapar(ticket.mesa || ticket.canal_etiqueta)}</strong><b>#${escapar(ticket.folio)}</b></div>
      <div class="ticket-cliente">${escapar(ticket.cliente_nombre || "Cliente sin nombre")}</div>
      <address>${escapar(ticket.cliente_domicilio || ticket.estado_etiqueta || "Sin domicilio capturado")}</address>
      <div class="ticket-pie"><strong>${dinero(ticket.total)}</strong>${ticket.fecha_programada ? `<time datetime="${ticket.fecha_programada}">${fechaCorta(ticket.fecha_programada)}</time>` : `<time>${ticket.terminal ? "Terminal" : "Efectivo"}</time>`}${controles}</div>
    </article>`;
  }

  function renderPendientes() {
    const admin = estado.administrador;
    $("#conteo-domicilios").textContent = admin.domicilios_sin_repartidor.length;
    $("#conteo-programados").textContent = admin.programados.length;
    $("#inicio-domicilios").innerHTML = admin.domicilios_sin_repartidor.length
      ? admin.domicilios_sin_repartidor.map(ticket => tarjetaTicket(ticket, { asignar: true })).join("")
      : '<p class="vacio">Todos los domicilios procesados ya tienen repartidor.</p>';
    $("#inicio-programados").innerHTML = admin.programados.length
      ? admin.programados.map(ticket => tarjetaTicket(ticket)).join("")
      : '<p class="vacio">No hay pedidos programados para fechas futuras.</p>';
    renderBloqueos("#inicio-bloqueos");
  }

  function renderBloqueos(selector) {
    const bloqueos = estado.administrador.bloqueos_corte || [];
    $(selector).innerHTML = bloqueos.length
      ? bloqueos.map(texto => `<div class="bloqueo">${escapar(texto)}</div>`).join("")
      : '<div class="bloqueo resuelto">Todo está conciliado. El corte de caja puede realizarse.</div>';
  }

  function renderTotales() {
    const totales = estado.administrador.totales_parciales || {};
    const canales = ["comedor", "llevar", "domicilio", "recoger"];
    const total = canales.reduce((suma, canal) => suma + Number(totales[canal] || 0), 0);
    canales.forEach(canal => { $(`#total-${canal}`).textContent = dinero(totales[canal]); });
    $("#total-general").textContent = dinero(total);
    renderBloqueos("#bloqueos-reporte");
  }

  function limpiarFormularioUsuario() {
    $("#form-usuario").reset();
    $("#usuario-id").value = "";
    $("#usuario-activo").checked = true;
    $("#usuario-clave").required = true;
    $("#titulo-form-usuario").textContent = "Nuevo usuario";
  }

  function renderPersonal() {
    const usuarios = estado.administrador.usuarios || [];
    $("#lista-usuarios").innerHTML = usuarios.length ? usuarios.map(usuario => `<button class="fila-persona ${usuario.activo ? "" : "inactivo"}" data-editar-usuario="${usuario.id}" type="button">
      <div><strong>${escapar(usuario.nombre)}</strong><span>${escapar(usuario.tipo_etiqueta)}</span></div>
      <span class="estado-chip">${usuario.activo ? "Activo" : "Inactivo"}</span><span>Editar</span>
    </button>`).join("") : '<p class="vacio">Aún no hay personal operativo registrado.</p>';
    $("#liquidacion-repartidor").innerHTML = opcionesRepartidores();
  }

  function renderDomicilios() {
    const tickets = (estado.administrador.tickets || []).filter(ticket => ticket.canal === "domicilio" && ticket.estado === "procesado");
    $("#lista-domicilios").innerHTML = tickets.length ? tickets.map(ticket => `<article class="fila-domicilio ${ticket.repartidor_id ? "asignado" : ""}" data-ticket-id="${ticket.id}">
      <div><strong>#${escapar(ticket.folio)} · ${escapar(ticket.cliente_nombre || "Sin nombre")}</strong><span>${escapar(ticket.cliente_domicilio || "Sin domicilio")} · ${ticket.terminal ? "Terminal" : "Efectivo"}</span></div>
      <b class="total-fila">${dinero(ticket.total)}</b>
      <div class="grupo-control asignacion-control"><select aria-label="Repartidor">${opcionesRepartidores(ticket.repartidor_id)}</select><button class="boton mini primario" data-accion-ticket="asignar" type="button">${ticket.repartidor_id ? "Cambiar" : "Asignar"}</button></div>
    </article>`).join("") : '<p class="vacio">No hay domicilios procesados en el turno.</p>';

    const administrables = (estado.administrador.tickets || []).filter(ticket => ["abierto", "procesado", "cobrar", "programado"].includes(ticket.estado));
    $("#lista-operaciones-ticket").innerHTML = administrables.length ? administrables.map(ticket => {
      const reasignable = ticket.estado !== "programado" && ticket.canal !== "sucursales";
      return `<article class="fila-operacion" data-ticket-id="${ticket.id}">
        <div><strong>#${escapar(ticket.folio)} · ${escapar(ticket.mesa)}</strong><small>${escapar(ticket.canal_etiqueta)} · ${escapar(ticket.estado_etiqueta)} · ${dinero(ticket.total)}</small></div>
        ${reasignable ? `<div class="grupo-control"><select aria-label="Nueva posición">${opcionesPosiciones()}</select><button class="boton mini" data-accion-ticket="reasignar" type="button">Mover</button></div>` : '<small>Sin reasignación disponible</small>'}
        <div class="grupo-control"><input type="number" min="0" max="100" step="0.01" value="${escapar(ticket.descuento_porcentaje)}" aria-label="Descuento porcentual"><button class="boton mini" data-accion-ticket="descuento" type="button">%</button></div>
        <button class="boton mini peligro" data-accion-ticket="cancelar" type="button">Cancelar</button>
      </article>`;
    }).join("") : '<p class="vacio">No hay pedidos activos para administrar.</p>';
  }

  function renderProgramados() {
    const procesados = (estado.administrador.tickets || []).filter(ticket => ticket.canal === "domicilio" && ticket.estado === "procesado");
    $("#lista-programables").innerHTML = procesados.length
      ? procesados.map(ticket => tarjetaTicket(ticket, { programar: true })).join("")
      : '<p class="vacio">No hay domicilios procesados que puedan programarse.</p>';
    $("#lista-programados").innerHTML = estado.administrador.programados.length
      ? estado.administrador.programados.map(ticket => tarjetaTicket(ticket)).join("")
      : '<p class="vacio">La agenda futura está vacía.</p>';
  }

  function renderMovimientos() {
    const movimientos = estado.administrador.movimientos || [];
    $("#lista-movimientos").innerHTML = movimientos.length ? movimientos.map(item => `<article class="fila-movimiento ${item.tipo}">
      <b>${item.tipo === "entrada" ? "+" : "−"}</b><div><strong>${escapar(item.concepto)}</strong><small>${fechaHora(item.creado_en)}</small></div><b>${dinero(item.importe)}</b>
    </article>`).join("") : '<p class="vacio">No hay entradas ni salidas en este turno.</p>';
  }

  function renderSucursales() {
    const sucursales = estado.administrador.sucursales || [];
    $("#lista-sucursales").innerHTML = sucursales.length ? sucursales.map(item => `<article class="tarjeta-sucursal" data-sucursal-id="${item.id}">
      <header><h2>${escapar(item.nombre)}</h2><b>${item.pendientes}</b></header>
      <p>${item.pendientes ? `${item.pendientes} pedido(s) procesado(s) se agruparán por producto en el ticket.` : "Sin pedidos procesados pendientes de corte."}</p>
      <button class="boton ${item.pendientes ? "primario" : "secundario"}" data-accion-sucursal="corte" type="button" ${item.pendientes ? "" : "disabled"}>Realizar corte</button>
    </article>`).join("") : '<p class="vacio">No hay sucursales cliente configuradas.</p>';
  }

  function renderTodo() {
    const admin = estado.administrador;
    $("#turno-inicio").textContent = `Desde ${fechaHora(admin.inicio_turno)}`;
    renderPendientes();
    renderTotales();
    renderPersonal();
    renderDomicilios();
    renderProgramados();
    renderMovimientos();
    renderSucursales();
  }

  async function accionTicket(boton) {
    const fila = boton.closest("[data-ticket-id]");
    const ticketId = fila?.dataset.ticketId;
    if (!ticketId) return;
    const accion = boton.dataset.accionTicket;
    if (accion === "asignar") {
      const repartidorId = fila.querySelector("select")?.value;
      if (!repartidorId) return toast("Selecciona un repartidor.", true);
      await ejecutarConClave({ titulo: "Asignar repartidor", ayuda: "Confirma quién llevará este domicilio.", url: `/api/administrador/tickets/${ticketId}/repartidor/`, cuerpo: { repartidor_id: repartidorId }, mensaje: "Repartidor asignado." });
      return;
    }
    if (accion === "programar") {
      const fecha = fila.querySelector('input[type="date"]')?.value;
      if (!fecha) return toast("Selecciona la fecha de preparación o entrega.", true);
      await ejecutarConClave({ titulo: "Programar domicilio", ayuda: `El pedido se activará al iniciar el programa el ${fechaCorta(fecha)}, sin esperar la hora.`, url: `/api/administrador/tickets/${ticketId}/programar/`, cuerpo: { fecha_programada: fecha }, mensaje: "Pedido agregado a la agenda futura." });
      return;
    }
    if (accion === "reasignar") {
      const mesaId = fila.querySelector("select")?.value;
      if (!mesaId) return toast("Selecciona una posición libre.", true);
      await ejecutarConClave({ titulo: "Reasignar pedido", ayuda: "El pedido conservará sus productos, estado e importe.", url: `/api/administrador/tickets/${ticketId}/reasignar/`, cuerpo: { mesa_id: mesaId }, mensaje: "Pedido reasignado." });
      return;
    }
    if (accion === "descuento") {
      const porcentaje = fila.querySelector('input[type="number"]')?.value;
      await ejecutarConClave({ titulo: "Aplicar descuento", ayuda: `Confirma el descuento de ${porcentaje || 0}% para este pedido.`, url: `/api/administrador/tickets/${ticketId}/descuento/`, cuerpo: { porcentaje }, mensaje: "Descuento actualizado." });
      return;
    }
    if (accion === "cancelar") {
      if (!window.confirm("¿Cancelar este pedido? Dejará de aparecer en reportes y cortes.")) return;
      await ejecutarConClave({ titulo: "Cancelar pedido", ayuda: "Esta acción libera su posición y excluye el pedido del corte.", url: `/api/administrador/tickets/${ticketId}/cancelar/`, mensaje: "Pedido cancelado." });
    }
  }

  document.addEventListener("click", async evento => {
    const navegacion = evento.target.closest("[data-panel], [data-panel-ir]");
    if (navegacion) {
      mostrarPanel(navegacion.dataset.panel || navegacion.dataset.panelIr);
      return;
    }
    const accion = evento.target.closest("[data-accion-ticket]");
    if (accion) {
      await accionTicket(accion);
      return;
    }
    const usuario = evento.target.closest("[data-editar-usuario]");
    if (usuario) {
      const datos = estado.administrador.usuarios.find(item => item.id === usuario.dataset.editarUsuario);
      if (!datos) return;
      $("#usuario-id").value = datos.id;
      $("#usuario-nombre").value = datos.nombre;
      $("#usuario-tipo").value = datos.tipo;
      $("#usuario-clave").value = "";
      $("#usuario-clave").required = false;
      $("#usuario-activo").checked = datos.activo;
      $("#titulo-form-usuario").textContent = "Editar usuario";
      $("#usuario-nombre").focus();
      return;
    }
    const accionGeneral = evento.target.closest("[data-accion]")?.dataset.accion;
    if (accionGeneral === "reporte-parcial") {
      await ejecutarConClave({ titulo: "Imprimir reporte parcial", ayuda: "El ticket mostrará el total de Comedor, Llevar, Domicilio, Recoger y la suma general.", url: "/api/administrador/reportes/parcial/", mensaje: "Reporte parcial enviado a impresión." });
      return;
    }
    if (accionGeneral === "corte-caja") {
      if (!window.confirm("¿Realizar el corte de caja? Iniciará un nuevo turno para los siguientes movimientos.")) return;
      await ejecutarConClave({ titulo: "Realizar corte de caja", ayuda: "Sólo continuará si no quedan cobros, repartidores o liquidaciones pendientes.", url: "/api/administrador/corte-caja/", mensaje: "Corte de caja generado." });
      return;
    }
    const corteSucursal = evento.target.closest("[data-accion-sucursal='corte']");
    if (corteSucursal) {
      const tarjeta = corteSucursal.closest("[data-sucursal-id]");
      await ejecutarConClave({ titulo: "Corte de sucursal", ayuda: "Todas sus partidas procesadas se agruparán en un ticket y quedarán cobradas.", url: "/api/administrador/corte-sucursal/", cuerpo: { cliente_sucursal_id: tarjeta.dataset.sucursalId }, mensaje: "Corte de sucursal generado." });
    }
  });

  $("#form-dialogo-clave").addEventListener("submit", evento => {
    evento.preventDefault();
    const clave = $("#clave-administrador").value.trim();
    if (!/^\d{4}$/.test(clave)) {
      $("#error-clave").textContent = "La clave debe contener exactamente 4 dígitos.";
      $("#error-clave").hidden = false;
      return;
    }
    resolverClave(clave);
  });
  $("#cancelar-clave").addEventListener("click", () => resolverClave(null));
  $("#dialogo-clave").addEventListener("cancel", evento => { evento.preventDefault(); resolverClave(null); });
  $("#actualizar-resumen").addEventListener("click", cargarResumen);
  $("#nuevo-usuario").addEventListener("click", limpiarFormularioUsuario);

  $("#form-usuario").addEventListener("submit", async evento => {
    evento.preventDefault();
    const id = $("#usuario-id").value;
    const cuerpo = {
      nombre: $("#usuario-nombre").value,
      tipo: $("#usuario-tipo").value,
      clave: $("#usuario-clave").value,
      activo: $("#usuario-activo").checked,
    };
    const resultado = await ejecutarConClave({ titulo: id ? "Actualizar usuario" : "Registrar usuario", ayuda: "Confirma los datos del personal operativo.", url: id ? `/api/administrador/usuarios/${id}/` : "/api/administrador/usuarios/", method: id ? "PATCH" : "POST", cuerpo, mensaje: id ? "Usuario actualizado." : "Usuario registrado." });
    if (resultado) limpiarFormularioUsuario();
  });

  $("#form-liquidacion").addEventListener("submit", async evento => {
    evento.preventDefault();
    const repartidorId = $("#liquidacion-repartidor").value;
    if (!repartidorId) return toast("Selecciona un repartidor.", true);
    await ejecutarConClave({ titulo: "Imprimir total de repartidor", ayuda: "Incluye efectivo, terminal, fondo y total a entregar.", url: "/api/administrador/liquidaciones/", cuerpo: { repartidor_id: repartidorId, fondo: $("#liquidacion-fondo").value || "0" }, mensaje: "Total de repartidor generado." });
  });

  $("#form-movimiento").addEventListener("submit", async evento => {
    evento.preventDefault();
    const resultado = await ejecutarConClave({ titulo: "Registrar movimiento de caja", ayuda: "Este importe formará parte del corte actual.", url: "/api/administrador/movimientos/", cuerpo: { tipo: $("#movimiento-tipo").value, concepto: $("#movimiento-concepto").value, importe: $("#movimiento-importe").value }, mensaje: "Movimiento registrado." });
    if (resultado) evento.currentTarget.reset();
  });

  $("#form-clave").addEventListener("submit", async evento => {
    evento.preventDefault();
    const nuevaClave = $("#nueva-clave").value;
    const resultado = await ejecutarConClave({ titulo: "Cambiar clave", ayuda: "Ingresa primero la clave administrativa vigente.", url: "/api/administrador/clave/", cuerpo: { nueva_clave: nuevaClave }, mensaje: "Clave de administrador actualizada.", refrescar: false });
    if (resultado) evento.currentTarget.reset();
  });

  const panelInicial = window.location.hash.slice(1);
  if ($(`[data-admin-panel="${CSS.escape(panelInicial)}"]`)) mostrarPanel(panelInicial);
  cargarResumen();
})();
