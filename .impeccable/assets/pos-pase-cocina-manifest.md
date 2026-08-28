# Manifiesto de recursos · pase de cocina

## Alcance y autoridad

Este manifiesto registra únicamente las miniaturas que el usuario indicó reutilizar
desde el sitio existente de Los Tocayos. No se descargaron fotografías externas ni se
generaron imágenes sintéticas. La aplicación conserva las copias dentro de
`ventas/static/ventas/menu/` para operar sin conexión.

- Fuente de menú autorizada por el usuario: `C:\tcysWeb\img\menu\cards\*-480.webp`.
- Fuente del respaldo de marca: `C:\tcysWeb\img\utils\cards\mainlogo-480.webp`.
- Destino operativo: `ventas/static/ventas/menu/`.
- Formato y tamaño: WebP, `480×360`.
- Integridad comprobada: cada archivo de destino tiene el mismo SHA-256 que su fuente.
- Responsable de resolución: `ventas/menu_imagenes.py`.

## Inventario

| Destino | Fuente |
|---|---|
| `agua-mineral.webp` | `menu/cards/agua-mineral-480.webp` |
| `aguasfrescas.webp` | `menu/cards/aguasfrescas-480.webp` |
| `barbacoaservida.webp` | `menu/cards/barbacoaservida-480.webp` |
| `bistek.webp` | `menu/cards/bistek-480.webp` |
| `consomelitro.webp` | `menu/cards/consomelitro-480.webp` |
| `consomemediolitro.webp` | `menu/cards/consomemediolitro-480.webp` |
| `consomevaso.webp` | `menu/cards/consomevaso-480.webp` |
| `gringa.webp` | `menu/cards/gringa-480.webp` |
| `gringabistec.webp` | `menu/cards/gringabistec-480.webp` |
| `gringachorizo.webp` | `menu/cards/gringachorizo-480.webp` |
| `lonche-bistec.webp` | `menu/cards/lonche-bistec-480.webp` |
| `lonche4.webp` | `menu/cards/lonche4-480.webp` |
| `lonchechorizo.webp` | `menu/cards/lonchechorizo-480.webp` |
| `loncheytaco.webp` | `menu/cards/loncheytaco-480.webp` |
| `mainlogo.webp` | `utils/cards/mainlogo-480.webp` |
| `refresco.webp` | `menu/cards/refresco-480.webp` |
| `tacobistecconqueso.webp` | `menu/cards/tacobistecconqueso-480.webp` |
| `tacochorizo.webp` | `menu/cards/tacochorizo-480.webp` |
| `tacochorizoconqueso.webp` | `menu/cards/tacochorizoconqueso-480.webp` |
| `tacoconqueso.webp` | `menu/cards/tacoconqueso-480.webp` |
| `tacoplanchado.webp` | `menu/cards/tacoplanchado-480.webp` |
| `tacoplanchadoconqueso.webp` | `menu/cards/tacoplanchadoconqueso-480.webp` |
| `tacosdorados.webp` | `menu/cards/tacosdorados-480.webp` |
| `tacospreparados.webp` | `menu/cards/tacospreparados-480.webp` |
| `tacosyagua.webp` | `menu/cards/tacosyagua-480.webp` |

## Reglas de uso

1. Una miniatura privada cargada en `Producto.imagen` tiene prioridad.
2. El mapa por código reutiliza deliberadamente fotos entre variantes equivalentes.
3. Lonches sin queso usan la foto del lonche normal correspondiente.
4. Todas las aguas frescas usan `aguasfrescas.webp`.
5. Todos los refrescos usan el recurso local `refresco.webp`.
6. Todo código sin asignación usa `mainlogo.webp`; nunca depende de una URL remota.

La titularidad o licencia comercial no se infiere de la ubicación del archivo. Si en el
futuro se incorpora un recurso externo, deberá añadirse aquí su URL de origen, autor,
licencia y fecha de obtención antes de publicarlo.
