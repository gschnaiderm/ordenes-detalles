# ms-ordenes-detalle

Microservicio de composición (FastAPI) que consume **ms-ordenes** (`ordenes.yaml`) y
**ms-lineas-orden** (`lineas_orden.yaml`) para exponer el detalle de una orden y verificar
la consistencia de sus importes.

Documentación interactiva generada desde el código: `GET /docs` (Swagger) y `GET /openapi.json`.

## Endpoints

| Método | Ruta | Respuestas |
|---|---|---|
| GET | `/health` | 200 |
| GET | `/ordenes/{id}/detalle` | 200, 404, 502 |
| GET | `/ordenes/{id}/verificacion` | 200, 404, 502 |

`id`: path, string, **obligatorio** (ej. `ORD001`). Las órdenes existentes cumplen `^ORD[0-9]+$`;
un `id` con otro formato (ej. `123`) responde **404** sin consultar los upstreams.

### Validaciones y tipos (resumen)

| Campo | Tipo | Obligatorio | Rango / formato |
|---|---|---|---|
| `ordenId` | string | sí | `^ORD[0-9]+$` |
| `clienteId` | string | sí | `^CLI[0-9]+$` |
| `fecha` | string (date) | sí | `YYYY-MM-DD` (UTC) |
| `estado` | string | sí | `pendiente`, `pagada`, `enviada`, `entregada`, `cancelada` |
| `total`, `totalRegistrado`, `sumaLineas` | number | sí | `>= 0` |
| `cantidadUnidades` | integer | sí | `>= 0` |
| `lineas[].lineaId` | string | sí | `^LIN[0-9]+$` |
| `lineas[].productoId` | string | sí | `^PRD[0-9]+$` |
| `lineas[].cantidad` | integer | sí | `>= 1` |
| `lineas[].precioUnitario`, `lineas[].subtotal` | number | sí | `>= 0` |
| `diferencia` | number | sí | `sumaLineas - totalRegistrado` (puede ser negativo) |
| `upstream.status` | integer | no (solo si hubo respuesta HTTP) | 100–599 |

### Errores

| Caso | Código | Cuerpo |
|---|---|---|
| La orden no existe (ms-ordenes devuelve 404, o el `id` no tiene formato `ORD...`) | 404 | `{"error": "not_found", "message": "No existe la orden ORD999."}` |
| Upstream no responde en 5 s, error de conexión, respuesta ≠ 200 o JSON fuera de contrato | 502 | `{"error": "upstream_unavailable", "message": "...", "upstream": {"service", "url", "status"?}}` |

## Decisiones de diseño

- **Llamadas en paralelo** (`asyncio.gather`) a ambos upstreams; si los dos fallan se informa primero el de órdenes.
- **Paginación**: las líneas se piden con `limit=100` (máximo del contrato) y se recorren todas las páginas.
- **Validación de lo recibido**: las respuestas de los upstreams se validan contra su contrato OpenAPI con Pydantic;
  si no lo cumplen (p. ej. una página HTML de Render mientras el servicio despierta) se responde 502.
- **Verificación al centavo**: los importes se redondean a 2 decimales antes de comparar, para evitar falsos
  negativos por coma flotante (`0.1 + 0.2 != 0.3`).
- **Mapeo de campos**: `id → ordenId / lineaId`, `precioUnit → precioUnitario`, `fecha` (date-time) → fecha UTC `YYYY-MM-DD`.
- Un `id` que no cumple `^ORD[0-9]+$` responde **404** (no 400): no puede existir en ms-ordenes, y se evita
  consultar ms-lineas-orden, que respondería 400 a ese filtro.
- Errores de validación devuelven **400** (no el 422 por defecto de FastAPI), igual que los servicios consumidos.

## Estructura

```
app/
  config.py    # variables de entorno
  schemas.py   # modelos Pydantic (upstream y respuestas)
  upstream.py  # clientes HTTP de ms-ordenes y ms-lineas-orden
  mocks.py     # upstreams simulados (cuando no hay URL configurada)
  main.py      # app FastAPI, endpoints y manejo de errores
tests/test_api.py  # tests con upstreams simulados
render.yaml        # blueprint de Render
```

## Configuración

| Variable | Default | Descripción |
|---|---|---|
| `ORDENES_URL` | *(sin definir → mock)* | URL base de ms-ordenes |
| `LINEAS_URL` | *(sin definir → mock)* | URL base de ms-lineas-orden |
| `SERVICE_NAME` | `ms-ordenes-detalle` | Nombre devuelto por `/health` |
| `UPSTREAM_TIMEOUT` | `5` | Timeout (segundos) por llamada a un upstream |

### Modo mock

Si `ORDENES_URL` o `LINEAS_URL` no están definidas (o están vacías), ese upstream se reemplaza
por uno simulado en memoria (`app/mocks.py`) que implementa el mismo contrato (`/health`,
`/items` con filtros y paginación, `/items/{id}`). Cada servicio se simula de forma
independiente, así que se puede usar uno real y otro simulado. Al arrancar se loguea un
`WARNING` indicando qué servicio está simulado.

Datos de ejemplo:

| Orden | Caso |
|---|---|
| `ORD001` | Correcta: 2 líneas, 3 unidades, total 1500 |
| `ORD002` | Inconsistente: total 1500, líneas suman 1400 (`diferencia: -100`) |
| `ORD003` | Correcta: 3 líneas, 8 unidades, total 7250.50 |
| `ORD004` | Cancelada, sin líneas, total 0 |
| `ORD999` | Inexistente → 404 |

## Ejecutar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# opcional: sin estas variables se usan los mocks
export ORDENES_URL=https://<ms-ordenes>.onrender.com LINEAS_URL=https://<ms-lineas-orden>.onrender.com
uvicorn app.main:app --reload --port 8000
pytest
```

## Deploy en Render

1. Subir esta carpeta a un repositorio de GitHub.
2. En Render: **New → Blueprint** y elegir el repo (usa `render.yaml`), o **New → Web Service** con:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Health check path: `/health`
3. Cargar `ORDENES_URL` y `LINEAS_URL` con las URLs de los servicios consumidos (si se dejan vacías, el servicio arranca con los mocks).
