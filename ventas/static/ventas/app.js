(() => {
  "use strict";

  const productos = JSON.parse(document.getElementById("datos-productos").textContent);
  const posiciones = JSON.parse(document.getElementById("datos-posiciones").textContent);
  const permisos = JSON.parse(document.getElementById("datos-permisos").textContent);
  const nombresCanal = {
    comedor: "Comedor",
    llevar: "Llevar",
    domicilio: "Domicilio",
    recoger: "Recoger",
    sucursales: "Sucursales",
  };
  const canalesPareja = {
    comedor: "llevar",
    llevar: "comedor",
    domicilio: "recoger",
    recoger: "domicilio",
  };
  const productosAlFinal = new Set(["BBQ05", "BBQ1", "CO8", "CO05", "CO1"]);
  const terminosPreparacion = ["dorado", "medio", "blando"];
  const modificadores = [
    { codigo: "C/T", nombre: "CON TODO" },
    { codigo: "S/N", nombre: "SIN NADA" },
    { codigo: "CEB", nombre: "CEBOLLA" },
    { codigo: "CH G", nombre: "CHILE GÜERO" },
    { codigo: "CH V", nombre: "CHILE VERDE" },
    { codigo: "LLEVAR", nombre: "LLEVAR" },
  ];
  const comentariosGenerales = [
    { codigo: "TODO_PLATO", nombre: "TODO POR PLATO" },
    { codigo: "CEB_PLATO", nombre: "CEBOLLA POR PLATO" },
    { codigo: "CH_PLATO", nombre: "CHILE POR PLATO" },
    { codigo: "TODO_APARTE", nombre: "TODO A PARTE" },
    { codigo: "CEB_APARTE", nombre: "CEBOLLA A PARTE" },
    { codigo: "CH_APARTE", nombre: "CHILE A PARTE" },
    { codigo: "MAS_GUERO", nombre: "MÁS CHILE GÜERO" },
    { codigo: "MAS_VERDE", nombre: "MÁS CHILE VERDE" },
    { codigo: "MAS_CEB", nombre: "MÁS CEBOLLA" },
    ...modificadores,
  ];
  const opcionesSalsas = [
    "Con Todo", "Sin Nada", "Sólo Salsas", "Verde", "Roja", "Pepino", "Rábano", "Cebolla", "Limón",
    "Morada", "Serrano", "Cilantro", "Cacahuate", "Chipotle", "Mexicana", "Verde Tomate", "Habanero",
    "Roja Taquera",
  ];
  const prefijosSalsas = ["", "+ Más", "Nada más"];
  const DEVICE_ID_KEY = "tocayos_pos_device_id";
  const HEARTBEAT_BLOQUEO_MS = 10000;

  function crearDeviceId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const aleatorio = Math.random().toString(36).slice(2);
    return `tablet-${Date.now().toString(36)}-${aleatorio}`;
  }

  function obtenerDeviceId() {
    try {
      const guardado = localStorage.getItem(DEVICE_ID_KEY);
      if (guardado) return guardado;
      const nuevo = crearDeviceId();
      localStorage.setItem(DEVICE_ID_KEY, nuevo);
      return nuevo;
    } catch {
      return crearDeviceId();
    }
  }

  const estado = {
    deviceId: obtenerDeviceId(),
    canal: "comedor",
    persona: 1,
    modoMenu: "productos",
    objetivoModificador: { tipo: "persona", persona: 1 },
    edicion: null,
    colaEdicion: Promise.resolve(),
    errorEdicion: null,
    tickets: {},
    programados: [],
    ticket: null,
    operador: null,
    sucursalSeleccionada: null,
    resolucionClave: null,
    operando: false,
    resultadosClientes: [],
    temporizadorCliente: null,
    temporizadorNombre: null,
    temporizadorBloqueo: null,
    temporizadorSucursales: null,
    tokenBusquedaCliente: 0,
    clienteEditando: null,
    ultimoTerminoPorProducto: new Map(),
    prefijoSalsa: "",
    modoEntrega: "aproximada",
    entregaProgramadaDigitos: "",
    botonOperacion: null,
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const csrf = () => document.cookie.split("; ").find(v => v.startsWith("csrftoken="))?.split("=")[1] || "";
  const dinero = (valor) => new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(Number(valor || 0));
  const cantidad = (valor) => Number(valor).toLocaleString("es-MX", { maximumFractionDigits: 3 });
  const escapar = (valor) => String(valor ?? "").replace(/[&<>'"]/g, caracter => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[caracter]);

  async function api(url, opciones = {}) {
    const metodo = (opciones.method || "GET").toUpperCase();
    let body = opciones.body;
    if (requiereContratoTicket(url, metodo)) {
      body = cuerpoConContratoTicket(body);
    }
    const respuesta = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      ...opciones,
      body,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf(),
        "X-POS-Device-ID": estado.deviceId,
        ...(opciones.headers || {}),
      },
    });
    if (respuesta.status === 401) {
      const siguiente = encodeURIComponent(`${window.location.pathname}${window.location.search}`);
      window.location.replace(`/acceso/?next=${siguiente}`);
      throw new Error("La sesión expiró. Inicia sesión nuevamente.");
    }
    let datos;
    try { datos = await respuesta.json(); } catch { datos = {}; }
    if (!respuesta.ok) {
      if ([409, 423].includes(respuesta.status) && datos.ticket && estado.ticket?.id === datos.ticket.id) {
        estado.ticket = datos.ticket;
        actualizarVistaPorBloqueo();
      }
      const error = new Error(datos.error || "No fue posible completar la operación.");
      error.datos = datos;
      error.status = respuesta.status;
      throw error;
    }
    return datos;
  }

  function requiereContratoTicket(url, metodo) {
    if (!estado.ticket || ["GET", "HEAD"].includes(metodo)) return false;
    if (url.includes(`/api/tickets/${estado.ticket.id}/bloqueo/`)) return false;
    return url.includes(`/api/tickets/${estado.ticket.id}/`) || url.startsWith("/api/partidas/");
  }

  function cuerpoConContratoTicket(body) {
    let datos = {};
    if (typeof body === "string" && body.trim()) {
      datos = JSON.parse(body);
    } else if (body && typeof body === "object") {
      return body;
    }
    if (!datos || typeof datos !== "object" || Array.isArray(datos)) return body;
    return JSON.stringify({
      ...datos,
      device_id: estado.deviceId,
      version_entidad: datos.version_entidad ?? estado.ticket.version_entidad,
    });
  }

  function ticketActivo(ticket = estado.ticket) {
    return ["abierto", "procesado", "cobrar"].includes(ticket?.estado);
  }

  function bloqueoPropio(ticket = estado.ticket) {
    return Boolean(ticket?.bloqueo?.activo && ticket.bloqueo.es_mio);
  }

  function bloqueoDeOtro(ticket = estado.ticket) {
    return Boolean(ticket?.bloqueo?.activo && !ticket.bloqueo.es_mio);
  }

  function textoBloqueo(ticket = estado.ticket) {
    const bloqueo = ticket?.bloqueo;
    if (!bloqueo?.activo) return "";
    if (bloqueo.es_mio) return `Tomada por ${estado.operador?.nombre || "esta tableta"}`;
    return `Tomada por ${bloqueo.tomado_por || "otra tableta"}`;
  }

  function actualizarIndicadorBloqueoTicket() {
    const nodo = $("#ticket-bloqueo");
    if (!nodo || !estado.ticket) return;
    const texto = textoBloqueo();
    nodo.hidden = !texto;
    nodo.textContent = texto;
    nodo.classList.toggle("ajeno", bloqueoDeOtro());
  }

  function actualizarVistaPorBloqueo() {
    if (!estado.ticket) return;
    actualizarIndicadorBloqueoTicket();
    renderMenu();
    renderComanda();
    renderAcciones();
  }

  function iniciarHeartbeatBloqueo() {
    clearInterval(estado.temporizadorBloqueo);
    estado.temporizadorBloqueo = null;
    if (!ticketActivo() || !bloqueoPropio()) return;
    estado.temporizadorBloqueo = setInterval(() => renovarBloqueoTicket(), HEARTBEAT_BLOQUEO_MS);
  }

  async function renovarBloqueoTicket() {
    if (!ticketActivo() || !bloqueoPropio()) {
      clearInterval(estado.temporizadorBloqueo);
      estado.temporizadorBloqueo = null;
      return;
    }
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/bloqueo/`, {
        method: "POST",
        body: JSON.stringify({ device_id: estado.deviceId }),
      });
      estado.ticket = datos.ticket;
      actualizarIndicadorBloqueoTicket();
      renderAcciones();
    } catch (error) {
      clearInterval(estado.temporizadorBloqueo);
      estado.temporizadorBloqueo = null;
      toast(error.message, true);
    }
  }

  async function liberarBloqueoActual() {
    clearInterval(estado.temporizadorBloqueo);
    estado.temporizadorBloqueo = null;
    const ticket = estado.ticket;
    if (!bloqueoPropio(ticket)) return;
    try {
      await api(`/api/tickets/${ticket.id}/bloqueo/`, {
        method: "DELETE",
        body: JSON.stringify({ device_id: estado.deviceId }),
      });
    } catch {
      /* Si la red se cayó, el lease expira solo. */
    }
  }

  function liberarBloqueoEnSalida() {
    const ticket = estado.ticket;
    if (!bloqueoPropio(ticket)) return;
    fetch(`/api/tickets/${ticket.id}/bloqueo/`, {
      method: "DELETE",
      cache: "no-store",
      credentials: "same-origin",
      keepalive: true,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf(),
        "X-POS-Device-ID": estado.deviceId,
      },
      body: JSON.stringify({ device_id: estado.deviceId }),
    }).catch(() => {});
  }

  function cerrarToast() {
    const nodo = $("#toast");
    clearTimeout(toast.temporizador);
    nodo.classList.remove("visible", "persistente");
  }

  function toast(mensaje, error = false) {
    const nodo = $("#toast");
    nodo.setAttribute("role", error ? "alert" : "status");
    nodo.setAttribute("aria-live", error ? "assertive" : "polite");
    $("#toast-mensaje").textContent = mensaje;
    nodo.classList.toggle("error", error);
    nodo.classList.toggle("persistente", error);
    nodo.classList.add("visible");
    clearTimeout(toast.temporizador);
    if (!error) toast.temporizador = setTimeout(cerrarToast, 3300);
  }

  function pedirClavePos(titulo, ayuda) {
    const dialogo = $("#dialogo-clave-pos");
    $("#titulo-clave-pos").textContent = titulo;
    $("#ayuda-clave-pos").textContent = ayuda;
    $("#clave-pos").value = "";
    $("#error-clave-pos").hidden = true;
    dialogo.showModal();
    setTimeout(() => $("#clave-pos").focus(), 40);
    return new Promise(resolve => { estado.resolucionClave = resolve; });
  }

  function resolverClavePos(clave) {
    const dialogo = $("#dialogo-clave-pos");
    if (dialogo.open) dialogo.close();
    const resolver = estado.resolucionClave;
    estado.resolucionClave = null;
    resolver?.(clave);
  }

  function renderOperadorActual() {
    const contenedor = $("#operador-actual");
    const nombre = $("#operador-actual-nombre");
    if (!contenedor || !nombre) return;
    const operador = estado.operador?.nombre?.trim() || "";
    nombre.textContent = operador;
    contenedor.hidden = !operador;
  }

  async function entrarComoMesero({ mostrarPantalla = true } = {}) {
    const clave = await pedirClavePos("Código de mesero", "Ingresa el código de 4 dígitos asignado a tu nombre.");
    if (!clave) return false;
    try {
      const datos = await api("/api/operador/identificar/", {
        method: "POST",
        body: JSON.stringify({ clave }),
      });
      estado.operador = datos.operador;
      renderOperadorActual();
      if (mostrarPantalla) {
        $("#pantalla-acceso")?.classList.add("oculto");
        $("main").classList.remove("oculto");
        document.body.classList.add("en-operacion");
        document.body.classList.remove("en-ticket");
        await cargarEstado(estado.canal === "sucursales");
        programarSincronizacionSucursales();
      }
      toast(`Turno identificado: ${datos.operador.nombre}.`);
      return true;
    } catch (error) {
      toast(error.message, true);
      return false;
    }
  }

  async function abrirAdministrador() {
    const clave = await pedirClavePos("Clave de administrador", "Autoriza el acceso a la gestión del turno de esta sucursal.");
    if (!clave) return;
    try {
      const datos = await api("/api/administrador/acceso/", {
        method: "POST",
        body: JSON.stringify({ clave_administrador: clave }),
      });
      window.location.assign(datos.destino || "/administrador/");
    } catch (error) {
      toast(error.message, true);
    }
  }

  function bloquear(valor) {
    estado.operando = valor;
    document.body.setAttribute("aria-busy", String(valor));
    if (valor) {
      const boton = document.activeElement?.closest?.("button");
      if (boton && !boton.disabled) {
        const etiquetas = {
          procesar: "Procesando orden…",
          cobrar: "Registrando cobro…",
          "cancelar-orden": "Cancelando orden…",
          reimprimir: "Preparando impresión…",
        };
        boton.dataset.textoCarga = etiquetas[boton.id] || "Cargando…";
        boton.dataset.ariaOperacionOriginal = boton.getAttribute("aria-label") || "";
        boton.classList.add("operando");
        boton.setAttribute("aria-busy", "true");
        boton.setAttribute("aria-label", boton.dataset.textoCarga);
        estado.botonOperacion = boton;
      }
    } else if (estado.botonOperacion) {
      const boton = estado.botonOperacion;
      boton.classList.remove("operando");
      boton.removeAttribute("aria-busy");
      if (boton.dataset.ariaOperacionOriginal) boton.setAttribute("aria-label", boton.dataset.ariaOperacionOriginal);
      else boton.removeAttribute("aria-label");
      delete boton.dataset.textoCarga;
      delete boton.dataset.ariaOperacionOriginal;
      estado.botonOperacion = null;
    }
    $$("button").forEach(boton => boton.disabled = valor);
    if (!valor && estado.ticket) {
      renderMenu();
      renderComanda();
      renderAcciones();
    }
  }

  async function cargarEstado(sincronizarSucursales = false) {
    try {
      if (sincronizarSucursales && permisos.sincronizar) {
        await api("/api/sincronizacion/sucursales/", { method: "POST", body: "{}" });
      }
      const datos = await api("/api/estado/");
      estado.tickets = datos.tickets;
      estado.programados = datos.programados || [];
      renderPosiciones();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function ticketResumenEstado(ticket) {
    return {
      ticket_id: ticket.id,
      folio: ticket.folio,
      estado: ticket.estado,
      total: ticket.total,
      version_entidad: ticket.version_entidad,
      bloqueo: ticket.bloqueo,
    };
  }

  function renderPosiciones() {
    const contenedor = $("#rejilla-posiciones");
    contenedor.setAttribute("aria-label", `Posiciones de ${nombresCanal[estado.canal] || estado.canal}`);
    const renderTarjeta = (posicion, opciones = {}) => {
      const ticket = estado.tickets[posicion.id];
      const claseBase = ticket ? (ticket.estado === "abierto" ? "ocupada" : "procesada") : "libre";
      const claseBloqueo = ticket?.bloqueo?.activo ? (ticket.bloqueo.es_mio ? " propia" : " bloqueada") : "";
      const clase = `${claseBase}${claseBloqueo}`;
      const etiquetaBloqueo = ticket?.bloqueo?.activo
        ? (ticket.bloqueo.es_mio ? "Tomada por esta tableta" : `Tomada por ${ticket.bloqueo.tomado_por || "otra tableta"}`)
        : "";
      const etiquetaEstado = ticket ? (etiquetaBloqueo || (ticket.estado === "abierto" ? "Orden abierta" : "Procesada")) : "Libre";
      const detalle = ticket ? `Ticket ${ticket.folio} · ${dinero(ticket.total)}` : "Disponible";
      const partes = String(posicion.nombre).match(/^(.*?)[\s-]*(\d+)$/);
      const tipo = partes ? partes[1].trim() : "Posición";
      const numero = partes ? partes[2] : posicion.nombre;
      const tipoVisible = opciones.tipoVisible || tipo;
      const etiquetaAccesible = `${posicion.nombre}. ${etiquetaEstado}. ${detalle}`;
      return `<button class="posicion ${clase}" data-id="${posicion.id}" data-estado="${clase}" type="button" aria-label="${escapar(etiquetaAccesible)}">
        <span class="posicion-estado"><i aria-hidden="true"></i>${etiquetaEstado}</span>
        <strong><span class="posicion-tipo">${escapar(tipoVisible)}</span><span class="posicion-numero">${escapar(numero)}</span></strong>
        <small>${escapar(detalle)}</small>
      </button>`;
    };
    const renderTarjetas = canal => posiciones
      .filter(posicion => posicion.canal === canal)
      .sort((a, b) => a.orden - b.orden)
      .map(renderTarjeta).join("");
    if (estado.canal === "sucursales") {
      const grupos = new Map();
      for (const posicion of posiciones
        .filter(item => item.canal === "sucursales" && item.cliente_sucursal_id)
        .sort((a, b) => a.cliente_sucursal_orden - b.cliente_sucursal_orden || a.orden - b.orden)) {
        if (!grupos.has(posicion.cliente_sucursal_id)) {
          grupos.set(posicion.cliente_sucursal_id, {
            nombre: posicion.cliente_sucursal_nombre,
            orden: posicion.cliente_sucursal_orden,
            posiciones: [],
          });
        }
        grupos.get(posicion.cliente_sucursal_id).posiciones.push(posicion);
      }
      contenedor.className = "rejilla-posiciones rejilla-sucursales";
      const grupoSeleccionado = estado.sucursalSeleccionada ? grupos.get(estado.sucursalSeleccionada) : null;
      if (grupoSeleccionado) {
        const ocupadas = grupoSeleccionado.posiciones.filter(posicion => estado.tickets[posicion.id]).length;
        contenedor.innerHTML = `<section class="pantalla-sucursal">
          <header class="pantalla-sucursal-cabecera">
            <button class="boton volver-sucursales" data-volver-sucursales type="button">← Sucursales</button>
            <div><strong>${escapar(grupoSeleccionado.nombre)}</strong><small>${ocupadas} pedido${ocupadas === 1 ? "" : "s"} activo${ocupadas === 1 ? "" : "s"} · ${grupoSeleccionado.posiciones.length} casillas</small></div>
          </header>
          <div class="casillas-sucursal">${grupoSeleccionado.posiciones.map(posicion => renderTarjeta(posicion, { tipoVisible: "Pedido" })).join("")}</div>
        </section>`;
      } else {
        estado.sucursalSeleccionada = null;
        contenedor.innerHTML = `<section class="selector-sucursales">
          <header><strong>Sucursales</strong><small>Selecciona una para abrir sus casillas</small></header>
          <div>${[...grupos.entries()].map(([id, grupo]) => {
            const ocupadas = grupo.posiciones.filter(posicion => estado.tickets[posicion.id]).length;
            const procesadas = grupo.posiciones.filter(posicion => ["procesado", "cobrar"].includes(estado.tickets[posicion.id]?.estado)).length;
            return `<button class="tarjeta-sucursal-pos" data-sucursal-id="${id}" type="button" aria-label="Abrir ${escapar(grupo.nombre)}. ${ocupadas} pedidos activos.">
              <span class="sucursal-pos-estado"><i aria-hidden="true"></i>${procesadas ? `${procesadas} por cerrar` : "Sin cierres urgentes"}</span>
              <strong>${escapar(grupo.nombre)}</strong>
              <span class="sucursal-pos-conteo"><b>${ocupadas}</b><small>Pedidos activos</small></span>
              <span class="sucursal-pos-accion">Ver ${grupo.posiciones.length} casillas →</span>
            </button>`;
          }).join("")}</div>
        </section>` || '<p class="vacio">No hay sucursales configuradas.</p>';
      }
      return;
    }
    const secundario = estado.canal === "comedor" ? "llevar" : (estado.canal === "domicilio" ? "recoger" : "");
    const principales = renderTarjetas(estado.canal);
    if (!secundario) {
      contenedor.className = "rejilla-posiciones rejilla-simple";
      contenedor.innerHTML = principales || '<p class="vacio">No hay posiciones configuradas.</p>';
      return;
    }
    const auxiliares = renderTarjetas(secundario);
    contenedor.className = "rejilla-posiciones rejilla-dividida";
    const programados = estado.canal === "domicilio" && estado.programados.length
      ? `<section class="pedidos-programados-pos">
          <header><strong>Programados</strong><small>Se activarán por fecha en la primera casilla libre</small></header>
          <div>${estado.programados.map(ticket => `<article class="programado-pos">
            <span>Programado</span><strong>#${escapar(ticket.folio)} · ${escapar(ticket.cliente_nombre || "Cliente")}</strong>
            <small>${escapar(ticket.fecha_programada)}${ticket.entrega_aproximada ? ` · ${escapar(ticket.entrega_aproximada)}` : ""}</small><b>${dinero(ticket.total)}</b>
          </article>`).join("")}</div>
        </section>`
      : "";
    contenedor.innerHTML = `
      <section class="grupo-posiciones grupo-principal">
        <header><strong>${escapar(nombresCanal[estado.canal])}</strong><small>Pedidos activos y posiciones disponibles</small></header>
        <div>${principales || '<p class="vacio">No hay posiciones configuradas.</p>'}</div>${programados}
      </section>
      <section class="grupo-posiciones grupo-auxiliar">
        <header><strong>${escapar(nombresCanal[secundario])}</strong><small>${secundario === "recoger" ? "Nombre y celular" : "Nombre del cliente"}</small></header>
        <div>${auxiliares || '<p class="vacio">No hay posiciones configuradas.</p>'}</div>
      </section>`;
  }

  function programarSincronizacionSucursales() {
    clearInterval(estado.temporizadorSucursales);
    estado.temporizadorSucursales = null;
    if (estado.canal === "sucursales") {
      if (permisos.sincronizar) {
        estado.temporizadorSucursales = setInterval(() => cargarEstado(true), 300000);
      }
    }
  }

  async function cambiarCanal(canal) {
    estado.canal = canal;
    estado.sucursalSeleccionada = null;
    $$(".canal").forEach(b => {
      const activo = b.dataset.canal === canal;
      b.classList.toggle("activo", activo);
      b.setAttribute("aria-pressed", String(activo));
    });
    renderPosiciones();
    programarSincronizacionSucursales();
    if (canal === "sucursales") await cargarEstado(permisos.sincronizar);
  }

  async function abrirPosicion(mesaId) {
    if (estado.operando) return;
    if (!estado.operador) {
      const identificado = await entrarComoMesero({ mostrarPantalla: false });
      if (!identificado) return;
    }
    bloquear(true);
    try {
      const datos = await api("/api/tickets/abrir/", {
        method: "POST",
        body: JSON.stringify({ mesa_id: mesaId, device_id: estado.deviceId }),
      });
      estado.ticket = datos.ticket;
      mostrarTicket();
    } catch (error) {
      if (error.status === 423 && error.datos?.ticket) {
        estado.tickets[error.datos.ticket.mesa_id] = ticketResumenEstado(error.datos.ticket);
        renderPosiciones();
      }
      toast(error.message, true);
    } finally {
      bloquear(false);
    }
  }

  function mostrarTicket() {
    const ticket = estado.ticket;
    const esSucursal = ticket.canal === "sucursales";
    document.body.classList.add("en-ticket");
    $("#vista-posiciones").classList.add("oculto");
    $("#vista-ticket").classList.remove("oculto");
    $("#vista-ticket").classList.toggle("ticket-sucursal", esSucursal);
    $("#ticket-mesa").textContent = ticket.mesa;
    $("#ticket-folio").textContent = ticket.folio;
    actualizarIndicadorBloqueoTicket();
    iniciarHeartbeatBloqueo();
    estado.modoEntrega = ticket.tipo_entrega || "aproximada";
    estado.entregaProgramadaDigitos = (ticket.tipo_entrega === "programada" ? ticket.entrega_aproximada : "")?.replace(":", "") || "";
    $("#terminal").checked = Boolean(ticket.terminal);
    $("#paga-con").value = ticket.paga_con || "";
    $("#comentario").value = ticket.comentario_general || "";
    actualizarControlEntrega();
    const esDomicilio = ticket.canal === "domicilio";
    const esRecoger = ticket.canal === "recoger";
    const esLlevar = ticket.canal === "llevar";
    const esEntrega = esDomicilio || esRecoger;
    const esDirecto = esRecoger || esLlevar;
    $(".panel-orden").classList.toggle("con-domicilio", esEntrega || esDirecto);
    $("#datos-cliente").classList.toggle("oculto", !esDomicilio);
    $("#datos-servicio-directo").classList.toggle("oculto", !esDirecto);
    $("#pago-domicilio").classList.toggle("oculto", !esEntrega);
    $(".campo-entrega").classList.toggle("oculto", !esEntrega);
    $("#cliente-directo-telefono-label").classList.toggle("oculto", !esRecoger);
    $("#servicio-directo-eyebrow").textContent = esRecoger ? "Pedido para recoger" : "Pedido para llevar";
    $("#servicio-directo-titulo").textContent = esRecoger ? "Nombre y celular" : "Nombre del cliente";
    $("#cliente-directo-nombre").value = esDirecto ? (ticket.cliente?.nombre || "") : "";
    $("#cliente-directo-telefono").value = esRecoger ? (ticket.cliente?.telefono || "") : "";
    const admiteSwitches = !esSucursal && Object.hasOwn(canalesPareja, ticket.canal);
    $("#ticket-switches").classList.toggle("oculto", !admiteSwitches);
    $("#switch-tipo-pedido").checked = esRecoger || esLlevar;
    $("#switch-modo-nombres").checked = Boolean(ticket.captura_por_nombres);
    $("#switch-tipo-pedido").disabled = ticket.estado !== "abierto";
    $("#switch-modo-nombres").disabled = ticket.estado !== "abierto";
    $("#tipo-pedido-etiqueta").textContent = esEntrega ? (esRecoger ? "Recoger" : "Domicilio") : (esLlevar ? "Llevar" : "Mesa");
    $("#modo-nombres-etiqueta").textContent = ticket.captura_por_nombres ? "Por nombres" : "Normal";
    $("#nombre-persona-panel").classList.toggle("oculto", esSucursal || !ticket.captura_por_nombres);
    $(".panel-personas").classList.toggle("oculto", esSucursal);
    $(".datos-orden").classList.toggle("oculto", esSucursal);
    $(".orden-encabezado .eyebrow").textContent = esSucursal ? "Pedido mayorista en tiempo real" : "Comanda en tiempo real";
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
    if (estado.ticket?.canal === "sucursales") {
      $("#personas").innerHTML = "";
      return;
    }
    const nombres = estado.ticket?.nombres_comensales || {};
    const porNombres = Boolean(estado.ticket?.captura_por_nombres);
    $("#personas").classList.toggle("con-nombres", porNombres);
    $("#personas").innerHTML = Array.from({ length: 24 }, (_, i) => i + 1).map(numero => {
      const nombre = nombres[String(numero)] || "";
      return `<button class="persona ${estado.persona === numero ? "activa" : ""} ${nombre ? "con-nombre" : ""}" data-persona="${numero}" type="button"><b>${numero}</b>${porNombres ? `<small>${escapar(nombre || "Sin nombre")}</small>` : ""}</button>`;
    }).join("");
    actualizarNombrePersona();
  }

  function actualizarNombrePersona() {
    const panel = $("#nombre-persona-panel");
    if (!panel || !estado.ticket) return;
    panel.classList.toggle("oculto", !estado.ticket.captura_por_nombres);
    $("#nombre-persona-numero").textContent = estado.persona;
    $("#nombre-persona").value = estado.ticket.nombres_comensales?.[String(estado.persona)] || "";
  }

  async function guardarNombrePersona({ avanzar = false } = {}) {
    if (!estado.ticket?.captura_por_nombres || estado.ticket.estado !== "abierto") return;
    const nombres = { ...(estado.ticket.nombres_comensales || {}) };
    const valor = $("#nombre-persona").value.trim();
    if (valor) nombres[String(estado.persona)] = valor;
    else delete nombres[String(estado.persona)];
    const datos = await api(`/api/tickets/${estado.ticket.id}/`, {
      method: "PATCH",
      body: JSON.stringify({ nombres_comensales: nombres }),
    });
    estado.ticket = datos.ticket;
    if (avanzar && estado.persona < 24) estado.persona += 1;
    renderPersonas();
    renderComanda();
    if (avanzar) $("#nombre-persona").focus();
  }

  async function convertirTipoPedido() {
    if (!estado.ticket || !(await finalizarEdicion())) return;
    const destino = canalesPareja[estado.ticket.canal];
    if (!destino) return;
    bloquear(true);
    try {
      await guardarDatos();
      const datos = await api(`/api/tickets/${estado.ticket.id}/convertir/`, {
        method: "POST",
        body: JSON.stringify({ canal: destino }),
      });
      estado.ticket = datos.ticket;
      mostrarTicket();
      await cargarEstado();
      toast(`Pedido cambiado a ${nombresCanal[destino]}.`);
    } catch (error) {
      $("#switch-tipo-pedido").checked = ["recoger", "llevar"].includes(estado.ticket.canal);
      toast(error.message, true);
    } finally { bloquear(false); }
  }

  async function alternarCapturaPorNombres() {
    if (!estado.ticket || !(await finalizarEdicion())) return;
    const activo = $("#switch-modo-nombres").checked;
    bloquear(true);
    try {
      if (estado.ticket.captura_por_nombres) {
        clearTimeout(estado.temporizadorNombre);
        await guardarNombrePersona();
      }
      const datos = await api(`/api/tickets/${estado.ticket.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ captura_por_nombres: activo }),
      });
      estado.ticket = datos.ticket;
      mostrarTicket();
      if (activo) $("#nombre-persona").focus();
    } catch (error) {
      $("#switch-modo-nombres").checked = !activo;
      toast(error.message, true);
    } finally { bloquear(false); }
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

  function configurarAtajosMenu(nombresSecciones = []) {
    const contexto = $("#menu-contexto");
    const atajos = $("#atajos-menu");
    const mostrar = estado.modoMenu === "productos" && nombresSecciones.length > 0;
    if (contexto) contexto.hidden = mostrar;
    if (!atajos) return;
    atajos.hidden = !mostrar;
    if (!mostrar) return;
    atajos.innerHTML = nombresSecciones.slice(0, 7).map((nombre, indice) => {
      const numero = indice + 1;
      const etiqueta = `${numero}. ${nombre}`;
      return `<button data-menu-atajo="${numero}" type="button" aria-controls="menu-seccion-${numero}" aria-label="${escapar(`Ir a ${etiqueta}`)}" title="${escapar(etiqueta)}" ${numero === 1 ? 'aria-current="location"' : ""}>${numero}</button>`;
    }).join("");
  }

  function desplazarASeccionMenu(numero) {
    if (estado.modoMenu !== "productos") return;
    const contenedor = $("#productos");
    const destino = $(`#menu-seccion-${numero}`);
    if (!contenedor || !destino) return;
    const desplazamiento = destino.getBoundingClientRect().top
      - contenedor.getBoundingClientRect().top
      + contenedor.scrollTop;
    const comportamiento = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth";
    if (typeof contenedor.scrollTo === "function") {
      contenedor.scrollTo({ top: Math.max(0, desplazamiento), behavior: comportamiento });
    } else {
      contenedor.scrollTop = Math.max(0, desplazamiento);
    }
    $$("#atajos-menu [data-menu-atajo]").forEach(boton => {
      if (boton.dataset.menuAtajo === String(numero)) boton.setAttribute("aria-current", "location");
      else boton.removeAttribute("aria-current");
    });
  }

  function renderMenu() {
    const abierto = estado.ticket?.estado === "abierto";
    const esSucursal = estado.ticket?.canal === "sucursales";
    const modoCalculadora = ["calculadora", "calculadora-sucursal"].includes(estado.modoMenu);
    configurarAtajosMenu();
    $(".panel-productos")?.classList.toggle("modo-calculadora", modoCalculadora);
    $("#productos")?.classList.toggle("modo-calculadora", modoCalculadora);
    const volverProductos = $("#menu-productos");
    volverProductos.classList.toggle("oculto", ["productos", "calculadora", "calculadora-sucursal"].includes(estado.modoMenu));
    if (esSucursal) {
      if (estado.modoMenu === "calculadora-sucursal" && estado.edicion) {
        const producto = (estado.ticket.catalogo_sucursal || []).find(item => item.id === estado.edicion.productoId);
        if (!producto) return;
        $("#menu-contexto").textContent = "Cantidad del producto";
        $("#menu-indicacion").textContent = `${producto.unidad} · ${dinero(producto.precio)}${Number(producto.cantidad_por_precio) !== 1 ? ` cada ${cantidad(producto.cantidad_por_precio)} ${producto.unidad}` : ""}`;
        $("#productos").innerHTML = `
          <section class="calculadora-cantidad calculadora-sucursal">
            <div class="calculadora-producto"><strong>${escapar(producto.nombre)}</strong><span>${escapar(producto.unidad)}</span></div>
            <output class="pantalla-cantidad" aria-label="Cantidad">${escapar(estado.edicion.cantidadPantalla)}</output>
            <div class="teclado-cantidad teclado-cantidad-sucursal">
              ${[7, 8, 9, 4, 5, 6, 1, 2, 3].map(numero => `<button data-tecla-sucursal="${numero}" type="button">${numero}</button>`).join("")}
              <button data-tecla-sucursal="." type="button">.</button>
              <button data-tecla-sucursal="0" type="button">0</button>
              <button class="borrar" data-tecla-sucursal="borrar" type="button" aria-label="Borrar un dígito">←</button>
              <button class="eliminar" data-tecla-sucursal="eliminar" type="button">Eliminar producto</button>
            </div>
            <button class="confirmar-edicion" data-confirmar-sucursal type="button" aria-label="Guardar cantidad" title="Guardar cantidad">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>
            </button>
          </section>`;
        return;
      }
      $("#menu-contexto").textContent = "Productos de sucursal";
      $("#menu-indicacion").textContent = "Selecciona un producto y captura su cantidad";
      const capturados = new Set(estado.ticket.partidas.map(partida => partida.producto_sucursal_id));
      $("#productos").innerHTML = `<section class="catalogo-sucursal">${(estado.ticket.catalogo_sucursal || []).map(producto => `
        <button class="producto producto-sucursal ${capturados.has(producto.id) ? "en-pedido" : ""}" data-sucursal-producto="${producto.id}" type="button" ${!abierto ? "disabled" : ""}>
          <small>${escapar(producto.nombre_ticket)} · ${escapar(producto.unidad)}</small>
          <strong>${escapar(producto.nombre)}</strong>
          <b>${dinero(producto.precio)}</b>
        </button>`).join("")}</section>`;
      return;
    }
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
    if (estado.modoMenu === "entrega") {
      $("#menu-contexto").textContent = "Hora de entrega";
      $("#menu-indicacion").textContent = "Elige un tiempo aproximado o captura una hora programada";
      const minutos = Array.from({ length: 12 }, (_, indice) => (indice + 1) * 10);
      const etiquetaMinutos = valor => valor < 60 ? String(valor) : (valor === 60 ? "1 hr" : valor === 120 ? "2 hr" : `1:${String(valor - 60).padStart(2, "0")} hr`);
      const programada = String(estado.entregaProgramadaDigitos || "").padStart(4, "0");
      $("#productos").innerHTML = `
        <section class="selector-entrega-panel">
          <div class="selector-entrega-tipos">
            <button class="${estado.modoEntrega === "aproximada" ? "activo" : ""}" data-modo-entrega="aproximada" type="button">Aproximado</button>
            <button class="${estado.modoEntrega === "programada" ? "activo" : ""}" data-modo-entrega="programada" type="button">Programado</button>
          </div>
          ${estado.modoEntrega === "aproximada" ? `
            <div class="tiempos-aproximados">
              ${minutos.map(valor => `<button data-minutos-entrega="${valor}" type="button">${etiquetaMinutos(valor)}</button>`).join("")}
            </div>` : `
            <output class="pantalla-hora-programada">${programada.slice(0, 2)}:${programada.slice(2)}</output>
            <div class="teclado-hora-programada">
              ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map(numero => `<button data-tecla-entrega="${numero}" type="button">${numero}</button>`).join("")}
              <button class="borrar" data-tecla-entrega="borrar" type="button">←</button>
              <button data-tecla-entrega="0" type="button">0</button>
              <button class="confirmar" data-confirmar-entrega type="button">✓</button>
            </div>`}
        </section>`;
      return;
    }
    if (estado.modoMenu === "modificadores") {
      const esGeneral = estado.objetivoModificador.tipo === "grupo";
      const objetivo = esGeneral ? "toda la orden" : `comensal ${estado.objetivoModificador.persona}`;
      const opciones = esGeneral ? comentariosGenerales : modificadores;
      $("#menu-contexto").textContent = "Preparación";
      $("#menu-indicacion").textContent = `Aplicar a ${objetivo}`;
      const botonesComentarios = opciones.map(modificador => {
        const activo = esGeneral
          ? (estado.ticket.comentarios_generales || []).some(item => item.codigo === modificador.codigo)
          : estado.ticket.modificadores.some(item => item.comensal === estado.objetivoModificador.persona && item.codigo === modificador.codigo);
        return `<button class="producto opcion-preparacion ${activo ? "seleccionada" : ""}" data-tipo-comentario="${esGeneral ? "general" : "particular"}" data-codigo="${modificador.codigo}" data-nombre="${modificador.nombre}" type="button" ${!abierto ? "disabled" : ""}>
          <small>${modificador.codigo}</small><strong>${modificador.nombre}</strong>
        </button>`
      }).join("");
      $("#productos").innerHTML = `<section class="comentarios-grid ${esGeneral ? "generales" : "particulares"}">${botonesComentarios}</section>`;
      return;
    }
    if (estado.modoMenu === "salsas") {
      $("#menu-contexto").textContent = "Salsas y verduras";
      $("#menu-indicacion").textContent = "Agrupa opciones con + Más o Nada más";
      const grupos = estado.ticket.salsas_verduras || [];
      const seleccionadas = new Set(grupos.find(grupo => grupo.prefijo === estado.prefijoSalsa)?.elementos || []);
      $("#productos").innerHTML = `
        <section class="selector-salsas">
          <div class="prefijos-salsas">
            ${prefijosSalsas.map(prefijo => `<button class="${estado.prefijoSalsa === prefijo ? "activo" : ""}" data-prefijo-salsa="${escapar(prefijo)}" type="button">${prefijo || "Selección normal"}</button>`).join("")}
          </div>
          <div class="opciones-salsas">
            ${opcionesSalsas.map(opcion => `<button class="${seleccionadas.has(opcion) ? "activo" : ""}" data-opcion-salsa="${escapar(opcion)}" type="button">${escapar(opcion)}</button>`).join("")}
          </div>
        </section>`;
      return;
    }
    const soloBebidas = estado.modoMenu === "bebidas";
    const disponibles = soloBebidas ? productos.filter(esBebida) : productos;
    $("#menu-contexto").textContent = soloBebidas ? "Bebidas" : "Menú completo";
    $("#menu-indicacion").textContent = soloBebidas
      ? "Cada toque suma una bebida y abre la calculadora"
      : "";
    const segmentos = new Map();
    for (const producto of disponibles) {
      if (!segmentos.has(producto.categoria)) segmentos.set(producto.categoria, []);
      segmentos.get(producto.categoria).push(producto);
    }
    const secciones = [...segmentos.entries()];
    $("#productos").innerHTML = secciones.map(([segmento, items], indice) => {
      const numeroSeccion = soloBebidas ? "" : indice + 1;
      const idSeccion = numeroSeccion ? `menu-seccion-${numeroSeccion}` : "";
      const idTitulo = numeroSeccion ? `menu-seccion-titulo-${numeroSeccion}` : "";
      return `
      <section class="segmento-menu" ${idSeccion ? `id="${idSeccion}" data-menu-seccion="${numeroSeccion}" aria-labelledby="${idTitulo}"` : ""}>
        ${soloBebidas ? "" : `<h3 id="${idTitulo}"><span>${escapar(segmento.toLocaleUpperCase("es-MX"))}</span></h3>`}
        <div class="segmento-productos">
          ${items.map(producto => `<button class="producto producto-menu ${producto.es_promocion ? "promocion-menu" : ""} ${producto.es_promocion && !producto.disponible_hoy ? "no-disponible-hoy" : ""}" data-id="${producto.id}" type="button" aria-label="${escapar(`${producto.nombre}. Abreviatura ${producto.corto}`)}" ${!abierto || !producto.disponible_hoy ? "disabled" : ""}>
            ${producto.imagen_url
              ? `<span class="producto-imagen" data-abreviatura="${escapar(producto.corto)}"><img src="${escapar(producto.imagen_url)}" alt="" width="320" height="240" loading="lazy" decoding="async"></span>`
              : `<span class="producto-imagen producto-imagen-vacia" aria-hidden="true"><span><b>${escapar(producto.corto)}</b><small>Sin foto</small></span></span>`}
            <span class="producto-copy"><small>${escapar(producto.es_promocion ? `${producto.corto} · ${producto.promocion_dias}` : producto.corto)}</small><strong>${escapar(producto.nombre)}</strong></span>
          </button>`).join("")}
        </div>
      </section>`;
    }).join("") || '<div class="vacio">No hay opciones disponibles.</div>';
    configurarAtajosMenu(soloBebidas ? [] : secciones.map(([nombre]) => nombre));
  }

  function formatoFechaComanda(valor) {
    const fecha = new Date(valor);
    if (Number.isNaN(fecha.getTime())) return "";
    return `${fecha.toLocaleDateString("es-MX", { day: "2-digit", month: "2-digit", year: "numeric" })} ${fecha.toLocaleTimeString("es-MX", { hour: "numeric", minute: "2-digit" })}`;
  }

  function textoEntregaComanda(ticket) {
    if (!ticket.entrega_aproximada) return "Sin hora de entrega";
    if (ticket.tipo_entrega === "programada") return `Programado: ${ticket.entrega_aproximada}`;
    const tomada = new Date(ticket.creado_en).toLocaleTimeString("es-MX", { hour: "numeric", minute: "2-digit" });
    return `${tomada} - ${ticket.entrega_aproximada}`;
  }

  function textoSalsas(grupos) {
    return (grupos || []).map(grupo => `${grupo.prefijo ? `${grupo.prefijo} ` : ""}${grupo.elementos.join(", ")}`).join(" * ");
  }

  function encabezadoComandaPreview(ticket) {
    if (["domicilio", "recoger"].includes(ticket.canal)) {
      const prefijo = ticket.canal === "recoger" ? "R" : "";
      return `
        <div><span>${formatoFechaComanda(ticket.creado_en).split(" ")[0]}</span><strong>Ticket: ${ticket.folio}, ${prefijo}${ticket.posicion_numero}</strong></div>
        <div><span>${escapar(textoEntregaComanda(ticket))}</span><strong>${dinero(ticket.total)}</strong></div>
        ${ticket.cliente?.nombre ? `<b class="comanda-cliente-directo">${escapar(ticket.cliente.nombre)}${ticket.canal === "recoger" && ticket.cliente.telefono ? ` · ${escapar(ticket.cliente.telefono)}` : ""}</b>` : ""}`;
    }
    if (["comedor", "llevar"].includes(ticket.canal)) {
      return `
        <div><span>${formatoFechaComanda(ticket.creado_en)}</span><strong>${escapar(ticket.mesa)}</strong></div>
        <div><strong>Ticket: ${ticket.folio}</strong><strong>${dinero(ticket.total)}</strong></div>
        ${ticket.canal === "llevar" && ticket.cliente?.nombre ? `<b class="comanda-cliente-directo">${escapar(ticket.cliente.nombre)}</b>` : ""}`;
    }
    return `<div><span>${formatoFechaComanda(ticket.creado_en)}</span><strong>Ticket: ${ticket.folio}</strong></div><b>${dinero(ticket.total)}</b>`;
  }

  function renderComandaPorNombres(ticket, contenedor) {
    const esComplemento = partida => esBebida(partida) || productosAlFinal.has(partida.codigo);
    const principales = ticket.partidas.filter(partida => !partida.es_promocion && !esComplemento(partida));
    const columnas = [];
    const indiceColumnas = new Map();
    for (const partida of principales) {
      const clave = `${partida.producto_id}:${partida.termino || "unico"}`;
      if (!indiceColumnas.has(clave)) {
        indiceColumnas.set(clave, columnas.length);
        columnas.push({
          clave,
          productoId: partida.producto_id,
          termino: partida.termino || "",
          nombre: partida.nombre_corto,
          cantidades: new Map(),
        });
      }
      const columna = columnas[indiceColumnas.get(clave)];
      if (!columna.cantidades.has(partida.comensal)) columna.cantidades.set(partida.comensal, 0);
      columna.cantidades.set(partida.comensal, columna.cantidades.get(partida.comensal) + Number(partida.cantidad));
    }
    while (columnas.length < 4) columnas.push(null);
    const nombres = ticket.nombres_comensales || {};
    const personasUsadas = new Set(ticket.partidas.filter(partida => !partida.es_promocion).map(partida => partida.comensal));
    Object.keys(nombres).forEach(numero => personasUsadas.add(Number(numero)));
    if (!personasUsadas.size) personasUsadas.add(estado.persona);
    const personas = [...personasUsadas].sort((a, b) => a - b);
    const preparacion = new Map(personas.map(persona => [persona, ticket.modificadores.filter(mod => mod.comensal === persona).map(mod => mod.codigo).join(" ")]));
    const filas = personas.map(persona => {
      const celdas = columnas.slice(0, 4).map(columna => {
        if (!columna) return '<span class="comanda-nombre-celda vacia"></span>';
        const valor = columna.cantidades.get(persona) || 0;
        const seleccionada = estado.edicion?.clave === columna.clave && estado.edicion?.persona === persona;
        const texto = seleccionada ? estado.edicion.cantidadPantalla : (valor ? cantidad(valor) : "");
        return `<button class="comanda-nombre-celda cantidad-celda ${seleccionada ? "seleccionada" : ""}" data-celda-producto="${columna.productoId}" data-celda-persona="${persona}" data-celda-termino="${columna.termino}" data-celda-clave="${columna.clave}" type="button">${texto}</button>`;
      }).join("");
      return `<button class="comanda-nombre-persona" data-seleccionar-persona="${persona}" type="button"><b>${persona}. ${escapar(nombres[String(persona)] || "SIN NOMBRE")}</b><small>${escapar(preparacion.get(persona) || "")}</small></button>${celdas}`;
    }).join("");
    const complementos = new Map();
    for (const partida of ticket.partidas.filter(partida => !partida.es_promocion && esComplemento(partida))) {
      const clave = `${partida.comensal}:${partida.producto_id}`;
      if (!complementos.has(clave)) complementos.set(clave, { persona: partida.comensal, nombre: partida.nombre_corto, cantidad: 0 });
      complementos.get(clave).cantidad += Number(partida.cantidad);
    }
    const extras = [...complementos.values()].map(item => `${escapar(nombres[String(item.persona)] || `Persona ${item.persona}`)}: ${item.cantidad > 1 ? `${cantidad(item.cantidad)} ` : ""}${escapar(item.nombre)}`).join(" · ");
    const generalTexto = (ticket.comentarios_generales || []).map(item => item.nombre).join(" · ");
    const salsasTexto = textoSalsas(ticket.salsas_verduras);
    $("#orden-resumen").textContent = `${ticket.partidas.length} ${ticket.partidas.length === 1 ? "partida" : "partidas"}`;
    contenedor.innerHTML = `
      <section class="comanda-papel comanda-por-nombres">
        <header class="comanda-papel-encabezado">
          <em>Los Tocayos Tacos de Barbacoa</em>
          ${encabezadoComandaPreview(ticket)}
        </header>
        <button class="comanda-global ${generalTexto ? "seleccionado" : ""}" data-objetivo-modificador="grupo" data-inicio="1" type="button">${escapar(generalTexto || "Comentario General")}</button>
        <div class="comanda-matriz-nombres">
          <span class="encabezado-nombre">Nombre</span>
          ${columnas.slice(0, 4).map(columna => `<span class="encabezado-producto">${columna ? escapar(columna.nombre) : "PRODUCTO"}</span>`).join("")}
          ${filas}
        </div>
        <button class="comanda-extras-nombres" data-modo-menu="bebidas" type="button"><strong>Consomés y bebidas</strong><span>${extras || "Sin complementos"}</span></button>
        <button class="comanda-salsas" data-modo-menu="salsas" type="button"><strong>Salsas y verduras</strong><span>${escapar(salsasTexto || "Toca aquí para elegir salsas y verduras")}</span></button>
        ${ticket.comentario_general ? `<p class="comanda-comentario-general">${escapar(ticket.comentario_general)}</p>` : ""}
        ${ticket.terminal && ["domicilio", "recoger"].includes(ticket.canal) ? '<strong class="comanda-terminal">PAGO: TERMINAL</strong>' : ""}
      </section>`;
  }

  function renderPedidoSucursal(ticket, contenedor) {
    const abierto = ticket.estado === "abierto";
    const filas = [...ticket.partidas].sort((a, b) => a.orden - b.orden).map(partida => `
      <button class="fila-partida-sucursal ${estado.edicion?.partidaId === partida.id ? "seleccionada" : ""}" data-partida-sucursal="${partida.id}" data-producto-sucursal="${partida.producto_sucursal_id}" type="button" ${!abierto ? "disabled" : ""}>
        <span class="concepto"><strong>${escapar(partida.nombre_catalogo || partida.nombre)}</strong><small>${escapar(partida.nombre)}</small></span>
        <span><b>${cantidad(partida.cantidad)}</b><small>${escapar(partida.unidad)}</small></span>
        <span><small>Precio</small>${dinero(partida.precio)}</span>
        <span><small>Importe</small><b>${dinero(partida.importe)}</b></span>
      </button>`).join("");
    $("#orden-resumen").textContent = `${ticket.partidas.length} ${ticket.partidas.length === 1 ? "producto" : "productos"} · ${dinero(ticket.total)}`;
    contenedor.innerHTML = `
      <section class="pedido-sucursal-lista">
        <header>
          <div><small>Sucursal / cliente</small><strong>${escapar(ticket.cliente_sucursal.nombre)}</strong></div>
          <div><small>Pedido</small><strong>${escapar(ticket.mesa)}</strong></div>
          <div><small>Ticket</small><strong>${ticket.folio}</strong></div>
        </header>
        <div class="encabezado-lista-sucursal"><span>Concepto</span><span>Cantidad</span><span>Precio</span><span>Importe</span></div>
        <div class="partidas-sucursal">${filas || '<p class="vacio">Selecciona productos para comenzar el pedido.</p>'}</div>
        <footer><span>Total del pedido</span><strong>${dinero(ticket.total)}</strong></footer>
      </section>`;
  }

  function renderComanda() {
    const ticket = estado.ticket;
    const contenedor = $("#comanda-papel-preview");
    if (!ticket || !contenedor) return;
    if (ticket.canal === "sucursales") {
      renderPedidoSucursal(ticket, contenedor);
      return;
    }
    if (ticket.captura_por_nombres) {
      renderComandaPorNombres(ticket, contenedor);
      return;
    }
    const { inicio, personas } = bloqueComensales();
    const promocionesTicket = ticket.partidas.filter(partida => partida.es_promocion && !partida.promocion_id);
    const partidasComida = ticket.partidas.filter(partida => !esBebida(partida) && !partida.es_promocion);
    const filas = new Map();
    for (const partida of partidasComida) {
      const clave = `${partida.producto_id}:${partida.termino || "unico"}`;
      if (!filas.has(clave)) {
        filas.set(clave, {
          clave,
          productoId: partida.producto_id,
          termino: partida.termino || "",
          nombre: partida.nombre_corto,
          cantidades: new Map(),
        });
      }
      const fila = filas.get(clave);
      if (!fila.cantidades.has(partida.comensal)) fila.cantidades.set(partida.comensal, { cantidad: 0, ids: [] });
      const celda = fila.cantidades.get(partida.comensal);
      celda.cantidad += Number(partida.cantidad);
      celda.ids.push(partida.id);
    }
    const promociones = new Map();
    for (const partida of promocionesTicket) {
      if (!promociones.has(partida.producto_id)) {
        promociones.set(partida.producto_id, { productoId: partida.producto_id, nombre: partida.codigo, cantidades: new Map() });
      }
      const cantidades = promociones.get(partida.producto_id).cantidades;
      if (!cantidades.has(partida.comensal)) cantidades.set(partida.comensal, { cantidad: 0, ids: [] });
      cantidades.get(partida.comensal).cantidad += Number(partida.cantidad);
      cantidades.get(partida.comensal).ids.push(partida.id);
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
    }).join("");
    const promocionesHtml = [...promociones.values()].map(fila => {
      const celdas = personas.map(persona => {
        const celda = fila.cantidades.get(persona);
        const valor = celda ? `${celda.cantidad > 1 ? `${cantidad(celda.cantidad)} ` : ""}P` : "";
        return `<button class="comanda-celda promocion-celda" data-promocion-producto="${fila.productoId}" data-promocion-persona="${persona}" type="button">${valor}</button>`;
      }).join("");
      return `<span class="comanda-etiqueta promocion-etiqueta">${escapar(fila.nombre)}</span>${celdas}`;
    }).join("");
    const filasProductos = promocionesHtml + filasHtml || `<button class="comanda-vacio producto-zona" data-modo-menu="productos" type="button">Toca aquí para mostrar productos y comenzar la orden</button>`;
    const bebidasHtml = [...bebidas.values()].map(bebida =>
      `${bebida.cantidad >= 2 ? `<strong>( ${cantidad(bebida.cantidad)} )</strong> ` : ""}${escapar(bebida.nombre)}`
    ).join('<b class="separador-bebida">* </b>');
    const generales = ticket.comentarios_generales || [];
    const generalTexto = generales.map(item => item.nombre).join(" · ");
    const salsasTexto = textoSalsas(ticket.salsas_verduras);
    const encabezado = encabezadoComandaPreview(ticket);
    $("#orden-resumen").textContent = `${ticket.partidas.length} ${ticket.partidas.length === 1 ? "partida" : "partidas"}`;
    contenedor.innerHTML = `
      <section class="comanda-papel">
        <header class="comanda-papel-encabezado">
          <em>Los Tocayos Tacos de Barbacoa</em>
          ${encabezado}
        </header>
        <div class="comanda-matriz">
          <span class="comanda-etiqueta encabezado">Comensal</span>
          ${personas.map(persona => `<button class="comanda-numero ${estado.persona === persona ? "activo" : ""}" data-seleccionar-persona="${persona}" type="button">${persona}</button>`).join("")}
          <button class="comanda-global ${generalTexto ? "seleccionado" : ""}" data-objetivo-modificador="grupo" data-inicio="${inicio}" type="button">${escapar(generalTexto || "Comentario General")}</button>
          ${generalTexto ? "" : `
            <span class="comanda-etiqueta encabezado">Prep.</span>
            ${personas.map(persona => {
              const texto = preparacion.get(persona).map(mod => mod.codigo).join(" ") || "+";
              return `<button class="comanda-comentario ${estado.persona === persona ? "activo" : ""}" data-objetivo-modificador="persona" data-persona="${persona}" type="button">${escapar(texto)}</button>`;
            }).join("")}`}
          ${filasProductos}
        </div>
        <button class="comanda-bebidas" data-modo-menu="bebidas" type="button">
          <strong>Bebidas</strong>
          <span>${bebidasHtml || "Toca aquí para elegir bebidas"}</span>
        </button>
        <button class="comanda-salsas" data-modo-menu="salsas" type="button">
          <strong>Salsas y verduras</strong>
          <span>${escapar(salsasTexto || "Toca aquí para elegir salsas y verduras")}</span>
        </button>
        ${ticket.comentario_general ? `<p class="comanda-comentario-general">${escapar(ticket.comentario_general)}</p>` : ""}
        ${ticket.terminal && ["domicilio", "recoger"].includes(ticket.canal) ? '<strong class="comanda-terminal">PAGO: TERMINAL</strong>' : ""}
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
      const datos = await api("/api/clientes/buscar/", {
        method: "POST",
        body: JSON.stringify({ q: consulta, limite: 12 }),
      });
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
    const bloqueoAjeno = bloqueoDeOtro();
    const abierto = estado.ticket.estado === "abierto";
    const cobrable = ["procesado", "cobrar"].includes(estado.ticket.estado);
    const esSucursal = estado.ticket.canal === "sucursales";
    const esDomicilio = estado.ticket.canal === "domicilio";
    const editable = abierto && !bloqueoAjeno;
    const operable = !bloqueoAjeno;
    $("#procesar").textContent = esSucursal ? "Procesar e imprimir" : "Procesar orden";
    $("#cobrar").textContent = esSucursal ? "Completar pedido" : "Cobrar";
    $("#procesar").classList.toggle("oculto", !abierto);
    $("#cancelar-orden").classList.toggle("oculto", !abierto && !cobrable);
    $("#cobrar").classList.toggle("oculto", !cobrable || esDomicilio);
    $("#reimprimir").classList.toggle("oculto", abierto);
    $("#procesar").disabled = !editable;
    $("#cancelar-orden").disabled = !operable;
    $("#cobrar").disabled = !operable;
    $$(".persona, .opcion-preparacion, .comanda-papel button, .producto, .fila-partida-sucursal").forEach(b => {
      b.disabled = !abierto || b.classList.contains("no-disponible-hoy");
      if (bloqueoAjeno) b.disabled = true;
    });
    $$("#datos-cliente button, #datos-cliente input, #datos-cliente textarea").forEach(control => control.disabled = !editable);
    $$("#datos-servicio-directo input, #ticket-switches input, #nombre-persona").forEach(control => control.disabled = !editable);
    $$("#entrega, #pago-domicilio input, #comentario").forEach(control => control.disabled = !editable);
  }

  function partidasDeEdicion(productoId, persona, termino) {
    return estado.ticket.partidas.filter(partida =>
      partida.producto_id === productoId
      && partida.comensal === persona
      && (partida.termino || "") === (termino || "")
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

  function abrirCalculadoraSucursal(producto, partida) {
    estado.edicion = {
      productoId: producto.id,
      partidaId: partida.id,
      cantidadPantalla: String(Number(partida.cantidad)),
      reemplazar: true,
    };
    estado.modoMenu = "calculadora-sucursal";
    renderMenu();
    renderComanda();
  }

  async function seleccionarProductoSucursal(productoId) {
    if (!(await finalizarEdicion())) return;
    const producto = (estado.ticket.catalogo_sucursal || []).find(item => item.id === productoId);
    if (!producto) return;
    let partida = estado.ticket.partidas.find(item => item.producto_sucursal_id === productoId);
    if (!partida) {
      try {
        const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/`, {
          method: "POST",
          body: JSON.stringify({ producto_sucursal_id: productoId, cantidad: "1" }),
        });
        estado.ticket = datos.ticket;
        partida = estado.ticket.partidas.find(item => item.producto_sucursal_id === productoId);
      } catch (error) {
        toast(error.message, true);
        return;
      }
    }
    abrirCalculadoraSucursal(producto, partida);
  }

  async function editarPartidaSucursal(partidaId) {
    if (!(await finalizarEdicion())) return;
    const partida = estado.ticket.partidas.find(item => item.id === partidaId);
    const producto = (estado.ticket.catalogo_sucursal || []).find(item => item.id === partida?.producto_sucursal_id);
    if (partida && producto) abrirCalculadoraSucursal(producto, partida);
  }

  async function guardarEdicionSucursal(eliminar = false) {
    if (!estado.edicion || estado.ticket?.canal !== "sucursales") return true;
    const edicion = { ...estado.edicion };
    let datos;
    try {
      if (eliminar) {
        datos = await api(`/api/partidas/${edicion.partidaId}/`, { method: "DELETE" });
      } else {
        const valor = Number(edicion.cantidadPantalla);
        if (!Number.isFinite(valor) || valor < 0.001 || valor > 999.999) {
          toast("Captura una cantidad entre 0.001 y 999.999.", true);
          return false;
        }
        datos = await api(`/api/partidas/${edicion.partidaId}/`, {
          method: "PATCH",
          body: JSON.stringify({ cantidad: edicion.cantidadPantalla }),
        });
      }
      estado.ticket = datos.ticket;
      estado.edicion = null;
      estado.modoMenu = "productos";
      renderMenu();
      renderComanda();
      return true;
    } catch (error) {
      toast(error.message, true);
      return false;
    }
  }

  async function manejarTeclaCantidadSucursal(tecla) {
    if (!estado.edicion) return;
    if (tecla === "eliminar") {
      await guardarEdicionSucursal(true);
      return;
    }
    let texto = String(estado.edicion.cantidadPantalla || "0");
    if (tecla === "borrar") {
      texto = texto.slice(0, -1) || "0";
      estado.edicion.reemplazar = false;
    } else if (tecla === ".") {
      if (!texto.includes(".")) texto = estado.edicion.reemplazar ? "0." : `${texto}.`;
      estado.edicion.reemplazar = false;
    } else {
      texto = estado.edicion.reemplazar || texto === "0" ? String(tecla) : `${texto}${tecla}`;
      const [enteros, decimales = ""] = texto.split(".");
      texto = `${enteros.slice(0, 3)}${texto.includes(".") ? `.${decimales.slice(0, 3)}` : ""}`;
      estado.edicion.reemplazar = false;
    }
    if (Number(texto) <= 999.999) estado.edicion.cantidadPantalla = texto;
    renderMenu();
    renderComanda();
  }

  async function seleccionarProducto(productoId) {
    if (!(await finalizarEdicion())) return;
    const producto = productos.find(item => item.id === productoId);
    if (!producto) return;
    const termino = siguienteTerminoProducto(producto);
    let partidas = partidasDeEdicion(producto.id, estado.persona, termino);
    if (esBebida(producto) && partidas.length) {
      const totalActual = partidas.reduce((total, partida) => total + Number(partida.cantidad), 0);
      try {
        const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/ajustar/`, {
          method: "POST",
          body: JSON.stringify({ partida_ids: partidas.map(partida => partida.id), cantidad: totalActual + 1, termino }),
        });
        estado.ticket = datos.ticket;
        partidas = partidasDeEdicion(producto.id, estado.persona, termino);
      } catch (error) {
        toast(error.message, true);
        return;
      }
    }
    if (!partidas.length) {
      try {
        const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/`, {
          method: "POST",
          body: JSON.stringify({
            producto_id: productoId,
            comensal: estado.persona,
            cantidad: 1,
            termino,
          }),
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
    if (estado.ticket?.canal === "sucursales") return guardarEdicionSucursal();
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

  async function seleccionarPromocionCelda(productoId, persona) {
    if (!(await finalizarEdicion())) return;
    estado.persona = persona;
    renderPersonas();
    const producto = productos.find(item => item.id === productoId);
    if (!producto) return;
    const partidas = partidasDeEdicion(productoId, persona, "");
    if (!partidas.length) {
      await seleccionarProducto(productoId);
      return;
    }
    abrirCalculadora(producto, partidas, "");
  }

  async function seleccionarPersona(persona) {
    if (!(await finalizarEdicion())) return;
    if (estado.ticket?.captura_por_nombres && persona !== estado.persona) {
      clearTimeout(estado.temporizadorNombre);
      try { await guardarNombrePersona(); }
      catch (error) { toast(error.message, true); return; }
    }
    estado.persona = persona;
    estado.objetivoModificador = { tipo: "persona", persona };
    estado.modoMenu = "productos";
    renderPersonas();
    renderMenu();
    renderComanda();
    if (estado.ticket?.captura_por_nombres) $("#nombre-persona").focus();
  }

  async function aplicarPreparacion(codigo, nombre) {
    try {
      const objetivo = estado.objetivoModificador;
      let cuerpo;
      if (objetivo.tipo === "grupo") {
        cuerpo = { tipo: "general", codigo, nombre };
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

  function actualizarControlEntrega() {
    const boton = $("#entrega");
    if (!boton || !estado.ticket) return;
    boton.textContent = estado.ticket.entrega_aproximada
      ? (estado.ticket.tipo_entrega === "programada" ? `Programado · ${estado.ticket.entrega_aproximada}` : `Aproximado · ${estado.ticket.entrega_aproximada}`)
      : "Seleccionar hora";
  }

  async function guardarEntrega(tipo, hora) {
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ tipo_entrega: tipo, entrega_aproximada: hora }),
      });
      estado.ticket = datos.ticket;
      estado.modoEntrega = tipo;
      actualizarControlEntrega();
      estado.modoMenu = "productos";
      renderMenu();
      renderComanda();
    } catch (error) { toast(error.message, true); }
  }

  async function seleccionarMinutosEntrega(minutos) {
    const entrega = new Date(new Date(estado.ticket.creado_en).getTime() + Number(minutos) * 60000);
    const hora = `${String(entrega.getHours()).padStart(2, "0")}:${String(entrega.getMinutes()).padStart(2, "0")}`;
    await guardarEntrega("aproximada", hora);
  }

  function manejarTeclaEntrega(tecla) {
    if (tecla === "borrar") estado.entregaProgramadaDigitos = estado.entregaProgramadaDigitos.slice(0, -1);
    else estado.entregaProgramadaDigitos = `${estado.entregaProgramadaDigitos}${tecla}`.slice(-4);
    renderMenu();
  }

  async function confirmarEntregaProgramada() {
    const digitos = String(estado.entregaProgramadaDigitos || "").padStart(4, "0");
    const horas = Number(digitos.slice(0, 2));
    const minutos = Number(digitos.slice(2));
    if (horas > 23 || minutos > 59 || !estado.entregaProgramadaDigitos) {
      toast("Captura una hora válida en formato de 24 horas, por ejemplo 1330.", true);
      return;
    }
    await guardarEntrega("programada", `${String(horas).padStart(2, "0")}:${String(minutos).padStart(2, "0")}`);
  }

  async function alternarSalsa(opcion) {
    const grupos = (estado.ticket.salsas_verduras || []).map(grupo => ({ prefijo: grupo.prefijo, elementos: [...grupo.elementos] }));
    let grupo = grupos.find(item => item.prefijo === estado.prefijoSalsa);
    if (!grupo) {
      grupo = { prefijo: estado.prefijoSalsa, elementos: [] };
      grupos.push(grupo);
    }
    const indice = grupo.elementos.indexOf(opcion);
    if (indice >= 0) grupo.elementos.splice(indice, 1);
    else grupo.elementos.push(opcion);
    const seleccion = grupos.filter(item => item.elementos.length);
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/`, {
        method: "PATCH", body: JSON.stringify({ salsas_verduras: seleccion }),
      });
      estado.ticket = datos.ticket;
      renderMenu();
      renderComanda();
    } catch (error) { toast(error.message, true); }
  }

  async function guardarDatos() {
    if (estado.ticket?.canal === "sucursales") return;
    clearTimeout(estado.temporizadorNombre);
    const nombres = { ...(estado.ticket.nombres_comensales || {}) };
    if (estado.ticket.captura_por_nombres) {
      const nombreActual = $("#nombre-persona").value.trim();
      if (nombreActual) nombres[String(estado.persona)] = nombreActual;
      else delete nombres[String(estado.persona)];
    }
    const cuerpo = {
      comentario_general: $("#comentario").value,
      terminal: $("#terminal").checked,
      paga_con: $("#paga-con").value,
      contacto_pedido_nombre: $("#contacto-pedido-nombre")?.value || "",
      contacto_pedido_telefono: $("#contacto-pedido-telefono")?.value || "",
      ...(estado.ticket.captura_por_nombres ? { nombres_comensales: nombres } : {}),
      ...(["recoger", "llevar"].includes(estado.ticket.canal) ? {
        cliente_nombre: $("#cliente-directo-nombre").value,
        cliente_telefono: estado.ticket.canal === "recoger" ? $("#cliente-directo-telefono").value : "",
      } : {}),
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

  async function cobrar(formaPago, importeRecibido, imprimirTicket, claveAdministrador) {
    bloquear(true);
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/cobrar/`, {
        method: "POST", body: JSON.stringify({
          forma_pago: formaPago,
          importe_recibido: importeRecibido,
          imprimir_ticket: imprimirTicket,
          clave_administrador: claveAdministrador,
        }),
      });
      estado.ticket = datos.ticket;
      resumirImpresiones(datos.impresiones, imprimirTicket ? "Cobro registrado." : "Cobro registrado sin imprimir ticket.");
      await volver(true);
    } catch (error) { toast(error.message, true); }
    finally { bloquear(false); }
  }

  async function completarSucursal() {
    const claveAdministrador = await pedirClavePos("Completar pedido de sucursal", "Confirma con la clave de administrador que el pedido puede cerrarse.");
    if (!claveAdministrador) return;
    bloquear(true);
    try {
      await api(`/api/tickets/${estado.ticket.id}/completar-sucursal/`, {
        method: "POST",
        body: JSON.stringify({ clave_administrador: claveAdministrador }),
      });
      toast("Pedido de sucursal completado; la posición quedó disponible.");
      await volver(true);
    } catch (error) { toast(error.message, true); }
    finally { bloquear(false); }
  }

  async function reimprimir() {
    const formato = estado.ticket.canal === "sucursales"
      ? "sucursal"
      : (estado.ticket.canal === "recoger" ? "comanda" : (estado.ticket.estado === "pagado" ? "cuenta" : "comanda"));
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
  }

  async function cancelarOrden() {
    if (!estado.ticket || !window.confirm("¿Cancelar esta orden? Se borrarán todos los productos y la posición quedará disponible.")) return;
    let claveAdministrador = "";
    if (estado.ticket.estado !== "abierto") {
      claveAdministrador = await pedirClavePos("Cancelar pedido procesado", "Sólo el administrador puede cancelar un pedido después de imprimirlo.");
      if (!claveAdministrador) return;
    }
    clearTimeout(estado.temporizadorNombre);
    bloquear(true);
    try {
      await api(`/api/tickets/${estado.ticket.id}/cancelar/`, {
        method: "POST",
        body: JSON.stringify({ clave_administrador: claveAdministrador }),
      });
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
    clearTimeout(estado.temporizadorNombre);
    await liberarBloqueoActual();
    estado.ticket = null;
    document.body.classList.remove("en-ticket");
    $("#vista-ticket").classList.add("oculto");
    $("#vista-posiciones").classList.remove("oculto");
    cargarEstado();
  }

  async function salirModoMesero() {
    if (!(await finalizarEdicion())) return;
    await liberarBloqueoActual();
    estado.ticket = null;
    document.body.classList.remove("en-ticket");
    $("#vista-ticket").classList.add("oculto");
    $("#vista-posiciones").classList.remove("oculto");
    $("main").classList.add("oculto");
    $("#pantalla-acceso")?.classList.remove("oculto");
    document.body.classList.remove("en-operacion");
    clearInterval(estado.temporizadorSucursales);
    estado.temporizadorSucursales = null;
    estado.operador = null;
    renderOperadorActual();
    try { await api("/api/operador/salir/", { method: "POST", body: "{}" }); }
    catch (error) { toast(error.message, true); }
  }

  async function salirModoTableta() {
    if (!(await finalizarEdicion())) return;
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      try {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      } catch { /* El navegador puede conservar el modo standalone de la PWA. */ }
    }
    if (estado.ticket) await volver(true);
    toast("Pantalla completa desactivada. Ya puedes cerrar o cambiar de aplicación.");
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
  $("#entrar-mesero")?.addEventListener("click", () => entrarComoMesero());
  $("#entrar-administrador")?.addEventListener("click", abrirAdministrador);
  $("#form-clave-pos")?.addEventListener("submit", evento => {
    evento.preventDefault();
    const clave = $("#clave-pos").value.trim();
    if (!/^\d{4}$/.test(clave)) {
      $("#error-clave-pos").textContent = "El código debe contener exactamente 4 dígitos.";
      $("#error-clave-pos").hidden = false;
      return;
    }
    resolverClavePos(clave);
  });
  $("#cancelar-clave-pos")?.addEventListener("click", () => resolverClavePos(null));
  $("#dialogo-clave-pos")?.addEventListener("cancel", evento => {
    evento.preventDefault();
    resolverClavePos(null);
  });
  $("#toast-cerrar")?.addEventListener("click", cerrarToast);
  $$('[data-salir-mesero]').forEach(boton => boton.addEventListener("click", salirModoMesero));
  $("#pantalla-completa")?.addEventListener("click", alternarPantallaCompleta);
  $("#salir-tableta")?.addEventListener("click", salirModoTableta);
  $("#rejilla-posiciones").addEventListener("click", evento => {
    const sucursal = evento.target.closest("[data-sucursal-id]");
    if (sucursal) {
      estado.sucursalSeleccionada = sucursal.dataset.sucursalId;
      renderPosiciones();
      return;
    }
    if (evento.target.closest("[data-volver-sucursales]")) {
      estado.sucursalSeleccionada = null;
      renderPosiciones();
      return;
    }
    const boton = evento.target.closest(".posicion");
    if (boton) abrirPosicion(boton.dataset.id);
  });
  $("#volver").addEventListener("click", () => volver());
  $("#personas").addEventListener("click", async evento => {
    const boton = evento.target.closest(".persona");
    if (boton) await seleccionarPersona(Number(boton.dataset.persona));
  });
  $("#productos").addEventListener("click", async evento => {
    const productoSucursal = evento.target.closest("[data-sucursal-producto]");
    if (productoSucursal) {
      await seleccionarProductoSucursal(productoSucursal.dataset.sucursalProducto);
      return;
    }
    const teclaSucursal = evento.target.closest("[data-tecla-sucursal]");
    if (teclaSucursal) {
      await manejarTeclaCantidadSucursal(teclaSucursal.dataset.teclaSucursal);
      return;
    }
    if (evento.target.closest("[data-confirmar-sucursal]")) {
      await guardarEdicionSucursal();
      return;
    }
    const modoEntrega = evento.target.closest("[data-modo-entrega]");
    if (modoEntrega) {
      estado.modoEntrega = modoEntrega.dataset.modoEntrega;
      if (estado.modoEntrega === "programada" && !estado.entregaProgramadaDigitos) estado.entregaProgramadaDigitos = "";
      renderMenu();
      return;
    }
    const minutosEntrega = evento.target.closest("[data-minutos-entrega]");
    if (minutosEntrega) {
      await seleccionarMinutosEntrega(Number(minutosEntrega.dataset.minutosEntrega));
      return;
    }
    const teclaEntrega = evento.target.closest("[data-tecla-entrega]");
    if (teclaEntrega) {
      manejarTeclaEntrega(teclaEntrega.dataset.teclaEntrega);
      return;
    }
    if (evento.target.closest("[data-confirmar-entrega]")) {
      await confirmarEntregaProgramada();
      return;
    }
    const prefijoSalsa = evento.target.closest("[data-prefijo-salsa]");
    if (prefijoSalsa) {
      estado.prefijoSalsa = prefijoSalsa.dataset.prefijoSalsa;
      renderMenu();
      return;
    }
    const opcionSalsa = evento.target.closest("[data-opcion-salsa]");
    if (opcionSalsa) {
      await alternarSalsa(opcionSalsa.dataset.opcionSalsa);
      return;
    }
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
  $("#productos").addEventListener("error", evento => {
    const imagen = evento.target.closest?.(".producto-imagen img");
    if (!imagen) return;
    const contenedor = imagen.closest(".producto-imagen");
    const abreviatura = contenedor.dataset.abreviatura || "Producto";
    contenedor.classList.add("producto-imagen-vacia");
    contenedor.setAttribute("aria-hidden", "true");
    contenedor.innerHTML = `<span><b>${escapar(abreviatura)}</b><small>Sin foto</small></span>`;
  }, true);
  $("#atajos-menu")?.addEventListener("click", evento => {
    const atajo = evento.target.closest("[data-menu-atajo]");
    if (atajo) desplazarASeccionMenu(Number(atajo.dataset.menuAtajo));
  });
  $("#menu-productos").addEventListener("click", async () => cambiarModoMenu("productos"));
  $("#comanda-preview").addEventListener("click", async evento => {
    const partidaSucursal = evento.target.closest("[data-partida-sucursal]");
    if (partidaSucursal) {
      await editarPartidaSucursal(partidaSucursal.dataset.partidaSucursal);
      return;
    }
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
    const promocion = evento.target.closest("[data-promocion-producto]");
    if (promocion) {
      await seleccionarPromocionCelda(
        promocion.dataset.promocionProducto,
        Number(promocion.dataset.promocionPersona),
      );
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
  $("#entrega").addEventListener("click", async () => cambiarModoMenu("entrega"));
  $("#switch-tipo-pedido").addEventListener("change", convertirTipoPedido);
  $("#switch-modo-nombres").addEventListener("change", alternarCapturaPorNombres);
  $("#nombre-persona").addEventListener("input", evento => {
    if (!estado.ticket?.captura_por_nombres) return;
    const nombres = { ...(estado.ticket.nombres_comensales || {}) };
    const valor = evento.currentTarget.value;
    if (valor.trim()) nombres[String(estado.persona)] = valor;
    else delete nombres[String(estado.persona)];
    estado.ticket.nombres_comensales = nombres;
    const persona = $(`.persona[data-persona="${estado.persona}"]`);
    if (persona) {
      persona.classList.toggle("con-nombre", Boolean(valor.trim()));
      persona.querySelector("small").textContent = valor.trim() || "Sin nombre";
    }
    renderComanda();
    clearTimeout(estado.temporizadorNombre);
    estado.temporizadorNombre = setTimeout(() => guardarNombrePersona().catch(error => toast(error.message, true)), 650);
  });
  $("#nombre-persona").addEventListener("keydown", async evento => {
    if (evento.key !== "Enter") return;
    evento.preventDefault();
    clearTimeout(estado.temporizadorNombre);
    try { await guardarNombrePersona({ avanzar: true }); }
    catch (error) { toast(error.message, true); }
  });
  $("#cliente-directo-nombre").addEventListener("input", evento => {
    if (!estado.ticket || !["recoger", "llevar"].includes(estado.ticket.canal)) return;
    estado.ticket.cliente.nombre = evento.currentTarget.value;
    renderComanda();
  });
  $("#cliente-directo-telefono").addEventListener("input", evento => {
    evento.currentTarget.value = evento.currentTarget.value.replace(/[^\d +()-]/g, "");
    if (estado.ticket?.canal !== "recoger") return;
    estado.ticket.cliente.telefono = evento.currentTarget.value;
    renderComanda();
  });
  $("#terminal").addEventListener("change", evento => {
    if (evento.currentTarget.checked) $("#paga-con").value = "";
  });
  $("#paga-con").addEventListener("input", evento => {
    evento.currentTarget.value = evento.currentTarget.value.replace(/\D/g, "");
    if (evento.currentTarget.value) $("#terminal").checked = false;
  });
  $("#comentario").addEventListener("input", evento => {
    if (estado.ticket) estado.ticket.comentario_general = evento.currentTarget.value;
    renderComanda();
  });
  $("#procesar").addEventListener("click", procesar);
  $("#cancelar-orden").addEventListener("click", cancelarOrden);
  $("#cobrar").addEventListener("click", () => {
    if (estado.ticket?.canal === "sucursales") {
      completarSucursal();
      return;
    }
    $("#cobro-total").textContent = dinero(estado.ticket.total);
    $("#importe-recibido").value = estado.ticket.total;
    const soloComanda = estado.ticket.canal === "recoger";
    $("#opciones-impresion").classList.toggle("oculto", soloComanda);
    if (soloComanda) $("#opciones-impresion input[value='no']").checked = true;
    $("#dialogo-cobro").showModal();
  });
  $("#form-cobro").addEventListener("submit", async evento => {
    evento.preventDefault();
    if (evento.submitter?.value === "cancel") { $("#dialogo-cobro").close(); return; }
    const formulario = new FormData(evento.currentTarget);
    const formaPago = formulario.get("forma_pago");
    const importe = $("#importe-recibido").value;
    const imprimir = formulario.get("imprimir_ticket") === "si";
    $("#dialogo-cobro").close();
    const claveAdministrador = await pedirClavePos("Autorizar cobro", "Marcar el pedido como cobrado requiere la clave de administrador.");
    if (!claveAdministrador) {
      $("#dialogo-cobro").showModal();
      return;
    }
    cobrar(
      formaPago,
      importe,
      imprimir,
      claveAdministrador,
    );
  });
  $("#reimprimir").addEventListener("click", reimprimir);

  window.addEventListener("online", () => {
    if (bloqueoPropio()) renovarBloqueoTicket();
    cargarEstado(estado.canal === "sucursales");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && bloqueoPropio()) renovarBloqueoTicket();
  });
  window.addEventListener("pagehide", liberarBloqueoEnSalida);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  renderOperadorActual();
  renderPersonas();
  renderMenu();
  cargarEstado();
})();
