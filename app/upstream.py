"""Clientes de los microservicios consumidos: ms-ordenes y ms-lineas-orden."""

import httpx
from pydantic import ValidationError

from .config import settings
from .schemas import LineaListUpstream, LineaUpstream, OrdenUpstream

ORDENES = "ms-ordenes"
LINEAS = "ms-lineas-orden"
PAGE_SIZE = 100  # máximo permitido por el contrato de /items


class UpstreamError(Exception):
    """El upstream no respondió a tiempo, falló o devolvió algo fuera de contrato."""

    def __init__(self, service: str, url: str, status: int | None = None):
        super().__init__(f"{service} ({url}) status={status}")
        self.service = service
        self.url = url
        self.status = status


async def _get(client: httpx.AsyncClient, service: str, base_url: str, path: str, params=None) -> httpx.Response:
    try:
        return await client.get(f"{base_url}{path}", params=params, timeout=settings.upstream_timeout)
    except httpx.HTTPError as exc:  # timeout, conexión rechazada, DNS, etc.
        raise UpstreamError(service, base_url) from exc


async def get_orden(client: httpx.AsyncClient, orden_id: str) -> OrdenUpstream | None:
    """Devuelve la orden o `None` si ms-ordenes responde 404."""
    base = settings.ordenes_url
    resp = await _get(client, ORDENES, base, f"/items/{orden_id}")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise UpstreamError(ORDENES, base, resp.status_code)
    try:
        return OrdenUpstream.model_validate(resp.json())
    except (ValueError, ValidationError) as exc:
        raise UpstreamError(ORDENES, base, resp.status_code) from exc


async def get_lineas(client: httpx.AsyncClient, orden_id: str) -> list[LineaUpstream]:
    """Devuelve todas las líneas de la orden recorriendo la paginación de ms-lineas-orden."""
    base = settings.lineas_url
    lineas: list[LineaUpstream] = []
    offset = 0
    while True:
        params = {"ordenId": orden_id, "limit": PAGE_SIZE, "offset": offset}
        resp = await _get(client, LINEAS, base, "/items", params)
        if resp.status_code != 200:
            raise UpstreamError(LINEAS, base, resp.status_code)
        try:
            page = LineaListUpstream.model_validate(resp.json())
        except (ValueError, ValidationError) as exc:
            raise UpstreamError(LINEAS, base, resp.status_code) from exc

        lineas.extend(page.items)
        offset += len(page.items)
        if not page.items or offset >= page.total:
            return lineas
