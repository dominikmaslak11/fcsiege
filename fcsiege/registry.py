"""Parser plikow .ruleset Freeciva (format "registry" / secfile).

Format jest wlasnym formatem Freeciva, zblizonym do INI, ale z kilkoma
dodatkami, ktorych configparser nie obsluguje:

    [nazwa_sekcji]
    klucz     = 42
    tekst     = _("Przetlumaczalny napis")
    lista     = "a", "b", "c"
    tabela    =
      { "kolumna1", "kolumna2"
        "wiersz1a", "wiersz1b"
        "wiersz2a", "wiersz2b"
      }
    dlugi     = _("Linia jedna\
     ciag dalszy")

Komentarze zaczynaja sie od ';' lub '#'. Dyrektywa *include wciaga inny plik.
"""

from __future__ import annotations

import os
import re
from typing import Any


class Table:
    """Tabela z pliku ruleset: naglowek kolumn + wiersze."""

    __slots__ = ("columns", "rows")

    def __init__(self, columns: list[str], rows: list[list[Any]]):
        self.columns = columns
        self.rows = rows

    def dicts(self) -> list[dict[str, Any]]:
        """Wiersze jako slowniki {kolumna: wartosc}."""
        out = []
        for row in self.rows:
            out.append({c: (row[i] if i < len(row) else None)
                        for i, c in enumerate(self.columns)})
        return out

    def __iter__(self):
        return iter(self.dicts())

    def __len__(self):
        return len(self.rows)

    def __repr__(self):
        return f"Table({self.columns}, {len(self.rows)} wierszy)"


_SECTION_RE = re.compile(r"^\[([^\]]+)\]")
_ENTRY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.*)$")
_INCLUDE_RE = re.compile(r'^\*?include\s+"([^"]+)"')


def _strip_comment(line: str) -> str:
    """Usuwa komentarz z konca linii, respektujac cudzyslowy."""
    out = []
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch in ";#":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _tokenize_values(text: str) -> list[Any]:
    """Rozbija 'a, "b", _("c")' na liste wartosci Pythona."""
    values: list[Any] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ", \t":
            i += 1
            continue
        # _("...") oraz podobne opakowania tlumaczen: pomijamy nawiasy
        if text.startswith("_(", i):
            i += 2
            continue
        if ch in "()":
            i += 1
            continue
        if ch == '"':
            # napis; wewnatrz moga byc sekwencje \" oraz \<newline>
            i += 1
            buf = []
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt == '"':
                        buf.append('"')
                        i += 2
                        continue
                    if nxt == "n":
                        buf.append("\n")
                        i += 2
                        continue
                    if nxt == "\\":
                        buf.append("\\")
                        i += 2
                        continue
                    i += 1
                    continue
                if text[i] == '"':
                    i += 1
                    break
                buf.append(text[i])
                i += 1
            values.append("".join(buf))
            continue
        # goly token: liczba, TRUE/FALSE, identyfikator
        j = i
        while j < n and text[j] not in ", \t":
            j += 1
        tok = text[i:j]
        i = j
        if not tok:
            continue
        values.append(_atom(tok))
    return values


def _atom(tok: str) -> Any:
    up = tok.upper()
    if up == "TRUE":
        return True
    if up == "FALSE":
        return False
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return tok


def _quotes_balanced(text: str) -> bool:
    """Czy w tekscie liczba niezescape'owanych cudzyslowow jest parzysta."""
    count = 0
    i = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            count += 1
        i += 1
    return count % 2 == 0


class Section(dict):
    """Sekcja pliku ruleset. Zachowuje sie jak slownik z pomocnikami."""

    def __init__(self, name: str):
        super().__init__()
        self.name = name

    def str(self, key: str, default: str = "") -> str:
        v = self.get(key, default)
        if isinstance(v, list):
            v = v[0] if v else default
        return str(v) if v is not None else default

    def int(self, key: str, default: int = 0) -> int:
        v = self.get(key, default)
        if isinstance(v, list):
            v = v[0] if v else default
        if isinstance(v, bool):
            return int(v)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def bool(self, key: str, default: bool = False) -> bool:
        v = self.get(key, default)
        if isinstance(v, list):
            v = v[0] if v else default
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.upper() == "TRUE"
        return bool(v)

    def list(self, key: str) -> list[Any]:
        v = self.get(key)
        if v is None:
            return []
        if isinstance(v, Table):
            return []
        if isinstance(v, list):
            return [x for x in v if x not in ("", None)]
        if v in ("", None):
            return []
        return [v]

    def table(self, key: str) -> Table | None:
        v = self.get(key)
        return v if isinstance(v, Table) else None


