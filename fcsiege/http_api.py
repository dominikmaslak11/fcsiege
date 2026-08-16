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
import queue
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import i18n, webui
from .i18n import _
from .aitools import TOOL_SPECS, dispatch, localized_specs
from .control import ControlClient
from .headless import HeadlessBridge

MAX_BODY = 256 * 1024


class ChatSessions:
    """Historia rozmowy dla klientow webowych.

    Klucz to token sesji przyslany przez przegladarke; bez niego wszyscy
    dzielilyby jedna historie. Trzymamy to w pamieci procesu - serwer i tak
    zyje tyle, co partia.
    """

    def __init__(self, limit: int = 8):
        self._by_id: dict[str, object] = {}
        self._order: list[str] = []
        self._limit = limit
        self._lock = threading.Lock()

    def get(self, sid: str):
        from .chat import Conversation
        with self._lock:
            if sid not in self._by_id:
                self._by_id[sid] = Conversation()
                self._order.append(sid)
                while len(self._order) > self._limit:
                    self._by_id.pop(self._order.pop(0), None)
            return self._by_id[sid]

    def clear(self, sid: str) -> None:
        with self._lock:
            conv = self._by_id.get(sid)
            if conv is not None:
                conv.clear()


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


SESSIONS = ChatSessions()


class SaveBroadcaster:
    """Jeden obserwator zapisow, wielu sluchaczy.

    Kazdy klient SSE dostaje wlasna kolejke; obserwator startuje leniwie przy
    pierwszym sluchaczu, zeby serwer bez otwartej przegladarki nic nie robil.
    """

    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._watcher = None

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=8)
        with self._lock:
            self._subs.append(q)
            if self._watcher is None:
                from .watcher import SaveWatcher
                self._watcher = SaveWatcher(self._fire, interval=3.0)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def _fire(self, path: str) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(path)
            except queue.Full:
                pass


WATCH = SaveBroadcaster()


