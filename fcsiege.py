#!/usr/bin/env python3
"""Punkt wejscia FCSiege.

    python3 fcsiege.py                okno aplikacji
    python3 fcsiege.py --control      okno + gniazdo sterujace (dla MCP/API)
    python3 fcsiege.py mcp            serwer MCP po stdio
    python3 fcsiege.py api            API HTTP
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

USAGE = __doc__


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv and argv[0] == "mcp":
        from fcsiege.mcp_server import main as mcp_main
        return mcp_main(argv[1:])
    if argv and argv[0] == "api":
        from fcsiege.http_api import main as api_main
        return api_main(argv[1:])

    from fcsiege.app import main as gui_main
    if argv and argv[0] == "watch":
        from fcsiege.cli_watch import main as watch_main
        return watch_main(argv[1:])

    lang = None
    for a in argv:
        if a.startswith(("--lang=", "--jezyk=")):
            lang = a.split("=", 1)[1]
    return gui_main(control="--control" in argv, lang=lang)


if __name__ == "__main__":
    sys.exit(main())
