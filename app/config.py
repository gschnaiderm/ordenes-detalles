"""Configuración del servicio, leída de variables de entorno (Render las inyecta).

Si `ORDENES_URL` o `LINEAS_URL` no están definidas, ese upstream se simula en memoria
(ver `app/mocks.py`) usando una URL interna que nunca sale a la red.
"""

import os
from dataclasses import dataclass

MOCK_ORDENES_URL = "http://mock-ms-ordenes"
MOCK_LINEAS_URL = "http://mock-ms-lineas-orden"


@dataclass(frozen=True)
class Settings:
    service_name: str
    ordenes_url: str
    lineas_url: str
    ordenes_mock: bool
    lineas_mock: bool
    upstream_timeout: float


def _url(var: str) -> str | None:
    value = os.getenv(var, "").strip().rstrip("/")
    return value or None


def load_settings() -> Settings:
    ordenes_url = _url("ORDENES_URL")
    lineas_url = _url("LINEAS_URL")
    return Settings(
        service_name=os.getenv("SERVICE_NAME", "ms-ordenes-detalle"),
        ordenes_url=ordenes_url or MOCK_ORDENES_URL,
        lineas_url=lineas_url or MOCK_LINEAS_URL,
        ordenes_mock=ordenes_url is None,
        lineas_mock=lineas_url is None,
        upstream_timeout=float(os.getenv("UPSTREAM_TIMEOUT", "5")),
    )


settings = load_settings()
