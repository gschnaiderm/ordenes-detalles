"""Upstreams simulados en memoria para cuando no hay URL configurada.

Implementan el contrato común de ordenes.yaml / lineas_orden.yaml
(`GET /health`, `GET /items`, `GET /items/{id}`) como `httpx.MockTransport`, de modo que
el resto del servicio los consume exactamente igual que a los reales.

Datos de ejemplo:
- ORD001: correcta (2 líneas, 3 unidades).
- ORD002: inconsistente (las líneas suman 100 menos que el total).
- ORD003: correcta (3 líneas).
- ORD004: cancelada, sin líneas y total 0.
"""

import httpx

ORDENES = [
    {"id": "ORD001", "clienteId": "CLI001", "fecha": "2026-10-11T14:22:00Z", "estado": "entregada", "total": 1500.00, "moneda": "ARS"},
    {"id": "ORD002", "clienteId": "CLI002", "fecha": "2026-10-12T09:05:00Z", "estado": "pagada", "total": 1500.00, "moneda": "ARS"},
    {"id": "ORD003", "clienteId": "CLI001", "fecha": "2026-10-15T18:40:00Z", "estado": "enviada", "total": 7250.50, "moneda": "ARS"},
    {"id": "ORD004", "clienteId": "CLI003", "fecha": "2026-10-17T11:00:00Z", "estado": "cancelada", "total": 0.00, "moneda": "ARS"},
]

LINEAS_ORDEN = [
    {"id": "LIN001", "ordenId": "ORD001", "productoId": "PRD001", "cantidad": 1, "precioUnit": 500.00, "subtotal": 500.00},
    {"id": "LIN002", "ordenId": "ORD001", "productoId": "PRD002", "cantidad": 2, "precioUnit": 500.00, "subtotal": 1000.00},
    {"id": "LIN003", "ordenId": "ORD002", "productoId": "PRD001", "cantidad": 1, "precioUnit": 500.00, "subtotal": 500.00},
    {"id": "LIN004", "ordenId": "ORD002", "productoId": "PRD003", "cantidad": 3, "precioUnit": 300.00, "subtotal": 900.00},
    {"id": "LIN005", "ordenId": "ORD003", "productoId": "PRD004", "cantidad": 1, "precioUnit": 4500.00, "subtotal": 4500.00},
    {"id": "LIN006", "ordenId": "ORD003", "productoId": "PRD005", "cantidad": 5, "precioUnit": 350.10, "subtotal": 1750.50},
    {"id": "LIN007", "ordenId": "ORD003", "productoId": "PRD002", "cantidad": 2, "precioUnit": 500.00, "subtotal": 1000.00},
]


def _error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": code, "message": message})


def _int_param(params, name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = params.get(name)
    if raw is None:
        return default
    value = int(raw)  # ValueError si no es entero
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError
    return value


def _transport(collection: str, items: list[dict], filters: tuple[str, ...]) -> httpx.MockTransport:
    items = sorted(items, key=lambda item: item["id"])

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        params = request.url.params

        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": f"mock-{collection}", "collection": collection})

        if path == "/items":
            try:
                limit = _int_param(params, "limit", 20, 1, 100)
                offset = _int_param(params, "offset", 0, 0)
            except ValueError:
                return _error(400, "bad_request", "limit debe estar entre 1 y 100 y offset debe ser >= 0.")
            # Filtros por igualdad exacta sin distinguir mayúsculas, combinados con AND.
            matched = [
                item
                for item in items
                if all(str(item[f]).lower() == params[f].lower() for f in filters if f in params)
            ]
            page = matched[offset : offset + limit]
            return httpx.Response(200, json={"items": page, "total": len(matched), "limit": limit, "offset": offset})

        if path.startswith("/items/"):
            item_id = path.removeprefix("/items/")
            for item in items:
                if item["id"] == item_id:
                    return httpx.Response(200, json=item)
            return _error(404, "not_found", f"No existe {item_id}.")

        return _error(404, "not_found", f"Ruta inexistente: {path}")

    return httpx.MockTransport(handler)


def ordenes_transport() -> httpx.MockTransport:
    return _transport("ordenes", ORDENES, ("clienteId", "estado"))


def lineas_transport() -> httpx.MockTransport:
    return _transport("lineas_orden", LINEAS_ORDEN, ("ordenId", "productoId"))
