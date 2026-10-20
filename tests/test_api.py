"""Tests con upstreams simulados (httpx.MockTransport): no requieren red."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import mocks
from app.config import settings
from app.main import app, get_http_client

ORDEN = {
    "id": "ORD001",
    "clienteId": "CLI001",
    "fecha": "2026-10-11T14:22:00Z",
    "estado": "entregada",
    "total": 1500,
    "moneda": "ARS",
}
LINEAS = [
    {"id": "LIN001", "ordenId": "ORD001", "productoId": "PRD001", "cantidad": 1, "precioUnit": 500, "subtotal": 500},
    {"id": "LIN002", "ordenId": "ORD001", "productoId": "PRD002", "cantidad": 2, "precioUnit": 500, "subtotal": 1000},
]


def upstream_ok(orden=ORDEN, lineas=LINEAS):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(settings.ordenes_url):
            if request.url.path == f"/items/{orden['id']}":
                return httpx.Response(200, json=orden)
            return httpx.Response(404, json={"error": "not_found", "message": "No existe"})
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        page = lineas[offset : offset + limit]
        return httpx.Response(200, json={"items": page, "total": len(lineas), "limit": limit, "offset": offset})

    return handler


@pytest.fixture
def client_with():
    def make(handler):
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app.dependency_overrides[get_http_client] = lambda: mock
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def test_health():
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == settings.service_name
    assert "timestamp" in body


def test_detalle(client_with):
    r = client_with(upstream_ok()).get("/ordenes/ORD001/detalle")
    assert r.status_code == 200
    assert r.json() == {
        "ordenId": "ORD001",
        "clienteId": "CLI001",
        "fecha": "2026-10-11",
        "estado": "entregada",
        "total": 1500.0,
        "cantidadUnidades": 3,
        "lineas": [
            {"lineaId": "LIN001", "productoId": "PRD001", "cantidad": 1, "precioUnitario": 500.0, "subtotal": 500.0},
            {"lineaId": "LIN002", "productoId": "PRD002", "cantidad": 2, "precioUnitario": 500.0, "subtotal": 1000.0},
        ],
    }


def test_detalle_pagina_todas_las_lineas(client_with):
    muchas = [
        {"id": f"LIN{i:03}", "ordenId": "ORD001", "productoId": "PRD001", "cantidad": 1, "precioUnit": 10, "subtotal": 10}
        for i in range(250)
    ]
    r = client_with(upstream_ok(lineas=muchas)).get("/ordenes/ORD001/detalle")
    assert r.status_code == 200
    assert len(r.json()["lineas"]) == 250
    assert r.json()["cantidadUnidades"] == 250


def test_verificacion_ok(client_with):
    r = client_with(upstream_ok()).get("/ordenes/ORD001/verificacion")
    assert r.json() == {
        "ordenId": "ORD001",
        "verificado": True,
        "totalRegistrado": 1500.0,
        "sumaLineas": 1500.0,
        "diferencia": 0.0,
        "mensaje": "La orden está correcta",
    }


def test_verificacion_inconsistente(client_with):
    lineas = [LINEAS[0], {**LINEAS[1], "subtotal": 900}]
    r = client_with(upstream_ok(lineas=lineas)).get("/ordenes/ORD001/verificacion")
    body = r.json()
    assert body["verificado"] is False
    assert body["sumaLineas"] == 1400.0
    assert body["diferencia"] == -100.0
    assert body["mensaje"] == "Inconsistencia: suma de líneas no coincide con el total"


def test_verificacion_sin_errores_de_coma_flotante(client_with):
    orden = {**ORDEN, "total": 0.3}
    lineas = [{**LINEAS[0], "subtotal": 0.1}, {**LINEAS[1], "subtotal": 0.2}]
    r = client_with(upstream_ok(orden=orden, lineas=lineas)).get("/ordenes/ORD001/verificacion")
    assert r.json()["verificado"] is True


@pytest.mark.parametrize("orden_id", ["123", "ord001", "ORD", "ORDX1"])
def test_id_invalido_400(client_with, orden_id):
    r = client_with(upstream_ok()).get(f"/ordenes/{orden_id}/detalle")
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"


def test_orden_inexistente_404(client_with):
    r = client_with(upstream_ok()).get("/ordenes/ORD999/detalle")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "No existe la orden ORD999."}


def test_timeout_502(client_with):
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    r = client_with(handler).get("/ordenes/ORD001/detalle")
    assert r.status_code == 502
    assert r.json() == {
        "error": "upstream_unavailable",
        "message": "No se pudo conectar a ms-ordenes. Por favor reintenta en unos segundos.",
        "upstream": {"service": "ms-ordenes", "url": settings.ordenes_url},
    }


def test_lineas_500_502(client_with):
    ok = upstream_ok()

    def handler(request):
        if str(request.url).startswith(settings.lineas_url):
            return httpx.Response(500, text="boom")
        return ok(request)

    r = client_with(handler).get("/ordenes/ORD001/verificacion")
    assert r.status_code == 502
    assert r.json()["upstream"] == {"service": "ms-lineas-orden", "url": settings.lineas_url, "status": 500}


def test_respuesta_fuera_de_contrato_502(client_with):
    def handler(request):
        return httpx.Response(200, text="<html>Service waking up</html>")

    r = client_with(handler).get("/ordenes/ORD001/detalle")
    assert r.status_code == 502
    assert r.json()["upstream"]["status"] == 200


def test_openapi_documenta_400_y_502():
    with TestClient(app) as c:
        responses = c.get("/openapi.json").json()["paths"]["/ordenes/{id}/detalle"]["get"]["responses"]
    assert {"200", "400", "404", "502"} <= set(responses)
    assert "422" not in responses


# --------------------------------------------------------------------------- upstreams simulados


@pytest.fixture
def mock_client(client_with):
    mock = httpx.AsyncClient(
        mounts={settings.ordenes_url: mocks.ordenes_transport(), settings.lineas_url: mocks.lineas_transport()}
    )
    app.dependency_overrides[get_http_client] = lambda: mock
    return TestClient(app)


def test_mock_detalle(mock_client):
    body = mock_client.get("/ordenes/ORD001/detalle").json()
    assert body["cantidadUnidades"] == 3
    assert [linea["lineaId"] for linea in body["lineas"]] == ["LIN001", "LIN002"]


@pytest.mark.parametrize(
    ("orden_id", "verificado", "diferencia"),
    [("ORD001", True, 0.0), ("ORD002", False, -100.0), ("ORD003", True, 0.0), ("ORD004", True, 0.0)],
)
def test_mock_verificacion(mock_client, orden_id, verificado, diferencia):
    body = mock_client.get(f"/ordenes/{orden_id}/verificacion").json()
    assert (body["verificado"], body["diferencia"]) == (verificado, diferencia)


def test_mock_orden_inexistente(mock_client):
    assert mock_client.get("/ordenes/ORD999/detalle").status_code == 404


def test_mock_paginacion_y_validacion_de_parametros():
    async def run():
        async with httpx.AsyncClient(transport=mocks.lineas_transport(), base_url="http://mock") as c:
            page = (await c.get("/items", params={"ordenId": "ord003", "limit": 2, "offset": 1})).json()
            bad = await c.get("/items", params={"limit": 0})
        return page, bad

    page, bad = asyncio.run(run())
    assert page["total"] == 3
    assert [item["id"] for item in page["items"]] == ["LIN006", "LIN007"]
    assert bad.status_code == 400