def openapi_schema() -> dict:
    specs = localized_specs()
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
                    "description": _("wynik narzędzia"),
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }},
            }
        } for spec in specs
    }
    en = i18n.language() == "en"
    paths["/stan"] = {"get": {"summary": "Current scenario" if en
                                         else "Obecny scenariusz",
                              "operationId": "stan",
                              "responses": {"200": {"description": "stan"}}}}
    paths["/zdrowie"] = {"get": {"summary": "Health check" if en
                                            else "Kontrola życia",
                                 "operationId": "zdrowie",
                                 "responses": {"200": {"description": "ok"}}}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "FCSiege API", "version": "1.0.0",
                 "description": ("City-assault calculator for Freeciv. "
                                 "Computes straight from the .ruleset files.")
                                if en else
                                ("Kalkulator walki o miasto dla Freeciva. "
                                 "Liczy wprost z plików .ruleset.")},
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

    def _send_html(self, body: str) -> None:
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _wants_html(self) -> bool:
        return "text/html" in (self.headers.get("Accept") or "")

    def _send(self, code: int, payload: dict) -> None:
        payload = i18n.translate(payload)
        body = json.dumps(payload, ensure_ascii=False, default=str,
                          indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _set_language(self) -> str:
        """Jezyk zadania: ?lang=en, potem naglowek Accept-Language."""
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        want = None
        for part in query.split("&"):
            if part.startswith(("lang=", "jezyk=")):
                want = part.split("=", 1)[1]
        return i18n.set_language(
            i18n.normalize(want or self.headers.get("Accept-Language")))

    # sama strona nie zawiera zadnych danych partii, wiec moze pojsc bez tokenu -
    # inaczej przegladarka nie mialaby jak o token poprosic
    PUBLIC_PATHS = ("/", "/ui")

    def _authorized(self) -> bool:
        if not self.token:
            return True
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in self.PUBLIC_PATHS and self.command == "GET" and self._wants_html():
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {self.token}"

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError(_("ciało żądania jest za duże"))
        raw = self.rfile.read(length)
        if not raw.strip():
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError(_("oczekiwano obiektu JSON"))
        return data

    # ---------------------------------------------------------------- trasy

    def do_GET(self) -> None:  # noqa: N802 - podpis z biblioteki
        self._set_language()
        if not self._authorized():
            return self._send(401, {"blad": _("brak lub zły token")})
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/ui"):
            if path == "/ui" or self._wants_html():
                return self._send_html(webui.page(i18n.language()))
            return self._send(200, INDEX)
        if path == "/api":
            return self._send(200, INDEX)
        if path in ("/zdrowie", "/health"):
            return self._send(200, {"status": "ok", "zrodlo": self.engine.source()})
        if path in ("/narzedzia", "/tools"):
            return self._send(200, {"narzedzia": localized_specs()})
        if path == "/openapi.json":
            return self._send(200, openapi_schema())
        if path in ("/dostawcy", "/providers"):
            from . import providers
            return self._send(200, providers.status())
        if path in ("/analiza", "/analysis"):
            return self._analysis()
        if path in ("/zdarzenia", "/events"):
            return self._events()
        if path in ("/stan", "/state"):
            return self._call("pokaz_stan", {})
        return self._send(404, {"blad": f"{_('nie ma ścieżki')} {path}"})

    def do_POST(self) -> None:  # noqa: N802
        self._set_language()
        if not self._authorized():
            return self._send(401, {"blad": _("brak lub zły token")})
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            body = self._body()
        except ValueError as exc:
            return self._send(400, {"blad": str(exc)})
        except json.JSONDecodeError as exc:
            return self._send(400, {"blad": f"{_('nieprawidłowy JSON')}: {exc}"})

        if path in ("/policz", "/compute"):
            scenario = body.get("scenariusz")
            if isinstance(scenario, dict) and scenario:
                applied = self.engine.call("ustaw_scenariusz", scenario)
                warn = applied.get("ostrzezenia") if isinstance(applied, dict) else None
                result = self.engine.call("policz", {})
                if warn:
                    result = {**result, "ostrzezenia": warn}
                return self._send(200, result)
            return self._call("policz", {})

        if path.startswith(("/dostawcy/", "/providers/")):
            # Klucze przyjmujemy TYLKO ta droga - nigdy narzedziem, ktore
            # model moglby wywolac. Klucz nie ma prawa przejsc przez rozmowe.
            from . import providers
            name = path.split("/", 2)[2]
            if name not in providers.PROVIDERS:
                return self._send(404, {"blad": f"nie znam dostawcy {name}",
                                        "dostepne": sorted(providers.PROVIDERS)})
            klucz = str(body.get("klucz") or body.get("key") or "").strip()
            if body.get("usun") or body.get("delete"):
                providers.forget_key(name)
            elif klucz:
                providers.save_key(name, klucz, str(body.get("model") or ""))
            if body.get("aktywny") or body.get("active"):
                providers.set_active(name)
            return self._send(200, providers.status())

        if path in ("/porownaj", "/compare"):
            return self._compare(body)

        if path in ("/czat", "/chat"):
            return self._chat(body)

        if path.startswith(("/narzedzie/", "/tool/")):
            name = path.split("/", 2)[2]
            known = {t["name"] for t in TOOL_SPECS}
            if i18n.canonical_tool(name) not in known:
                return self._send(404, {
                    "blad": f"{_('nie ma narzędzia')} {name}",
                    "dostepne": sorted(i18n.tool_name(n) for n in known)})
            return self._call(name, body)

        return self._send(404, {"blad": f"{_('nie ma ścieżki')} {path}"})

    ANALIZA = (
        ("alerty", {}),
        ("uklady_dyplomatyczne", {}),
        ("potencjal_wzrostu", {}),
        ("korupcja", {}),
        ("mobilnosc", {"tury": 2}),
        ("plan_kampanii", {"tury": 2}),
        ("plan_tury", {}),
        ("plan_badan", {"strategia": "gospodarka"}),
    )

    def _analysis(self) -> None:
        """Komplet analiz jednym zadaniem - dla przycisku i dla automatu.

        Kazde narzedzie liczymy osobno i osobno lapiemy blad, zeby jedna
        niedostepna analiza nie wywalila calego raportu.
        """
        out: dict = {}
        for nazwa, args in self.ANALIZA:
            # nazwa sekcji tez w jezyku zadania, inaczej klient dostaje
            # angielska tresc pod polskimi kluczami
            klucz = i18n.tool_name(nazwa)
            try:
                out[klucz] = self.engine.call(nazwa, dict(args))
            except Exception as exc:  # noqa: BLE001
                out[klucz] = {"blad": f"{type(exc).__name__}: {exc}"}
        try:
            out[i18n.tool_name("pokaz_stan")] = self.engine.call("pokaz_stan", {})
        except Exception:  # noqa: BLE001
            pass
        return self._send(200, out)

    def _events(self) -> None:
        """Strumien zdarzen: nowy zapis gry -> stan partii i ostrzezenia.

        Dzieki temu aplikacja odzywa sie sama po kazdej turze, zamiast czekac
        na pytanie.
        """
        from .watcher import summarize

        q = WATCH.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def emit(payload: dict) -> bool:
            try:
                self.wfile.write(b"data: " + json.dumps(
                    i18n.translate(payload), ensure_ascii=False,
                    default=str).encode("utf-8") + b"\n\n")
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        try:
            if not emit({"typ": "polaczono"}):
                return
            while True:
                try:
                    path = q.get(timeout=20)
                except queue.Empty:
                    if not emit({"typ": "puls"}):     # podtrzymanie polaczenia
                        return
                    continue
                payload = summarize(self.engine._local(), path, i18n.language())
                if not emit({"typ": "nowy_zapis", **payload}):
                    return
        finally:
            WATCH.unsubscribe(q)

    def _compare(self, body: dict) -> None:
        """To samo pytanie do kilku silnikow naraz, odpowiedzi obok siebie.

        Swiadomie NIE wybieramy "najlepszej" rady automatycznie - to byloby
        udawanie sadu, ktorego nie ma jak uzasadnic. Pokazujemy natomiast, po
        jakie narzedzia kazdy silnik siegnal i gdzie odpowiedzi sie schodza,
        bo rozjazd miedzy nimi jest sam w sobie informacja.
        """
        import concurrent.futures

        from . import providers
        from .chat import Conversation, reply

        text = str(body.get("tekst") or body.get("text") or "").strip()
        if not text:
            return self._send(400, {"blad": "puste pytanie"})
        chce = body.get("dostawcy") or body.get("providers") or None
        lang = i18n.language()

        kandydaci = [n for n in (chce or providers.PROVIDERS)
                     if n in providers.PROVIDERS and providers.resolve(n).ok]
        if not kandydaci:
            return self._send(503, {"blad": _("Brak klucza API"),
                                    "podpowiedz": "skonfiguruj choć jednego dostawcę"})

        def zapytaj(nazwa: str) -> dict:
            conv = Conversation()
            tekst, narzedzia, blad = [], [], None
            i18n.set_language(lang)          # watek ma wlasny kontekst
            try:
                for ev in reply(conv, text,
                                lambda n, a: self.engine.call(
                                    i18n.canonical_tool(n),
                                    i18n.untranslate_args(a)),
                                "", None, lang, nazwa):
                    if ev[0] == "delta":
                        tekst.append(ev[1])
                    elif ev[0] == "tool_start":
                        narzedzia.append(ev[1])
                    elif ev[0] == "error":
                        blad = ev[1]
            except Exception as exc:  # noqa: BLE001
                blad = f"{type(exc).__name__}: {exc}"
            return {"dostawca": nazwa,
                    "model": providers.resolve(nazwa).model,
                    "odpowiedz": "".join(tekst),
                    "uzyte_narzedzia": narzedzia,
                    "blad": blad}

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            wyniki = list(pool.map(zapytaj, kandydaci))

        udane = [w for w in wyniki if not w["blad"] and w["odpowiedz"]]
        wspolne = set.intersection(*[set(w["uzyte_narzedzia"]) for w in udane]) \
            if udane else set()
        import re as _re
        liczby = {w["dostawca"]: set(_re.findall(r"\d[\d\s]{0,12}\d|\d", w["odpowiedz"]))
                  for w in udane}
        zgodne = set.intersection(*liczby.values()) if liczby else set()
        return self._send(200, {
            "pytanie": text,
            "odpowiedzi": wyniki,
            "odpowiedzialo": len(udane),
            "narzedzia_uzyte_przez_wszystkich": sorted(wspolne),
            "liczby_zgodne_u_wszystkich": sorted(zgodne)[:12],
            "jak_czytac": (
                "nie wybieramy najlepszej odpowiedzi automatycznie — rozjazd "
                "między silnikami jest informacją sam w sobie; sprawdź, który "
                "sięgnął po właściwe narzędzie"),
        })

    def _chat(self, body: dict) -> None:
        """Rozmowa z asystentem, strumieniowana jako Server-Sent Events.

        Uzywamy tej samej petli, co okno (`chat.stream_reply`), i tego samego
        dispatchera narzedzi - wiec asystent w przegladarce steruje dokladnie
        tym samym silnikiem, lacznie z otwartym oknem aplikacji, jesli dziala.
        """
        from . import providers
        from .chat import reply

        text = str(body.get("tekst") or body.get("text") or "").strip()
        if not text:
            return self._send(400, {"blad": "puste pytanie"})

        dostawca = str(body.get("dostawca") or body.get("provider")
                       or providers.active_provider())
        key = providers.resolve(dostawca)
        if not key.ok:
            return self._send(503, {
                "blad": _("Brak klucza API"),
                "dostawca": dostawca,
                "podpowiedz": key.detail,
            })

        sid = str(body.get("sesja") or self.headers.get("X-FCSiege-Session") or "web")
        if body.get("wyczysc") or body.get("clear"):
            SESSIONS.clear(sid)
        conversation = SESSIONS.get(sid)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        lang = i18n.language()

        def emit(payload: dict) -> bool:
            try:
                self.wfile.write(b"data: " + json.dumps(
                    payload, ensure_ascii=False, default=str).encode("utf-8")
                    + b"\n\n")
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        broken = False

        def run_tool(name: str, args: dict):
            return self.engine.call(i18n.canonical_tool(name),
                                    i18n.untranslate_args(args))

        try:
            note = ""
            try:
                note = json.dumps(self.engine.call("pokaz_stan", {}),
                                  ensure_ascii=False, default=str)[:1500]
            except Exception:  # noqa: BLE001 - kontekst jest mile widziany, nie konieczny
                pass
            for event in reply(conversation, text, run_tool, note,
                               lambda: broken, lang, dostawca):
                kind = event[0]
                if kind in ("delta", "thinking", "error"):
                    ok = emit({"typ": kind, "tekst": event[1]})
                elif kind == "tool_start":
                    ok = emit({"typ": kind, "nazwa": event[1], "argumenty": event[2]})
                elif kind == "tool_end":
                    ok = emit({"typ": kind, "nazwa": event[1]})
                else:
                    ok = emit({"typ": "done"})
                if not ok:
                    broken = True
                    return
        except Exception as exc:  # noqa: BLE001
            emit({"typ": "error", "tekst": f"{type(exc).__name__}: {exc}"})

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


def tailscale_address() -> str | None:
    """Adres tego komputera w tailnecie, albo None.

    Najpierw pytamy `tailscale ip -4`, bo to zrodlo prawdy. Gdy CLI nie ma,
    szukamy adresu z zakresu CGNAT 100.64.0.0/10, ktorego Tailscale uzywa.
    """
    import shutil
    import socket
    import subprocess

    exe = shutil.which("tailscale")
    if exe:
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True,
                                 text=True, timeout=5)
            first = (out.stdout or "").strip().splitlines()
            if out.returncode == 0 and first:
                return first[0].strip()
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    for info in infos:
        addr = info[4][0]
        octets = addr.split(".")
        if len(octets) == 4 and octets[0] == "100" and 64 <= int(octets[1]) <= 127:
            return addr
    return None


