"""API HTTP kalkulatora - dla skryptow, botow i wszystkiego poza MCP.

Bez zadnych zaleznosci poza biblioteka standardowa. Nasluchuje domyslnie tylko
na 127.0.0.1; wystawienie na siec wymaga swiadomej decyzji i tokenu.

    GET  /                 krotki opis i lista sciezek
    GET  /zdrowie          czy zyje i skad liczy
    GET  /narzedzia        definicje narzedzi (te same, co w MCP)
    GET  /openapi.json     schemat OpenAPI wygenerowany z definicji
    GET  /stan             obecny scenariusz
    POST /narzedzie/<nazwa>   {...argumenty...} -> wynik
    POST /policz           skrot: ustawia scenariusz i od razu liczy
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .aitools import TOOL_SPECS, dispatch
from .control import ControlClient
from .headless import HeadlessBridge

MAX_BODY = 256 * 1024


class Engine:
    """Wspolny dostep do silnika: okno aplikacji albo stan lokalny."""

    def __init__(self, ruleset: str, attach: str):
        self.attach = attach
        self._client = ControlClient()
        self._bridge: HeadlessBridge | None = None
        self._ruleset = ruleset
        self._lock = threading.Lock()

    def _local(self) -> HeadlessBridge:
        if self._bridge is None:
            self._bridge = HeadlessBridge(self._ruleset)
        return self._bridge

    def source(self) -> str:
        if self.attach != "nigdy" and self._client.available():
            return "okno aplikacji"
        return "silnik lokalny"

    def call(self, name: str, args: dict) -> dict:
        if self.attach != "nigdy" and self._client.available():
            try:
                result = self._client.call(name, args)
                return {**result, "zrodlo": "okno aplikacji"} \
                    if isinstance(result, dict) else result
            except (OSError, RuntimeError):
                if self.attach == "zawsze":
                    raise
        with self._lock:                      # stan lokalny nie jest wspolbiezny
            result = dispatch(self._local(), name, args)
        return {**result, "zrodlo": "silnik lokalny"} \
            if isinstance(result, dict) else result


def openapi_schema() -> dict:
    paths = {
        "/narzedzie/" + spec["name"]: {
            "post": {
                "summary": spec["description"].split(".")[0],
                "description": spec["description"],
                "operationId": spec["name"],
                "requestBody": {
                    "required": bool(spec["input_schema"].get("required")),
                    "content": {"application/json": {"schema": spec["input_schema"]}},
                },
                "responses": {"200": {
                    "description": "wynik narzędzia",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }},
            }
        } for spec in TOOL_SPECS
    }
    paths["/stan"] = {"get": {"summary": "Obecny scenariusz",
                              "operationId": "stan",
                              "responses": {"200": {"description": "stan"}}}}
    paths["/zdrowie"] = {"get": {"summary": "Kontrola życia",
                                 "operationId": "zdrowie",
                                 "responses": {"200": {"description": "ok"}}}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "FCSiege API", "version": "1.0.0",
                 "description": "Kalkulator walki o miasto dla Freeciva. "
                                "Liczy wprost z plików .ruleset."},
        "servers": [{"url": "/"}],
        "paths": paths,
    }


INDEX = {
    "nazwa": "FCSiege API",
    "opis": "Kalkulator walki o miasto dla Freeciva.",
    "sciezki": {
        "GET /zdrowie": "czy serwer żyje i skąd liczy",
        "GET /narzedzia": "definicje narzędzi",
        "GET /openapi.json": "schemat OpenAPI",
        "GET /stan": "obecny scenariusz",
        "POST /narzedzie/<nazwa>": "wywołanie narzędzia, ciało = argumenty JSON",
        "POST /policz": "skrót: {scenariusz:{…}} ustawia i od razu liczy",
    },
}


class Handler(BaseHTTPRequestHandler):
    server_version = "FCSiege/1.0"
    engine: Engine
    token: str | None

    def log_message(self, fmt, *args):  # noqa: A003 - podpis z biblioteki
        sys.stderr.write("fcsiege-api: " + fmt % args + "\n")

    # ------------------------------------------------------------ pomocnicze

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str,
                          indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not self.token:
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {self.token}"

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("ciało żądania jest za duże")
        raw = self.rfile.read(length)
        if not raw.strip():
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("oczekiwano obiektu JSON")
        return data

    # ---------------------------------------------------------------- trasy

    def do_GET(self) -> None:  # noqa: N802 - podpis z biblioteki
        if not self._authorized():
            return self._send(401, {"blad": "brak lub zły token"})
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            return self._send(200, INDEX)
        if path == "/zdrowie":
            return self._send(200, {"status": "ok", "zrodlo": self.engine.source()})
        if path == "/narzedzia":
            return self._send(200, {"narzedzia": TOOL_SPECS})
        if path == "/openapi.json":
            return self._send(200, openapi_schema())
        if path == "/stan":
            return self._call("pokaz_stan", {})
        return self._send(404, {"blad": f"nie ma ścieżki {path}"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return self._send(401, {"blad": "brak lub zły token"})
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            body = self._body()
        except ValueError as exc:
            return self._send(400, {"blad": str(exc)})
        except json.JSONDecodeError as exc:
            return self._send(400, {"blad": f"nieprawidłowy JSON: {exc}"})

        if path == "/policz":
            scenario = body.get("scenariusz")
            if isinstance(scenario, dict) and scenario:
                applied = self.engine.call("ustaw_scenariusz", scenario)
                warn = applied.get("ostrzezenia") if isinstance(applied, dict) else None
                result = self.engine.call("policz", {})
                if warn:
                    result = {**result, "ostrzezenia": warn}
                return self._send(200, result)
            return self._call("policz", {})

        if path.startswith("/narzedzie/"):
            name = path[len("/narzedzie/"):]
            if name not in {t["name"] for t in TOOL_SPECS}:
                return self._send(404, {"blad": f"nie ma narzędzia {name}",
                                        "dostepne": [t["name"] for t in TOOL_SPECS]})
            return self._call(name, body)

        return self._send(404, {"blad": f"nie ma ścieżki {path}"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _call(self, name: str, args: dict) -> None:
        try:
            result = self.engine.call(name, args)
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"blad": f"{type(exc).__name__}: {exc}"})
        code = 400 if isinstance(result, dict) and "blad" in result else 200
        self._send(code, result)


def serve(host: str, port: int, engine: Engine, token: str | None) -> None:
    handler = type("BoundHandler", (Handler,), {"engine": engine, "token": token})
    httpd = ThreadingHTTPServer((host, port), handler)
    where = f"http://{host}:{port}"
    print(f"FCSiege API słucha na {where} (źródło: {engine.source()})",
          file=sys.stderr)
    if host not in ("127.0.0.1", "localhost", "::1") and not token:
        print("UWAGA: serwer jest wystawiony poza localhost bez tokenu — "
              "każdy w sieci może sterować kalkulatorem.", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fcsiege api", description="API HTTP kalkulatora FCSiege.")
    ap.add_argument("--host", default="127.0.0.1", help="domyślnie tylko lokalnie")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--ruleset", default="classic")
    ap.add_argument("--attach", choices=["auto", "zawsze", "nigdy"], default="auto",
                    help="czy sterować uruchomionym oknem aplikacji")
    ap.add_argument("--token", default=None,
                    help="wymagaj nagłówka Authorization: Bearer <token>")
    args = ap.parse_args(argv)
    serve(args.host, args.port, Engine(args.ruleset, args.attach), args.token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
