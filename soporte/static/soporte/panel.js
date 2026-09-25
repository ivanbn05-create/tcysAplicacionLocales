(() => {
  "use strict";
  const $ = selector => document.querySelector(selector);
  const estado = { impresoras: [], terminales: [], terminal: null, validacion: null };
  const api = "/soporte/api/";

  function escapar(valor) {
    return String(valor ?? "").replace(/[&<>"']/g, caracter => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[caracter]);
  }
  function csrf() {
    const cookie = document.cookie.split("; ").find(item => item.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : "";
  }
  async function solicitud(ruta, opciones = {}) {
    const respuesta = await fetch(api + ruta, {
      credentials: "same-origin", cache: "no-store", ...opciones,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), ...(opciones.headers || {}) }
    });
    let datos;
    try { datos = await respuesta.json(); }
    catch { throw new Error("El Edge no devolvió una respuesta válida."); }
    if (!respuesta.ok) throw new Error(datos.error || "La operación no se completó.");
    return datos;
  }
  function aviso(texto, error = false) {
    const elemento = $("#aviso");
    elemento.textContent = texto;
    elemento.classList.toggle("error", error);
    elemento.hidden = false;
    if (error) elemento.scrollIntoView({ block: "nearest" });
  }
  async function ocupado(formulario, accion) {
    const boton = formulario.querySelector('button[type="submit"]');
    const texto = boton.textContent;
    boton.disabled = true;
    boton.textContent = "Guardando…";
    formulario.setAttribute("aria-busy", "true");
    try { await accion(); }
    catch (error) { aviso(error.message, true); }
    finally {
      boton.disabled = false;
      boton.textContent = texto;
      formulario.removeAttribute("aria-busy");
    }
  }
  function fecha(valor) {
    if (!valor) return "Sin actividad";
    const instante = new Date(valor);
    return Number.isNaN(instante.valueOf()) ? "Sin actividad" :
      instante.toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" });
  }
  function filasDatos(filas) {
    return filas.map(([titulo, valor, clase = ""]) =>
      '<div><dt>' + escapar(titulo) + '</dt><dd class="' + clase + '">' +
      escapar(valor) + '</dd></div>'
    ).join("");
  }
  async function cargarEstado() {
    const [resumen, edge] = await Promise.all([solicitud("resumen/"), solicitud("edge/")]);
    $("#resumen-edge").innerHTML = filasDatos([
      ["Versión", resumen.version], ["Sucursal", resumen.sucursal],
      ["Escucha", resumen.edge.escucha],
      ["Impresión", resumen.edge.backend_impresion === "tcp" ? "TCP" : "Vista previa"]
    ]);
    $("#resumen-servicios").innerHTML = filasDatos([
      ["Edge", "Activo", "positivo"], ["Base local", resumen.servicios.base_local, "positivo"],
      ["Cola de impresión", resumen.servicios.impresion === "activo" ? "Activa" : "Sin actividad reciente",
        resumen.servicios.impresion === "activo" ? "positivo" : "alerta"],
      ["Último latido", fecha(resumen.servicios.ultimo_latido_impresion)],
      ["Pendientes", resumen.cola.pendiente || 0],
      ["Errores", resumen.cola.error || 0, (resumen.cola.error || 0) ? "alerta" : ""]
    ]);
    const pendientes = Object.entries(resumen.sincronizacion.outbox)
      .filter(([clave]) => clave !== "entregado")
      .reduce((total, [, cantidad]) => total + cantidad, 0);
    $("#resumen-sync").innerHTML = filasDatos([
      ["Pedidos", resumen.sincronizacion.pedidos],
      ["Último contacto", fecha(resumen.sincronizacion.pedidos_ultima)],
      ["Outbox por enviar", pendientes, pendientes ? "alerta" : "positivo"]
    ]);
    estado.validacion = resumen.validacion_impresion;
    const estadoFisico = $("#validacion-estado");
    estadoFisico.textContent = estado.validacion.lista ? "Validada" : "Impresión pendiente de validar";
    estadoFisico.classList.toggle("pendiente", !estado.validacion.lista);
    estadoFisico.classList.toggle("positiva", estado.validacion.lista);
    $("#validacion-mensaje").textContent = estado.validacion.mensaje +
      (estado.validacion.confirmado_en ?
        " Última confirmación: " + fecha(estado.validacion.confirmado_en) +
        " por " + estado.validacion.confirmado_por + "." : "");
    $("#edge-host").value = edge.host;
    $("#edge-puerto").value = edge.puerto;
    $("#edge-https").checked = edge.usar_https;
    $("#edge-url").textContent = edge.url_anunciada ?
      "URL anunciada: " + edge.url_anunciada :
      "Sin dirección anunciada. Configúrala tras verificar el listener y el certificado.";
  }
  function renderImpresoras() {
    $("#lista-impresoras").innerHTML = estado.impresoras.length ?
      estado.impresoras.map(item =>
        '<article class="fila"><header><strong>' + escapar(item.nombre) + '</strong>' +
        '<span class="estado ' + (item.activa ? "" : "inactiva") + '">' +
        (item.activa ? "Activa" : "Inactiva") + '</span></header><p>' +
        escapar(item.host) + ':' + escapar(item.puerto) +
        (item.descripcion ? ' · ' + escapar(item.descripcion) : '') + '</p>' +
        '<div class="acciones"><button type="button" class="boton mini secundario" data-impresora-accion="editar" data-id="' +
        escapar(item.id) + '">Editar</button><button type="button" class="boton mini secundario" data-impresora-accion="sondear" data-id="' +
        escapar(item.id) + '">Probar conexión</button></div></article>'
      ).join("") :
      '<p class="vacio">No hay impresoras registradas. Registra una para asignarla a terminales.</p>';
  }
  async function cargarImpresoras() {
    estado.impresoras = (await solicitud("impresoras/")).impresoras;
    renderImpresoras();
    if (estado.terminal) renderRutas(estado.terminal, false);
  }
  function limpiarImpresora() {
    $("#form-impresora").reset();
    $("#impresora-id").value = "";
    $("#impresora-puerto").value = 9100;
    $("#impresora-activa").checked = true;
    $("#titulo-form-impresora").textContent = "Registrar impresora";
  }
  function editarImpresora(id) {
    const item = estado.impresoras.find(valor => valor.id === id);
    if (!item) return;
    $("#impresora-id").value = item.id;
    $("#impresora-nombre").value = item.nombre;
    $("#impresora-host").value = item.host;
    $("#impresora-puerto").value = item.puerto;
    $("#impresora-descripcion").value = item.descripcion;
    $("#impresora-activa").checked = item.activa;
    $("#titulo-form-impresora").textContent = "Editar impresora";
    $("#form-impresora").scrollIntoView({ block: "nearest" });
    $("#impresora-nombre").focus();
  }
  function renderTerminales() {
    $("#lista-terminales").innerHTML = estado.terminales.length ?
      estado.terminales.map(item => {
        const rutas = Object.entries(item.rutas).map(([destino, id]) => {
          const impresora = estado.impresoras.find(valor => valor.id === id);
          return escapar(destino + ": " + (impresora ? impresora.nombre : "recurso no disponible"));
        }).join(" · ");
        return '<article class="fila"><header><strong>' + escapar(item.nombre) +
          '</strong><span class="estado ' + (item.activa ? "" : "inactiva") + '">' +
          (item.activa ? "Activa" : "Inactiva") + '</span></header><small>' +
          escapar(item.device_id) + '</small><p>' +
          (rutas || "Sin recursos asignados; ruta heredada/general.") + '</p>' +
          '<div class="acciones"><button type="button" class="boton mini secundario" data-terminal-accion="editar" data-id="' +
          escapar(item.id) + '">Editar</button><button type="button" class="boton mini secundario" data-terminal-accion="rutas" data-id="' +
          escapar(item.id) + '">Configurar rutas</button></div></article>';
      }).join("") :
      '<p class="vacio">No hay terminales registradas. Añade el ID que muestra cada cliente.</p>';
  }
  async function cargarTerminales() {
    estado.terminales = (await solicitud("terminales/")).terminales;
    if (estado.terminal) {
      estado.terminal = estado.terminales.find(item => item.id === estado.terminal.id) || null;
    }
    renderTerminales();
    if (estado.terminal) renderRutas(estado.terminal, false);
  }
  function limpiarTerminal() {
    $("#form-terminal").reset();
    $("#terminal-id").value = "";
    $("#terminal-activa").checked = true;
    $("#titulo-form-terminal").textContent = "Registrar terminal";
  }
  function editarTerminal(id) {
    const item = estado.terminales.find(valor => valor.id === id);
    if (!item) return;
    $("#terminal-id").value = item.id;
    $("#terminal-nombre").value = item.nombre;
    $("#terminal-device-id").value = item.device_id;
    $("#terminal-activa").checked = item.activa;
    $("#titulo-form-terminal").textContent = "Editar terminal";
    $("#form-terminal").scrollIntoView({ block: "nearest" });
    $("#terminal-nombre").focus();
  }
  function opcionesRuta(seleccionada) {
    return '<option value="">Sin excepción / ruta general</option>' +
      estado.impresoras.map(item =>
        '<option value="' + escapar(item.id) + '" ' +
        (item.id === seleccionada ? "selected" : "") + ' ' +
        (item.activa ? "" : "disabled") + '>' + escapar(item.nombre) +
        (item.activa ? "" : " (inactiva)") + '</option>'
      ).join("");
  }
  function renderRutas(item, desplazar = true) {
    estado.terminal = item;
    $("#form-rutas").hidden = false;
    $("#rutas-terminal-nombre").textContent = item.nombre;
    for (const destino of ["todos", "caja", "cocina", "barra"]) {
      $("#ruta-" + destino).innerHTML = opcionesRuta(item.rutas[destino]);
    }
    if (desplazar) $("#form-rutas").scrollIntoView({ block: "nearest" });
  }
  async function cargarCola() {
    const trabajos = (await solicitud("cola/")).trabajos;
    $("#lista-cola").innerHTML = trabajos.length ? trabajos.map(item => {
      const accion = item.estado === "error" ?
        '<div class="acciones"><button type="button" class="boton mini secundario" data-reintentar="' + escapar(item.id) +
        '" data-actualizar="false">Misma ruta</button><button type="button" class="boton mini secundario" data-reintentar="' +
        escapar(item.id) + '" data-actualizar="true">Ruta actual</button></div>' : "—";
      return '<tr><td>' + escapar(fecha(item.creado_en)) + '</td><td>' +
        escapar(item.formato) + ' · ' + escapar(item.destino) + '</td><td><code>' +
        escapar(item.device_id || "Sin ID") + '</code></td><td>' +
        escapar(item.impresora || item.host || "Sin ruta") +
        (item.puerto ? ':' + escapar(item.puerto) : '') + '</td><td><span class="estado ' +
        (item.estado === "error" ? "error" : escapar(item.estado)) + '">' +
        escapar(item.estado) + '</span>' +
        (item.error ? '<br><small>' + escapar(item.error) + '</small>' : '') +
        '</td><td>' + accion + '</td></tr>';
    }).join("") : '<tr><td colspan="6" class="vacio">La cola está vacía.</td></tr>';
  }

  $("#actualizar-estado").addEventListener("click", async () => {
    try { await cargarEstado(); aviso("Estado actualizado."); }
    catch (error) { aviso(error.message, true); }
  });
  $("#actualizar-cola").addEventListener("click", async () => {
    try { await cargarCola(); aviso("Cola actualizada."); }
    catch (error) { aviso(error.message, true); }
  });
  $("#form-validacion").addEventListener("submit", evento => {
    evento.preventDefault();
    ocupado(evento.currentTarget, async () => {
      await solicitud("impresion/confirmar-prueba-fisica/", {
        method: "POST",
        body: JSON.stringify({
          confirmada: $("#validacion-confirmada").checked,
          nota: $("#validacion-nota").value.trim()
        })
      });
      $("#form-validacion").reset();
      await cargarEstado();
      aviso("Prueba física registrada para la configuración actual.");
    });
  });
  $("#form-edge").addEventListener("submit", evento => {
    evento.preventDefault();
    ocupado(evento.currentTarget, async () => {
      await solicitud("edge/", { method: "PUT", body: JSON.stringify({
        host: $("#edge-host").value.trim(),
        puerto: Number($("#edge-puerto").value),
        usar_https: $("#edge-https").checked
      }) });
      await cargarEstado();
      aviso("Dirección anunciada guardada.");
    });
  });
  $("#nueva-impresora").addEventListener("click", () => {
    limpiarImpresora(); $("#impresora-nombre").focus();
  });
  $("#cancelar-impresora").addEventListener("click", limpiarImpresora);
  $("#form-impresora").addEventListener("submit", evento => {
    evento.preventDefault();
    ocupado(evento.currentTarget, async () => {
      const id = $("#impresora-id").value;
      await solicitud(id ? "impresoras/" + id + "/" : "impresoras/", {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify({
          nombre: $("#impresora-nombre").value.trim(),
          host: $("#impresora-host").value.trim(),
          puerto: Number($("#impresora-puerto").value),
          descripcion: $("#impresora-descripcion").value.trim(),
          activa: $("#impresora-activa").checked
        })
      });
      await Promise.all([cargarImpresoras(), cargarTerminales()]);
      limpiarImpresora();
      aviso("Impresora guardada. Los trabajos anteriores mantienen su dirección.");
    });
  });
  $("#lista-impresoras").addEventListener("click", async evento => {
    const boton = evento.target.closest("[data-impresora-accion]");
    if (!boton) return;
    if (boton.dataset.impresoraAccion === "editar") {
      editarImpresora(boton.dataset.id); return;
    }
    boton.disabled = true;
    boton.textContent = "Probando…";
    try {
      const datos = await solicitud("impresoras/" + boton.dataset.id + "/sondeo/", {
        method: "POST", body: "{}"
      });
      aviso(datos.impresora.nombre + ": " + datos.sondeo.mensaje, !datos.sondeo.alcanzable);
    } catch (error) { aviso(error.message, true); }
    finally { boton.disabled = false; boton.textContent = "Probar conexión"; }
  });
  $("#nueva-terminal").addEventListener("click", () => {
    limpiarTerminal(); $("#terminal-nombre").focus();
  });
  $("#cancelar-terminal").addEventListener("click", limpiarTerminal);
  $("#form-terminal").addEventListener("submit", evento => {
    evento.preventDefault();
    ocupado(evento.currentTarget, async () => {
      const id = $("#terminal-id").value;
      await solicitud(id ? "terminales/" + id + "/" : "terminales/", {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify({
          nombre: $("#terminal-nombre").value.trim(),
          device_id: $("#terminal-device-id").value.trim(),
          activa: $("#terminal-activa").checked
        })
      });
      await cargarTerminales();
      limpiarTerminal();
      aviso("Terminal guardada.");
    });
  });
  $("#lista-terminales").addEventListener("click", evento => {
    const boton = evento.target.closest("[data-terminal-accion]");
    if (!boton) return;
    if (boton.dataset.terminalAccion === "editar") editarTerminal(boton.dataset.id);
    else {
      const item = estado.terminales.find(valor => valor.id === boton.dataset.id);
      if (item) renderRutas(item);
    }
  });
  $("#form-rutas").addEventListener("submit", evento => {
    evento.preventDefault();
    ocupado(evento.currentTarget, async () => {
      const rutas = {};
      for (const destino of ["todos", "caja", "cocina", "barra"]) {
        const id = $("#ruta-" + destino).value;
        if (id) rutas[destino] = id;
      }
      await solicitud("terminales/" + estado.terminal.id + "/rutas/", {
        method: "PUT", body: JSON.stringify({ rutas })
      });
      await cargarTerminales();
      aviso("Rutas guardadas. Aplican a trabajos nuevos.");
    });
  });
  $("#lista-cola").addEventListener("click", async evento => {
    const boton = evento.target.closest("[data-reintentar]");
    if (!boton) return;
    const actualizar = boton.dataset.actualizar === "true";
    if (!window.confirm("Confirma que revisaste el papel físico. Un trabajo interrumpido pudo imprimirse antes del error. ¿Reintentar ahora?")) return;
    boton.disabled = true;
    try {
      await solicitud("cola/" + boton.dataset.reintentar + "/reintentar/", {
        method: "POST", body: JSON.stringify({ actualizar_ruta: actualizar })
      });
      await Promise.all([cargarCola(), cargarEstado()]);
      aviso("Trabajo devuelto a la cola para un reintento controlado.");
    } catch (error) { aviso(error.message, true); boton.disabled = false; }
  });
  Promise.all([cargarEstado(), cargarImpresoras(), cargarTerminales(), cargarCola()])
    .catch(error => aviso(error.message, true));
})();
