"""Testy trzech powierzchni: MCP, API HTTP i gniazda sterujacego oknem.

Uruchom: python3 tests/test_surfaces.py

Serwer MCP jest sprawdzany prawdziwym klientem MCP po stdio, API - prawdziwymi
zadaniami HTTP, a gniazdo - uruchomionym oknem Qt. Zaden test nie chodzi do
sieci ani do zadnego API zewnetrznego.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fcsiege.aitools import TOOL_SPECS, dispatch  # noqa: E402
from fcsiege.headless import HeadlessBridge  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  OK   " if ok else "  BLAD ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------- silnik lokalny

def test_headless_matches_gui():
    """Rdzen bez Qt musi dawac te same liczby, co okno aplikacji."""
    print("\nZgodnosc silnika bez Qt z oknem aplikacji:")
    from PySide6.QtWidgets import QApplication
    from fcsiege.app import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    app.processEvents()

    steps = [
        ("ustaw_scenariusz", {"tryb": "szturm", "zestaw_regul": "classic",
                              "teren_miasta": "Hills", "budowle": ["City Walls"],
                              "w_miescie": True, "okopani": True,
                              "wielkosc_miasta": 8}),
        ("ustaw_sily_wroga", {"jednostki": [{"jednostka": "Warriors", "liczba": 5}]}),
        ("ustaw_moja_jednostke", {"jednostka": "Catapult", "liczba": 8}),
    ]
    head = HeadlessBridge("classic")
    for name, args in steps:
        dispatch(win, name, args)
        dispatch(head, name, args)

    a = dispatch(win, "policz", {})
    b = dispatch(head, "policz", {})
    for key in ("sila_ataku", "sila_obrony", "potrzeba_90proc", "potrzeba_99proc"):
        check(f"zgodne: {key}", a[key] == b[key], f"okno={a[key]} bez_qt={b[key]}")
    check("zbliżona średnia liczba ataków",
          abs(a["srednio_atakow"] - b["srednio_atakow"]) < 0.3,
          f"{a['srednio_atakow']} vs {b['srednio_atakow']}")

    for name, args in [("ustaw_scenariusz", {"tryb": "obrona",
                                             "teren_miasta": "Plains"}),
                       ("ustaw_sily_wroga",
                        {"jednostki": [{"jednostka": "Legion", "liczba": 3}]}),
                       ("ustaw_moja_jednostke", {"jednostka": "Pikemen"})]:
        dispatch(win, name, args)
        dispatch(head, name, args)
    a = dispatch(win, "policz", {})
    b = dispatch(head, "policz", {})
    check("tryb obrony: zgodna siła obrony",
          a["sila_mojej_obrony"] == b["sila_mojej_obrony"],
          f"{a['sila_mojej_obrony']} vs {b['sila_mojej_obrony']}")
    check("tryb obrony: zgodne minimum obrońców",
          a["minimum_obroncow_na_95proc"] == b["minimum_obroncow_na_95proc"],
          f"{a['minimum_obroncow_na_95proc']} vs {b['minimum_obroncow_na_95proc']}")
    return app, win


# ------------------------------------------------------------------------ MCP

async def _mcp_roundtrip():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(ROOT, "fcsiege.py"), "mcp",
              "--ruleset", "sandbox", "--attach", "nigdy"],
        env={**os.environ, "PYTHONPATH": ROOT},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            resources = await session.list_resources()
            res = await session.call_tool("ustaw_scenariusz", {
                "teren_miasta": "Grassland", "ulepszenia_kafla": ["River"],
                "budowle": [], "poziom_technologiczny": 8})
            await session.call_tool("ustaw_sily_wroga", {"jednostki": [
                {"jednostka": "Phalanx", "liczba": 1},
                {"jednostka": "Settlers", "liczba": 1}]})
            await session.call_tool("ustaw_moja_jednostke",
                                    {"jednostka": "Catapult"})
            calc = await session.call_tool("policz", {})
            bad = await session.call_tool("dane_jednostki",
                                          {"jednostka": "Katapulta"})
            return init, tools, resources, res, calc, bad


def test_mcp():
    print("\nSerwer MCP (prawdziwy klient po stdio):")
    try:
        init, tools, resources, applied, calc, bad = asyncio.run(
            asyncio.wait_for(_mcp_roundtrip(), timeout=180))
    except Exception as exc:  # noqa: BLE001
        check("uchwyt MCP nawiązany", False, f"{type(exc).__name__}: {exc}")
        return

    check("serwer się przedstawia", init.serverInfo.name == "fcsiege",
          init.serverInfo.name)
    check("instrukcja serwera dotarła",
          bool(init.instructions) and "ruleset" in init.instructions)
    check("wystawia komplet narzędzi", len(tools.tools) == len(TOOL_SPECS),
          f"{len(tools.tools)} z {len(TOOL_SPECS)}")
    check("każde narzędzie ma schemat wejścia",
          all(t.inputSchema.get("type") == "object" for t in tools.tools))
    check("wystawia zasób z instrukcją", len(resources.resources) >= 1)

    payload = json.loads(applied.content[0].text)
    check("ustaw_scenariusz zadziałało przez MCP",
          payload["teren_miasta"] == "Grassland"
          and payload["ulepszenia_kafla"] == ["River"], str(payload.get("teren_miasta")))

    numbers = json.loads(calc.content[0].text)
    check("policz zwraca liczby przez MCP",
          numbers["obroncy"] == ["1x Phalanx", "1x Settlers"]
          and numbers["potrzeba_90proc"] is not None,
          f"obrońcy={numbers.get('obroncy')} 90%={numbers.get('potrzeba_90proc')}")
    check("wynik mówi, skąd pochodzi",
          numbers.get("zrodlo") == "silnik lokalny", str(numbers.get("zrodlo")))

    miss = json.loads(bad.content[0].text)
    check("literówka w nazwie daje podpowiedź, nie wyjątek",
          "Catapult" in miss.get("podobne", []), str(miss.get("podobne")))


# ------------------------------------------------------------------ API HTTP

def _get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read().decode())


def _post(url, body, token=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_http():
    print("\nAPI HTTP (prawdziwe żądania):")
    port = free_port()
    token = "tajne123"
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "fcsiege.py"), "api",
         "--port", str(port), "--ruleset", "sandbox",
         "--attach", "nigdy", "--token", token],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": ROOT})
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                _get(base + "/zdrowie", token)
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)
        else:
            check("serwer API wystartował", False, "brak odpowiedzi")
            return

        code, health = _get(base + "/zdrowie", token)
        check("zdrowie odpowiada", code == 200 and health["status"] == "ok")

        try:
            _get(base + "/zdrowie")
            check("bez tokenu odmawia", False, "przepuścił")
        except urllib.error.HTTPError as e:
            check("bez tokenu odmawia", e.code == 401, str(e.code))

        code, tools = _get(base + "/narzedzia", token)
        check("wystawia definicje narzędzi",
              code == 200 and len(tools["narzedzia"]) == len(TOOL_SPECS))

        code, spec = _get(base + "/openapi.json", token)
        check("generuje schemat OpenAPI",
              code == 200 and spec["openapi"].startswith("3.")
              and len(spec["paths"]) >= len(TOOL_SPECS))

        code, out = _post(base + "/policz", {"scenariusz": {
            "tryb": "szturm", "teren_miasta": "Hills", "budowle": ["City Walls"],
            "moja_jednostka": {"jednostka": "Catapult"},
            "sily_wroga": [{"jednostka": "Warriors", "liczba": 5}]}}, token)
        check("skrót /policz ustawia i liczy jednym żądaniem",
              code == 200 and out["potrzeba_90proc"] is not None,
              f"90%={out.get('potrzeba_90proc')}")

        code, out = _post(base + "/narzedzie/ranking", {"limit": 3}, token)
        check("ranking przez API", code == 200 and len(out["pozycje"]) == 3)

        code, out = _post(base + "/narzedzie/nie_ma", {}, token)
        check("nieznane narzędzie daje 404", code == 404)

        code, out = _post(base + "/narzedzie/dane_jednostki",
                          {"jednostka": "Nieistnieje"}, token)
        check("błąd merytoryczny daje 400 z wyjaśnieniem",
              code == 400 and "blad" in out, str(code))

        code, out = _get(base + "/stan", token)
        check("stan zwraca scenariusz", code == 200 and "tryb" in out)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ------------------------------------------------- gniazdo sterujace oknem

def test_control_socket(app, win):
    print("\nGniazdo sterujące oknem:")
    from fcsiege.control import ControlClient
    sock_path = os.path.join("/tmp", f"fcsiege-test-{os.getpid()}.sock")
    os.environ["FCSIEGE_SOCKET"] = sock_path
    ok = win.start_control_server()
    check("okno otworzyło gniazdo", ok)
    if not ok:
        return

    client = ControlClient(sock_path, timeout=60)
    check("klient widzi gniazdo", client.available())

    result: dict = {}
    error: dict = {}

    def worker():
        try:
            client.call("ustaw_scenariusz", {"teren_miasta": "Mountains",
                                             "budowle": []})
            result["stan"] = client.call("pokaz_stan", {})
            result["policz"] = client.call("policz", {})
        except Exception as exc:  # noqa: BLE001
            error["exc"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    deadline = time.time() + 60
    while t.is_alive() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    t.join(timeout=5)

    check("wywołanie przez gniazdo się udało", not error, error.get("exc", ""))
    if error:
        return
    check("gniazdo przestawiło kontrolkę w oknie",
          win.cmb_def_terrain.currentData() == "Mountains",
          str(win.cmb_def_terrain.currentData()))
    check("stan z gniazda zgadza się z oknem",
          result["stan"]["teren_miasta"] == "Mountains")
    # w trybie szturmu klucz to sila_obrony, w trybie obrony sila_mojej_obrony
    liczby = result["policz"]
    check("obliczenie przez gniazdo zwraca liczby",
          (liczby.get("sila_obrony") or liczby.get("sila_mojej_obrony")) is not None,
          f"tryb={liczby.get('tryb')}")

    win._control.close()
    os.environ.pop("FCSIEGE_SOCKET", None)


def test_savegame():
    """Czytanie zapisu gry i respektowanie mgly wojny."""
    print("\nCzytanie zapisow gry:")
    from fcsiege.savegame import find_saves

    saves = find_saves()
    if not saves:
        check("znaleziono zapis gry", False, "brak zapisów w ~/.freeciv/saves")
        return
    bridge = HeadlessBridge("classic")

    mgla = dispatch(bridge, "wczytaj_zapis", {})
    check("zapis się wczytał", "blad" not in mgla and mgla.get("tura", 0) > 0,
          f"tura={mgla.get('tura')}")
    check("zestaw reguł przestawiony na ten z zapisu",
          mgla.get("zestaw_regul_ustawiony") == mgla.get("zestaw_regul"),
          f"{mgla.get('zestaw_regul_ustawiony')} / {mgla.get('zestaw_regul')}")
    check("domyślnie działa mgła wojny",
          "mgła wojny" in mgla.get("tryb_wywiadu", ""), mgla.get("tryb_wywiadu"))
    check("rozpoznano gracza ludzkiego", "ja" in mgla and mgla["ja"]["nacja"],
          str(mgla.get("ja", {}).get("nacja")))

    target = None
    for row in mgla.get("dyplomacja", []):
        if row["znane_miasta"] > 0:
            target = row["nacja"]
            break
    check("znam jakieś obce miasta", target is not None, str(target))
    if target is None:
        return

    fog = dispatch(bridge, "wywiad_o_nacji", {"nacja": target})
    check("we mgle nie ma cudzych wojsk",
          "wszystkie_wojska" not in fog and "czego_nie_wiem" in fog)
    check("we mgle są odkryte miasta", len(fog.get("znane_miasta", [])) > 0)

    cheat = dispatch(bridge, "wywiad_o_nacji", {"nacja": target, "pelny_wglad": True})
    check("pełny wgląd ujawnia wojska i garnizony",
          "wszystkie_wojska" in cheat and "garnizony" in cheat)
    check("pełny wgląd jest wyraźnie oznaczony",
          "chity" in cheat.get("tryb_wywiadu", ""), cheat.get("tryb_wywiadu"))
    check("pełny wgląd zna nie mniej miast niż mgła",
          len(cheat.get("wszystkie_miasta", [])) >= len(fog.get("znane_miasta", [])),
          f"{len(cheat.get('wszystkie_miasta', []))} vs {len(fog.get('znane_miasta', []))}")

    army = dispatch(bridge, "moje_wojska", {})
    check("rozpiska własnej armii", army.get("razem_jednostek", 0) > 0,
          f"{army.get('razem_jednostek')} jednostek")

    front = dispatch(bridge, "linia_frontu", {"nacja": target})
    check("linia frontu podaje dystanse",
          bool(front.get("fronty"))
          and "moje_najblizsze_miasta" in front["fronty"][0])

    audit = dispatch(bridge, "audyt_miast", {})
    check("audyt miast czyta próg darmowego utrzymania z reguł",
          "za darmo" in audit.get("zasada_darmowego_utrzymania", ""),
          audit.get("zasada_darmowego_utrzymania"))
    check("darmowe utrzymanie rośnie z wielkością i się nasyca",
          audit["przyklad"]["rozmiar 4"] < audit["przyklad"]["rozmiar 12"]
          and audit["przyklad"]["rozmiar 20"] == audit["przyklad"]["rozmiar 24"],
          str(audit["przyklad"]))
    check("robotnicy i karawany nie jedzą",
          "Caravan" in audit["jednostki_bez_zywnosci"]
          and "Workers" in audit["jednostki_bez_zywnosci"])
    check("jednostki bojowe jedzą",
          "Catapult" in audit["jednostki_jedzace"]
          and "Pikemen" in audit["jednostki_jedzace"])
    check("liczy limit wzrostu z budynków",
          all(c["limit_wielkosci"] for c in audit["miasta"]),
          str(audit["miasta"][0]["limit_wielkosci"]))

    reach = dispatch(bridge, "przejezdnosc", {"jednostki": ["Catapult"]})
    kat = reach.get("jednostki", {}).get("Catapult")
    if kat:
        check("rozpoznaje klasę ciężkiej jednostki", kat["klasa"] == "Big Land",
              kat["klasa"])
        check("wie, że Big Land nie wejdzie na bagna i góry bez drogi",
              {"Swamp", "Mountains", "Jungle"} <= set(kat["nie_wchodzi_bez_drogi"]),
              str(kat["nie_wchodzi_bez_drogi"]))
        check("liczy obszary przejezdne i sztuki w nich",
              bool(kat["moje_sztuki_wg_obszaru"]))
        if kat.get("polaczenia_drogowe"):
            link = kat["polaczenia_drogowe"][0]
            check("planuje połączenie drogowe dla odciętych jednostek",
                  link["kafli_do_zbudowania"] > 0
                  and link["lacznie_tur_pracy"] >= link["kafli_do_zbudowania"],
                  f"{link['kafli_do_zbudowania']} kafli / "
                  f"{link['lacznie_tur_pracy']} tur pracy")

    piech = dispatch(bridge, "przejezdnosc", {"jednostki": ["Pikemen"]})
    pk = piech.get("jednostki", {}).get("Pikemen")
    if pk:
        check("zwykła piechota wchodzi wszędzie poza Inaccessible",
              pk["nie_wchodzi_bez_drogi"] == ["Inaccessible"],
              str(pk["nie_wchodzi_bez_drogi"]))

    govs = dispatch(bridge, "porownaj_ustroje",
                    {"ustroje": ["Monarchy", "Republic", "Fundamentalism"]})
    check("porównanie ustrojów zwraca wszystkie trzy",
          set(govs.get("ustroje", {})) == {"Monarchy", "Republic", "Fundamentalism"})
    mon = govs["ustroje"]["Monarchy"]
    check("czyta wymagania technologiczne ustroju",
          mon["wymaga_technologii"] == ["Monarchy"], str(mon["wymaga_technologii"]))
    check("liczy utrzymanie wojsk z prawdziwego zapisu",
          mon.get("utrzymanie_wojsk", {}).get("koszt_na_ture", 0) > 0,
          str(mon.get("utrzymanie_wojsk")))
    check("wie, których ustrojów jeszcze nie mam",
          govs["ustroje"]["Republic"]["dostepny_teraz"] is not None)
    check("liczy kary za wielkość imperium",
          "poziomow_kary_przy_twoich_miastach" in mon.get("kara_za_wielkosc", {}))
    check("Republika ma wyższy suwak niż Monarchia",
          govs["ustroje"]["Republic"]["efekty"]["Max_Rates"]["wartosci"][0]["wartosc"]
          > mon["efekty"]["Max_Rates"]["wartosci"][0]["wartosc"])

    miss = dispatch(bridge, "wywiad_o_nacji", {"nacja": "Marsjanie"})
    check("nieznana nacja daje czytelny błąd", "blad" in miss and "dostepne" in miss)


if __name__ == "__main__":
    app, win = test_headless_matches_gui()
    test_savegame()
    test_control_socket(app, win)
    test_mcp()
    test_http()

    print("\n" + "=" * 60)
    if failures:
        print(f"NIEPOWODZENIA ({len(failures)}): " + ", ".join(failures))
        sys.exit(1)
    print("Wszystkie testy powierzchni przeszly.")