def serve(host: str, port: int, engine: Engine, token: str | None) -> None:
    handler = type("BoundHandler", (Handler,), {"engine": engine, "token": token})
    httpd = ThreadingHTTPServer((host, port), handler)
    where = f"http://{host}:{port}"
    print(f"FCSiege API słucha na {where} (źródło: {engine.source()})",
          file=sys.stderr)
    print(f"Interfejs webowy: {where}/ui", file=sys.stderr)
    if token:
        print(f"Link z tokenem (jednorazowy, potraktuj jak hasło):\n"
              f"  {where}/ui?token={token}", file=sys.stderr)
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
    ap.add_argument("--tailscale", action="store_true",
                    help="nasłuchuj na adresie z tailnetu i wygeneruj token, "
                         "jeśli go nie podano")
    args = ap.parse_args(argv)
    host, token = args.host, args.token
    if args.tailscale:
        address = tailscale_address()
        if address is None:
            print("Nie znalazłem adresu w tailnecie. Czy Tailscale działa "
                  "(`tailscale status`)?", file=sys.stderr)
            return 2
        host = address
        if not token:
            token = secrets.token_urlsafe(24)
            print("Wygenerowałem token na tę sesję.", file=sys.stderr)
    serve(host, args.port, Engine(args.ruleset, args.attach), token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
