"""Modelos de datos.

- Modelos *upstream*: validan lo que devuelven ms-ordenes y ms-lineas-orden según sus
  contratos OpenAPI (ordenes.yaml / lineas_orden.yaml). Si un upstream responde algo que
  no cumple su contrato, se trata como falla del upstream (502).
- Modelos de *respuesta*: documentan y validan lo que expone este servicio.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ORDEN_ID_PATTERN = r"^ORD[0-9]+$"
CLIENTE_ID_PATTERN = r"^CLI[0-9]+$"
LINEA_ID_PATTERN = r"^LIN[0-9]+$"
PRODUCTO_ID_PATTERN = r"^PRD[0-9]+$"

EstadoOrden = Literal["pendiente", "pagada", "enviada", "entregada", "cancelada"]


# --------------------------------------------------------------------------- upstream


class _Upstream(BaseModel):
    model_config = ConfigDict(extra="ignore")


class OrdenUpstream(_Upstream):
    id: str = Field(pattern=ORDEN_ID_PATTERN)
    clienteId: str = Field(pattern=CLIENTE_ID_PATTERN)
    fecha: datetime
    estado: EstadoOrden
    total: float = Field(ge=0)
    moneda: Literal["ARS"]


class LineaUpstream(_Upstream):
    id: str = Field(pattern=LINEA_ID_PATTERN)
    ordenId: str = Field(pattern=ORDEN_ID_PATTERN)
    productoId: str = Field(pattern=PRODUCTO_ID_PATTERN)
    cantidad: int = Field(ge=1)
    precioUnit: float = Field(ge=0)
    subtotal: float = Field(ge=0)


class LineaListUpstream(_Upstream):
    items: list[LineaUpstream]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


# --------------------------------------------------------------------------- respuestas


class Health(BaseModel):
    status: Literal["ok"] = Field(description="Siempre `ok` si el servicio responde.")
    timestamp: datetime = Field(description="Fecha y hora actual del servidor, ISO 8601 UTC.")
    service: str = Field(description="Nombre de este microservicio.", examples=["ms-ordenes-detalle"])


class LineaDetalle(BaseModel):
    lineaId: str = Field(pattern=LINEA_ID_PATTERN, examples=["LIN001"])
    productoId: str = Field(pattern=PRODUCTO_ID_PATTERN, examples=["PRD001"])
    cantidad: int = Field(ge=1, description="Unidades compradas (>= 1).", examples=[1])
    precioUnitario: float = Field(ge=0, description="Precio unitario (>= 0).", examples=[500.0])
    subtotal: float = Field(ge=0, description="Subtotal registrado de la línea (>= 0).", examples=[500.0])


class OrdenDetalle(BaseModel):
    ordenId: str = Field(pattern=ORDEN_ID_PATTERN, examples=["ORD001"])
    clienteId: str = Field(pattern=CLIENTE_ID_PATTERN, examples=["CLI001"])
    fecha: date = Field(description="Fecha de la orden (YYYY-MM-DD, UTC).")
    estado: EstadoOrden
    total: float = Field(ge=0, description="Total registrado en la orden (>= 0).", examples=[1500.0])
    cantidadUnidades: int = Field(ge=0, description="Suma de `cantidad` de todas las líneas.", examples=[3])
    lineas: list[LineaDetalle] = Field(description="Líneas de la orden, ordenadas por `lineaId`. Puede estar vacía.")


class Verificacion(BaseModel):
    ordenId: str = Field(pattern=ORDEN_ID_PATTERN, examples=["ORD001"])
    verificado: bool = Field(description="`true` si la suma de subtotales coincide con el total (tolerancia 0.01).")
    totalRegistrado: float = Field(ge=0, examples=[1500.0])
    sumaLineas: float = Field(ge=0, examples=[1500.0])
    diferencia: float = Field(description="`sumaLineas - totalRegistrado`. Negativo si las líneas suman menos.", examples=[0.0])
    mensaje: str = Field(examples=["La orden está correcta"])


class ErrorResponse(BaseModel):
    error: Literal["bad_request", "not_found"]
    message: str


class UpstreamInfo(BaseModel):
    service: Literal["ms-ordenes", "ms-lineas-orden"]
    url: str
    status: int | None = Field(
        default=None, ge=100, le=599, description="Código HTTP recibido del upstream (se omite si no hubo respuesta)."
    )


class UpstreamErrorResponse(BaseModel):
    error: Literal["upstream_unavailable"]
    message: str
    upstream: UpstreamInfo
