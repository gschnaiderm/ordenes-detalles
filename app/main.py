"""ms-ordenes-detalle: compone ms-ordenes y ms-lineas-orden.

Documentación interactiva: /docs (Swagger) y /openapi.json.
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import mocks, upstream
from .config import settings
from .schemas import (
    ORDEN_ID_PATTERN,
    ErrorResponse,
    Health,
    LineaDetalle,
    LineaUpstream,
    OrdenDetalle,
    OrdenUpstream,
    UpstreamErrorResponse,
    Verificacion,
)


logger = logging.getLogger("uvicorn.error")


def _mock_mounts() -> dict[str, httpx.AsyncBaseTransport]:
    """Monta un upstream simulado por cada servicio sin URL configurada."""
    mounts = {}
    if settings.ordenes_mock:
        mounts[settings.ordenes_url] = mocks.ordenes_transport()
        logger.warning("ORDENES_URL no definida: se usa ms-ordenes simulado (%s).", settings.ordenes_url)
    if settings.lineas_mock:
        mounts[settings.lineas_url] = mocks.lineas_transport()
        logger.warning("LINEAS_URL no definida: se usa ms-lineas-orden simulado (%s).", settings.lineas_url)
    return mounts


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Un único cliente para reutilizar conexiones (keep-alive) hacia los upstreams.
    async with httpx.AsyncClient(timeout=settings.upstream_timeout, mounts=_mock_mounts()) as client:
        app.state.http = client
        yield


app = FastAPI(
    title="ms-ordenes-detalle",
    version="1.0.0",
    description=(
        "Microservicio de composición que consume **ms-ordenes** y **ms-lineas-orden** para "
        "devolver el detalle de una orden y verificar la consistencia de sus importes.\n\n"
        "- Cada llamada a un upstream tiene un timeout de 5 segundos.\n"
        "- Si un upstream no responde, falla (5xx) o responde fuera de contrato → **502**.\n"
        "- Orden inexistente → **404**, incluso si el `id` no tiene el formato `ORD...`.\n"
        "- Si `ORDENES_URL` o `LINEAS_URL` no están definidas, ese upstream se simula en memoria "
        "con datos de ejemplo (`ORD001`–`ORD004`)."
    ),
    lifespan=lifespan,
)

# Permite que páginas de otros dominios (p. ej. el monitor del dashboard) llamen al servicio
# desde el navegador. El servicio es público y de solo lectura.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


HttpClient = Annotated[httpx.AsyncClient, Depends(get_http_client)]
OrdenId = Annotated[
    str,
    Path(
        description=(
            "Identificador de la orden. Las órdenes existentes tienen el formato `ORD` seguido de "
            "dígitos; cualquier otro valor responde 404."
        ),
        min_length=1,
        examples=["ORD001"],
    ),
]

ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "La orden no existe (o el `id` no tiene formato `ORD...`)."},
    502: {"model": UpstreamErrorResponse, "description": "Un microservicio consumido no respondió o falló."},
}


# --------------------------------------------------------------------------- errores


class OrdenNoEncontrada(Exception):
    def __init__(self, orden_id: str):
        self.orden_id = orden_id


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    err = exc.errors()[0]
    campo = err["loc"][-1]
    return _error(400, "bad_request", f"Parámetro '{campo}' inválido: {err['msg']}.")


@app.exception_handler(OrdenNoEncontrada)
async def not_found_handler(_: Request, exc: OrdenNoEncontrada) -> JSONResponse:
    return _error(404, "not_found", f"No existe la orden {exc.orden_id}.")


@app.exception_handler(StarletteHTTPException)
async def http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "bad_request"
    return _error(exc.status_code, code, str(exc.detail))


@app.exception_handler(upstream.UpstreamError)
async def upstream_handler(_: Request, exc: upstream.UpstreamError) -> JSONResponse:
    info = {"service": exc.service, "url": exc.url}
    if exc.status is not None:
        info["status"] = exc.status
    return JSONResponse(
        status_code=502,
        content={
            "error": "upstream_unavailable",
            "message": f"No se pudo conectar a {exc.service}. Por favor reintenta en unos segundos.",
            "upstream": info,
        },
    )


# --------------------------------------------------------------------------- lógica


async def obtener_orden_y_lineas(client: httpx.AsyncClient, orden_id: str) -> tuple[OrdenUpstream, list[LineaUpstream]]:
    """Consulta ambos upstreams en paralelo. La orden tiene prioridad al reportar errores."""
    # Un id fuera del formato del contrato no puede existir en ms-ordenes: se responde 404 sin
    # consultar los upstreams (ms-lineas-orden respondería 400 al filtro y terminaría en 502).
    if not re.fullmatch(ORDEN_ID_PATTERN, orden_id):
        raise OrdenNoEncontrada(orden_id)
    orden, lineas = await asyncio.gather(
        upstream.get_orden(client, orden_id),
        upstream.get_lineas(client, orden_id),
        return_exceptions=True,
    )
    if isinstance(orden, BaseException):
        raise orden
    if orden is None:
        raise OrdenNoEncontrada(orden_id)
    if isinstance(lineas, BaseException):
        raise lineas
    return orden, lineas


def _fecha_utc(dt: datetime):
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt).date()


# --------------------------------------------------------------------------- endpoints


@app.get("/health", response_model=Health, tags=["Servicio"], summary="Estado del servicio")
async def health() -> Health:
    return Health(status="ok", timestamp=datetime.now(timezone.utc), service=settings.service_name)


@app.get(
    "/ordenes/{id}/detalle",
    response_model=OrdenDetalle,
    responses=ERROR_RESPONSES,
    tags=["Ordenes"],
    summary="Orden con sus líneas y cantidad de unidades",
)
async def detalle_orden(id: OrdenId, client: HttpClient) -> OrdenDetalle:
    orden, lineas = await obtener_orden_y_lineas(client, id)
    return OrdenDetalle(
        ordenId=orden.id,
        clienteId=orden.clienteId,
        fecha=_fecha_utc(orden.fecha),
        estado=orden.estado,
        total=orden.total,
        cantidadUnidades=sum(linea.cantidad for linea in lineas),
        lineas=[
            LineaDetalle(
                lineaId=linea.id,
                productoId=linea.productoId,
                cantidad=linea.cantidad,
                precioUnitario=linea.precioUnit,
                subtotal=linea.subtotal,
            )
            for linea in lineas
        ],
    )


@app.get(
    "/ordenes/{id}/verificacion",
    response_model=Verificacion,
    responses=ERROR_RESPONSES,
    tags=["Ordenes"],
    summary="Verifica que la suma de subtotales coincida con el total",
)
async def verificar_orden(id: OrdenId, client: HttpClient) -> Verificacion:
    orden, lineas = await obtener_orden_y_lineas(client, id)
    total = round(orden.total, 2)
    suma = round(sum(linea.subtotal for linea in lineas), 2)
    diferencia = round(suma - total, 2)  # se compara al centavo para evitar errores de coma flotante
    verificado = diferencia == 0
    return Verificacion(
        ordenId=orden.id,
        verificado=verificado,
        totalRegistrado=total,
        sumaLineas=suma,
        diferencia=diferencia + 0.0,  # normaliza -0.0
        mensaje="La orden está correcta" if verificado else "Inconsistencia: suma de líneas no coincide con el total",
    )


# --------------------------------------------------------------------------- OpenAPI


def custom_openapi():
    """Quita el 422 que FastAPI documenta por defecto: este servicio responde 400."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    for path in schema["paths"].values():
        for op in path.values():
            op.get("responses", {}).pop("422", None)
    for name in ("HTTPValidationError", "ValidationError"):
        schema.get("components", {}).get("schemas", {}).pop(name, None)
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