class Registry:
    """Zbior sekcji jednego pliku ruleset (po rozwinieciu *include)."""

    def __init__(self):
        self.sections: list[Section] = []
        self._by_name: dict[str, Section] = {}

    def add(self, section: Section) -> None:
        self.sections.append(section)
        self._by_name[section.name] = section

    def get(self, name: str) -> Section | None:
        return self._by_name.get(name)

    def prefixed(self, prefix: str) -> list[Section]:
        """Wszystkie sekcje o nazwie zaczynajacej sie od prefiksu."""
        return [s for s in self.sections if s.name.startswith(prefix)]

    def __contains__(self, name: str) -> bool:
        return name in self._by_name


def parse_file(path: str, base_dir: str | None = None,
               _seen: set[str] | None = None) -> Registry:
    """Wczytuje plik ruleset i zwraca Registry."""
    reg = Registry()
    _parse_into(reg, path, base_dir or os.path.dirname(os.path.dirname(path)),
                _seen if _seen is not None else set())
    return reg


def _parse_into(reg: Registry, path: str, base_dir: str, seen: set[str]) -> None:
    real = os.path.realpath(path)
    if real in seen or not os.path.exists(real):
        return
    seen.add(real)

    with open(real, encoding="utf-8", errors="replace") as fh:
        raw_lines = fh.read().split("\n")

    current: Section | None = None
    i = 0
    n = len(raw_lines)

    while i < n:
        raw = raw_lines[i]
        i += 1
        line = _strip_comment(raw).strip()
        if not line:
            continue

        inc = _INCLUDE_RE.match(line)
        if inc:
            target = os.path.join(base_dir, inc.group(1))
            _parse_into(reg, target, base_dir, seen)
            continue

        sec = _SECTION_RE.match(line)
        if sec:
            current = Section(sec.group(1))
            reg.add(current)
            continue

        ent = _ENTRY_RE.match(line)
        if not ent or current is None:
            continue

        key, rest = ent.group(1), ent.group(2).strip()

        # Tabela: wartosc otwiera sie '{' (moze byc w nastepnej linii).
        if rest == "" and i < n and _strip_comment(raw_lines[i]).strip().startswith("{"):
            rest = _strip_comment(raw_lines[i]).strip()
            i += 1
        if rest.startswith("{"):
            body = rest[1:]
            rows_text = []
            while True:
                stripped = body.strip()
                if stripped.endswith("}"):
                    rows_text.append(stripped[:-1])
                    break
                rows_text.append(stripped)
                if i >= n:
                    break
                body = _strip_comment(raw_lines[i])
                i += 1
            parsed_rows = [_tokenize_values(t) for t in rows_text]
            parsed_rows = [r for r in parsed_rows if r]
            if parsed_rows:
                cols = [str(c) for c in parsed_rows[0]]
                current[key] = Table(cols, parsed_rows[1:])
            else:
                current[key] = Table([], [])
            continue

        # Wartosc moze byc kontynuowana w kolejnych liniach:
        #  - napis rozbity backslashem na koncu,
        #  - lista zakonczona przecinkiem,
        #  - niedomkniety cudzyslow.
        value_text = rest
        while i < n:
            trimmed = value_text.rstrip()
            needs_more = (
                trimmed.endswith(",")
                or trimmed.endswith("\\")
                or not _quotes_balanced(value_text)
                or trimmed.count("(") > trimmed.count(")")
            )
            if not needs_more:
                break
            if trimmed.endswith("\\"):
                value_text = trimmed[:-1]
            nxt = raw_lines[i]
            if not _quotes_balanced(value_text):
                nxt_clean = nxt  # wewnatrz napisu nie tniemy komentarzy
            else:
                nxt_clean = _strip_comment(nxt)
                if not nxt_clean.strip():
                    i += 1
                    continue
            i += 1
            value_text = value_text + nxt_clean.strip() if not _quotes_balanced(value_text) \
                else value_text + " " + nxt_clean.strip()

        vals = _tokenize_values(value_text)
        if len(vals) == 1:
            current[key] = vals[0]
        else:
            current[key] = vals


def parse_ruleset_dir(dir_path: str, filenames: list[str]) -> dict[str, Registry]:
    """Wczytuje wskazane pliki .ruleset z katalogu zestawu regul."""
    out: dict[str, Registry] = {}
    base = os.path.dirname(dir_path)
    for fname in filenames:
        path = os.path.join(dir_path, fname + ".ruleset")
        if os.path.exists(path):
            out[fname] = parse_file(path, base_dir=base)
    return out
