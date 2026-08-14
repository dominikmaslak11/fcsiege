"""Serwer MCP: udostepnia kalkulator dowolnemu klientowi Claude'a.

Uruchamiany po stdio, wiec dziala z Claude Code, Claude Desktop i kazdym innym
klientem MCP. Narzedzia sa te same, co w czacie w aplikacji - definicje bierzemy
wprost z aitools.TOOL_SPECS, wiec nie ma szans, zeby sie rozjechaly.

Domyslnie liczy we wlasnym stanie (nie wymaga uruchomionego okna). Jesli jednak
okno dziala i nasluchuje na gniezdzie sterujacym, narzedzia ida do niego -
wtedy Claude przestawia kontrolki, ktore uzytkownik ma przed soba.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from .aitools import SYSTEM_PROMPT, TOOL_SPECS, dispatch
from .control import ControlClient
from .headless import HeadlessBridge

SERVER_NAME = "fcsiege"
VERSION = "1.0.0"

INSTRUCTIONS = (
    "Kalkulator walki o miasto dla gry Freeciv. Liczy wprost z plików .ruleset, "
    "więc wyniki zależą od wybranego zestawu reguł (classic, sandbox, civ2civ3…).\n\n"
    "Odpowiada na dwa pytania: ile jednostek trzeba, żeby zdobyć miasto wroga, "
    "oraz jaki minimalny garnizon utrzyma miasto własne.\n\n"
    "Wszystkie liczby bierz z narzędzia 'policz' albo 'ranking' — nigdy nie "
    "szacuj wyniku walki samodzielnie. Najpierw ustaw scenariusz narzędziem "
    "'ustaw_scenariusz', potem licz.\n\n"
    "Uwaga o mechanice: teren, z którego atakujesz, NIE zmienia siły ataku — "
    "liczy się wyłącznie kafel obrońcy."
)


class Backend:
    """Wybiera, gdzie wykonac narzedzie: w oknie aplikacji czy u siebie."""

    def __init__(self, ruleset: str, attach: str):
        self.attach = attach
        self._client = ControlClient()
        self._local: HeadlessBridge | None = None
        self._ruleset = ruleset
        if attach == "nigdy":
            self._ensure_local()

    def _ensure_local(self) -> HeadlessBridge:
        if self._local is None:
            self._local = HeadlessBridge(self._ruleset)
        return self._local

    def use_window(self) -> bool:
        if self.attach == "nigdy":
            return False
        if self.attach == "zawsze":
            return True
        return self._client.available()

    def call(self, name: str, args: dict) -> tuple[dict, str]:
        """Zwraca (wynik, zrodlo)."""
        if self.use_window():
            try:
                return self._client.call(name, args), "okno aplikacji"
            except (OSError, RuntimeError) as exc:
                if self.attach == "zawsze":
                    raise
                print(f"fcsiege-mcp: okno niedostępne ({exc}), liczę lokalnie",
                      file=sys.stderr)
        return dispatch(self._ensure_local(), name, args), "silnik lokalny"


def build_server(backend: Backend) -> Server:
    server = Server(SERVER_NAME, version=VERSION, instructions=INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name=spec["name"],
                           description=spec["description"],
                           inputSchema=spec["input_schema"])
                for spec in TOOL_SPECS]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        loop = asyncio.get_running_loop()
        # obliczenia sa synchroniczne i moga potrwac - nie blokujemy petli
        result, source = await loop.run_in_executor(
            None, backend.call, name, arguments or {})
        if isinstance(result, dict) and "zrodlo" not in result:
            result = {**result, "zrodlo": source}
        return [types.TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False, default=str))]

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [types.Resource(
            uri=types.AnyUrl("fcsiege://instrukcja"),
            name="Zasady pracy z kalkulatorem",
            description="Prompt systemowy: jak korzystać z narzędzi i o czym "
                        "pamiętać w mechanice Freeciva.",
            mimeType="text/markdown")]

    @server.read_resource()
    async def read_resource(uri: types.AnyUrl) -> str:
        return SYSTEM_PROMPT

    return server


async def _serve(backend: Backend) -> None:
    server = build_server(backend)
    async with stdio_server() as (read, write):
        await server.run(read, write, InitializationOptions(
            server_name=SERVER_NAME,
            server_version=VERSION,
            instructions=INSTRUCTIONS,
            capabilities=server.get_capabilities(
                notification_options=NotificationOptions(),
                experimental_capabilities={}),
        ))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fcsiege mcp",
        description="Serwer MCP kalkulatora FCSiege (transport stdio).")
    ap.add_argument("--ruleset", default="classic",
                    help="zestaw reguł dla trybu lokalnego (domyślnie classic)")
    ap.add_argument("--attach", choices=["auto", "zawsze", "nigdy"], default="auto",
                    help="czy sterować uruchomionym oknem aplikacji "
                         "(auto: gdy nasłuchuje)")
    args = ap.parse_args(argv)

    backend = Backend(args.ruleset, args.attach)
    try:
        asyncio.run(_serve(backend))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
