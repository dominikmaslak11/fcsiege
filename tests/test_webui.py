"""Testy webowego interfejsu i strumienia czatu po HTTP.

Silnik zostaje na komputerze, przy zapisach gry, a przegladarka jest cienkim
klientem - wiec sprawdzamy trzy rzeczy: czy strona w ogole wychodzi bez tokenu
(inaczej nie ma jak o token poprosic), czy dane sa za tokenem, i czy czat
naprawde strumieniuje zdarzenia SSE z tej samej petli, ktorej uzywa okno.

Nic nie wychodzi do sieci - klient Anthropica jest podstawiony.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import types
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fcsiege import aicreds, http_api, i18n, webui      # noqa: E402
from fcsiege.http_api import Engine, Handler            # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'BLAD'} {name}{'  ' + str(detail) if detail else ''}")
    if not ok:
        failures.append(name)


# ------------------------------------------------------------ atrapa klienta

def block(**kw):
    return types.SimpleNamespace(**kw)


def text_event(text: str):
    return types.SimpleNamespace(
        type="content_block_delta",
        delta=types.SimpleNamespace(type="text_delta", text=text))


class FakeStream:
    def __init__(self, message, events):
        self._message, self._events = message, events

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._message


class FakeClient:
    def __init__(self, turns):
        self.calls = []
        outer = self

        class Messages:
            def stream(self, **kwargs):
                outer.calls.append(kwargs)
                message, events = turns.pop(0)
                return FakeStream(message, events)

        self.beta = types.SimpleNamespace(messages=Messages())


# ------------------------------------------------------------------ serwer

class Server:
    def __init__(self, token: str | None):
        handler = type("H", (Handler,),
                       {"engine": Engine("sandbox", "nigdy"), "token": token})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.auth = {"Authorization": f"Bearer {token}"} if token else {}

    def close(self):
        self.httpd.shutdown()

    def get(self, path, **headers):
        req = urllib.request.Request(self.base + path,
                                     headers={**self.auth, **headers})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()

    def post_stream(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(),
            headers={**self.auth, "Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=60)


# ------------------------------------------------------------------- testy

def test_page() -> None:
    print("\nStrona:")
    for lang in ("pl", "en"):
        html = webui.page(lang)
        check(f"[{lang}] szablon podstawiony w calosci",
              "{t[" not in html and "{lang}" not in html and "{bg}" not in html)
        check(f"[{lang}] jest tytul i viewport",
              "<title>" in html and "viewport" in html)
        check(f"[{lang}] nic z zewnatrz",
              "http://" not in html.replace("http://127.0.0.1", "")
              and "https://" not in html and "cdn" not in html.lower())
    check("wersje jezykowe sie roznia", webui.page("pl") != webui.page("en"))
    check("angielska nie ma polskich znakow w napisach",
          not any(c in v for v in webui.TEXTS["en"].values() for c in "ąćęłńóśźż"))
    check("oba katalogi maja te same klucze",
          set(webui.TEXTS["pl"]) == set(webui.TEXTS["en"]))


def test_serving() -> None:
    print("\nSerwowanie i token:")
    srv = Server("tajne-haslo")
    try:
        status, ctype, body = srv.get("/ui", Accept="text/html")
        check("strona wychodzi", status == 200 and ctype.startswith("text/html"))

        # bez tokenu: strona tak, dane nie
        req = urllib.request.Request(srv.base + "/ui", headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            check("strona dziala BEZ tokenu (inaczej nie ma jak go podać)",
                  resp.status == 200)
        try:
            urllib.request.urlopen(
                urllib.request.Request(srv.base + "/stan"), timeout=30)
            check("dane wymagają tokenu", False)
        except urllib.error.HTTPError as exc:
            check("dane wymagają tokenu", exc.code == 401)
        try:
            urllib.request.urlopen(
                urllib.request.Request(srv.base + "/", headers={"Accept": "*/*"}),
                timeout=30)
            check("JSON pod / wymaga tokenu", False)
        except urllib.error.HTTPError as exc:
            check("JSON pod / wymaga tokenu", exc.code == 401)

        # ten sam adres: HTML dla przegladarki, JSON dla skryptu
        _, html_ct, _ = srv.get("/", Accept="text/html,*/*")
        _, json_ct, raw = srv.get("/", Accept="application/json")
        check("/ rozróżnia przeglądarkę i skrypt",
              html_ct.startswith("text/html") and json_ct.startswith("application/json"))
        check("/api zawsze zwraca JSON",
              srv.get("/api", Accept="text/html")[1].startswith("application/json"))

        _, _, en = srv.get("/ui?lang=en", Accept="text/html")
        check("strona respektuje ?lang=en", b"Build plan" in en)
    finally:
        srv.close()


def test_chat_stream() -> None:
    print("\nStrumien czatu (klient podstawiony):")
    turns = [
        (types.SimpleNamespace(
            stop_reason="tool_use",
            content=[block(type="tool_use", id="t1", name="policz", input={})]),
         []),
        (types.SimpleNamespace(
            stop_reason="end_turn",
            content=[block(type="text", text="Gotowe.")]),
         [text_event("Potrzebujesz "), text_event("13 katapult.")]),
    ]
    fake = FakeClient(turns)
    # rozmowa idzie przez warstwe dostawcow: klucz bierzemy ze srodowiska,
    # a sam klient podstawiamy
    saved_make = aicreds.make_client
    saved_env = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    aicreds.make_client = lambda creds: fake

    srv = Server(None)
    try:
        events = []
        with srv.post_stream("/czat", {"tekst": "Ile katapult?", "sesja": "test"}) as resp:
            check("odpowiedź to strumień SSE",
                  resp.headers.get("Content-Type", "").startswith("text/event-stream"),
                  resp.headers.get("Content-Type"))
            buffer = b""
            for raw in resp:
                buffer += raw
                while b"\n\n" in buffer:
                    chunk, _, buffer = buffer.partition(b"\n\n")
                    line = next((l for l in chunk.split(b"\n")
                                 if l.startswith(b"data:")), None)
                    if line:
                        events.append(json.loads(line[5:].decode("utf-8")))

        kinds = [e["typ"] for e in events]
        check("poszły zdarzenia narzędzia", "tool_start" in kinds and "tool_end" in kinds,
              kinds)
        check("narzędziem był policz",
              any(e.get("nazwa") == "policz" for e in events))
        check("tekst dotarł kawałkami",
              "".join(e["tekst"] for e in events if e["typ"] == "delta")
              == "Potrzebujesz 13 katapult.")
        check("strumień kończy się zdarzeniem done", kinds[-1] == "done", kinds[-1])
        check("bez błędów", "error" not in kinds, [e for e in events if e["typ"] == "error"])

        sent = fake.calls[0]
        check("użyty model to claude-opus-5", sent["model"] == "claude-opus-5")
        check("narzędzia poszły w komplecie", len(sent["tools"]) >= 20)
        check("kontekst stanu doklejony do promptu",
              "zestaw_regul" in sent["system"][0]["text"])

        # historia zyje miedzy zadaniami
        conv = http_api.SESSIONS.get("test")
        check("historia rozmowy zapamiętana", len(conv.messages) >= 3,
              f"{len(conv.messages)} wiadomości")
    finally:
        srv.close()
        aicreds.make_client = saved_make
        if saved_env is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved_env


def test_analysis() -> None:
    print("\nZbiorcza analiza:")
    srv = Server("tajne")
    try:
        try:
            urllib.request.urlopen(
                urllib.request.Request(srv.base + "/analiza"), timeout=20)
            check("analiza wymaga tokenu", False)
        except urllib.error.HTTPError as exc:
            check("analiza wymaga tokenu", exc.code == 401)

        i18n.set_language("pl")
        _, _, raw = srv.get("/analiza")
        pl = json.loads(raw)
        check("po polsku wszystkie sekcje",
              {"alerty", "korupcja", "mobilnosc"} <= set(pl), sorted(pl))
        check("żadna sekcja się nie wywaliła",
              not [k for k, v in pl.items() if isinstance(v, dict) and "blad" in v],
              [k for k, v in pl.items() if isinstance(v, dict) and "blad" in v])

        _, _, raw = srv.get("/analysis?lang=en")
        en = json.loads(raw)
        check("po angielsku nazwy sekcji też przetłumaczone",
              {"alerts", "waste", "mobility"} <= set(en), sorted(en))
        check("angielskie sekcje bez polskich kluczy",
              not any(c in k for k in en for c in "ąćęłńóśźż"), sorted(en))
    finally:
        srv.close()
        i18n.set_language("pl")


def test_chat_without_key() -> None:
    print("\nCzat bez klucza:")
    from fcsiege import providers
    saved_env = os.environ.pop("ANTHROPIC_API_KEY", None)
    saved_file = providers.CRED_FILE
    providers.CRED_FILE = saved_file + ".pusty-test"
    srv = Server(None)
    try:
        srv.post_stream("/czat", {"tekst": "cześć"})
        check("brak klucza zgłoszony jako błąd", False)
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read())
        check("brak klucza daje 503 z podpowiedzią",
              exc.code == 503 and "podpowiedz" in body, body.get("blad"))
    finally:
        srv.close()
        providers.CRED_FILE = saved_file
        if saved_env is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_env


def test_providers() -> None:
    print("\nDostawcy modeli i klucze:")
    from fcsiege import providers

    real = providers.CRED_FILE
    tmp = real + ".test"
    providers.CRED_FILE = tmp                      # nie ruszamy pliku użytkownika
    try:
        st = providers.status()
        check("znamy czterech dostawców", len(st["dostawcy"]) == 4,
              [d["dostawca"] for d in st["dostawcy"]])
        check("Claude idzie własnym protokołem",
              providers.PROVIDERS["claude"].protocol == "anthropic")
        check("reszta protokołem OpenAI",
              all(providers.PROVIDERS[p].protocol == "openai"
                  for p in ("openai", "gemini", "deepseek")))

        providers.save_key("deepseek", "sk-TAJNE-TESTOWE", "deepseek-chat")
        blob = json.dumps(providers.status(), ensure_ascii=False)
        check("klucz NIE wycieka do status()", "TAJNE-TESTOWE" not in blob)
        check("resolve widzi zapisany klucz",
              providers.resolve("deepseek").api_key == "sk-TAJNE-TESTOWE")
        check("plik ma prawa 0600",
              oct(os.stat(tmp).st_mode & 0o777) == "0o600",
              oct(os.stat(tmp).st_mode & 0o777))

        os.environ["DEEPSEEK_API_KEY"] = "sk-ZE-SRODOWISKA"
        try:
            k = providers.resolve("deepseek")
            check("zmienna środowiskowa ma pierwszeństwo przed plikiem",
                  k.api_key == "sk-ZE-SRODOWISKA" and k.source == "env")
        finally:
            del os.environ["DEEPSEEK_API_KEY"]

        providers.forget_key("deepseek")
        check("usunięcie klucza działa", not providers.resolve("deepseek").ok)

        from fcsiege.openai_chat import tools_for_openai
        narzedzia = tools_for_openai("pl")
        check("narzędzia przełożone na format funkcji OpenAI",
              narzedzia and all(t["type"] == "function"
                                and "parameters" in t["function"]
                                for t in narzedzia), len(narzedzia))
    finally:
        providers.CRED_FILE = real
        if os.path.exists(tmp):
            os.remove(tmp)


def test_provider_endpoint() -> None:
    print("\nPunkt HTTP dostawcow:")
    srv = Server("tajne")
    try:
        _, _, raw = srv.get("/dostawcy")
        d = json.loads(raw)
        check("lista dostawców po HTTP", len(d.get("dostawcy", [])) == 4)
        check("żaden klucz w odpowiedzi",
              "sk-" not in json.dumps(d).replace("sk-ant-...", "").replace("sk-...", ""))
        try:
            urllib.request.urlopen(
                urllib.request.Request(srv.base + "/dostawcy"), timeout=20)
            check("lista dostawców wymaga tokenu", False)
        except urllib.error.HTTPError as exc:
            check("lista dostawców wymaga tokenu", exc.code == 401)
        try:
            srv.post_stream("/dostawcy/nie-ma", {"klucz": "x"})
            check("nieznany dostawca daje 404", False)
        except urllib.error.HTTPError as exc:
            check("nieznany dostawca daje 404", exc.code == 404)
    finally:
        srv.close()


def test_tailscale() -> None:
    print("\nWykrywanie tailnetu:")
    address = http_api.tailscale_address()
    if address is None:
        check("brak tailnetu zgłoszony jako None (Tailscale nie działa)", True)
        return
    octets = address.split(".")
    check("adres z zakresu CGNAT 100.64.0.0/10",
          len(octets) == 4 and octets[0] == "100" and 64 <= int(octets[1]) <= 127,
          address)


if __name__ == "__main__":
    i18n.set_language("pl")
    test_page()
    test_serving()
    test_chat_stream()
    test_analysis()
    test_chat_without_key()
    test_providers()
    test_provider_endpoint()
    test_tailscale()

    print("\n" + "=" * 60)
    if failures:
        print(f"NIEPOWODZENIA ({len(failures)}): " + ", ".join(failures))
        sys.exit(1)
    print("Wszystkie testy interfejsu webowego przeszly.")
