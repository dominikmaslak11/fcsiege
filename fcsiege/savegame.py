"""Czytanie zapisow gry Freeciva.

Zapis (.sav, .sav.gz, .sav.xz, .sav.bz2, .sav.zst) to ten sam format secfile,
co pliki regul - uzywamy wiec tego samego parsera.

MGLA WOJNY. Zapis zawiera stan WSZYSTKICH graczy, takze to, czego twoja
cywilizacja nie widzi. Domyslnie pokazujemy wylacznie twoja wiedze:
 * twoje miasta i jednostki,
 * cudze miasta, ktore masz odkryte (tabela "dc" - dosl. discovered cities),
 * stosunki dyplomatyczne.
Pelny wglad (cudze jednostki, niewidziane miasta) trzeba wlaczyc swiadomie
parametrem pelny_wglad=True. Kazda odpowiedz mowi, w ktorym trybie powstala.
"""

from __future__ import annotations

import glob
import collections
import os
from dataclasses import dataclass, field

from .model import clean_name
from .registry import parse_file

DEFAULT_SAVE_DIRS = [
    os.path.expanduser("~/.freeciv/saves"),
    os.path.expanduser("~/.local/share/freeciv/saves"),
]


# ------------------------------------------------------------- dekompresja

def _read_text(path: str) -> str:
    """Rozpakowuje zapis do tekstu, niezaleznie od uzytej kompresji."""
    with open(path, "rb") as fh:
        head = fh.read(6)
        fh.seek(0)
        blob = fh.read()

    if head[:4] == b"\x28\xb5\x2f\xfd":                       # zstd
        try:
            import zstandard
        except ImportError as exc:                            # pragma: no cover
            raise RuntimeError(
                "zapis jest spakowany zstd — zainstaluj moduł 'zstandard'") from exc
        blob = zstandard.ZstdDecompressor().decompress(
            blob, max_output_size=512 * 1024 * 1024)
    elif head[:2] == b"\x1f\x8b":                             # gzip
        import gzip
        blob = gzip.decompress(blob)
    elif head[:6] == b"\xfd7zXZ\x00":                         # xz
        import lzma
        blob = lzma.decompress(blob)
    elif head[:3] == b"BZh":                                  # bzip2
        import bz2
        blob = bz2.decompress(blob)
    return blob.decode("utf-8", errors="replace")


def find_saves(directories: list[str] | None = None) -> list[str]:
    """Zapisy posortowane od najnowszego."""
    out: list[str] = []
    for d in (directories or DEFAULT_SAVE_DIRS):
        if os.path.isdir(d):
            out.extend(glob.glob(os.path.join(d, "*.sav*")))
    return sorted(out, key=os.path.getmtime, reverse=True)


# ---------------------------------------------------------------- struktury

@dataclass
class Player:
    slot: int
    name: str
    nation: str
    human: bool
    alive: bool
    gold: int
    government: str
    ncities: int
    nunits: int
    diplomacy: dict[int, str] = field(default_factory=dict)


@dataclass
class City:
    name: str
    owner: int
    x: int
    y: int
    size: int
    mine: bool
    walls: bool = False
    occupied: bool | None = None
    capital: bool = False
    buildings: list[str] = field(default_factory=list)
    building_now: str | None = None
    shield_stock: int = 0


@dataclass
class Unit:
    type: str
    owner: int
    x: int
    y: int
    veteran: int
    hp: int
    homecity: int = 0
    activity: str = ""


class Save:
    """Wczytany zapis gry."""

    def __init__(self, path: str):
        self.path = path
        text = _read_text(path)
        tmp = None
        try:
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".sav", delete=False,
                                             encoding="utf-8") as fh:
                fh.write(text)
                tmp = fh.name
            self.reg = parse_file(tmp)
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

        sf = self.reg.get("savefile")
        game = self.reg.get("game")
        self.ruleset = sf.str("rulesetdir") if sf else "classic"
        self.version = sf.str("revision") if sf else ""
        self.turn = game.int("turn") if game else 0
        self.year = game.int("year") if game else 0
        self._improvements = sf.list("improvement_vector") if sf else []
        self._activities = sf.list("activities_vector") if sf else []

        self.players: dict[int, Player] = {}
        self._sections: dict[int, object] = {}
        for sec in self.reg.sections:
            if not sec.name.startswith("player") or not sec.name[6:].isdigit():
                continue
            slot = int(sec.name[6:])
            flags = sec.list("flags")
            self._sections[slot] = sec
            self.players[slot] = Player(
                slot=slot,
                name=sec.str("name"),
                nation=sec.str("nation"),
                human="ai" not in [str(f) for f in flags],
                alive=sec.bool("is_alive", True),
                gold=sec.int("gold"),
                government=sec.str("government_name"),
                ncities=sec.int("ncities"),
                nunits=sec.int("nunits"),
            )

        for slot, sec in self._sections.items():
            tbl = sec.table("diplstate")
            if tbl:
                self.players[slot].diplomacy = {
                    i: str(row.get("current")) for i, row in enumerate(tbl.dicts())}

        humans = [p for p in self.players.values() if p.human and p.alive]
        self.me: Player | None = humans[0] if humans else None

    # ------------------------------------------------------------ pomocnicze

    def _bits(self, blob) -> list[str]:
        s = str(blob or "")
        return [name for i, name in enumerate(self._improvements)
                if i < len(s) and s[i] == "1"]

    def _activity(self, code) -> str:
        try:
            return self._activities[int(code)]
        except (ValueError, TypeError, IndexError):
            return str(code)

    def nation_slot(self, nation: str) -> int | None:
        """Znajduje gracza po nazwie nacji albo przywodcy (bez wielkosci liter)."""
        needle = nation.strip().lower()
        for p in self.players.values():
            if needle in (p.nation.lower(), p.name.lower()):
                return p.slot
        for p in self.players.values():
            if needle and (needle in p.nation.lower() or needle in p.name.lower()):
                return p.slot
        return None

    # ----------------------------------------------------------------- dane

    def cities_of(self, slot: int) -> list[City]:
        """Miasta z sekcji gracza - pelne dane (dla obcych to pelny wglad)."""
        sec = self._sections.get(slot)
        tbl = sec.table("c") if sec else None
        if not tbl:
            return []
        out = []
        for row in tbl.dicts():
            out.append(City(
                name=str(row.get("name") or "?"),
                owner=slot, x=int(row.get("x") or 0), y=int(row.get("y") or 0),
                size=int(row.get("size") or 0),
                mine=(self.me is not None and slot == self.me.slot),
                buildings=self._bits(row.get("improvements")),
                building_now=str(row.get("currently_building_name") or "") or None,
                shield_stock=int(row.get("shield_stock") or 0),
            ))
        for c in out:
            c.walls = any(b.startswith("City Walls") or b == "Force Walls"
                          for b in c.buildings)
        return out

    def known_cities(self) -> list[City]:
        """Cudze miasta, ktore gracz ma odkryte - to jest wiedza z mgly wojny."""
        if self.me is None:
            return []
        sec = self._sections.get(self.me.slot)
        tbl = sec.table("dc") if sec else None
        if not tbl:
            return []
        out = []
        for row in tbl.dicts():
            out.append(City(
                name=str(row.get("name") or "?"),
                owner=int(row.get("owner") or -1),
                x=int(row.get("x") or 0), y=int(row.get("y") or 0),
                size=int(row.get("size") or 0),
                mine=False,
                walls=bool(row.get("walls")),
                occupied=bool(row.get("occupied")),
                capital=str(row.get("capital") or "Not") != "Not",
                buildings=self._bits(row.get("improvements")),
            ))
        return out

    def units_of(self, slot: int) -> list[Unit]:
        sec = self._sections.get(slot)
        tbl = sec.table("u") if sec else None
        if not tbl:
            return []
        return [Unit(
            type=str(row.get("type_by_name") or "?"),
            owner=slot, x=int(row.get("x") or 0), y=int(row.get("y") or 0),
            veteran=int(row.get("veteran") or 0), hp=int(row.get("hp") or 0),
            homecity=int(row.get("homecity") or 0),
            activity=self._activity(row.get("activity")),
        ) for row in tbl.dicts()]


# ----------------------------------------------------------- mapa i ruch

class TerrainMap:
    """Teren i ulepszenia z zapisu - potrzebne do liczenia przejezdnosci."""

    def __init__(self, save: "Save"):
        m = save.reg.get("map")
        sf = save.reg.get("savefile")
        self._m = m
        ti = sf.table("terrident") if sf else None
        self.ident = {str(r["identifier"]): str(r["name"]) for r in ti} if ti else {}
        self.extras = {n: i for i, n in enumerate(sf.list("extras_vector"))} if sf else {}
        self.rows = []
        y = 0
        while True:
            row = m.get(f"t{y:04d}") if m else None
            if row is None:
                break
            self.rows.append(str(row))
            y += 1
        self.height = len(self.rows)
        self.width = max((len(r) for r in self.rows), default=0)

    def terrain(self, x: int, y: int) -> str | None:
        if not (0 <= y < self.height) or x >= len(self.rows[y]) or x < 0:
            return None
        return self.ident.get(self.rows[y][x])

    def has_extra(self, name: str, x: int, y: int) -> bool:
        i = self.extras.get(name)
        if i is None:
            return False
        row = str(self._m.get(f"e{i // 4:02d}_{y:04d}") or "")
        if x >= len(row) or x < 0:
            return False
        try:
            return bool(int(row[x], 16) & (1 << (i % 4)))
        except ValueError:
            return False

    def owner(self, x: int, y: int) -> int | None:
        """Numer wlasciciela kafla albo None dla ziemi niczyjej."""
        cache = getattr(self, "_owner_rows", None)
        if cache is None:
            cache = self._owner_rows = {}
        if y not in cache:
            raw = str(self._m.get(f"owner{y:04d}") or "")
            cache[y] = raw.split(",") if raw else []
        row = cache[y]
        if not (0 <= x < len(row)):
            return None
        cell = row[x].strip()
        return None if cell in ("", "-") else int(cell)

    def has_road(self, x: int, y: int) -> bool:
        """Droga, kolej, maglev albo rzeka - wszystkie sa 'NativeTile'."""
        return any(self.has_extra(n, x, y)
                   for n in ("Road", "Railroad", "Maglev", "River"))


def passability(rs, tmap: TerrainMap, uclass: str):
    """Zwraca funkcje (x,y)->bool: czy jednostka tej klasy moze tam stanac.

    Kafel jest przejezdny, gdy teren jest natywny dla klasy albo lezy na nim
    ulepszenie z flaga NativeTile (droga, kolej, rzeka).
    """
    native = {t.name for t in rs.terrains.values() if uclass in t.native_to}

    def ok(x: int, y: int) -> bool:
        t = tmap.terrain(x, y)
        if t is None:
            return False
        terr = rs.terrains.get(t)
        if terr is None or not terr.is_land:
            return False
        return t in native or tmap.has_road(x, y)

    return ok


def road_link(rs, tmap: TerrainMap, passable, start: tuple[int, int],
              target_region: int, reg: dict, max_nodes: int = 200000,
              geom: "MapGeometry | None" = None) -> dict | None:
    """Najtansze polaczenie drogowe z kieszeni do wskazanego obszaru.

    Koszt kafla to liczba tur pracy potrzebnych, zeby zbudowac tam droge
    (road_time z regul); kafle juz przejezdne kosztuja zero.
    """
    import heapq
    road_time = {t.name: getattr(t, "road_time", 0) for t in rs.terrains.values()}
    W, H = tmap.width, tmap.height

    def cost(x: int, y: int):
        t = tmap.terrain(x, y)
        if t is None:
            return None
        terr = rs.terrains.get(t)
        if terr is None or not terr.is_land:
            return None
        if passable(x, y):
            return 0
        return road_time.get(t) or 4

    dist = {start: 0}
    prev: dict = {}
    pq = [(0, start)]
    seen = 0
    goal = None
    while pq and seen < max_nodes:
        d, (x, y) = heapq.heappop(pq)
        seen += 1
        if d > dist.get((x, y), 1 << 30):
            continue
        if reg.get((x, y)) == target_region:
            goal = (x, y)
            break
        for nx, ny in (geom.neighbours(x, y) if geom is not None else
                       [((x + dx) % W, y + dy)
                        for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                        if (dx or dy) and 0 <= y + dy < H]):
            c = cost(nx, ny)
            if c is None:
                continue
            nd = d + c
            if nd < dist.get((nx, ny), 1 << 30):
                dist[(nx, ny)] = nd
                prev[(nx, ny)] = (x, y)
                heapq.heappush(pq, (nd, (nx, ny)))
    if goal is None:
        return None
    path = [goal]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    path.reverse()
    todo = [{"x": x, "y": y, "teren": tmap.terrain(x, y),
             "tur_pracy": road_time.get(tmap.terrain(x, y)) or 4}
            for x, y in path if not passable(x, y)]
    total = sum(t["tur_pracy"] for t in todo)
    return {"kafli_do_zbudowania": len(todo), "lacznie_tur_pracy": total,
            "kafle": todo,
            "robotnikow_na_jedna_ture": total,
            "przy_8_robotnikach_tur": max(1, -(-total // 8)) if total else 0}



def _enter_cost_fn(rs, tmap: TerrainMap, ut, full: int, blocked=None,
                   goal=None, geom=None, cities=None):
    """Zwraca funkcje (skad, dokad) -> koszt w ulamkach ruchu albo None.

    Wejscie kosztuje `movement_cost` pelnych ruchow, chyba ze oba kafle laczy
    ulepszenie liniowe - wtedy placi sie jego `move_cost` w ulamkach. Koszt jest
    ograniczony do pelnego zapasu jednostki.

    Miasto jest "bezpieczna przystania": kafel z miastem jest dostepny dla
    kazdej klasy, ktora ma obok siebie kafel natywny (movement.c,
    can_exist_at_tile). Dzieki temu statek wplywa do nadmorskiego portu, choc
    samo miasto stoi na ladzie.
    """
    single = max(1, rs.move_fragments)
    uclass = rs.uclass_of(ut)
    road_cost = _road_move_costs(rs)
    nat_extras = _native_extras(rs)
    cities = cities or set()

    def natywny_kafel(tile: tuple[int, int]) -> bool:
        """Jak is_native_tile_to_class(): teren ALBO ulepszenie z NativeTile."""
        name = tmap.terrain(*tile)
        terr = rs.terrains.get(name) if name else None
        if terr is not None and uclass.name in terr.native_to:
            return True
        for extra, opis in nat_extras.items():
            if uclass.name in opis["native_to"] and tmap.has_extra(extra, *tile):
                return True
        return False

    def native_near(tile: tuple[int, int]) -> bool:
        if geom is None:
            return False
        for nb in geom.neighbours(*tile):
            name = tmap.terrain(*nb)
            terr = rs.terrains.get(name) if name else None
            if terr is not None and uclass.name in terr.native_to:
                return True
        return False

    def cost(frm: tuple[int, int], to: tuple[int, int]) -> int | None:
        name = tmap.terrain(*to)
        terr = rs.terrains.get(name) if name else None
        if terr is None:
            return None
        if blocked is not None and to != goal and blocked(*to):
            return None                    # np. cudze terytorium przy pokoju
        best = None
        for extra, c in road_cost.items():
            if tmap.has_extra(extra, *frm) and tmap.has_extra(extra, *to):
                best = c if best is None else min(best, c)

        if best is None and uclass.name not in terr.native_to:
            # teren nie jest natywny - ale kafel moze byc natywny przez
            # ulepszenie (rzeka, droga), a przejscie miedzy dwoma roznymi
            # typami drog dopuszczaja flagi JumpFrom/JumpTo (is_native_move)
            wolno = False
            if natywny_kafel(to):
                if not natywny_kafel(frm):
                    wolno = True           # zejscie z transportu / z miasta
                else:
                    for extra, opis in nat_extras.items():
                        if not (uclass.name in opis["native_to"]
                                and tmap.has_extra(extra, *to)):
                            continue
                        if not opis["droga"]:
                            wolno = True   # ulepszenie niedrogowe wystarcza
                            break
                        if not opis["jump_to"]:
                            continue
                        for inne, o2 in nat_extras.items():
                            if (inne != extra and o2["droga"]
                                    and o2["jump_from"]
                                    and uclass.name in o2["native_to"]
                                    and tmap.has_extra(inne, *frm)):
                                wolno = True
                                break
                        if wolno:
                            break
            if not wolno and to in cities and native_near(to):
                wolno = True               # miasto jako bezpieczna przystan
            if not wolno:
                return None                # klasa tu nie wejdzie
            best = max(1, terr.movement_cost) * single
        if best is None:
            best = max(1, terr.movement_cost) * single
        return min(best, full)

    return cost


def city_tiles(save: "Save") -> set[tuple[int, int]]:
    """Kafle wszystkich miast na mapie - dzialaja jak przystanie."""
    out = set()
    for sec in save._sections.values():
        tbl = sec.table("c") if sec else None
        for r in (tbl.dicts() if tbl else []):
            out.add((int(r.get("x") or 0), int(r.get("y") or 0)))
    return out


def _step(turns: int, used: int, full: int, cost: int) -> tuple[int, int]:
    """Nowy stan (tury, zuzyte) po wejsciu na kafel o danym koszcie.

    Jednostka z choc jednym ulamkiem zawsze wykona jeszcze jeden ruch i konczy
    ture z zerem - stad srodkowa galaz.
    """
    left = full - used
    if left >= cost:
        return turns, used + cost
    if left > 0:
        return turns, full
    return turns + 1, min(cost, full)


def march_turns(rs, tmap: TerrainMap, geom: "MapGeometry", ut,
                start: tuple[int, int], goal: tuple[int, int],
                max_nodes: int = 60000, blocked=None, cities=None) -> int | None:
    """Ile tur marszu z A do B - po koszcie ruchu, nie po odleglosci.

    Zwraca liczbe pelnych tur do przebycia (0 = dojdzie jeszcze w tej turze)
    albo None, gdy cel jest nieosiagalny dla tej klasy.
    """
    import heapq

    full = max(1, ut.move_rate) * max(1, rs.move_fragments)
    cost_of = _enter_cost_fn(rs, tmap, ut, full, blocked, goal, geom, cities)

    best: dict[tuple[int, int], tuple[int, int]] = {start: (0, 0)}
    pq = [(0, 0, start)]
    seen = 0
    while pq and seen < max_nodes:
        turns, used, node = heapq.heappop(pq)
        seen += 1
        if node == goal:
            return turns
        if (turns, used) > best.get(node, (1 << 30, 0)):
            continue
        for nb in geom.neighbours(*node):
            c = cost_of(node, nb)
            if c is None:
                continue
            nxt = _step(turns, used, full, c)
            if nxt < best.get(nb, (1 << 30, 0)):
                best[nb] = nxt
                heapq.heappush(pq, (nxt[0], nxt[1], nb))
    return None


def reach_within(rs, tmap: TerrainMap, geom: "MapGeometry", ut,
                 start: tuple[int, int], max_turns: int = 2,
                 blocked=None, cities=None) -> dict[tuple[int, int], int]:
    """Wszystkie kafle osiagalne w zadanej liczbie tur, z liczba tur.

    Odwrotna perspektywa do `march_turns`: nie "ile tur do celu", tylko
    "dokad ta jednostka zdazy". Przy malym `max_turns` czolo przeszukiwania
    jest niewielkie, wiec liczy sie to szybko nawet dla wielu jednostek.
    """
    import heapq

    full = max(1, ut.move_rate) * max(1, rs.move_fragments)
    cost_of = _enter_cost_fn(rs, tmap, ut, full, blocked, None, geom, cities)

    best: dict[tuple[int, int], tuple[int, int]] = {start: (0, 0)}
    pq = [(0, 0, start)]
    while pq:
        turns, used, node = heapq.heappop(pq)
        if (turns, used) > best.get(node, (1 << 30, 0)):
            continue
        if turns >= max_turns:
            continue
        for nb in geom.neighbours(*node):
            c = cost_of(node, nb)
            if c is None:
                continue
            nxt = _step(turns, used, full, c)
            if nxt[0] > max_turns:
                continue
            if nxt < best.get(nb, (1 << 30, 0)):
                best[nb] = nxt
                heapq.heappush(pq, (nxt[0], nxt[1], nb))
    return {tile: t for tile, (t, _u) in best.items()}


def _resource_yields(rs) -> dict[str, tuple[int, int, int]]:
    """Ile daje zasob specjalny na kaflu: (jedzenie, tarcze, handel).

    Zasoby siedza w sekcjach `[resource_*]`, ktore wskazuja swoje ulepszenie
    polem `extra` - i to pod nazwa tego ulepszenia zapis trzyma je na mapie.
    Bez tego Zloto na gorach wygladalo na 0/3/0 zamiast 0/3/6, a wiec kazde
    miasto z zasobem mialo zanizony handel albo tarcze.
    """
    cached = getattr(rs, "_resource_cache", None)
    if cached is not None:
        return cached
    import os

    from .registry import parse_file
    out: dict[str, tuple[int, int, int]] = {}
    path = os.path.join(rs.path, "terrain.ruleset")
    if os.path.exists(path):
        reg = parse_file(path, base_dir=os.path.dirname(rs.path))
        for sec in reg.prefixed("resource_"):
            extra = clean_name(sec.str("extra"))
            if extra:
                out[extra] = (sec.int("food"), sec.int("shield"),
                              sec.int("trade"))
    try:
        rs._resource_cache = out
    except Exception:  # noqa: BLE001
        pass
    return out


def _tile_bonuses(rs) -> tuple[dict, dict]:
    """Ile daje irygacja i kopalnia na danym terenie oraz kopalnia w tarczach."""
    cached = getattr(rs, "_tile_bonus_cache", None)
    if cached is not None:
        return cached
    import os

    from .registry import parse_file
    mine: dict[str, int] = {}
    road_pct: dict[str, int] = {}
    path = os.path.join(rs.path, "terrain.ruleset")
    if os.path.exists(path):
        reg = parse_file(path, base_dir=os.path.dirname(rs.path))
        for sec in reg.prefixed("terrain_"):
            nm = clean_name(sec.str("rule_name") or sec.str("name"))
            if not nm:
                continue
            mine[nm] = sec.int("mining_shield_incr")
            road_pct[nm] = sec.int("road_trade_incr_pct")
    out = (mine, road_pct)
    try:
        rs._tile_bonus_cache = out
    except Exception:  # noqa: BLE001 - reguly moga byc zamrozone
        pass
    return out


def _road_build_times(rs) -> dict[str, int]:
    """Ile tur pracy zajmuje zbudowanie drogi na danym terenie."""
    cached = getattr(rs, "_road_time_cache", None)
    if cached is not None:
        return cached
    import os

    from .registry import parse_file
    out: dict[str, int] = {}
    path = os.path.join(rs.path, "terrain.ruleset")
    if os.path.exists(path):
        reg = parse_file(path, base_dir=os.path.dirname(rs.path))
        for sec in reg.prefixed("terrain_"):
            nm = clean_name(sec.str("rule_name") or sec.str("name"))
            if nm:
                out[nm] = sec.int("road_time")
    try:
        rs._road_time_cache = out
    except Exception:  # noqa: BLE001
        pass
    return out


def _city_trade(rs, tmap: "TerrainMap", geom: "MapGeometry",
                x: int, y: int, size: int, blds: set[str]) -> int:
    """Ile handlu daje miasto z obrabianych kafli.

    Zapis nie trzyma wyliczonych plonow miasta, wiec odtwarzamy je z mapy:
    bierzemy obszar o promieniu 2, obsadzamy tyle kafli, ilu miasto ma
    obywateli, i sumujemy handel z terenu, rzeki i drogi. Obywatele ida
    najpierw za jedzeniem - to przyblizenie, ale bez niego wychodzi sufit
    handlu, a nie handel faktyczny.
    """
    mine, road_pct = _tile_bonuses(rs)
    zasoby = _resource_yields(rs)

    def yields(a: int, b: int) -> tuple[int, int, int] | None:
        name = tmap.terrain(a, b)
        terr = rs.terrains.get(name) if name else None
        if terr is None:
            return None
        f, sh, tr = terr.food, terr.shield, terr.trade
        for zn, (zf, zs, zt) in zasoby.items():
            if (zf or zs or zt) and tmap.has_extra(zn, a, b):
                f += zf; sh += zs; tr += zt
                break
        if tmap.has_extra("River", a, b):
            tr += 1
        if tmap.has_extra("Road", a, b):
            tr += 1 * road_pct.get(name, 0) // 100
        if tmap.has_extra("Irrigation", a, b):
            f += terr.irrigation_food_incr or 0
        if tmap.has_extra("Mine", a, b):
            sh += mine.get(name, 0)
        if terr.flags and "Sea" in terr.flags and (
                "Harbour" in blds or "Harbor" in blds):
            f += 1
        return f, sh, tr

    area = []
    for dx in range(-3, 4):
        for dy in range(-4, 5):
            if (dx, dy) == (0, 0):
                continue
            nb = ((x + dx) % geom.xsize, (y + dy) % geom.ysize)
            if geom.real_distance((x, y), nb) <= 2:
                area.append(nb)
    vals = [v for v in (yields(a, b) for a, b in area) if v]
    vals.sort(key=lambda v: (-v[0], -v[1], -v[2]))
    centre = yields(x, y) or (0, 0, 0)
    return sum(v[2] for v in vals[:max(0, size)]) + centre[2]


def _native_extras(rs) -> dict[str, dict]:
    """Ulepszenia, ktore SAME czynia kafel natywnym dla klasy jednostki.

    W silniku (`is_native_tile_to_class`) kafel jest natywny, gdy natywny
    jest jego TEREN albo gdy lezy na nim ulepszenie z flaga `NativeTile`,
    natywne dla tej klasy. Klasa Merchant nie jest natywna dla zadnego
    terenu - jest natywna wylacznie dla Drogi, Kolei, Maglevu i RZEKI,
    i to wlasnie one pozwalaja karawanie w ogole gdziekolwiek stanac.

    Zbieramy tez flagi `JumpFrom`/`JumpTo` typow drog, bo to one decyduja,
    czy jednostka moze przejsc Z drogi NA rzeke i odwrotnie
    (`is_native_move`). Bez tego karawana wyglada na uwieziona po jednej
    stronie kazdej rzeki.
    """
    cached = getattr(rs, "_native_extra_cache", None)
    if cached is not None:
        return cached
    import os

    from .registry import parse_file
    out: dict[str, dict] = {}
    path = os.path.join(rs.path, "terrain.ruleset")
    if os.path.exists(path):
        reg = parse_file(path, base_dir=os.path.dirname(rs.path))
        for sec in reg.prefixed("extra_"):
            nm = clean_name(sec.str("rule_name") or sec.str("name"))
            flags = {str(f) for f in sec.list("flags")}
            if not nm or "NativeTile" not in flags:
                continue
            out[nm] = {
                "native_to": {clean_name(str(c)) for c in sec.list("native_to")},
                "droga": "Road" in {str(c) for c in sec.list("causes")},
                "jump_from": False, "jump_to": False,
            }
        # flagi skoku siedza przy TYPIE drogi, nie przy ulepszeniu
        for sec in reg.prefixed("road_"):
            extra = clean_name(sec.str("extra")) or clean_name(sec.str("name"))
            if extra in out:
                flags = {str(f) for f in sec.list("flags")}
                out[extra]["jump_from"] = "JumpFrom" in flags
                out[extra]["jump_to"] = "JumpTo" in flags
    try:
        rs._native_extra_cache = out
    except Exception:  # noqa: BLE001
        pass
    return out


def _road_move_costs(rs) -> dict[str, int]:
    """Koszt ruchu po ulepszeniach liniowych, w ulamkach - wprost z regul."""
    cached = getattr(rs, "_road_move_costs", None)
    if cached is not None:
        return cached
    import os

    from .registry import parse_file
    out: dict[str, int] = {}
    path = os.path.join(rs.path, "terrain.ruleset")
    if os.path.exists(path):
        reg = parse_file(path, base_dir=os.path.dirname(rs.path))
        for sec in reg.prefixed("road_"):
            extra = clean_name(sec.str("extra")) or clean_name(sec.str("name"))
            if extra:
                out[extra] = max(0, sec.int("move_cost"))
    rs._road_move_costs = out
    return out


def regions(tmap: TerrainMap, passable,
            geom: "MapGeometry | None" = None) -> dict[tuple[int, int], int]:
    """Spojne obszary przejezdne, wg prawdziwego sasiedztwa mapy.

    Bez geometrii z zapisu przyjmujemy siatke kwadratowa (8 kierunkow); to
    zawyza spojnosc na mapach heksowych, dlatego wolamy z geometria.
    """
    seen: dict[tuple[int, int], int] = {}
    cid = 0
    W, H = tmap.width, tmap.height

    def around(cx, cy):
        if geom is not None:
            yield from geom.neighbours(cx, cy)
            return
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    ny = cy + dy
                    if 0 <= ny < H:
                        yield (cx + dx) % W, ny

    for y in range(H):
        for x in range(len(tmap.rows[y])):
            if (x, y) in seen or not passable(x, y):
                continue
            cid += 1
            stack = [(x, y)]
            seen[(x, y)] = cid
            while stack:
                cx, cy = stack.pop()
                for nb in around(cx, cy):
                    if nb not in seen and passable(*nb):
                        seen[nb] = cid
                        stack.append(nb)
    return seen


def _upkeep_profile(rs, gov: str) -> tuple[str, int, int]:
    """(typ kosztu, darmowe na miasto, mnoznik) dla danego ustroju."""
    all_govs = set(rs.governments)
    out_type, free, factor = "Gold", 0, 0
    for eff in rs.effects_by_type.get("Unit_Upkeep_Free_Per_City", []):
        govs = [r.name for r in eff.reqs if r.type == "Gov" and r.present]
        types = [r.name for r in eff.reqs if r.type == "OutputType"]
        if gov in govs and types:
            out_type, free = types[0], eff.value
    for eff in rs.effects_by_type.get("Upkeep_Factor", []):
        pos = [r.name for r in eff.reqs if r.type == "Gov" and r.present]
        neg = [r.name for r in eff.reqs if r.type == "Gov" and not r.present]
        types = [r.name for r in eff.reqs if r.type == "OutputType"]
        if types and types[0] != out_type:
            continue
        if pos and gov not in pos:
            continue
        if neg and gov in neg:
            continue
        if not pos and not neg:
            continue
        if neg and not pos and not (all_govs - set(neg)):
            continue
        factor += eff.value
    return out_type, free, max(1, factor)


def _known_techs(save: "Save") -> set[str]:
    research = save.reg.get("research")
    sf = save.reg.get("savefile")
    if not research or not sf:
        return set()
    tbl = research.table("r")
    vector = sf.list("technology_vector")
    if not tbl or not len(tbl):
        return set()
    done = str(list(tbl.dicts())[0].get("done") or "")
    return {vector[i] for i, ch in enumerate(done) if ch == "1" and i < len(vector)}


# ------------------------------------------------------------------ wywiad

class MapGeometry:
    """Geometria mapy z zapisu - potrzebna do liczenia realnych odleglosci.

    Zapis trzyma wspolrzedne NATYWNE (nat_x, nat_y), a Freeciv liczy odleglosc
    we wspolrzednych MAPOWYCH, po zawinieciu wektora w natywnych. Na mapie
    iso-hex nie da sie isc po przekatnej NE/SW, wiec odleglosc nie jest zwyklym
    maksimum. Odwzorowanie jeden do jednego z common/map.c (3.2).
    """

    def __init__(self, xsize: int = 0, ysize: int = 0, topology: str = "",
                 wrap: str = ""):
        self.xsize = xsize or 1
        self.ysize = ysize or 1
        t = (topology or "").upper()
        w = (wrap or "").upper()
        self.iso = "ISO" in t
        self.hex = "HEX" in t
        self.wrapx = "WRAPX" in w
        self.wrapy = "WRAPY" in w

    @classmethod
    def from_save(cls, save: "Save") -> "MapGeometry":
        vals: dict[str, str] = {}
        st = save.reg.get("settings")
        tbl = st.table("set") if st else None
        for row in (tbl.dicts() if tbl else []):
            vals[str(row.get("name"))] = str(row.get("value"))
        return cls(int(vals.get("xsize") or 0), int(vals.get("ysize") or 0),
                   vals.get("topology", ""), vals.get("wrap", ""))

    def to_map(self, nx: int, ny: int) -> tuple[int, int]:
        """NATIVE_TO_MAP_POS z map.h."""
        if not self.iso:
            return nx, ny
        mx = (ny + (ny & 1)) // 2 + nx
        return mx, ny - mx + self.xsize

    def real_distance(self, a: tuple[int, int], b: tuple[int, int]) -> int:
        """Odleglosc jak real_map_distance(); argumenty w natywnych."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        if self.wrapx:
            dx = (dx + self.xsize // 2) % self.xsize - self.xsize // 2
        if self.wrapy:
            dy = (dy + self.ysize // 2) % self.ysize - self.ysize // 2
        x0, y0 = self.to_map(a[0], a[1])
        x1, y1 = self.to_map(a[0] + dx, a[1] + dy)
        return self._vector_distance(x1 - x0, y1 - y0)

    def neighbours(self, x: int, y: int):
        """Kafle faktycznie sasiadujace - na heksie jest ich 6, nie 8."""
        for dx, dy in self._offsets(y):
            nx, ny = x + dx, y + dy
            if self.wrapx:
                nx %= self.xsize
            elif not 0 <= nx < self.xsize:
                continue
            if self.wrapy:
                ny %= self.ysize
            elif not 0 <= ny < self.ysize:
                continue
            yield nx, ny

    def _offsets(self, y: int) -> list[tuple[int, int]]:
        """Przesuniecia do sasiadow; na mapach iso zaleza od parzystosci y."""
        key = (y % 2) if self.iso else 0
        cache = getattr(self, "_off_cache", None)
        if cache is None:
            cache = self._off_cache = {}
        if key not in cache:
            # baza z dala od krawedzi, o tej samej parzystosci co y
            bx = self.xsize // 2
            by = 2 * (self.ysize // 4) + key
            cache[key] = [(dx, dy)
                          for dx in range(-2, 3) for dy in range(-2, 3)
                          if (dx, dy) != (0, 0)
                          and self.real_distance((bx, by),
                                                 (bx + dx, by + dy)) == 1]
        return cache[key]

    def _vector_distance(self, dx: int, dy: int) -> int:
        adx, ady = abs(dx), abs(dy)
        if not self.hex:
            return max(adx, ady)
        if self.iso:
            if (dx < 0 and dy > 0) or (dx > 0 and dy < 0):
                return adx + ady          # iso-hex: brak ruchu po NE i SW
        elif (dx > 0 and dy > 0) or (dx < 0 and dy < 0):
            return adx + ady              # hex: brak ruchu po SE i NW
        return max(adx, ady)


def _distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Zapasowa odleglosc, gdy nie znamy geometrii (mapa kwadratowa)."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class Intel:
    """Odpowiada na pytania o partie, respektujac (albo nie) mgle wojny."""

    def __init__(self, save: Save):
        self.save = save
        self.geom = MapGeometry.from_save(save)

    # --------------------------------------------------------------- ogolne

    def summary(self, full: bool) -> dict:
        s = self.save
        me = s.me
        out = {
            "plik": os.path.basename(s.path),
            "tura": s.turn, "rok": s.year,
            "zestaw_regul": s.ruleset,
            "wersja_gry": s.version,
            "tryb_wywiadu": "pełny wgląd (świadome chity)" if full
            else "tylko moja wiedza (mgła wojny)",
        }
        if me is None:
            out["blad"] = "nie znalazłem gracza ludzkiego w tym zapisie"
            return out

        my_cities = s.cities_of(me.slot)
        my_units = s.units_of(me.slot)
        out["ja"] = {
            "przywodca": me.name, "nacja": me.nation,
            "ustroj": me.government, "zloto": me.gold,
            "miast": len(my_cities), "jednostek": len(my_units),
        }
        out["dyplomacja"] = [
            {"nacja": p.nation, "przywodca": p.name, "stan": me.diplomacy.get(p.slot, "?"),
             "zywy": p.alive,
             "znane_miasta": sum(1 for c in s.known_cities() if c.owner == p.slot)}
            for p in sorted(s.players.values(), key=lambda x: x.slot)
            if p.slot != me.slot and p.alive
        ]
        if full:
            for row in out["dyplomacja"]:
                slot = s.nation_slot(row["nacja"])
                if slot is not None:
                    row["miast_naprawde"] = s.players[slot].ncities
                    row["jednostek_naprawde"] = s.players[slot].nunits
                    row["zloto"] = s.players[slot].gold
        return out

    # ----------------------------------------------------------------- armia

    def my_army(self) -> dict:
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        units = s.units_of(s.me.slot)
        by_type: dict[str, dict] = {}
        for u in units:
            e = by_type.setdefault(u.type, {"jednostka": u.type, "sztuk": 0,
                                            "stopnie": {}, "ranne": 0})
            e["sztuk"] += 1
            e["stopnie"][u.veteran] = e["stopnie"].get(u.veteran, 0) + 1
            if u.hp < 10:
                e["ranne"] += 1
        cities = s.cities_of(s.me.slot)
        building: dict[str, int] = {}
        for c in cities:
            if c.building_now:
                building[c.building_now] = building.get(c.building_now, 0) + 1
        return {
            "razem_jednostek": len(units),
            "wg_typu": sorted(by_type.values(), key=lambda e: -e["sztuk"]),
            "miast": len(cities),
            "co_buduja_miasta": sorted(
                ({"co": k, "w_ilu_miastach": v} for k, v in building.items()),
                key=lambda e: -e["w_ilu_miastach"]),
        }

    # ------------------------------------------------------------ przeciwnik

    def nation(self, name: str, full: bool) -> dict:
        s = self.save
        slot = s.nation_slot(name)
        if slot is None:
            return {"blad": f"nie ma nacji {name} w tym zapisie",
                    "dostepne": sorted(p.nation for p in s.players.values() if p.alive)}
        p = s.players[slot]
        me = s.me
        known = [c for c in s.known_cities() if c.owner == slot]
        out = {
            "nacja": p.nation, "przywodca": p.name, "zywy": p.alive,
            "stan_dyplomatyczny": me.diplomacy.get(slot, "?") if me else "?",
            "tryb_wywiadu": "pełny wgląd (świadome chity)" if full
            else "tylko moja wiedza (mgła wojny)",
            "znane_miasta": [{
                "nazwa": c.name, "x": c.x, "y": c.y, "rozmiar": c.size,
                "mury": c.walls, "obsadzone": c.occupied, "stolica": c.capital,
                "budowle": c.buildings,
            } for c in sorted(known, key=lambda c: -c.size)],
        }
        if not full:
            out["czego_nie_wiem"] = (
                "liczebność i rozmieszczenie ich wojsk, miasta jeszcze nieodkryte, "
                "ich złoto i technologie")
            return out

        out["zloto"] = p.gold
        out["ustroj"] = p.government
        cities = s.cities_of(slot)
        units = s.units_of(slot)
        out["wszystkie_miasta"] = [{
            "nazwa": c.name, "x": c.x, "y": c.y, "rozmiar": c.size,
            "mury": c.walls, "buduje": c.building_now, "budowle": c.buildings,
        } for c in sorted(cities, key=lambda c: -c.size)]
        by_type: dict[str, int] = {}
        for u in units:
            by_type[u.type] = by_type.get(u.type, 0) + 1
        out["wszystkie_wojska"] = sorted(
            ({"jednostka": k, "sztuk": v} for k, v in by_type.items()),
            key=lambda e: -e["sztuk"])
        # co stoi w ktorym miescie
        garrisons: dict[str, list[str]] = {}
        for c in cities:
            here = [u for u in units if (u.x, u.y) == (c.x, c.y)]
            if here:
                garrisons[c.name] = sorted(f"{u.type} (wet {u.veteran}, {u.hp} hp)"
                                           for u in here)
        out["garnizony"] = garrisons
        return out








    # ------------------------------------------------------- potencjal wzrostu

    def growth_potential(self, rs, miasto: str = "", limit: int = 10) -> dict:
        """Dlaczego miasto nie rosnie i ile pracy kosztuje to naprawic.

        Trzy rozne przyczyny wygladaja tak samo z zewnatrz: limit wielkosci,
        deficyt utrzymania na zywnosci albo po prostu jalowa ziemia. Rozdzielamy
        je, a przy ziemi liczymy, co dalby sie z nia zrobic: irygacja podnosi
        zywnosc od reki, przemiana terenu bardziej, ale kosztuje wielokrotnie
        wiecej tur pracy. Wszystkie liczby z terrain.ruleset.
        """
        import os

        from .registry import parse_file

        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        tmap = TerrainMap(s)
        sec = s._sections[s.me.slot]
        rows = list(sec.table("c").dicts()) if sec.table("c") else []
        if not rows:
            return {"blad": "brak miast"}

        # plony, surowce i przemiany wprost z regul
        plony: dict[str, dict] = {}
        surowce: dict[str, int] = {}
        path = os.path.join(rs.path, "terrain.ruleset")
        if os.path.exists(path):
            reg = parse_file(path, base_dir=os.path.dirname(rs.path))
            for t in reg.prefixed("resource_"):
                nm = clean_name(t.str("rule_name") or t.str("name"))
                if nm:
                    surowce[nm] = t.int("food")
            for t in reg.prefixed("terrain_"):
                nm = clean_name(t.str("rule_name") or t.str("name"))
                plony[nm] = {
                    "zywnosc": t.int("food"),
                    "tarcze": t.int("shield"),
                    "handel": t.int("trade"),
                    "irygacja_daje": t.int("irrigation_food_incr"),
                    "irygacja_tur": t.int("irrigation_time"),
                    # uprawa i zalesienie sa dostepne dla zwyklych robotnikow,
                    # przemiana terenu wymaga jednostki z flaga Transform
                    "uprawa_w": clean_name(t.str("cultivate_result")),
                    "uprawa_tur": t.int("cultivate_time"),
                    "przemiana_w": clean_name(t.str("transform_result")),
                    "przemiana_tur": t.int("transform_time"),
                }

        techs = _known_techs(s) - {"A_NONE"}
        gov = s.me.government or ""
        mine_blds: set[str] = set()
        for r in rows:
            mine_blds |= set(s._bits(r.get("improvements")))

        # Kto moze wykonac dana prace. Prace polowe (irygacja, uprawa) robi
        # kazda jednostka z flaga Settlers; PRZEMIANE terenu tylko jednostka
        # z flaga Transform - w sandboksie sa to wylacznie Engineers. Bez tego
        # rozroznienia narzedzie doradzaloby prace, ktorych nie ma czym zrobic.
        def kto_umie(flaga: str) -> dict:
            umieja = [u for u in rs.units.values() if flaga in u.flags]
            mam = [u.name for u in umieja
                   if all(t in techs for t in u.req_techs())]
            posiadane = {u.type for u in s.units_of(s.me.slot)}
            return {
                "jednostki": sorted(u.name for u in umieja),
                "moge_budowac": sorted(mam),
                "mam_w_grze": sorted(set(mam) & posiadane),
                "dostepne": bool(set(mam) & posiadane) or bool(mam),
                "brakuje_technologii": sorted(
                    {t for u in umieja for t in u.req_techs() if t not in techs}),
            }

        prace = {"polowe": kto_umie("Settlers"), "przemiana": kto_umie("Transform")}

        base, steps = 0, []
        for eff in rs.effects_by_type.get("Unit_Upkeep_Free_Per_City", []):
            if [x.name for x in eff.reqs if x.type == "OutputType"] != ["Food"]:
                continue
            if [x for x in eff.reqs if x.type == "Gov"]:
                continue
            sizes = [int(x.name) for x in eff.reqs
                     if x.type == "MinSize" and str(x.name).isdigit()]
            if sizes:
                steps.extend([sizes[0]] * eff.value)
            else:
                base += eff.value
        steps.sort()

        jedzacy = collections.Counter()
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if ut and getattr(ut, "uk_food", 0) > 0:
                jedzacy[u.homecity] += 1

        wybrane = [r for r in rows
                   if not miasto or str(r.get("name", "")).lower() == miasto.lower()]
        if miasto and not wybrane:
            return {"blad": f"nie mam miasta {miasto}",
                    "dostepne": sorted(str(r.get("name")) for r in rows)}

        out = []
        for r in wybrane:
            x, y = int(r["x"]), int(r["y"])
            size = int(r.get("size") or 0)
            blds = set(s._bits(r.get("improvements")))
            cap, unlim = 0, False
            for eff in rs.effects_by_type.get("Size_Adj", []):
                need = [q for q in eff.reqs if q.type == "Building"]
                if all((q.name in blds) == q.present for q in need):
                    cap += eff.value
            for eff in rs.effects_by_type.get("Size_Unlimit", []):
                need = [q for q in eff.reqs if q.type == "Building"]
                if need and all((q.name in blds) == q.present for q in need):
                    unlim = True
            wolne = base + sum(1 for m in steps if size >= m)
            deficyt = max(0, jedzacy.get(int(r.get("id") or 0), 0) - wolne)

            # Ktory wariant akweduktu miasto w ogole moze postawic. Tanie
            # warianty maja wymog Extra/Terrain o zasiegu "Adjacent", a na
            # mapie hex sasiadow jest SZESC, nie osiem - liczenie kwadratem
            # 3x3 dopisuje kafle, ktore sasiadami nie sa, i obiecuje budynek,
            # ktorego gra nie pozwoli zbudowac.
            sasiedzi = list(self.geom.neighbours(x, y))
            ma_rzeke = tmap.has_extra("River", x, y) or any(
                tmap.has_extra("River", a, b) for a, b in sasiedzi)
            ma_jezioro = any(tmap.terrain(a, b) == "Lake" for a, b in sasiedzi)
            akwedukt = None
            for nazwa in ("Aqueduct, Lake", "Aqueduct, River", "Aqueduct"):
                bl = rs.buildings.get(nazwa)
                if bl is None or nazwa in blds:
                    continue
                wymogi = [(q.type, q.name, q.present) for q in bl.reqs]
                ok = True
                for typ, nm, obecny in wymogi:
                    if typ == "Extra" and nm == "River":
                        ok = ok and (ma_rzeke == obecny)
                    elif typ == "Terrain" and nm == "Lake":
                        ok = ok and (ma_jezioro == obecny)
                    elif typ == "Tech":
                        ok = ok and ((nm in techs) == obecny)
                if ok:
                    akwedukt = {
                        "budynek": nazwa, "koszt_tarcz": bl.build_cost,
                        "utrzymanie_zl": bl.upkeep,
                        "wymaga_technologii": [q.name for q in bl.reqs
                                               if q.type == "Tech"
                                               and q.name not in techs],
                    }
                    break
            brakuje_akweduktu = None
            if akwedukt is None and not any(
                    n in blds for n in ("Aqueduct", "Aqueduct, River",
                                        "Aqueduct, Lake")):
                brak_t = sorted({q.name for n in ("Aqueduct",)
                                 for q in (rs.buildings[n].reqs
                                           if n in rs.buildings else [])
                                 if q.type == "Tech" and q.name not in techs})
                brakuje_akweduktu = {
                    "rzeka_obok": ma_rzeke, "jezioro_obok": ma_jezioro,
                    "brakuje_technologii": brak_t,
                    "powod": ("brak rzeki i jeziora obok — tanie warianty "
                              "za 20 tarcz odpadają" if not (ma_rzeke or ma_jezioro)
                              else "brakuje technologii"),
                }

            # miasto obrabia obszar o promieniu 2 (city_radius_sq), a nie samo
            # sasiedztwo, i obsadza tyle kafli, ilu ma obywateli
            obszar = []
            for dx in range(-3, 4):
                for dy in range(-4, 5):
                    if (dx, dy) == (0, 0):
                        continue
                    nb = ((x + dx) % self.geom.xsize,
                          (y + dy) % self.geom.ysize)
                    if self.geom.real_distance((x, y), nb) <= 2:
                        obszar.append(nb)

            ma_port = "Harbour" in blds or "Harbor" in blds
            port_bonus = 0
            if ma_port:
                for eff in rs.effects_by_type.get("Output_Add_Tile", []):
                    outs = [q.name for q in eff.reqs if q.type == "OutputType"]
                    bl = [q.name for q in eff.reqs if q.type == "Building" and q.present]
                    if outs == ["Food"] and bl and bl[0] in blds:
                        port_bonus += eff.value

            kafle, zysk_dostepny, zysk_zablokowany, praca = [], 0, 0, 0
            for nb in obszar:
                nm = tmap.terrain(*nb)
                if nm is None or nm not in plony:
                    continue
                p = plony[nm]
                teraz = p["zywnosc"]
                # surowiec na kaflu i port podnosza plon, a to potrafi zdecydowac
                surowiec = next((r for r, f in surowce.items()
                                 if f and tmap.has_extra(r, *nb)), None)
                if surowiec:
                    teraz += surowce[surowiec]
                if ma_port and not rs.terrains[nm].is_land:
                    teraz += port_bonus
                ma_irygacje = tmap.has_extra("Irrigation", *nb)
                mozliwa_irygacja = p["irygacja_daje"] > 0 and not ma_irygacje
                cel = p["przemiana_w"]
                po_przemianie = None
                if cel and cel in plony:
                    po_przemianie = plony[cel]["zywnosc"] + plony[cel]["irygacja_daje"]
                wpis = {
                    "kafel": [nb[0], nb[1]], "teren": nm,
                    **({"surowiec": surowiec} if surowiec else {}),
                    "zywnosc_teraz": teraz + (p["irygacja_daje"] if ma_irygacje else 0),
                    "irygowany": ma_irygacje,
                }

                # wszystkie realne drogi do wiekszego plonu z tego kafla
                opcje = []
                if p["irygacja_daje"] > 0 and not ma_irygacje:
                    opcje.append({
                        "praca": "irygacja",
                        "daje": p["irygacja_daje"],
                        "tur_pracy": p["irygacja_tur"],
                        "wymaga": "polowe",
                    })
                cel = p["uprawa_w"]
                if cel and cel in plony and cel.lower() not in ("no", "none"):
                    po = plony[cel]
                    zysk = (po["zywnosc"] + po["irygacja_daje"]) - wpis["zywnosc_teraz"]
                    if zysk > 0:
                        opcje.append({
                            "praca": f"uprawa w {cel}" + (
                                " i irygacja" if po["irygacja_daje"] else ""),
                            "daje": zysk,
                            "tur_pracy": p["uprawa_tur"] + (
                                po["irygacja_tur"] if po["irygacja_daje"] else 0),
                            "wymaga": "polowe",
                        })
                cel = p["przemiana_w"]
                if cel and cel in plony and cel.lower() not in ("no", "none"):
                    po = plony[cel]
                    zysk = (po["zywnosc"] + po["irygacja_daje"]) - wpis["zywnosc_teraz"]
                    if zysk > 0:
                        opcje.append({
                            "praca": f"przemiana w {cel}" + (
                                " i irygacja" if po["irygacja_daje"] else ""),
                            "daje": zysk,
                            "tur_pracy": p["przemiana_tur"] + (
                                po["irygacja_tur"] if po["irygacja_daje"] else 0),
                            "wymaga": "przemiana",
                        })
                for o in opcje:
                    o["dostepne_teraz"] = prace[o["wymaga"]]["dostepne"]
                    o["czym"] = (prace[o["wymaga"]]["mam_w_grze"]
                                 or prace[o["wymaga"]]["moge_budowac"] or [])
                    if not o["dostepne_teraz"]:
                        o["brakuje"] = prace[o["wymaga"]]["brakuje_technologii"]
                    o["tur_na_zywnosc"] = round(o["tur_pracy"] / o["daje"], 1)
                mozliwe = [o for o in opcje if o["dostepne_teraz"]]
                if mozliwe:
                    naj = min(mozliwe, key=lambda o: o["tur_na_zywnosc"])
                    wpis["najlepsza_praca"] = naj
                    zysk_dostepny += naj["daje"]
                    praca += naj["tur_pracy"]
                if opcje:
                    wpis["opcje"] = opcje
                zablokowane = [o for o in opcje if not o["dostepne_teraz"]]
                if zablokowane:
                    zysk_zablokowany += max(o["daje"] for o in zablokowane)
                kafle.append(wpis)
            # obywatele obsadzaja najlepsze kafle, po jednym na glowe
            najlepsze = sorted(kafle, key=lambda k: -k["zywnosc_teraz"])[:size]
            centrum = plony.get(tmap.terrain(x, y), {}).get("zywnosc", 1)
            zywnosc_teraz = sum(k["zywnosc_teraz"] for k in najlepsze) + max(1, centrum)
            kafle.sort(key=lambda k: (k.get("najlepsza_praca") or {}).get(
                "tur_na_zywnosc", 1e9))

            if not unlim and cap and size >= cap:
                powod = f"limit wielkości {cap} — potrzebna kanalizacja"
            elif deficyt:
                powod = (f"deficyt utrzymania: {jedzacy.get(int(r.get('id') or 0), 0)} "
                         f"jednostek na żywności przy {wolne} darmowych")
            elif zywnosc_teraz < size * 2:
                powod = (f"jałowa ziemia: {size} najlepszych kafli plus centrum "
                         f"dają {zywnosc_teraz} żywności, a {size} obywateli "
                         f"zjada {size * 2}")
            else:
                powod = "rośnie normalnie"

            out.append({
                "miasto": str(r.get("name")), "x": x, "y": y, "rozmiar": size,
                "teren_miasta": tmap.terrain(x, y),
                "limit_wielkosci": "bez limitu" if unlim else cap,
                "akwedukt_do_zbudowania": akwedukt,
                "akweduktu_nie_da_sie_zbudowac": brakuje_akweduktu,
                "deficyt_utrzymania": deficyt,
                "kafli_w_zasiegu": len(kafle),
                "zywnosc_z_obrabianych_kafli": zywnosc_teraz,
                "zjadaja_obywatele": size * 2,
                "powod": powod,
                "zysk_dostepny_teraz": zysk_dostepny,
                "tur_pracy_lacznie": praca,
                "zysk_po_zdobyciu_technologii": zysk_zablokowany,
                "kafle": kafle[:limit],
            })
        out.sort(key=lambda c: -c["zysk_dostepny_teraz"])

        # zbiorczy plan dla robotnikow: wszystkie prace w panstwie, od
        # najtanszej za jednostke zywnosci
        plan = []
        for c in out:
            for k in c["kafle"]:
                naj = k.get("najlepsza_praca")
                if not naj:
                    continue
                plan.append({
                    "miasto": c["miasto"], "kafel": k["kafel"],
                    "teren": k["teren"], "praca": naj["praca"],
                    "daje_zywnosci": naj["daje"], "tur_pracy": naj["tur_pracy"],
                    "tur_na_zywnosc": naj["tur_na_zywnosc"],
                    "czym": naj["czym"],
                })
        plan.sort(key=lambda j: j["tur_na_zywnosc"])

        return {
            "kto_moze_pracowac": prace,
            "plan_robot": plan[:60],
            "prac_lacznie": len(plan),
            "zywnosci_do_zyskania": sum(j["daje_zywnosci"] for j in plan),
            "tur_pracy_lacznie": sum(j["tur_pracy"] for j in plan),
            "miasta": out,
            "jak_czytac": (
                "irygacja działa od ręki i jest tania; przemiana terenu daje "
                "więcej, ale kosztuje wielokrotnie więcej tur pracy. Obywatel "
                "zjada 2 żywności, więc miasto rozmiaru N potrzebuje 2N tylko "
                "na wyżywienie siebie."),
            "czego_nie_liczymy": (
                "które kafle miasto faktycznie obrabia — zapis nie przechowuje "
                "przydziału obywateli do kafli, więc bierzemy całe sąsiedztwo"),
        }

    # ------------------------------------------------------------ dyplomacja

    def diplomacy(self, rs) -> dict:
        """Co sie stanie z kazdym ukladem i co realnie wplywa na decyzje AI.

        Uwaga na nazewnictwo, bo myli sie najczesciej: **rozejm (Armistice)
        sam zamienia sie w POKOJ**, a **zawieszenie broni (Cease-fire) wygasa
        do WOJNY**. Odliczanie jest deterministyczne (srv_main.c), wiec te
        liczby sa pewne. Nastawienia AI (`love`) zapis nie przechowuje, wiec
        zamiast zmyslonego prawdopodobienstwa podajemy przeslanki, ktorymi AI
        sie kieruje: sile stron, wspolnych wrogow, ambasady i to, czy druga
        strona ma powod do zerwania.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        tmap = TerrainMap(s)
        sec = s._sections[s.me.slot]
        tbl = sec.table("diplstate")
        stany = {}
        for idx, r in enumerate(tbl.dicts() if tbl else []):
            stany[idx] = {
                "stan": str(r.get("current") or "?"),
                "najblizszy_kiedykolwiek": str(r.get("closest") or "?"),
                "turns_left": int(r.get("turns_left") or 0),
                "ma_powod_do_zerwania": bool(int(r.get("has_reason_to_cancel") or 0)),
                "ambasada": bool(r.get("embassy")),
            }

        # sila stron: jednostki bojowe i miasta
        def sila(slot: int) -> dict:
            bojowe = 0
            for u in s.units_of(slot):
                ut = rs.units.get(u.type)
                if ut and (ut.attack or ut.defense) and "NonMil" not in ut.flags:
                    bojowe += 1
            zaczepne = sum(
                1 for u in s.units_of(slot)
                if (ut := rs.units.get(u.type)) and ut.attack > 1
                and "NonMil" not in ut.flags)
            ss = s._sections.get(slot)
            tb = ss.table("c") if ss else None
            miasta = list(tb.dicts()) if tb else []
            return {"jednostek_bojowych": bojowe, "zdolnych_do_ataku": zaczepne,
                    "miast": len(miasta),
                    "ludnosci": sum(int(c.get("size") or 0) for c in miasta),
                    "zloto": ss.int("gold") if ss else 0}

        moja = sila(s.me.slot)
        # kto z kim wojuje - wspolny wrog jest najsilniejsza przeslanka ukladu
        wojny = {}
        for slot, ss in s._sections.items():
            if slot not in s.players:
                continue
            t2 = ss.table("diplstate")
            wojny[slot] = {i for i, r in enumerate(t2.dicts() if t2 else [])
                           if str(r.get("current")) == "War"}

        # jednostki, ktore zginą przy przejsciu rozejmu w pokoj
        zagrozone = collections.Counter()
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if not ut or "NonMil" in ut.flags:
                continue                       # cywile moga wchodzic w granice
            o = tmap.owner(u.x, u.y)
            if o is None or o == s.me.slot:
                continue
            if stany.get(o, {}).get("stan") == "Armistice":
                zagrozone[o] += 1

        out = []
        for slot, info in stany.items():
            if slot == s.me.slot or slot not in s.players:
                continue
            stan = info["stan"]
            if stan in ("Never met", "?"):
                continue
            ich = sila(slot)
            wspolni = sorted(s.players[x].nation for x in
                             (wojny.get(slot, set()) & wojny.get(s.me.slot, set()))
                             if x in s.players)
            if stan == "Armistice":
                co_dalej = "pokój — automatycznie, bez pytania o zgodę"
                ryzyko = "brak; rozejm nie wygasa do wojny"
            elif stan == "Cease-fire":
                co_dalej = "WOJNA — zawieszenie broni wygasa"
                ryzyko = ("wysokie: po wygaśnięciu wracacie do wojny, "
                          "trzeba wynegocjować pokój wcześniej")
            elif stan == "War":
                co_dalej = "trwa, dopóki ktoś nie zaproponuje układu"
                ryzyko = "—"
            else:
                co_dalej = "stan trwały"
                ryzyko = "—"
            przeslanki = []
            if ich["zdolnych_do_ataku"] == 0:
                przeslanki.append("nie ma ani jednej jednostki zdolnej do "
                                  "natarcia — nie ma czym prowadzić wojny")
            if moja["jednostek_bojowych"] > 5 * max(1, ich["jednostek_bojowych"]):
                przeslanki.append("przewaga militarna po Twojej stronie "
                                  "jest miażdżąca, co sprzyja układowi")
            if wspolni:
                przeslanki.append(f"wspólny wróg: {', '.join(wspolni)}")
            if not info["ambasada"]:
                przeslanki.append("brak ambasady — negocjacje trudniejsze, "
                                  "rozważ dyplomatę")
            if info["ma_powod_do_zerwania"]:
                przeslanki.append("ma formalny powód do zerwania układu")
            out.append({
                "nacja": s.players[slot].nation,
                "stan": stan,
                "tur_do_zmiany": info["turns_left"] or None,
                "co_sie_stanie": co_dalej,
                "ryzyko": ryzyko,
                "najblizszy_kiedykolwiek": info["najblizszy_kiedykolwiek"],
                "ambasada": info["ambasada"],
                "ich_sila": ich,
                "moje_jednostki_do_rozwiazania": zagrozone.get(slot, 0),
                "przeslanki": przeslanki,
            })
        porzadek = {"Cease-fire": 0, "War": 1, "Armistice": 2, "Peace": 3,
                    "Alliance": 4}
        out.sort(key=lambda d: (porzadek.get(d["stan"], 9),
                                d["tur_do_zmiany"] or 99))
        return {
            "moja_sila": moja,
            "uklady": out,
            "wygasa_do_wojny": [d["nacja"] for d in out
                                if d["stan"] == "Cease-fire"],
            "stanie_sie_pokojem": [d["nacja"] for d in out
                                   if d["stan"] == "Armistice"],
            "jak_to_dziala": (
                "Rozejm (Armistice) odlicza tury i sam zamienia się w pokój; "
                "przy tej zmianie Twoje jednostki wojskowe stojące na cudzym "
                "terytorium zostają ROZWIĄZANE (srv_main.c, "
                "remove_illegal_armistice_units). Zawieszenie broni "
                "(Cease-fire) odlicza tury i wygasa do WOJNY — to jedyny stan, "
                "który wymaga działania przed czasem."),
            "czego_nie_wiem": (
                "zapis nie przechowuje nastawienia AI (`love`), więc nie podaję "
                "prawdopodobieństwa przyjęcia układu — tylko deterministyczne "
                "terminy i przesłanki, którymi AI się kieruje"),
        }

    # ----------------------------------------------------------- ostrzezenia

    def alerts(self, rs) -> dict:
        """Co sie psuje w panstwie i co z tym zrobic - posortowane wg pilnosci.

        Kazde ostrzezenie niesie liczbe tur do szkody, zeby dalo sie ustawic
        kolejnosc dzialania, oraz konkretna rade, a nie sama diagnoze.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        techs = _known_techs(s) - {"A_NONE"}
        gov = s.me.government or ""
        sec = s._sections[s.me.slot]
        rows = list(sec.table("c").dicts()) if sec.table("c") else []
        mine_blds: set[str] = set()
        for r in rows:
            mine_blds |= set(s._bits(r.get("improvements")))

        # darmowe utrzymanie zywnosci wg rozmiaru, wprost z regul
        base, steps = 0, []
        for eff in rs.effects_by_type.get("Unit_Upkeep_Free_Per_City", []):
            if [x.name for x in eff.reqs if x.type == "OutputType"] != ["Food"]:
                continue
            if [x for x in eff.reqs if x.type == "Gov"]:
                continue
            sizes = [int(x.name) for x in eff.reqs
                     if x.type == "MinSize" and str(x.name).isdigit()]
            if sizes:
                steps.extend([sizes[0]] * eff.value)
            else:
                base += eff.value
        steps.sort()

        def free_food(size: int) -> int:
            return base + sum(1 for m in steps if size >= m)

        cities = {int(r.get("id") or 0): str(r.get("name") or "?") for r in rows}
        jedzacy = collections.Counter()
        w_polu = collections.Counter()
        na_kaflu = collections.Counter()
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if ut is None:
                continue
            if getattr(ut, "uk_food", 0) > 0:
                jedzacy[u.homecity] += 1
            if getattr(ut, "uk_happy", 0) > 0:
                w_polu[u.homecity] += 1
            if (ut.attack or ut.defense) and "NonMil" not in ut.flags:
                na_kaflu[(u.x, u.y)] += 1

        uf = max(1, self._city_effect(rs, "Unhappy_Factor", "", set(), gov,
                                      techs, mine_blds, 0))
        mcm = self._city_effect(rs, "Make_Content_Mil", "", set(), gov, techs,
                                mine_blds, 0)
        na_polu_limit = mcm // uf

        alerty = []
        for r in rows:
            nazwa = str(r.get("name") or "?")
            cid = int(r.get("id") or 0)
            size = int(r.get("size") or 0)
            zapas = int(r.get("food_stock") or 0)
            blds = set(s._bits(r.get("improvements")))
            deficyt = max(0, jedzacy.get(cid, 0) - free_food(size))
            if deficyt > 0:
                tur = zapas // deficyt if deficyt else None
                nadmiar = jedzacy.get(cid, 0) - free_food(size)
                alerty.append({
                    "waga": "krytyczne" if (tur or 99) <= 5 else "pilne",
                    "tur_do_szkody": tur,
                    "miasto": nazwa, "rodzaj": "spadek wielkości miasta",
                    "co_sie_dzieje": (
                        f"rozmiar {size} daje {free_food(size)} darmowych "
                        f"utrzymań na żywności, a miasto żywi "
                        f"{jedzacy.get(cid, 0)} jednostek — deficyt {deficyt}"),
                    "rada": (
                        f"przenieś {nadmiar} jednostek do innego miasta "
                        f"macierzystego (wejdź nimi do dużego miasta i zmień "
                        f"miasto macierzyste) — to nic nie kosztuje i działa "
                        f"od razu"),
                })
            if str(r.get("currently_building_name")) == "Coinage":
                alerty.append({
                    "waga": "warte uwagi", "tur_do_szkody": None,
                    "miasto": nazwa, "rodzaj": "produkcja zamieniana na złoto",
                    "co_sie_dzieje": "miasto buduje Coinage",
                    "rada": "przestaw na budynek albo jednostkę — złota i tak "
                            "masz nadmiar, a tarcze przepadają",
                })
            if int(r.get("anarchy") or 0) > 0:
                alerty.append({
                    "waga": "krytyczne", "tur_do_szkody": 0,
                    "miasto": nazwa, "rodzaj": "zamieszki",
                    "co_sie_dzieje": "miasto jest w stanie zamieszek — "
                                     "produkcja stoi",
                    "rada": "wprowadź jednostkę wojskową (stan wojenny), "
                            "podnieś luksus albo dostaw świątynię",
                })
            # zdobyte miasto bez garnizonu latwiej odkupic
            if int(r.get("original") or 0) != s.me.slot and not na_kaflu.get(
                    (int(r["x"]), int(r["y"])), 0):
                alerty.append({
                    "waga": "pilne", "tur_do_szkody": None,
                    "miasto": nazwa, "rodzaj": "zdobyte miasto bez garnizonu",
                    "co_sie_dzieje": "obce z pochodzenia miasto stoi puste, "
                                     "więc jest tanie do odkupienia",
                    "rada": "wstaw jedną jednostkę — koszt przekupienia "
                            "podwaja się (Incite_Cost_Pct +100)",
                })

        for cid, n in w_polu.items():
            koszt = max(0, n * uf - mcm)
            if koszt > 0:
                alerty.append({
                    "waga": "warte uwagi", "tur_do_szkody": None,
                    "miasto": cities.get(cid, "?"),
                    "rodzaj": "wojsko w polu robi niezadowolonych",
                    "co_sie_dzieje": (
                        f"{n} jednostek w polu przy limicie {na_polu_limit} — "
                        f"{koszt} niezadowolonych"),
                    "rada": "zawróć nadmiar do garnizonu; w mieście te same "
                            "jednostki robią zadowolonych przez stan wojenny",
                })

        # --- miasta, ktore nie rosna: rozdzielamy przyczyne i podajemy lek
        try:
            wzrost = self.growth_potential(rs)
        except Exception:  # noqa: BLE001 - alerty maja dzialac nawet bez tego
            wzrost = {"miasta": []}
        for c in wzrost.get("miasta", []):
            if c["powod"].startswith("rośnie"):
                continue
            if c["deficyt_utrzymania"]:
                continue                       # juz zgloszone wyzej, nie dubluj
            if str(c["limit_wielkosci"]) != "bez limitu" and \
                    c["rozmiar"] >= (c["limit_wielkosci"] or 0):
                alerty.append({
                    "waga": "warte uwagi", "tur_do_szkody": None,
                    "miasto": c["miasto"], "rodzaj": "miasto uderzyło w limit wielkości",
                    "co_sie_dzieje": f"rozmiar {c['rozmiar']} przy limicie "
                                     f"{c['limit_wielkosci']}",
                    "rada": "dobuduj akwedukt, a przy limicie 16 kanalizację "
                            "(Sanitation)",
                })
                continue
            brak = c["zjadaja_obywatele"] - c["zywnosc_z_obrabianych_kafli"]
            if brak <= 0:
                continue
            naj = None
            for k in c["kafle"]:
                for rodzaj, daje, tur in (("irygacja", k.get("irygacja_da"),
                                           k.get("irygacja_tur_pracy")),
                                          ("przemiana w " + str(k.get("przemiana_w")),
                                           k.get("przemiana_da"),
                                           k.get("przemiana_tur_pracy"))):
                    if not daje or not tur:
                        continue
                    koszt = tur / daje
                    if naj is None or koszt < naj[0]:
                        naj = (koszt, rodzaj, daje, tur, k["teren"], k["kafel"])
            rada = ("brak kafli, które da się poprawić — to miasto nie urośnie "
                    "bez zmiany otoczenia; traktuj je jako produkcyjne")
            if naj:
                _koszt, rodzaj, daje, tur, teren, kafel = naj
                rada = (f"najtaniej: {rodzaj} na kaflu {kafel} ({teren}) — "
                        f"+{daje} żywności za {tur} tur pracy robotnika. "
                        f"Do wyjścia na zero brakuje {brak} żywności; "
                        f"łącznie irygacja da +{c['zysk_z_irygacji']}, "
                        f"przemiana terenu +{c['zysk_z_przemiany']}")
            alerty.append({
                "waga": "pilne", "tur_do_szkody": None,
                "miasto": c["miasto"], "rodzaj": "wzrost zatrzymany przez jałową ziemię",
                "co_sie_dzieje": (f"obrabiane kafle dają {c['zywnosc_z_obrabianych_kafli']} "
                                  f"żywności, a {c['rozmiar']} obywateli zjada "
                                  f"{c['zjadaja_obywatele']}"),
                "rada": rada,
            })

        # --- prace terenowe: dla KAZDEGO miasta, ktore ma co poprawiac
        plan = wzrost.get("plan_robot", [])
        umie = wzrost.get("kto_moze_pracowac", {})
        po_miastach = collections.defaultdict(list)
        for job in plan:
            po_miastach[job["miasto"]].append(job)
        for nazwa, joby in po_miastach.items():
            zysk = sum(j["daje_zywnosci"] for j in joby)
            tur = sum(j["tur_pracy"] for j in joby)
            naj = joby[0]
            alerty.append({
                "waga": "informacja", "tur_do_szkody": None,
                "miasto": nazwa, "rodzaj": "prace terenowe do wykonania",
                "co_sie_dzieje": (f"{len(joby)} kafli do poprawy, razem "
                                  f"+{zysk} żywności za {tur} tur pracy"),
                "rada": (f"zacznij od: {naj['praca']} na kaflu {naj['kafel']} "
                         f"({naj['teren']}) — +{naj['daje_zywnosci']} za "
                         f"{naj['tur_pracy']} tur, czym: "
                         f"{', '.join(naj['czym']) or 'brak jednostki'}"),
            })
        if plan:
            blok = umie.get("przemiana", {})
            alerty.append({
                "waga": "informacja", "tur_do_szkody": None,
                "miasto": "— całe państwo",
                "rodzaj": "plan robót dla robotników",
                "co_sie_dzieje": (
                    f"{wzrost.get('prac_lacznie', 0)} prac w "
                    f"{len(po_miastach)} miastach: razem "
                    f"+{wzrost.get('zywnosci_do_zyskania', 0)} żywności za "
                    f"{wzrost.get('tur_pracy_lacznie', 0)} tur pracy"),
                "rada": (
                    "przemiana terenu wymaga jednostki z flagą Transform "
                    f"({', '.join(blok.get('jednostki', [])) or '—'}), a brakuje "
                    f"do niej: {', '.join(blok.get('brakuje_technologii', [])) or '—'}. "
                    "Do tego czasu robotnicy mogą irygować i uprawiać."
                    if not blok.get("dostepne") else
                    "masz czym wykonać wszystkie prace, łącznie z przemianą terenu"),
            })

        kolejnosc = {"krytyczne": 0, "pilne": 1, "warte uwagi": 2, "informacja": 3}
        alerty.sort(key=lambda a: (kolejnosc.get(a["waga"], 9),
                                   a["tur_do_szkody"]
                                   if a["tur_do_szkody"] is not None else 999))
        return {
            "alertow": len(alerty),
            "krytycznych": sum(1 for a in alerty if a["waga"] == "krytyczne"),
            "pilnych": sum(1 for a in alerty if a["waga"] == "pilne"),
            "alerty": alerty,
            "zasada_darmowej_zywnosci":
                f"{base} jednostek za darmo, +1 za każdy rozmiar od "
                f"{steps[0] if steps else '-'} do {steps[-1] if steps else '-'}",
        }

    # ------------------------------------------------------- obrona miasta

    def city_defense(self, rs, miasto: str = "", napastnik: str = "",
                     limit: int = 8) -> dict:
        """Czym bronic KONKRETNEGO miasta - z jego prawdziwym terenem i murami.

        Rozni sie od ogolnego rankingu tym, ze bierze teren spod miasta,
        ulepszenia kafla, faktyczne budynki, rozmiar i ustroj z zapisu, a liste
        dostepnych jednostek z realnie zbadanych technologii. Za napastnika
        przyjmuje najgrozniejsza jednostke, jaka widac u sasiadow - bo bronimy
        sie przed tym, co istnieje, a nie przed tym, co teoretycznie mozliwe.
        """
        from .advisor import rank_defenders
        from .combat import Side, Situation

        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        tmap = TerrainMap(s)
        sec = s._sections[s.me.slot]
        rows = list(sec.table("c").dicts()) if sec.table("c") else []
        if not rows:
            return {"blad": "brak miast"}

        row = None
        if miasto:
            row = next((r for r in rows
                        if str(r.get("name", "")).lower() == miasto.lower()), None)
            if row is None:
                return {"blad": f"nie mam miasta {miasto}",
                        "dostepne": sorted(str(r.get("name")) for r in rows)}
        else:
            # bez wskazania: najbardziej wysuniete, czyli najdalsze od stolicy
            stolica = next(((int(r["x"]), int(r["y"])) for r in rows
                            if "Palace" in set(s._bits(r.get("improvements")))), None)
            row = max(rows, key=lambda r: self.geom.real_distance(
                (int(r["x"]), int(r["y"])), stolica)) if stolica else rows[0]

        x, y = int(row["x"]), int(row["y"])
        blds = set(s._bits(row.get("improvements")))
        wonders, plain = set(), set()
        for name in blds:
            b = rs.buildings.get(name)
            if b and b.is_wonder:
                wonders.add(name)
            elif b:
                plain.add(name)
        for r in rows:                       # cuda dzialaja w calym panstwie
            for name in s._bits(r.get("improvements")):
                b = rs.buildings.get(name)
                if b and b.is_wonder:
                    wonders.add(name)

        terr_name = tmap.terrain(x, y)
        terr = rs.terrains.get(terr_name)
        if terr is None:
            return {"blad": f"nie rozpoznaję terenu pod miastem {row.get('name')}"}
        extras = {e for e in rs.extras
                  if tmap.has_extra(e, x, y)} if rs.extras else set()

        techs = _known_techs(s) - {"A_NONE"}
        sit = Situation(
            terrain=terr, extras=extras, in_city=True,
            city_size=int(row.get("size") or 1),
            buildings=plain, player_buildings=wonders,
            fortified=True, gov=s.me.government or "",
            techs=techs, units_on_tile=1,
        )

        # napastnik: najgrozniejsze, co realnie stoi u sasiadow
        groza = None
        for slot in s._sections:
            if slot == s.me.slot or slot not in s.players:
                continue
            for u in s.units_of(slot):
                ut = rs.units.get(u.type)
                if not (ut and ut.attack > 0 and "NonMil" not in ut.flags):
                    continue
                if groza is None or ut.attack > groza.attack:
                    groza = ut
        if napastnik:
            groza = rs.units.get(napastnik) or groza
        if groza is None:
            groza = max((u for u in rs.units_available(techs) if u.attack > 0),
                        key=lambda u: u.attack, default=None)
        if groza is None:
            return {"blad": "nie znalazłem żadnej jednostki atakującej"}

        att = [Side(utype=groza, count=1, vet=0)]
        opts = rank_defenders(rs, att, sit, techs, confidence=0.95,
                              promotions=True, trials=3000, from_barracks=True)

        out = []
        for o in opts[:limit]:
            ut = o.utype
            out.append({
                "jednostka": ut.name,
                "stopien_przy_budowie": o.vet_name,
                "obrona": ut.defense, "zycie": ut.hitpoints,
                "koszt": ut.build_cost,
                "utrzymanie_tarcze": ut.uk_shield,
                "utrzymanie_zywnosc": ut.uk_food,
                "niezadowolonych_gdy_w_polu": ut.uk_happy,
                "jedna_sztuka_zatrzyma": o.stops_alone,
                "sztuk_by_utrzymac": o.min_count,
                "tarcz_lacznie": o.shields,
                "szansa_przy_jednej_proc": round(o.p_single * 100, 1),
                "koszt_na_zatrzymanego": (round(ut.build_cost / o.stops_alone, 1)
                                          if o.stops_alone else None),
            })
        out.sort(key=lambda d: (d["koszt_na_zatrzymanego"] is None,
                                d["koszt_na_zatrzymanego"] or 1e9))
        return {
            "miasto": str(row.get("name")),
            "x": x, "y": y,
            "rozmiar": sit.city_size,
            "teren": terr.name,
            "ulepszenia_kafla": sorted(extras),
            "budynki": sorted(plain),
            "przed_kim": {"jednostka": groza.name, "atak": groza.attack,
                          "skad": "najgroźniejsza jednostka widziana u sąsiadów"},
            "ranking": out,
            "zasada": (
                "kolejność wg kosztu za jednego zatrzymanego napastnika; "
                "utrzymanie i niezadowolenie w polu podane osobno, bo garnizon "
                "stoi w mieście i nie generuje tego drugiego"),
        }

    # ------------------------------------------------------------- mobilnosc

    def mobility(self, rs, tury: int = 2, jednostka: str = "") -> dict:
        """Logistyka: dokad kazda jednostka zdazy i gdzie sie przegrupowac.

        Odwrotna perspektywa do `gotowosc_wojenna`: tam pytamy "ile tur do tego
        miasta", tu "dokad ta jednostka w ogole zdazy". Zasieg liczymy realnym
        kosztem ruchu po heksie, a nie odlegloscia, wiec wzgorza i las skracaja
        go dwukrotnie, gory trzykrotnie, a drogi wydluzaja wielokrotnie.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        tury = max(1, min(4, int(tury)))
        tmap = TerrainMap(s)

        # --- moje jednostki bojowe
        units = []
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if not (ut and (ut.attack or ut.defense) and "NonMil" not in ut.flags):
                continue
            if jednostka and u.type.lower() != jednostka.lower():
                continue
            units.append((u, ut))
        if not units:
            return {"tury": tury, "jednostek_bojowych": 0, "grupy": [],
                    "punkty_zborne": [], "cele_wroga_w_zasiegu": [],
                    "rozciagniecie": {"w_polu": 0, "w_miastach": 0,
                                      "odciete": [],
                                      "wg_miasta_macierzystego": []},
                    "uwaga": ("nie masz jeszcze jednostek bojowych"
                              + (f" typu {jednostka}" if jednostka else "")
                              + " — nie ma czego rozstawiać")}

        # --- moje i cudze miasta
        moje, obce = {}, {}
        for slot, sec in s._sections.items():
            tbl = sec.table("c") if sec else None
            if tbl is None:
                continue
            nation = s.players[slot].nation if slot in s.players else "?"
            wojska = collections.Counter()
            if slot != s.me.slot:
                for u in s.units_of(slot):
                    ut = rs.units.get(u.type)
                    if ut and (ut.attack or ut.defense) and "NonMil" not in ut.flags:
                        wojska[(u.x, u.y)] += 1
            for r in tbl.dicts():
                key = (int(r.get("x") or 0), int(r.get("y") or 0))
                entry = {"miasto": str(r.get("name") or "?"),
                         "x": key[0], "y": key[1],
                         "rozmiar": int(r.get("size") or 0)}
                if slot == s.me.slot:
                    moje[key] = entry
                else:
                    obce[key] = {**entry, "nacja": nation,
                                 "obroncow": wojska.get(key, 0)}

        # --- zasieg kazdej jednostki
        na_miasto = collections.defaultdict(dict)      # kafel -> {indeks: tury}
        zasiegi = []
        for idx, (u, ut) in enumerate(units):
            reach = reach_within(rs, tmap, self.geom, ut, (u.x, u.y), tury)
            zasiegi.append(len(reach))
            for tile, t in reach.items():
                if tile in moje or tile in obce:
                    prev = na_miasto[tile].get(idx)
                    na_miasto[tile][idx] = t if prev is None else min(prev, t)

        def zbierz(pool: dict) -> list[dict]:
            out = []
            for tile, info in pool.items():
                kto = na_miasto.get(tile, {})
                if not kto:
                    continue
                wg = collections.Counter(units[i][0].type for i in kto)
                out.append({**info, "moich_w_zasiegu": len(kto),
                            "najszybciej_tur": min(kto.values()),
                            "wg_typu": dict(wg.most_common())})
            out.sort(key=lambda c: (-c["moich_w_zasiegu"], c["najszybciej_tur"]))
            return out

        punkty = zbierz(moje)
        cele = zbierz(obce)

        # --- rozciagniecie: co stoi w polu i ile to kosztuje
        w_miastach = {k for k in moje}
        w_polu = [(u, ut) for u, ut in units if (u.x, u.y) not in w_miastach]
        odciete = []
        for idx, (u, ut) in enumerate(units):
            if not any(tile in moje for tile in na_miasto
                       if idx in na_miasto[tile]):
                odciete.append({"jednostka": u.type, "x": u.x, "y": u.y,
                                "powod": f"nie wróci do żadnego miasta w {tury} turach"})

        techs = _known_techs(s) - {"A_NONE"}
        gov = s.me.government or ""
        mine_blds: set[str] = set()
        rows = list(s._sections[s.me.slot].table("c").dicts())
        for r in rows:
            mine_blds |= set(s._bits(r.get("improvements")))
        uf = max(1, self._city_effect(rs, "Unhappy_Factor", "", set(), gov,
                                      techs, mine_blds, 0))
        mcm = self._city_effect(rs, "Make_Content_Mil", "", set(), gov, techs,
                                mine_blds, 0)
        nazwy = {int(r.get("id") or 0): str(r.get("name") or "?") for r in rows}
        pole_wg_domu = collections.Counter()
        for u, ut in w_polu:
            if getattr(ut, "uk_happy", 0) > 0:
                pole_wg_domu[nazwy.get(u.homecity, "(bez miasta)")] += 1

        grupy = collections.Counter((u.type, u.veteran) for u, _ in units)
        return {
            "tury": tury,
            "jednostek_bojowych": len(units),
            "grupy": [{"jednostka": t, "stopien": v, "sztuk": n,
                       "ruch": rs.units[t].move_rate,
                       "klasa": rs.units[t].uclass_id}
                      for (t, v), n in grupy.most_common()],
            "sredni_zasieg_kafli": round(sum(zasiegi) / len(zasiegi), 1),
            "punkty_zborne": punkty[:12],
            "cele_wroga_w_zasiegu": cele[:12],
            "rozciagniecie": {
                "w_polu": len(w_polu),
                "w_miastach": len(units) - len(w_polu),
                "limit_bez_kosztu_na_miasto": mcm // uf,
                "wg_miasta_macierzystego": [
                    {"miasto_macierzyste": m, "jednostek_w_polu": n,
                     "niezadowolonych": max(0, n * uf - mcm)}
                    for m, n in pole_wg_domu.most_common()],
                "odciete": odciete[:12],
            },
            "czego_nie_liczymy": (
                "stref kontroli, jednostek wroga na trasie i zapasu ruchu, "
                "który jednostka już wydała w tej turze — zasięg liczony "
                "od pełnego zapasu"),
        }






    # ------------------------------------------------------ ocena zagrozenia

    def threat_assessment(self, rs, tury: int = 8) -> dict:
        """Kto realnie zagraza, a kto tylko wyglada grozniе na liscie nacji.

        Sama liczba jednostek nie mowi nic: wojsko po drugiej stronie mapy,
        bez drogi ladowej i bez floty, nie zajmie zadnego miasta. Liczymy wiec
        zdolnosc do UZYCIA sily - realny marsz po heksie, istnienie polaczenia
        ladowego, posiadanie transportu - i osobno latwosc uderzenia w druga
        strone, bo to sa dwie rozne rzeczy.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        tmap = TerrainMap(s)
        ct = city_tiles(s)
        dzicy = {"Animal Kingdom", "Barbarian", "Pirate"}

        moje_miasta = [(int(r["x"]), int(r["y"]), str(r["name"]))
                       for r in (s._sections[s.me.slot].table("c").dicts()
                                 if s._sections[s.me.slot].table("c") else [])]
        if not moje_miasta:
            return {"blad": "brak miast"}

        def is_land(x, y):
            n = tmap.terrain(x, y)
            t = rs.terrains.get(n) if n else None
            return bool(t and t.is_land)

        try:
            lad = regions(tmap, is_land, self.geom)
        except Exception:  # noqa: BLE001
            lad = {}
        moje_lady = {lad.get((x, y)) for x, y, _n in moje_miasta}

        stany = {}
        tbl = s._sections[s.me.slot].table("diplstate")
        for idx, r in enumerate(tbl.dicts() if tbl else []):
            if idx in s.players:
                stany[idx] = (str(r.get("current") or "?"),
                              int(r.get("turns_left") or 0))

        wyniki = []
        for slot, pl in s.players.items():
            if slot == s.me.slot or pl.nation in dzicy:
                continue
            stan, tur_do_zmiany = stany.get(slot, ("?", 0))
            if stan in ("Never met", "?"):
                continue
            sec = s._sections.get(slot)
            tb = sec.table("c") if sec else None
            ich = [(int(r.get("x") or 0), int(r.get("y") or 0),
                    str(r.get("name") or "?"), int(r.get("size") or 0),
                    set(s._bits(r.get("improvements"))))
                   for r in (tb.dicts() if tb else [])]
            if not ich:
                continue

            zaczepne, obronne, floty = [], [], []
            for u in s.units_of(slot):
                ut = rs.units.get(u.type)
                if not ut or "NonMil" in ut.flags:
                    if ut and getattr(ut, "transport_cap", 0):
                        floty.append(ut)
                    continue
                if ut.attack > 1:
                    zaczepne.append(ut)
                elif ut.defense > 0:
                    obronne.append(ut)
            # flota liczy sie tylko wtedy, gdy ma czym przewiezc wojsko
            ma_flote = any((t2 := rs.units.get(u.type)) is not None
                           and rs.uclass_of(t2).name in ("Sea", "Trireme")
                           and t2.transport_cap > 0
                           for u in s.units_of(slot))

            # najkrotszy dystans miasto-miasto i czy istnieje droga ladowa
            najblizej, para = None, None
            wspolny_lad = False
            for ix, iy, inazwa, _sz, _b in ich:
                for mx, my, mnazwa in moje_miasta:
                    d = self.geom.real_distance((ix, iy), (mx, my))
                    if najblizej is None or d < najblizej:
                        najblizej, para = d, (inazwa, mnazwa)
                if lad.get((ix, iy)) in moje_lady:
                    wspolny_lad = True

            # ile tur marszu ich najlepsza jednostka potrzebuje do mojego miasta
            marsz = None
            wzor = (max(zaczepne, key=lambda u: u.attack) if zaczepne
                    else (obronne[0] if obronne else None))
            if wzor is not None and wspolny_lad:
                for ix, iy, _n, _sz, _b in ich:
                    for mx, my, _mn in moje_miasta:
                        if self.geom.real_distance((ix, iy), (mx, my)) > \
                                max(1, wzor.move_rate) * (tury + 2):
                            continue
                        t3 = march_turns(rs, tmap, self.geom, wzor, (ix, iy),
                                         (mx, my), max_nodes=6000, cities=ct)
                        if t3 is not None and (marsz is None or t3 < marsz):
                            marsz = t3

            ludnosc = sum(sz for _x, _y, _n, sz, _b in ich)
            zloto = sec.int("gold") if sec else 0

            # --- zdolnosc uderzenia W NAS
            if not zaczepne:
                zdolnosc, czemu = 0, "nie ma ani jednej jednostki zdolnej do natarcia"
            elif not wspolny_lad and not ma_flote:
                zdolnosc, czemu = 0, "inny ląd i brak floty — nie ma jak dotrzeć"
            elif not wspolny_lad:
                zdolnosc, czemu = 2, "inny ląd, ale ma flotę — desant możliwy"
            elif marsz is None:
                zdolnosc, czemu = 1, "wspólny ląd, ale marsz dłuższy niż horyzont"
            elif marsz <= 2:
                zdolnosc, czemu = 5, f"dojdzie w {marsz + 1} tur — może uderzyć i się wycofać"
            elif marsz <= 5:
                zdolnosc, czemu = 3, f"dojdzie w {marsz + 1} tur"
            else:
                zdolnosc, czemu = 1, f"dojdzie dopiero w {marsz + 1} tur"

            zagrozenie = zdolnosc * (1 + len(zaczepne) * 0.5)
            if stan == "War":
                zagrozenie *= 2
            elif stan == "Cease-fire":
                zagrozenie *= 1.5

            # --- jak latwo NAM uderzyc na nich
            teren = collections.Counter()
            mury = 0
            for ix, iy, _n, _sz, b in ich:
                nm = tmap.terrain(ix, iy)
                if nm:
                    teren[nm] += 1
                if "City Walls" in b:
                    mury += 1
            trudny = sum(v for k, v in teren.items()
                         if (t4 := rs.terrains.get(k)) and t4.defense_bonus >= 50)
            wyniki.append({
                "nacja": pl.nation,
                "stan": stan,
                "tur_do_zmiany_stanu": tur_do_zmiany or None,
                "miast": len(ich), "ludnosc": ludnosc, "zloto": zloto,
                "jednostek_zaczepnych": len(zaczepne),
                "jednostek_obronnych": len(obronne),
                "ma_flote": ma_flote,
                "wspolny_lad": wspolny_lad,
                "najblizsza_para_miast": para,
                "dystans": najblizej,
                "tur_marszu_do_mnie": (marsz + 1) if marsz is not None else None,
                "zdolnosc_uderzenia": zdolnosc,
                "dlaczego": czemu,
                "ocena_zagrozenia": round(zagrozenie, 1),
                "dla_mnie_jako_cel": {
                    "teren_ich_miast": dict(teren.most_common(3)),
                    "miast_na_trudnym_terenie": trudny,
                    "miast_z_murami": mury,
                    "ich_zloto": zloto,
                    "latwosc": ("łatwy" if trudny == 0 and mury == 0 else
                                "trudny" if trudny >= len(ich) / 2 else "średni"),
                },
            })

        # --- kolejnosc podboju: blisko, latwo, bogato
        #
        # Wartosc celu i latwosc jego zdobycia to co innego niz zagrozenie
        # z jego strony. Panstwo gorzyste i bogate moze byc warte wiecej, ale
        # kosztowac trzy razy tyle - i to musi byc widoczne osobno.
        cele = []
        for w in wyniki:
            c = w["dla_mnie_jako_cel"]
            blisko = max(0.0, 12 - (w["dystans"] or 20)) / 12      # 0..1
            latwo = {"łatwy": 1.0, "średni": 0.55, "trudny": 0.25}[c["latwosc"]]
            latwo *= max(0.3, 1 - c["miast_z_murami"] / max(1, w["miast"]))
            wartosc = w["ludnosc"] * 2 + w["miast"] * 3 + c["ich_zloto"] / 40
            oplacalnosc = wartosc * latwo * (0.4 + 0.6 * blisko)
            przeszkody = []
            if c["miast_na_trudnym_terenie"]:
                przeszkody.append(
                    f"{c['miast_na_trudnym_terenie']} z {w['miast']} miast na "
                    f"terenie dającym premię obrony — potrzebne machiny "
                    f"oblężnicze albo przewaga liczebna")
            if not w["wspolny_lad"]:
                przeszkody.append("inny ląd — potrzebny transport morski")
            if c["miast_z_murami"]:
                przeszkody.append(f"{c['miast_z_murami']} miast z murami")
            if w["stan"] in ("Peace", "Armistice"):
                przeszkody.append(
                    f"stan {w['stan']} — wojsko nie wejdzie na ich terytorium "
                    f"bez wypowiedzenia wojny")
            cele.append({
                "nacja": w["nacja"], "stan": w["stan"],
                "dystans": w["dystans"],
                "miast": w["miast"], "ludnosc": w["ludnosc"],
                "ich_zloto": c["ich_zloto"],
                "teren": c["teren_ich_miast"],
                "latwosc": c["latwosc"],
                "oplacalnosc": round(oplacalnosc, 1),
                "przeszkody": przeszkody,
            })
        cele.sort(key=lambda c: -c["oplacalnosc"])

        wyniki.sort(key=lambda w: -w["ocena_zagrozenia"])
        realni = [w["nacja"] for w in wyniki if w["ocena_zagrozenia"] >= 3]
        pozorni = [w["nacja"] for w in wyniki
                   if w["ocena_zagrozenia"] < 1 and w["jednostek_zaczepnych"]]
        return {
            "horyzont_tur": tury,
            "realne_zagrozenie": realni,
            "kolejnosc_podboju": cele,
            "grozni_tylko_na_papierze": pozorni,
            "nacje": wyniki,
            "jak_liczone": (
                "zdolność uderzenia liczona z realnego marszu po heksie i "
                "istnienia połączenia lądowego albo floty, nie z samej liczby "
                "jednostek; mnożona przez stan dyplomatyczny (wojna ×2, "
                "zawieszenie broni ×1,5)"),
        }

    # ------------------------------------------------------- plan produkcji

    def production_plan(self, rs, strategia: str = "auto") -> dict:
        """Co budowac w kazdym miescie, wzgledem strategii I sytuacji zewnetrznej.

        Sama strategia nie wystarcza: przy wolnej ziemi osadnik bije kazdy
        budynek, a przy wygasajacym zawieszeniu broni obrona bije osadnika.
        Dlatego najpierw oceniamy sytuacje - ile jest miejsca, kto grozi, ilu
        robotnikow brakuje do zaleglosci - a dopiero potem przydzielamy role.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        tmap = TerrainMap(s)
        techs = _known_techs(s) - {"A_NONE"}
        gov = s.me.government or ""
        sec = s._sections[s.me.slot]
        rows = list(sec.table("c").dicts()) if sec.table("c") else []
        if not rows:
            return {"blad": "brak miast"}
        mine_blds: set[str] = set()
        for r in rows:
            mine_blds |= set(s._bits(r.get("improvements")))

        # ---------- sytuacja miedzynarodowa
        dzicy = {"Animal Kingdom", "Barbarian", "Pirate"}
        stany = {}
        tbl = sec.table("diplstate")
        for idx, r in enumerate(tbl.dicts() if tbl else []):
            if idx in s.players and idx != s.me.slot:
                stany[s.players[idx].nation] = (str(r.get("current") or "?"),
                                                int(r.get("turns_left") or 0))
        wojna = [n for n, (st, _t) in stany.items()
                 if st == "War" and n not in dzicy]
        wygasa = [(n, t) for n, (st, t) in stany.items() if st == "Cease-fire"]
        # czy ktokolwiek ma czym uderzyc
        obce_zaczepne = 0
        for slot, pl in s.players.items():
            if slot == s.me.slot or pl.nation in dzicy:
                continue
            if stany.get(pl.nation, ("?", 0))[0] in ("Alliance", "Team"):
                continue
            for u in s.units_of(slot):
                ut = rs.units.get(u.type)
                if ut and ut.attack > 1 and "NonMil" not in ut.flags:
                    obce_zaczepne += 1

        # ---------- ile jest miejsca na nowe miasta
        def is_land(x, y):
            n = tmap.terrain(x, y)
            t = rs.terrains.get(n) if n else None
            return bool(t and t.is_land)

        wszystkie_miasta = [(int(r["x"]), int(r["y"])) for r in rows]
        for slot, ss in s._sections.items():
            if slot == s.me.slot:
                continue
            tb = ss.table("c") if ss else None
            for r in (tb.dicts() if tb else []):
                wszystkie_miasta.append((int(r.get("x") or 0), int(r.get("y") or 0)))
        try:
            obszary = regions(tmap, is_land, self.geom)
        except Exception:  # noqa: BLE001
            obszary = {}
        moje_obszary = {obszary.get(m) for m in
                        [(int(r["x"]), int(r["y"])) for r in rows]}
        wolne = 0
        for tile, g in obszary.items():
            if g not in moje_obszary:
                continue
            if all(self.geom.real_distance(tile, m) >= 3 for m in wszystkie_miasta):
                n = tmap.terrain(*tile)
                t = rs.terrains.get(n) if n else None
                if t and t.food + t.shield >= 2:
                    wolne += 1

        # ---------- zaleglosc robotnicza
        try:
            wzrost = self.growth_potential(rs)
            tur_pracy = wzrost.get("tur_pracy_lacznie", 0)
        except Exception:  # noqa: BLE001
            tur_pracy = 0
        robotnicy = sum(1 for u in s.units_of(s.me.slot)
                        if (ut := rs.units.get(u.type))
                        and "Settlers" in ut.flags and "Cities" not in ut.flags
                        and "AddToCity" not in ut.flags)
        tur_zaleglosci = tur_pracy // max(1, robotnicy)

        # ---------- strategia
        if strategia == "auto":
            if wojna or obce_zaczepne > 3:
                strategia = "wojna"
            elif wolne >= 20:
                strategia = "ekspansja"
            else:
                strategia = "gospodarka"

        # ---------- co da sie zbudowac
        def buduje_sie(u) -> bool:
            return u.build_cost > 0 and "NoBuild" not in u.flags

        osadnik = next((u for u in rs.units_available(techs)
                        if "Cities" in u.flags and buduje_sie(u)), None)
        robotnik = next((u for u in rs.units_available(techs)
                         if "Settlers" in u.flags and "Cities" not in u.flags
                         and "AddToCity" not in u.flags and buduje_sie(u)), None)
        obroncy = sorted((u for u in rs.units_available(techs)
                          if u.defense > 0 and buduje_sie(u)
                          and "NonMil" not in u.flags
                          and rs.uclass_of(u).name in ("Land", "Small Land")),
                         key=lambda u: u.build_cost / max(1, u.defense))
        obronca = obroncy[0] if obroncy else None
        dyplomata = next((u for u in rs.units_available(techs)
                          if "Diplomat" in u.flags and buduje_sie(u)), None)

        darmowe = sorted((b for b in rs.buildings.values()
                          if not b.is_wonder and b.upkeep == 0
                          and b.build_cost > 0
                          and all(t in techs for t in b.req_techs())),
                         key=lambda b: b.build_cost)

        garnizon = collections.Counter()
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if ut and (ut.attack or ut.defense) and "NonMil" not in ut.flags:
                garnizon[(u.x, u.y)] += 1
        ma_dyplomate = any(rs.units.get(u.type)
                           and "Diplomat" in rs.units[u.type].flags
                           for u in s.units_of(s.me.slot))
        buduje_dyplomate = sum(1 for r in rows
                               if dyplomata and
                               str(r.get("currently_building_name")) == dyplomata.name)

        plan, potrzeba_dyplomaty = [], bool(wygasa) and not ma_dyplomate
        for r in rows:
            x, y = int(r["x"]), int(r["y"])
            nazwa = str(r.get("name") or "?")
            rozmiar = int(r.get("size") or 0)
            teraz = str(r.get("currently_building_name") or "")
            blds = set(s._bits(r.get("improvements")))
            tarcze = int(r.get("last_turns_shield_surplus") or 0)
            brak_darmowych = [b for b in darmowe if b.name not in blds]

            if obronca and not garnizon.get((x, y)):
                co, why = obronca.name, (
                    f"miasto bez garnizonu — {obronca.build_cost} tarcz daje "
                    f"obronę, stan wojenny i podwojony koszt przekupienia")
            elif potrzeba_dyplomaty and dyplomata and buduje_dyplomate == 0:
                co, why = dyplomata.name, (
                    f"zawieszenie broni z {wygasa[0][0]} wygasa do wojny za "
                    f"{wygasa[0][1]} tur — ambasada jest tańsza niż wojna")
                potrzeba_dyplomaty = False
            elif strategia == "wojna" and obroncy:
                zaczepny = max((u for u in rs.units_available(techs)
                                if buduje_sie(u) and "NonMil" not in u.flags),
                               key=lambda u: u.attack, default=None)
                co = (zaczepny or obronca).name
                why = "trwa wojna — jednostki przed rozbudową"
            elif (strategia == "ekspansja" and osadnik
                  and rozmiar > osadnik.pop_cost and wolne >= 5):
                co, why = osadnik.name, (
                    f"{wolne} wolnych miejsc pod miasto — nowe miasto bije "
                    f"każdy budynek, a rozmiar {rozmiar} na to pozwala")
            elif robotnik and tur_zaleglosci > 12:
                co, why = robotnik.name, (
                    f"zaległość robót to {tur_zaleglosci} tur przy "
                    f"{robotnicy} robotnikach — teren czeka")
            elif brak_darmowych:
                b = brak_darmowych[0]
                co, why = (b.label or b.name), (
                    f"{b.build_cost} tarcz i ZERO utrzymania — jedyna kategoria "
                    f"budowli, która nie obciąża budżetu")
            elif osadnik and rozmiar > osadnik.pop_cost and wolne >= 5:
                co, why = osadnik.name, f"jeszcze {wolne} wolnych miejsc"
            elif robotnik:
                co, why = robotnik.name, "zawsze jest co ulepszać"
            else:
                co, why = "Coinage", "brak sensownego celu"

            plan.append({
                "miasto": nazwa, "rozmiar": rozmiar, "tarcz_na_ture": tarcze,
                "buduje_teraz": teraz, "buduj": co, "dlaczego": why,
                "zmiana": teraz != co,
                "garnizon": garnizon.get((x, y), 0),
                "budowle_bez_utrzymania_do_wziecia":
                    [b.label or b.name for b in brak_darmowych][:3],
            })

        return {
            "strategia": strategia,
            "sytuacja": {
                "wojna_z": wojna,
                "zawieszenie_broni_wygasa": [{"nacja": n, "za_tur": t}
                                             for n, t in wygasa],
                "obcych_jednostek_zdolnych_do_ataku": obce_zaczepne,
                "wolnych_miejsc_pod_miasto": wolne,
                "robotnikow": robotnicy,
                "zaleglosc_robot_w_turach": tur_zaleglosci,
                "miast_bez_garnizonu": sum(1 for p in plan if not p["garnizon"]),
            },
            "produkcja": plan,
            "do_zmiany": sum(1 for p in plan if p["zmiana"]),
            "zasada": (
                "kolejność potrzeb: garnizon → dyplomacja przed wygaśnięciem "
                "układu → strategia → budowle bez utrzymania → robotnicy"),
        }

    # ---------------------------------------------------------- plan badan

    STRATEGIE = {
        "gospodarka": {
            "opis": "złoto, handel, mniejsze marnotrawstwo, budowle bez utrzymania",
            "efekty": {"Output_Bonus": 3, "Output_Inc_Tile": 3, "Output_Add_Tile": 2,
                       "Output_Waste_Pct": 3, "Upkeep_Free": 3,
                       "Trade_Revenue_Bonus": 3, "Max_Trade_Routes": 2,
                       "Shield2Gold_Factor": 1, "Gov_Center": 3},
            "wyjscia": {"Gold": 3, "Trade": 3, "Luxury": 1, "Shield": 2,
                        "Science": 1, "Food": 1},
        },
        "nauka": {
            "opis": "biblioteki, uniwersytety, cudy naukowe",
            "efekty": {"Output_Bonus": 3, "Output_Add_Tile": 3,
                       "Tech_Parasite_Pct_Max": 4, "Give_Imm_Tech": 4,
                       "Output_Waste_Pct": 1},
            "wyjscia": {"Science": 4, "Trade": 2, "Gold": 1, "Shield": 1,
                        "Luxury": 0, "Food": 0},
        },
        "ekspansja": {
            "opis": "więcej miast, większe miasta, żywność",
            "efekty": {"Size_Adj": 4, "Size_Unlimit": 4, "Growth_Food": 3,
                       "Make_Content": 2, "Output_Add_Tile": 2,
                       "Empire_Size_Base": 3, "Health_Pct": 2, "Popcost_Free": 3},
            "wyjscia": {"Food": 4, "Shield": 1, "Trade": 1, "Gold": 1,
                        "Science": 1, "Luxury": 1},
        },
        "wojna": {
            "opis": "jednostki zaczepne, weterani, obrona",
            "efekty": {"Veteran_Build": 3, "Defend_Bonus": 3,
                       "Unit_Upkeep_Free_Per_City": 2, "Make_Content_Mil": 2,
                       "Martial_Law_Each": 2, "Unit_No_Lose_Pop": 2},
            "wyjscia": {"Shield": 3, "Gold": 1, "Food": 1, "Trade": 0,
                        "Science": 0, "Luxury": 0},
        },
    }

    def research_plan(self, rs, strategia: str = "gospodarka",
                      limit: int = 8) -> dict:
        """Kolejnosc badan pod obrana strategie, z uzasadnieniem liczbowym.

        Kazda technologia dostaje ocene za to, co odblokowuje: budowle wazymy
        ich efektami przez pryzmat strategii i karzemy za utrzymanie, jednostki
        po siile na tarcze, cudy dodatkowo za zasieg (efekt dzialajacy w calym
        panstwie jest wart wiecej niz w jednym miescie). Wynik dzielimy przez
        liczbe technologii do zdobycia, zeby porownywac cele blizsze z dalszymi.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        strategia = strategia if strategia in self.STRATEGIE else "gospodarka"
        waga = self.STRATEGIE[strategia]
        techs = _known_techs(s) - {"A_NONE"}
        gov = s.me.government or ""
        sec = s._sections[s.me.slot]
        rows = list(sec.table("c").dicts()) if sec.table("c") else []
        n_miast = max(1, len(rows))
        mine_blds: set[str] = set()
        for r in rows:
            mine_blds |= set(s._bits(r.get("improvements")))

        research = s.reg.get("research")
        row = list(research.table("r").dicts())[0] if research else {}
        tempo = sec.int("research.bulbs_last_turn")

        def ocena_budowli(b) -> tuple[float, list[str]]:
            punkty, czemu = 0.0, []
            for typ, lst in rs.effects_by_type.items():
                w = waga["efekty"].get(typ, 0)
                if not w:
                    continue
                for e in lst:
                    hit = [r for r in e.reqs if r.type == "Building"
                           and r.present and r.name == b.name]
                    if not hit:
                        continue
                    outs = [r.name for r in e.reqs if r.type == "OutputType"]
                    mnoz = max((waga["wyjscia"].get(o, 1) for o in outs),
                               default=1)
                    zasieg = hit[0].range.lower()
                    skala = n_miast if zasieg != "city" else 1
                    if b.is_wonder and zasieg == "city":
                        skala = 1
                    wartosc = w * mnoz * abs(e.value) / 50 * max(1, skala) ** 0.5
                    punkty += wartosc
                    czemu.append(f"{typ}{e.value:+d}"
                                 + (f" [{outs[0]}]" if outs else "")
                                 + (" w każdym mieście" if zasieg != "city" else ""))
            # utrzymanie boli tym bardziej, im wiecej miast
            punkty -= b.upkeep * n_miast * 0.4
            if b.upkeep == 0 and punkty > 0:
                czemu.append("zero utrzymania")
                punkty *= 1.4
            return punkty, czemu[:4]

        def ocena_jednostki(u) -> float:
            if "NoBuild" in u.flags or u.build_cost <= 0:
                return 0.0
            if strategia == "wojna":
                return (u.attack * 2 + u.defense) / max(1, u.build_cost) * 30
            if "Cities" in u.flags and strategia == "ekspansja":
                return 8.0
            if "TradeRoute" in u.flags and strategia == "gospodarka":
                return 10.0
            if "Settlers" in u.flags:
                return 3.0
            return 0.5

        kandydaci = []
        for name in rs.techs:
            if name in techs:
                continue
            lancuch = sorted(t for t in rs._closure(name, set())
                             if t in rs.techs and t not in techs)
            if not lancuch:
                continue
            punkty, powody = 0.0, []
            for b in rs.buildings.values():
                if name not in b.req_techs():
                    continue
                if not all(t in techs or t in lancuch for t in b.req_techs()):
                    continue
                p, czemu = ocena_budowli(b)
                if p > 0:
                    punkty += p
                    powody.append(f"{b.label or b.name}: " + ", ".join(czemu))
            for u in rs.units.values():
                if name not in u.req_techs():
                    continue
                if not all(t in techs or t in lancuch for t in u.req_techs()):
                    continue
                p = ocena_jednostki(u)
                if p > 1:
                    punkty += p
                    powody.append(f"{u.name} ({u.attack}/{u.defense}, "
                                  f"{u.build_cost} tarcz)")
            # ustroj to osobna kategoria: zdejmuje kary calego panstwa
            for g, wym in rs.gov_techs.items():
                if name in wym and g != gov:
                    kara_teraz = self._city_effect(rs, "Output_Penalty_Tile", "",
                                                   set(), gov, techs, mine_blds, 0)
                    kara_po = self._city_effect(rs, "Output_Penalty_Tile", "",
                                                set(), g, techs, mine_blds, 0)
                    mr_teraz = self._city_effect(rs, "Max_Rates", "", set(), gov,
                                                 techs, mine_blds, 0)
                    mr_po = self._city_effect(rs, "Max_Rates", "", set(), g,
                                              techs, mine_blds, 0)
                    zysk = (kara_teraz - kara_po) * 8 + (mr_po - mr_teraz) * 0.4
                    if zysk > 0:
                        punkty += zysk * n_miast ** 0.5
                        powody.append(
                            f"ustrój {g}: kara za kafel {kara_teraz}→{kara_po}, "
                            f"suwak {mr_teraz}→{mr_po}%")
            if punkty <= 0:
                continue
            kandydaci.append({
                "technologia": name,
                "technologii_do_zdobycia": len(lancuch),
                "lancuch": lancuch,
                "ocena": round(punkty, 1),
                "ocena_na_technologie": round(punkty / len(lancuch), 1),
                "tur_przy_obecnym_tempie": None,
                "dlaczego": powody[:4],
            })

        # koszt w turach: styl kosztu czytamy z regul
        import os
        from .registry import parse_file
        base, styl, box = 20, "Classic", 100
        path = os.path.join(rs.path, "game.ruleset")
        if os.path.exists(path):
            g = parse_file(path, base_dir=os.path.dirname(rs.path))
            r2 = g.get("research")
            if r2:
                base = r2.int("base_tech_cost", 20)
                styl = r2.str("tech_cost_style", "Classic")
        st = s.reg.get("settings")
        stbl = st.table("set") if st else None
        for x in (stbl.dicts() if stbl else []):
            if str(x.get("name")) == "sciencebox":
                box = int(x.get("value") or 100)

        znanych = len(techs)

        def koszt(ile: int) -> int:
            razem = 0
            for i in range(ile):
                reqs = znanych + i + 1
                if styl.lower().startswith("classic"):
                    c = base * (1 + reqs) * (1 + reqs) ** 0.5 / 2
                else:
                    c = base * reqs
                razem += int(c * box / 100)
            return razem

        for c in kandydaci:
            b = koszt(c["technologii_do_zdobycia"])
            c["bulbs"] = b
            c["tur_przy_obecnym_tempie"] = (max(1, -(-b // tempo))
                                            if tempo > 0 else None)
            if c["tur_przy_obecnym_tempie"]:
                c["ocena_na_ture"] = round(c["ocena"] / c["tur_przy_obecnym_tempie"], 2)

        kandydaci.sort(key=lambda c: -(c.get("ocena_na_ture") or
                                       c["ocena_na_technologie"]))
        return {
            "strategia": strategia,
            "co_to_znaczy": waga["opis"],
            "dostepne_strategie": {k: v["opis"] for k, v in self.STRATEGIE.items()},
            "znanych_technologii": znanych,
            "bulbs_na_ture": tempo,
            "badane_teraz": str(row.get("now_name") or "") or None,
            "kolejnosc": kandydaci[:limit],
            "jak_liczone": (
                "ocena to suma wartości tego, co technologia odblokowuje, ważona "
                "strategią; budowle karane za utrzymanie i premiowane za jego "
                "brak, efekty działające w całym państwie liczone wyżej niż "
                "jednomiastowe; wynik dzielony przez tury do zdobycia"),
        }

    # --------------------------------------------------------- plan na ture

    def turn_plan(self, rs, nastawienie: str = "auto") -> dict:
        """Co robic w tej turze: produkcja, podatki, badania, zloto.

        Rada zalezy od fazy gry, a faze rozpoznajemy z liczby miast, glebokosci
        drzewa i tego, czy trwa wojna - inaczej doradzalibysmy stolicy budowe
        cudow w turze pierwszej. Wszystkie progi (kara despotyzmu, maksymalny
        suwak, kara za wielkosc imperium) czytamy z regul, nie z pamieci.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        gov = s.me.government or ""
        techs = _known_techs(s) - {"A_NONE"}
        sec = s._sections[s.me.slot]
        rows = list(sec.table("c").dicts()) if sec.table("c") else []
        if not rows:
            return {"blad": "brak miast"}
        tmap = TerrainMap(s)

        mine_blds: set[str] = set()
        for r in rows:
            mine_blds |= set(s._bits(r.get("improvements")))
        # Animal Kingdom i barbarzyncy sa "w stanie wojny" od pierwszej tury -
        # to nie jest wojna, tylko dzika zwierzyna
        dzicy = {"Animal Kingdom", "Barbarian", "Pirate"}
        wojna = [s.players[sl].nation for sl in s.players
                 if sl != s.me.slot and s.me.diplomacy.get(sl) == "War"
                 and s.players[sl].nation not in dzicy]
        glebokosc = max((rs.tech_depth(t) for t in techs), default=0)
        n_miast = len(rows)

        # Nastawienie gracza jest nadrzedne wobec wykrytej fazy: ktos moze
        # prowadzic wojne i mimo to chciec rozwijac gospodarke, i odwrotnie.
        if n_miast <= 3 and glebokosc <= 3:
            faza = "zasiedlanie"
        elif glebokosc <= 6 and not wojna:
            faza = "rozbudowa"
        elif wojna:
            faza = "wojna"
        else:
            faza = "rozwoj"
        pokojowo = nastawienie == "pokojowe" or (
            nastawienie == "auto" and faza != "wojna")
        if nastawienie == "pokojowe" and faza == "wojna":
            faza = "rozwoj"

        # --- podatki
        maks = self._city_effect(rs, "Max_Rates", "", set(), gov, techs,
                                 mine_blds, 0) or 100
        kara_kafla = self._city_effect(rs, "Output_Penalty_Tile", "", set(),
                                       gov, techs, mine_blds, 0)
        e_base = self._city_effect(rs, "Empire_Size_Base", "", set(), gov,
                                   techs, mine_blds, 0)
        e_step = self._city_effect(rs, "Empire_Size_Step", "", set(), gov,
                                   techs, mine_blds, 0)
        zloto = sec.int("gold")
        obecne = {"podatki": sec.int("rates.tax"),
                  "nauka": sec.int("rates.science"),
                  "luksus": sec.int("rates.luxury")}
        if zloto < 30:
            rada_pod = {"podatki": maks, "nauka": 100 - maks, "luksus": 0,
                        "dlaczego": f"złota tylko {zloto} — najpierw wypłacalność"}
        elif faza in ("zasiedlanie", "rozbudowa"):
            rada_pod = {"podatki": 100 - maks, "nauka": maks, "luksus": 0,
                        "dlaczego": ("na tym etapie technologie są warte więcej "
                                     f"niż złoto; {gov} pozwala na {maks}%")}
        else:
            rada_pod = {"podatki": max(0, 100 - maks - 10), "nauka": maks,
                        "luksus": 10,
                        "dlaczego": "przy większym państwie trochę luksusu "
                                    "tanio kupuje spokój"}

        # --- badania: co odblokowuje ustroj lepszy od obecnego
        lepsze = []
        for g in rs.governments:
            if g in ("Anarchy", gov):
                continue
            mr = self._city_effect(rs, "Max_Rates", "", set(), g, techs,
                                   mine_blds, 0)
            eb = self._city_effect(rs, "Empire_Size_Base", "", set(), g, techs,
                                   mine_blds, 0)
            kp = self._city_effect(rs, "Output_Penalty_Tile", "", set(), g,
                                   techs, mine_blds, 0)
            if mr > maks or eb > e_base or kp < kara_kafla:
                # wymagania bierzemy z regul ustroju, bo nazwa ustroju nie
                # musi sie pokrywac z nazwa technologii (Republic / The Republic)
                brak = sorted({t for wym in rs.gov_techs.get(g, [])
                               for t in rs._closure(wym, set())
                               if t in rs.techs and t not in techs})
                lepsze.append({"ustroj": g, "maks_suwak": mr,
                               "kara_za_miast": eb, "kara_kafla": kp,
                               "brakuje_technologii": brak,
                               "ile_technologii": len(brak)})
        # ustroj dostepny od reki to nie cel badan, tylko decyzja do podjecia
        od_reki = [x for x in lepsze if not x["ile_technologii"]]
        do_zbadania = [x for x in lepsze if x["ile_technologii"]]
        # wsrod tych do zbadania liczy sie zysk na technologie, a zdjecie kary
        # za kafel wazy wiecej niz kilka punktow suwaka
        do_zbadania.sort(key=lambda x: (
            x["ile_technologii"] + (0 if x["kara_kafla"] < kara_kafla else 3),
            -x["maks_suwak"]))
        lepsze = do_zbadania

        cel_badan = None
        if do_zbadania:
            cel = do_zbadania[0]
            cel_badan = {
                "cel": cel["ustroj"],
                "brakuje": cel["brakuje_technologii"],
                "dlaczego": (
                    f"{gov} ogranicza suwak do {maks}% i nakłada karę "
                    f"{kara_kafla} na kafel; {cel['ustroj']} daje "
                    f"{cel['maks_suwak']}% i karę {cel['kara_kafla']}"
                    if kara_kafla else
                    f"{cel['ustroj']} podnosi suwak do {cel['maks_suwak']}% "
                    f"i próg kary za wielkość do {cel['kara_za_miast']} miast"),
            }

        # --- produkcja miasto po miescie
        dostepne = {u.name for u in rs.units_available(techs)}
        def da_sie_zbudowac(u) -> bool:
            return (u.build_cost > 0 and "NoBuild" not in u.flags
                    and "NonMil" not in u.flags)

        tanie_obronne = sorted(
            (u for u in rs.units_available(techs)
             if u.defense > 0 and da_sie_zbudowac(u)
             and rs.uclass_of(u).name in ("Land", "Small Land", "Big Land")),
            key=lambda u: (u.build_cost / max(1, u.defense)))
        obronca = tanie_obronne[0].name if tanie_obronne else None
        osadnik = next((u.name for u in rs.units_available(techs)
                        if "Cities" in u.flags and u.build_cost > 0
                        and "NoBuild" not in u.flags), None)
        robotnik = next((u.name for u in rs.units_available(techs)
                         if "Settlers" in u.flags and "Cities" not in u.flags
                         and "AddToCity" not in u.flags and u.build_cost > 0
                         and "NoBuild" not in u.flags), None)

        ml = self._city_effect(rs, "Martial_Law_Each", "", set(), gov, techs,
                               mine_blds, 0)
        garnizon = collections.Counter()
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if ut and (ut.attack or ut.defense) and "NonMil" not in ut.flags:
                garnizon[(u.x, u.y)] += 1

        produkcja = []
        for r in rows:
            x, y = int(r["x"]), int(r["y"])
            nazwa = str(r.get("name") or "?")
            blds = set(s._bits(r.get("improvements")))
            teraz = str(r.get("currently_building_name") or "")
            ma_garnizon = garnizon.get((x, y), 0) > 0
            rozmiar = int(r.get("size") or 0)

            if not ma_garnizon and obronca:
                co, why = obronca, (
                    "brak garnizonu — jedna jednostka to obrona, stan wojenny "
                    "i podwojony koszt przekupienia miasta"
                    if ml else "brak garnizonu — miasto stoi otworem")
            elif faza == "zasiedlanie" and osadnik:
                co, why = osadnik, "więcej miast bije wszystko inne na starcie"
            elif pokojowo and robotnik:
                co, why = robotnik, (
                    "nastawienie pokojowe: robotnik zwraca się w każdej turze "
                    "irygacji i drogi, a nie kosztuje szczęścia w polu")
            elif faza in ("zasiedlanie", "rozbudowa") and osadnik and rozmiar >= 2:
                co, why = osadnik, "miasto urosło — stać je na osadnika"
            elif robotnik:
                co, why = robotnik, ("teren wokół daje mniej, niż mógłby — "
                                     "irygacja i drogi")
            else:
                co, why = (obronca or "Coinage"), "brak lepszego celu"

            brak_bud = [b.name for b in rs.buildings.values()
                        if not b.is_wonder and b.name not in blds
                        and all(t in techs for t in b.req_techs())
                        and b.upkeep == 0]
            produkcja.append({
                "miasto": nazwa, "rozmiar": rozmiar,
                "buduje_teraz": teraz,
                "buduj": co, "dlaczego": why,
                "ma_garnizon": ma_garnizon,
                "budynki_bez_utrzymania_do_wziecia": sorted(brak_bud)[:3],
                "zmiana": teraz != co,
            })

        inwestycje = []
        if zloto >= 100:
            inwestycje.append("dokupuj osadników w miastach, które i tak ich "
                              "budują — na starcie tura przewagi jest warta "
                              "więcej niż złoto")
        if kara_kafla:
            inwestycje.append(
                f"{gov} odbiera 1 z każdego kafla dającego więcej niż "
                f"{kara_kafla} — dlatego zmiana ustroju jest pilniejsza niż "
                f"jakikolwiek budynek")
        if n_miast >= e_base:
            inwestycje.append(
                f"masz {n_miast} miast przy progu {e_base} — od tego miejsca "
                f"każde kolejne miasto dokłada niezadowolonych co {e_step}")

        if pokojowo:
            inwestycje.append(
                "koszary kosztują złoto co turę i dają tylko stopień weterana "
                "— przy nastawieniu pokojowym buduj je wyłącznie tam, gdzie "
                "faktycznie rekrutujesz wojsko")
        return {
            "faza": faza,
            "nastawienie": "pokojowe" if pokojowo else "wojenne",
            "ustroj": gov, "miast": n_miast, "zloto": zloto,
            "wojna_z": wojna,
            "podatki_teraz": obecne,
            "podatki_rada": rada_pod,
            "maksymalny_suwak": maks,
            "kara_despotyzmu_na_kafel": kara_kafla or None,
            "badania_cel": cel_badan,
            "ustroje_dostepne_od_reki": [x["ustroj"] for x in od_reki],
            "lepsze_ustroje": lepsze[:3],
            "produkcja": produkcja,
            "do_zmiany": sum(1 for p in produkcja if p["zmiana"]),
            "inwestycje": inwestycje,
        }

    # -------------------------------------------------------- plan kampanii

    def campaign_plan(self, rs, tury: int = 2, rezerwa: int = 1) -> dict:
        """Rozkazy na te ture przy wojnie na kilka frontow.

        Laczy trzy rzeczy, ktore osobno nie wystarczaja: ile kosztuje zdobycie
        celu (silnik walki), ile jest wart (budynki, port, drogi, polaczenie
        z wlasna siecia) i czy w ogole zdazymy (koszt ruchu po heksie).
        Potem przydziela wojsko zachlannie - najpierw tam, gdzie stosunek
        wartosci do kosztu jest najlepszy, a cel jest osiagalny w tej turze.

        `rezerwa` to ile jednostek zostawiamy w kazdym zdobywanym miescie:
        garnizon podwaja koszt jego odkupienia i wlacza stan wojenny.
        """
        from .combat import Side, Situation, siege

        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        tmap = TerrainMap(s)
        ct = city_tiles(s)
        techs = _known_techs(s) - {"A_NONE"}
        gov = s.me.government or ""

        wrogowie = {slot for slot, st in
                    ((sl, s.me.diplomacy.get(sl)) for sl in s.players)
                    if st == "War" and slot != s.me.slot}
        if not wrogowie:
            return {"uwaga": "nie jesteś z nikim w stanie wojny",
                    "rozkazy": []}

        # --- moje jednostki zaczepne, pogrupowane
        moje = []
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if ut and ut.attack > 0 and "NonMil" not in ut.flags:
                moje.append((u, ut))
        if not moje:
            return {"fronty": sorted({s.players[sl].nation for sl in wrogowie
                                      if sl in s.players}),
                    "moich_zaczepnych": 0, "zaangazowanych": 0,
                    "w_rezerwie": 0, "rozkazy": [], "odlozone": [],
                    "uwaga": "nie masz jednostek zaczepnych — najpierw je zbuduj"}

        najlepszy = max((ut for _u, ut in moje), key=lambda t: t.attack)
        rows = list(s._sections[s.me.slot].table("c").dicts())
        mine_blds: set[str] = set()
        for r in rows:
            mine_blds |= set(s._bits(r.get("improvements")))
        wonders = {b for b in mine_blds
                   if rs.buildings.get(b) and rs.buildings[b].is_wonder}

        # --- cele
        cele = []
        for slot in wrogowie:
            sec = s._sections.get(slot)
            tbl = sec.table("c") if sec else None
            garnizon = collections.defaultdict(list)
            for u in s.units_of(slot):
                ut = rs.units.get(u.type)
                if ut and (ut.attack or ut.defense) and "NonMil" not in ut.flags:
                    garnizon[(u.x, u.y)].append(ut)
            for r in (tbl.dicts() if tbl else []):
                x, y = int(r.get("x") or 0), int(r.get("y") or 0)
                blds = set(s._bits(r.get("improvements")))
                rozmiar = int(r.get("size") or 0)
                obroncy = garnizon.get((x, y), [])

                terr = rs.terrains.get(tmap.terrain(x, y))
                if terr is None:
                    continue
                extras = {e for e in rs.extras if tmap.has_extra(e, x, y)}
                sit = Situation(
                    terrain=terr, extras=extras, in_city=True,
                    city_size=rozmiar,
                    buildings={b for b in blds
                               if rs.buildings.get(b)
                               and not rs.buildings[b].is_wonder},
                    player_buildings=set(), fortified=True,
                    gov="Despotism", techs=techs,
                    units_on_tile=max(1, len(obroncy)))

                if obroncy:
                    strony = [Side(utype=d, count=1, vet=0) for d in obroncy]
                    wynik = siege(rs, Side(utype=najlepszy, count=1, vet=2),
                                  strony, sit, trials=4000)
                    trzeba = wynik.attacks_for(0.90) or len(obroncy) * 3
                    straty = round(wynik.mean_losses, 2)
                else:
                    trzeba, straty = 1, 0.0

                # kto zdazy i skad
                w_zasiegu = []
                for u, ut in moje:
                    if self.geom.real_distance((x, y), (u.x, u.y)) > \
                            max(1, ut.move_rate) * (tury + 1) + 2:
                        continue
                    t_marsz = march_turns(rs, tmap, self.geom, ut, (u.x, u.y),
                                          (x, y), max_nodes=6000, cities=ct)
                    if t_marsz is not None and t_marsz < tury:
                        w_zasiegu.append((t_marsz, u, ut))
                w_zasiegu.sort(key=lambda z: (z[0], -z[2].attack))

                wart = self._wartosc_celu(rs, tmap, x, y, blds, rozmiar, rows)
                potrzeba = trzeba + rezerwa
                cele.append({
                    "nacja": s.players[slot].nation,
                    "miasto": str(r.get("name") or "?"),
                    "x": x, "y": y, "rozmiar": rozmiar,
                    "teren": terr.name,
                    "mury": "City Walls" in blds,
                    "obroncy": [d.name for d in obroncy],
                    "potrzeba_atakow_90proc": trzeba,
                    "z_rezerwa": potrzeba,
                    "srednie_straty": straty,
                    "wartosc": wart,
                    "_kandydaci": w_zasiegu,
                    "moich_w_zasiegu": len(w_zasiegu),
                    "najszybciej_tur": w_zasiegu[0][0] if w_zasiegu else None,
                })

        # --- przydzial: najpierw najlepszy stosunek wartosci do kosztu
        for c in cele:
            koszt = max(1, c["z_rezerwa"])
            c["oplacalnosc"] = round(c["wartosc"] / koszt, 2)
        cele.sort(key=lambda c: (c["najszybciej_tur"] is None,
                                 -c["oplacalnosc"]))

        zajete: set[int] = set()
        rozkazy, odlozone = [], []
        for c in cele:
            wolni = [(t, u, ut) for t, u, ut in c["_kandydaci"]
                     if id(u) not in zajete]
            if len(wolni) < c["z_rezerwa"]:
                odlozone.append({
                    "miasto": c["miasto"], "nacja": c["nacja"],
                    "powod": (f"w zasięgu {len(wolni)} jednostek, "
                              f"potrzeba {c['z_rezerwa']}"),
                    "wartosc": c["wartosc"],
                })
                continue
            wybrani = wolni[:c["z_rezerwa"]]
            for _t, u, _ut in wybrani:
                zajete.add(id(u))
            skad = collections.Counter(f"({u.x},{u.y})" for _t, u, _ut in wybrani)
            rozkazy.append({
                "cel": f"{c['nacja']} / {c['miasto']}",
                "x": c["x"], "y": c["y"],
                "teren": c["teren"], "mury": c["mury"],
                "obroncy": c["obroncy"] or ["brak — samo wejście"],
                "wyslij_jednostek": c["z_rezerwa"],
                "w_tym_do_walki": c["potrzeba_atakow_90proc"],
                "w_tym_garnizon": rezerwa,
                "skad": dict(skad.most_common(6)),
                "dotra_w_turach": max(t for t, _u, _ut in wybrani) + 1,
                "srednie_straty": c["srednie_straty"],
                "wartosc_zdobyczy": c["wartosc"],
                "oplacalnosc": c["oplacalnosc"],
            })

        wolnych = sum(1 for u, _ut in moje if id(u) not in zajete)
        for c in cele:
            c.pop("_kandydaci", None)
        return {
            "tura_zasiegu": tury,
            "fronty": sorted({c["nacja"] for c in cele}),
            "moich_zaczepnych": len(moje),
            "zaangazowanych": len(zajete),
            "w_rezerwie": wolnych,
            "rozkazy": rozkazy,
            "odlozone": odlozone,
            "zasada": (
                "kolejność wg wartości zdobyczy na jednostkę wysłaną; do każdej "
                "grupy doliczona rezerwa na garnizon, bo pusta zdobycz jest "
                "tania do odkupienia"),
        }

    def _wartosc_celu(self, rs, tmap, x, y, blds, rozmiar, moje_rows) -> int:
        """Ile państwo realnie zyska na zdobyciu tego miasta."""
        drogi = sum(1 for nb in self.geom.neighbours(x, y)
                    if tmap.has_road(*nb))
        nadmorskie = any(
            (t := rs.terrains.get(tmap.terrain(*nb))) is not None and not t.is_land
            for nb in self.geom.neighbours(x, y) if tmap.terrain(*nb))
        budynki = [b for b in blds
                   if rs.buildings.get(b) and not rs.buildings[b].is_wonder]
        stolica = next(((int(r["x"]), int(r["y"])) for r in moje_rows
                        if "Palace" in set(self.save._bits(r.get("improvements")))),
                       None)
        dyst = self.geom.real_distance((x, y), stolica) if stolica else 20
        return (rozmiar * 2 + len(budynki) * 3 + drogi
                + (4 if nadmorskie else 0) - dyst // 5)

    # ------------------------------------------------------- gotowosc wojenna

    def war_readiness(self, rs, nations: list[str], tury: int = 2) -> dict:
        """Czy uderzac teraz, czy czekac - liczby, nie opinia.

        Zbiera cztery rzeczy, ktore o tym decyduja, i kazda liczy z regul albo
        z zapisu: stan celow (mury, garnizony, co budują), zasieg wlasnych
        wojsk (realny ruch po heksie), koszt szczescia przy wymarszu garnizonow
        oraz to, co zmieni sie, jesli poczekasz.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        gov = s.me.government or ""
        techs = _known_techs(s) - {"A_NONE"}

        chcemy = {n.lower() for n in nations}
        slots = {slot: p.nation for slot, p in s.players.items()
                 if p.nation and p.nation.lower() in chcemy}
        if not slots:
            return {"blad": "nie znam tych nacji",
                    "dostepne": sorted(p.nation for p in s.players.values() if p.nation)}

        # --- moje jednostki bojowe i ich zasieg
        mine_units = []
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if ut and ut.attack > 0 and "NonMil" not in ut.flags:
                mine_units.append((u, ut))
        garrison = collections.Counter()
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if ut and (ut.attack or ut.defense) and "NonMil" not in ut.flags:
                garrison[(u.x, u.y)] += 1

        # --- cele
        tmap = TerrainMap(s)
        # Wartosc zdobyczy, nie tylko koszt zdobycia. Lekcja z kampanii, w
        # ktorej zdobyte miasta okazaly sie bezuzyteczne: bez drog, bez portu
        # i bez budynkow miasto jest obciazeniem, a nie nabytkiem.
        moje_kafle = {(int(r.get("x") or 0), int(r.get("y") or 0))
                      for r in (s._sections[s.me.slot].table("c").dicts()
                                if s._sections[s.me.slot].table("c") else [])}
        stolica = None
        for r in (s._sections[s.me.slot].table("c").dicts()
                  if s._sections[s.me.slot].table("c") else []):
            if "Palace" in set(s._bits(r.get("improvements"))):
                stolica = (int(r["x"]), int(r["y"]))
        siec = None
        try:
            merch = next((u for u in rs.units.values()
                          if "TradeRoute" in u.flags or "HelpWonder" in u.flags), None)
            if merch is not None:
                ok = passability(rs, tmap, rs.uclass_of(merch).name)
                siec = regions(tmap, ok, self.geom)
        except Exception:  # noqa: BLE001
            siec = None
        moje_obszary = {siec.get(k) for k in moje_kafle} if siec else set()

        def zdobycz(x: int, y: int, blds: set[str], rozmiar: int) -> dict:
            okolica = collections.Counter()
            drogi = 0
            for nb in self.geom.neighbours(x, y):
                nm = tmap.terrain(*nb)
                if nm:
                    okolica[nm] += 1
                if tmap.has_road(*nb):
                    drogi += 1
            nadmorskie = any(
                rs.terrains.get(tmap.terrain(*nb)) is not None
                and not rs.terrains[tmap.terrain(*nb)].is_land
                for nb in self.geom.neighbours(x, y) if tmap.terrain(*nb))
            dyst = (self.geom.real_distance((x, y), stolica)
                    if stolica else None)
            budynki = sorted(b for b in blds
                             if rs.buildings.get(b) and not rs.buildings[b].is_wonder)
            polaczone = (siec.get((x, y)) in moje_obszary) if siec else None
            # prosta ocena: co realnie dostaje panstwo
            ocena = rozmiar * 2 + len(budynki) * 3 + drogi
            if nadmorskie:
                ocena += 4
            if polaczone:
                ocena += 6
            if dyst is not None:
                ocena -= dyst // 5
            return {
                "budynki": budynki,
                "nadmorskie": nadmorskie,
                "drog_wokol": drogi,
                "polaczone_z_moja_siecia": polaczone,
                "dystans_do_mojej_stolicy": dyst,
                "otoczenie": dict(okolica.most_common(4)),
                "ocena_zdobyczy": ocena,
            }
        # przy pokoju wojsko NIE wejdzie na cudze terytorium (movement.c: MR_PEACE),
        # wiec czasy marszu podajemy w dwoch wariantach
        stany = {slot: (s.me.diplomacy.get(slot, "?") if s.me else "?")
                 for slot in slots}
        cele = []
        for slot, nation in slots.items():
            sec = s._sections.get(slot)
            tbl = sec.table("c") if sec else None
            wojska = collections.Counter()
            for u in s.units_of(slot):
                ut = rs.units.get(u.type)
                if ut and (ut.attack or ut.defense) and "NonMil" not in ut.flags:
                    wojska[(u.x, u.y)] += 1
            for r in (tbl.dicts() if tbl else []):
                x, y = int(r.get("x") or 0), int(r.get("y") or 0)
                blds = set(s._bits(r.get("improvements")))
                # zasieg liczymy KOSZTEM RUCHU, nie odlegloscia: wzgorza i las
                # kosztuja podwojnie, drogi prawie nic, a przy pokoju wojsko
                # w ogole nie wejdzie na cudze terytorium (movement.c: MR_PEACE)
                w_zasiegu = 0
                najblizej = None
                marsz = None
                marsz_pokoj = None
                for u, ut in mine_units:
                    d = self.geom.real_distance((x, y), (u.x, u.y))
                    najblizej = d if najblizej is None else min(najblizej, d)
                    if d > max(1, ut.move_rate) * (tury + 2) + 2:
                        continue          # zbyt daleko, zeby liczyc sciezke
                    t_marsz = march_turns(rs, tmap, self.geom, ut,
                                          (u.x, u.y), (x, y), max_nodes=8000)
                    if t_marsz is not None and stany.get(slot) == "Peace":
                        cudze = slot
                        blok = (lambda bx, by, owner=cudze:
                                tmap.owner(bx, by) == owner)
                        t_pokoj = march_turns(rs, tmap, self.geom, ut,
                                              (u.x, u.y), (x, y),
                                              max_nodes=8000, blocked=blok)
                        marsz_pokoj = (t_pokoj if marsz_pokoj is None
                                       else min(marsz_pokoj, t_pokoj)) \
                            if t_pokoj is not None else marsz_pokoj
                    if t_marsz is None:
                        continue
                    marsz = t_marsz if marsz is None else min(marsz, t_marsz)
                    if t_marsz < tury:
                        w_zasiegu += 1
                rozmiar = int(r.get("size") or 0)
                cele.append({
                    "nacja": nation,
                    "miasto": str(r.get("name") or "?"),
                    "x": x, "y": y,
                    "rozmiar": rozmiar,
                    # citytools.c: przy rozmiarze 1 miasto znika zamiast zmienic wlasciciela
                    "zniknie_przy_zdobyciu": rozmiar <= 1,
                    "mury": "City Walls" in blds,
                    "obroncow": wojska.get((x, y), 0),
                    "buduje": str(r.get("currently_building_name") or ""),
                    "moich_w_zasiegu": w_zasiegu,
                    "najblizszy_dystans": najblizej,
                    "tur_marszu": marsz,
                    "wartosc_zdobyczy": zdobycz(x, y, blds, rozmiar),
                    "stan_dyplomatyczny": stany.get(slot, "?"),
                    **({"tur_marszu_bez_wypowiedzenia_wojny": marsz_pokoj}
                       if stany.get(slot) == "Peace" else {}),
                })
        cele.sort(key=lambda c: (c["obroncow"],
                                 99 if c["tur_marszu"] is None else c["tur_marszu"]))

        # --- koszt szczescia: ktore miasta wisza na stanie wojennym
        base = self._city_effect(rs, "City_Unhappy_Size", "", set(), gov,
                                 techs, set(), 0)
        e_base = self._city_effect(rs, "Empire_Size_Base", "", set(), gov,
                                   techs, set(), 0)
        e_step = self._city_effect(rs, "Empire_Size_Step", "", set(), gov,
                                   techs, set(), 0)
        sec = s._sections[s.me.slot]
        rows = list(sec.table("c").dicts()) if sec.table("c") else []
        n_miast = len(rows)
        kara = 0
        if e_base > 0 and n_miast > e_base:
            kara = 1 + ((n_miast - e_base) // e_step if e_step > 0 else 0)
        zadowolonych_bazowo = max(0, base - kara)

        ml_each = self._city_effect(rs, "Martial_Law_Each", "", set(), gov,
                                    techs, set(), 0)
        ml_max = self._city_effect(rs, "Martial_Law_Max", "", set(), gov,
                                   techs, set(), 0)

        mine_blds: set[str] = set()
        for r in rows:
            mine_blds |= set(s._bits(r.get("improvements")))

        ryzyko = []
        for r in rows:
            x, y = int(r.get("x") or 0), int(r.get("y") or 0)
            size = int(r.get("size") or 0)
            blds = set(s._bits(r.get("improvements")))
            z_budynkow = self._city_effect(rs, "Make_Content", "", blds, gov,
                                           techs, mine_blds, size)
            wojsk = garrison.get((x, y), 0)
            ml_teraz = min(wojsk, ml_max) * ml_each if ml_each else 0
            teraz = zadowolonych_bazowo + z_budynkow + ml_teraz
            po_wymarszu = zadowolonych_bazowo + z_budynkow
            ryzyko.append({
                "miasto": str(r.get("name") or "?"),
                "rozmiar": size,
                "garnizon": wojsk,
                "zadowolonych_teraz": teraz,
                "zadowolonych_po_wymarszu": po_wymarszu,
                "niepokrytych_po_wymarszu": max(0, size - po_wymarszu),
                "ma_swiatynie": "Temple" in blds,
            })
        ryzyko.sort(key=lambda c: -c["niepokrytych_po_wymarszu"])

        # jednostki w polu robia niezadowolonych w SWOIM miescie macierzystym;
        # Make_Content_Mil znosi czesc tego z urzedu
        unhappy_factor = max(1, self._city_effect(rs, "Unhappy_Factor", "", set(),
                                                  gov, techs, mine_blds, 0))
        content_mil = self._city_effect(rs, "Make_Content_Mil", "", set(), gov,
                                        techs, mine_blds, 0)
        limit_w_polu = content_mil // unhappy_factor
        nazwy = {int(r.get("id") or 0): str(r.get("name") or "?") for r in rows}
        w_polu = collections.Counter()
        for u in s.units_of(s.me.slot):
            ut = rs.units.get(u.type)
            if ut and getattr(ut, "uk_happy", 0) > 0:
                w_polu[nazwy.get(u.homecity, "(bez miasta macierzystego)")] += 1
        obciazenie = []
        for miasto, ile in w_polu.most_common():
            obciazenie.append({
                "miasto_macierzyste": miasto,
                "jednostek": ile,
                "wolno_bez_kosztu": limit_w_polu,
                "niezadowolonych_gdy_wszystkie_wyjda":
                    max(0, ile * unhappy_factor - content_mil),
            })

        # --- co zmieni czekanie
        mury_w_budowie = [c["miasto"] for c in cele if c["buduje"] == "City Walls"]
        osadnicy = [c["miasto"] for c in cele if "Settler" in c["buduje"]]

        puste = [c for c in cele if c["obroncow"] == 0]
        osiagalne = [c for c in cele if c["moich_w_zasiegu"] > 0]
        return {
            "cel_wojny": sorted(slots.values()),
            "stany_dyplomatyczne": {n: stany[sl] for sl, n in slots.items()},
            **({"uwaga_pokoj":
                "z częścią tych nacji masz POKÓJ — wojsko nie wejdzie na ich "
                "terytorium, dopóki nie wypowiesz wojny (movement.c: MR_PEACE). "
                "Podane tury marszu zakładają, że wojna już trwa."}
               if any(v == "Peace" for v in stany.values()) else {}),
            "tury_zasiegu": tury,
            "moje_jednostki_bojowe": len(mine_units),
            "cele": cele,
            "podsumowanie_celow": {
                "miast_lacznie": len(cele),
                "zniknie_przy_zdobyciu":
                    [c["miasto"] for c in cele if c["zniknie_przy_zdobyciu"]],
                "bez_murow": sum(1 for c in cele if not c["mury"]),
                "bez_garnizonu": len(puste),
                "obroncow_lacznie": sum(c["obroncow"] for c in cele),
                "osiagalnych_w_tylu_turach": len(osiagalne),
            },
            "koszt_szczescia": {
                "zasada": (f"{base} zadowolonych z definicji, minus {kara} kary "
                           f"za {n_miast} miast (próg {e_base}, krok {e_step})"),
                "zadowolonych_bazowo": zadowolonych_bazowo,
                "stan_wojenny": (f"{ml_each} za jednostkę, maksymalnie {ml_max}"
                                 if ml_each else "ten ustrój go nie ma"),
                "miasta": ryzyko,
                "miast_z_niedoborem_po_wymarszu":
                    sum(1 for c in ryzyko if c["niepokrytych_po_wymarszu"] > 0),
                "wojsko_w_polu": {
                    "zasada": (f"każda jednostka w polu robi {unhappy_factor} "
                               f"niezadowolonych, miasto znosi {content_mil} "
                               f"z urzędu → {limit_w_polu} jednostek na miasto "
                               f"macierzyste bez kosztu"),
                    "limit_na_miasto": limit_w_polu,
                    "wg_miasta_macierzystego": obciazenie,
                    "miast_ponad_limit":
                        sum(1 for x in obciazenie
                            if x["niezadowolonych_gdy_wszystkie_wyjda"] > 0),
                },
                "czego_nie_liczymy": (
                    "luksusu z podatków — zapis nie zawiera handlu miasta, "
                    "więc realny margines jest wyższy niż tu pokazany"),
            },
            "co_zmieni_czekanie": {
                "buduja_mury": mury_w_budowie,
                "buduja_osadnikow": osadnicy,
                "uwaga": ("każda tura zwłoki to nowe miasta wroga i mury tam, "
                          "gdzie ich jeszcze nie ma"),
            },
        }

    # ------------------------------------------------------- korupcja i plan

    def _city_effect(self, rs, etype: str, output: str, blds: set[str],
                     gov: str, techs: set[str], mine: set[str],
                     size: int = 0) -> int:
        """Suma efektow danego typu dla miasta - jak get_city_output_bonus().

        Obsluguje warunki, ktore realnie wystepuja w regulach marnotrawstwa:
        rodzaj produkcji, ustroj, technologie, budynki (w miescie i u gracza)
        oraz minimalny rozmiar miasta.
        """
        total = 0
        for eff in rs.effects_by_type.get(etype, []):
            ok = True
            for r in eff.reqs:
                if r.type == "OutputType":
                    got = r.name.lower() == output.lower()
                elif r.type == "Gov":
                    got = r.name == gov
                elif r.type == "Tech":
                    got = r.name in techs
                elif r.type == "Building":
                    pool = blds if r.range.lower() == "city" else mine
                    got = r.name in pool
                elif r.type == "MinSize":
                    got = size >= int(r.name) if str(r.name).isdigit() else False
                elif r.type == "NationGroup":
                    got = False
                else:
                    got = False          # nieznany warunek - nie zaliczamy
                if got != r.present:
                    ok = False
                    break
            if ok:
                total += eff.value
        return total

    def corruption(self, rs) -> dict:
        """Ile kazde miasto traci na marnotrawstwie i co to zmieni.

        Wzor jeden do jednego z common/city.c: city_waste(). Poziom strat to
        stala od ustroju plus skladnik od odleglosci do najblizszego osrodka
        wladzy; budynki (ratusz, palac) zbijaja gotowa strate o procent.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        gov = s.me.government or ""
        techs = _known_techs(s) - {"A_NONE"}
        sec = s._sections[s.me.slot]
        tbl = sec.table("c")
        rows = list(tbl.dicts()) if tbl else []

        cities = []
        mine: set[str] = set()
        for r in rows:
            blds = set(s._bits(r.get("improvements")))
            mine |= blds
            cities.append({
                "nazwa": str(r.get("name") or "?"),
                "x": int(r.get("x") or 0), "y": int(r.get("y") or 0),
                "rozmiar": int(r.get("size") or 0),
                "budynki": blds,
                "nadwyzka_tarcz": int(r.get("last_turns_shield_surplus") or 0),
                "buduje": str(r.get("currently_building_name") or ""),
            })

        # osrodki wladzy: budynki dajace efekt Gov_Center
        centers = []
        for c in cities:
            if self._city_effect(rs, "Gov_Center", "", c["budynki"], gov,
                                 techs, mine, c["rozmiar"]) > 0:
                centers.append(c)

        def waste_for(c, output: str, extra: set[str] = frozenset()) -> dict:
            blds = c["budynki"] | set(extra)
            base = self._city_effect(rs, "Output_Waste", output, blds, gov,
                                     techs, mine, c["rozmiar"])
            by_d = self._city_effect(rs, "Output_Waste_By_Distance", output,
                                     blds, gov, techs, mine, c["rozmiar"])
            pct = self._city_effect(rs, "Output_Waste_Pct", output, blds, gov,
                                    techs, mine, c["rozmiar"])
            dist = None
            if by_d > 0:
                if not centers:
                    return {"procent": 100, "dystans": None,
                            "uwaga": "brak ośrodka władzy — przepada wszystko"}
                dist = min(self.geom.real_distance((c["x"], c["y"]),
                                                   (g["x"], g["y"]))
                           for g in centers)
                base += by_d * dist // 100
            level = max(0, base)
            level -= level * pct // 100
            return {"procent": min(100, max(0, level)), "dystans": dist}

        out_rows = []
        for c in cities:
            sh = waste_for(c, "Shield")
            tr = waste_for(c, "Trade")
            sh_ct = waste_for(c, "Shield", {"Courthouse"})
            tr_ct = waste_for(c, "Trade", {"Courthouse"})
            has_ct = "Courthouse" in c["budynki"]
            # ile tarcz brutto potrzeba, by zostala obecna nadwyzka
            netto = c["nadwyzka_tarcz"]
            brutto = round(netto / (1 - sh["procent"] / 100)) if sh["procent"] < 100 else None
            zysk = None
            if not has_ct and brutto:
                zysk = round(brutto * (sh["procent"] - sh_ct["procent"]) / 100)
            out_rows.append({
                "miasto": c["nazwa"], "x": c["x"], "y": c["y"],
                "rozmiar": c["rozmiar"],
                "dystans_do_wladzy": sh["dystans"],
                "marnuje_tarcz_proc": sh["procent"],
                "marnuje_handlu_proc": tr["procent"],
                "nadwyzka_tarcz": netto,
                "ma_ratusz": has_ct,
                "z_ratuszem_tarcze_proc": sh_ct["procent"],
                "z_ratuszem_handel_proc": tr_ct["procent"],
                "ratusz_odzyska_tarcz": zysk,
                "buduje": c["buduje"],
            })
        out_rows.sort(key=lambda r: -r["marnuje_tarcz_proc"])

        ct = rs.buildings.get("Courthouse")
        oplacalne = [r for r in out_rows
                     if not r["ma_ratusz"] and (r["ratusz_odzyska_tarcz"] or 0) > 0]
        for r in oplacalne:
            if ct and r["ratusz_odzyska_tarcz"]:
                r["ratusz_zwroci_sie_w_turach"] = (
                    -(-ct.build_cost // r["ratusz_odzyska_tarcz"]))
        return {
            "ustroj": gov,
            "osrodki_wladzy": [{"miasto": g["nazwa"], "x": g["x"], "y": g["y"]}
                               for g in centers],
            "budynki_znoszace_korupcje": self._anticorruption(rs, gov, techs),
            "miasta": out_rows,
            "ratusz_oplaca_sie_w": [r["miasto"] for r in
                                    sorted(oplacalne,
                                           key=lambda r: r.get("ratusz_zwroci_sie_w_turach", 999))],
        }

    def _anticorruption(self, rs, gov: str, techs: set[str]) -> list[dict]:
        """Co w tych regulach zbija marnotrawstwo - i czy juz to mam."""
        out = []
        for etype, opis in (("Output_Waste_Pct", "zbija gotową stratę"),
                            ("Gov_Center", "zeruje odległość (drugi ośrodek władzy)")):
            for eff in rs.effects_by_type.get(etype, []):
                names = [r.name for r in eff.reqs
                         if r.type == "Building" and r.present]
                if not names:
                    continue
                b = rs.buildings.get(names[0])
                if b is None:
                    continue
                outs = [r.name for r in eff.reqs if r.type == "OutputType"]
                out.append({
                    "budynek": b.label or b.name,
                    "nazwa_wewnetrzna": b.name,
                    "dziala_na": outs or ["wszystko"],
                    "efekt": f"-{eff.value}% ({opis})" if etype == "Output_Waste_Pct"
                             else opis,
                    "koszt": b.build_cost, "utrzymanie": b.upkeep,
                    "technologia": ", ".join(b.req_techs()) or "-",
                    "mam_technologie": all(t in techs for t in b.req_techs()),
                })
        # ustroje tez: ujemne Output_Waste_By_Distance znosi skladnik odleglosci
        for eff in rs.effects_by_type.get("Output_Waste_By_Distance", []):
            govs = [r.name for r in eff.reqs if r.type == "Gov" and r.present]
            if govs and eff.value < 0:
                outs = [r.name for r in eff.reqs if r.type == "OutputType"]
                out.append({"budynek": f"ustrój {govs[0]}",
                            "nazwa_wewnetrzna": govs[0],
                            "dziala_na": outs or ["wszystko"],
                            "efekt": "znosi cały składnik odległości",
                            "koszt": 0, "utrzymanie": 0,
                            "technologia": "-", "mam_technologie": True})
        return out


    # -------------------------------------------------- plan metropolia/kolonie

    def build_plan(self, rs, metropolia: str = "") -> dict:
        """Dzieli miasta na metropolie i kolonie i mowi, co gdzie budowac.

        Podzial nie jest arbitralny - wynika z tego, jak dziala dany efekt:
          * efekt liczony PROCENTEM od produkcji miasta (biblioteka, targ,
            fabryka) oplaca sie tam, gdzie produkcja jest duza -> metropolia;
          * efekt PLASKI (swiatynia, ratusz) daje tyle samo wszedzie -> kolonie;
          * cud o zasiegu "City" dziala tylko w swoim miescie -> metropolia;
            cud o zasiegu "Player"/"World" dziala wszedzie -> stawiaj tam,
            gdzie zbudujesz go najszybciej.
        Progi oplacalnosci liczymy z utrzymania: budynek za U zlota na ture
        musi dac wiecej niz U.
        """
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        gov = s.me.government or ""
        techs = _known_techs(s) - {"A_NONE"}
        sec = s._sections[s.me.slot]
        tbl = sec.table("c")
        rows = list(tbl.dicts()) if tbl else []
        if not rows:
            return {"blad": "brak miast"}

        mine: set[str] = set()
        cities = []
        for r in rows:
            blds = set(s._bits(r.get("improvements")))
            mine |= blds
            cities.append({
                "nazwa": str(r.get("name") or "?"),
                "x": int(r.get("x") or 0), "y": int(r.get("y") or 0),
                "rozmiar": int(r.get("size") or 0),
                "budynki": blds,
                "tarcze": int(r.get("last_turns_shield_surplus") or 0),
            })

        # metropolia: wskazana albo ta z palacem, a przy remisie najwieksza
        pick = None
        if metropolia:
            pick = next((c for c in cities
                         if c["nazwa"].lower() == metropolia.lower()), None)
            if pick is None:
                return {"blad": f"nie znam miasta {metropolia}",
                        "dostepne": sorted(c["nazwa"] for c in cities)}
        if pick is None:
            centra = [c for c in cities
                      if self._city_effect(rs, "Gov_Center", "", c["budynki"],
                                           gov, techs, mine, c["rozmiar"]) > 0]
            pool = centra or cities
            pick = max(pool, key=lambda c: (c["tarcze"], c["rozmiar"]))

        # --- klasyfikacja budynkow wprost z efektow
        # kultura wisi przy kazdym cudzie i nic nie mowi o zasiegu dzialania
        NIEISTOTNE = {"History"}
        SKALUJACE = {"Output_Bonus", "Output_Bonus_2", "Output_Per_Tile",
                     "Output_Inc_Tile", "Output_Waste_Pct", "Output_Add_Tile"}

        def klasyfikuj(b) -> dict:
            skala, plaskie = [], []
            w_miescie = u_gracza = False
            for etype, lst in rs.effects_by_type.items():
                for eff in lst:
                    hit = [r for r in eff.reqs
                           if r.type == "Building" and r.present
                           and r.name == b.name]
                    if not hit:
                        continue
                    opis = f"{etype} {eff.value:+d}"
                    (skala if etype in SKALUJACE else plaskie).append(opis)
                    if etype in NIEISTOTNE:
                        continue
                    if hit[0].range.lower() == "city":
                        w_miescie = True
                    else:
                        u_gracza = True
            zasieg = ("miasto" if w_miescie and not u_gracza else
                      "gracz" if u_gracza and not w_miescie else
                      "mieszany" if w_miescie else "—")
            if b.is_wonder:
                rola = ("metropolia" if zasieg == "miasto" else
                        "metropolia (część działa wszędzie)"
                        if zasieg == "mieszany" else "gdziekolwiek")
            elif skala and not plaskie:
                # +1 do kafla rosnie z liczba obrabianych kafli, ale dziala
                # tez w malym miescie - to nie to samo co bonus procentowy
                rola = ("najpierw metropolia"
                        if all(o.startswith("Output_Add_Tile") for o in skala)
                        else "metropolia")
            elif plaskie and not skala:
                rola = "wszędzie"
            elif skala:
                rola = "najpierw metropolia"
            else:
                rola = "wszędzie"
            return {"rola": rola, "skalujace": skala, "plaskie": plaskie,
                    "zasieg": zasieg}

        epoki = rs.eras()
        def epoka_of(b) -> str:
            d = max([rs.tech_depth(t) for t in b.req_techs()], default=0)
            return rs.era_at(d)["nazwa"]

        plan: dict[str, dict] = {}
        for name, b in rs.buildings.items():
            if name == "Coinage":
                continue
            k = klasyfikuj(b)
            ep = epoka_of(b)
            wpis = {
                "budynek": b.label or b.name,
                "nazwa_wewnetrzna": name,
                "koszt": b.build_cost,
                "utrzymanie": b.upkeep,
                "technologia": ", ".join(b.req_techs()) or "-",
                "mam_technologie": all(t in techs for t in b.req_techs()),
                "gdzie": k["rola"],
                "zasieg_efektu": k["zasieg"],
                "dlaczego": ("efekt procentowy od produkcji miasta"
                             if k["skalujace"] and not k["plaskie"]
                             else "efekt stały, taki sam w każdym mieście"
                             if k["plaskie"] and not k["skalujace"]
                             else "część efektu skaluje się z produkcją"),
                "efekty": k["skalujace"] + k["plaskie"],
                "cud": b.is_wonder,
                "mam_juz_w": sorted(c["nazwa"] for c in cities
                                    if name in c["budynki"]),
            }
            plan.setdefault(ep, {"budynki": [], "cuda": []})
            plan[ep]["cuda" if b.is_wonder else "budynki"].append(wpis)

        for ep in plan:
            for k in ("budynki", "cuda"):
                plan[ep][k].sort(key=lambda w: (not w["mam_technologie"],
                                                w["koszt"]))

        kolejnosc = [e["nazwa"] for e in epoki if e["nazwa"] in plan]
        kolejnosc += [e for e in plan if e not in kolejnosc]

        return {
            "metropolia": {"miasto": pick["nazwa"], "x": pick["x"],
                           "y": pick["y"], "rozmiar": pick["rozmiar"],
                           "tarcze_na_ture": pick["tarcze"]},
            "kolonie": sorted((c["nazwa"] for c in cities
                               if c["nazwa"] != pick["nazwa"])),
            "zasada": (
                "Cudy o zasięgu City stawiaj w metropolii — działają tylko "
                "tam. Cudy o zasięgu Player/World stawiaj w mieście, które "
                "zbuduje je najszybciej. Budynki z bonusem procentowym dają "
                "tyle, ile jest od czego liczyć — w małej kolonii +50% z 4 "
                "to +2. Budynki o stałym efekcie działają wszędzie tak samo."
            ),
            "epoki": [{"epoka": ep, **plan[ep]} for ep in kolejnosc],
        }

    # ----------------------------------------------------- drzewo technologii

    def tech_tree(self, rs, limit: int = 12) -> dict:
        """Twoje realne technologie, nie przyblizenie z suwaka.

        Badania czesto wyprzedzaja swoja epoke - gracz moze miec technologie
        z glebi drzewa, brakuje mu za to tanich z poczatku. Dlatego doradzamy
        z faktycznego zbioru, a nie z progu glebokosci.
        """
        s = self.save
        known = _known_techs(s)
        if not known:
            return {"blad": "zapis nie zawiera drzewa technologii"}
        real = {t for t in known if t != "A_NONE"}

        research = s.reg.get("research")
        row = list(research.table("r").dicts())[0] if research else {}
        sec = s._sections[s.me.slot] if s.me else None
        rate = sec.int("research.bulbs_last_turn") if sec else 0

        # koszt kolejnej technologii wg regul (styl Linear)
        import os
        from .registry import parse_file
        style, base, box = "Linear", 10, 100
        path = os.path.join(rs.path, "game.ruleset")
        if os.path.exists(path):
            g = parse_file(path, base_dir=os.path.dirname(rs.path))
            r = g.get("research")
            if r:
                style = r.str("tech_cost_style", "Linear")
                base = r.int("base_tech_cost", 10)
        st = s.reg.get("settings")
        stbl = st.table("set") if st else None
        for x in (stbl.dicts() if stbl else []):
            if str(x.get("name")) == "sciencebox":
                box = int(x.get("value") or 100)

        def cost_of(nth: int) -> int:
            """Koszt n-tej z kolei technologii."""
            if style.lower().startswith("linear"):
                return base * nth * box // 100
            return base * nth * box // 100      # inne style: to samo przyblizenie

        n_known = len(real)
        next_cost = cost_of(n_known + 1)
        have = int(row.get("bulbs") or 0)
        turns = None
        if rate > 0:
            turns = max(0, -(-(next_cost - have) // rate))

        # co odblokowuje dana brakujaca technologia
        def unlocks(tech: str) -> dict:
            plus = real | {tech}
            u = [x.name for x in rs.units.values()
                 if (x.attack or x.defense)
                 and tech in x.req_techs()
                 and all(t in plus for t in x.req_techs())]
            b = [x.name for x in rs.buildings.values()
                 if tech in x.req_techs() and all(t in plus for t in x.req_techs())]
            return {"jednostki": sorted(u),
                    "budynki": sorted(x for x in b if not rs.buildings[x].is_wonder),
                    "cuda": sorted(x for x in b if rs.buildings[x].is_wonder)}

        def missing_chain(tech: str) -> list[str]:
            need = rs._closure(tech, set())
            return sorted(t for t in need if t not in real)

        cands = []
        for name in rs.techs:
            if name in real:
                continue
            chain = missing_chain(name)
            if not chain:
                continue
            u = unlocks(name)
            waga = len(u["jednostki"]) + len(u["budynki"]) + 2 * len(u["cuda"])
            koszt = sum(cost_of(n_known + i + 1) for i in range(len(chain)))
            cands.append({
                "technologia": name,
                "brakuje_technologii": len(chain),
                "lancuch": chain,
                "koszt_bulbs": koszt,
                "tur_przy_obecnym_tempie": (max(1, -(-koszt // rate))
                                            if rate > 0 else None),
                "odblokowuje": u,
                "waga": waga,
            })
        # najpierw to, co cos daje i jest blisko
        cands.sort(key=lambda c: (c["brakuje_technologii"], -c["waga"],
                                  c["koszt_bulbs"]))
        useful = [c for c in cands if c["waga"] > 0][:limit]

        # technologie "wyprzedzajace epoke": glebsze niz mediana znanych
        depths = sorted(rs.tech_depth(t) for t in real)
        mediana = depths[len(depths) // 2] if depths else 0
        ahead = sorted(((rs.tech_depth(t), t) for t in real
                        if rs.tech_depth(t) > mediana + 3), reverse=True)

        return {
            "znanych_technologii": n_known,
            "epoka_wg_mediany": rs.era_at(mediana)["nazwa"],
            "epoka_wg_najglebszej": rs.era_at(depths[-1] if depths else 0)["nazwa"],
            "wyprzedzaja_epoke": [{"technologia": t, "glebokosc": d}
                                  for d, t in ahead[:10]],
            "badane_teraz": str(row.get("now_name") or "") or None,
            "cel_badan": str(row.get("goal_name") or "") or None,
            "bulbs_zebrane": have,
            "koszt_kolejnej": next_cost,
            "tempo_bulbs_na_ture": rate,
            "tur_do_konca": turns,
            "najblizsze_oplacalne": useful,
            "znane": sorted(real),
        }

    # ---------------------------------------------------------- handel

    def _continents(self) -> dict[tuple[int, int], int]:
        tmap = TerrainMap(self.save)
        ocean = {"Ocean", "Deep Ocean", "Lake", "Inaccessible"}

        def land(x, y):
            t = tmap.terrain(x, y)
            return t is not None and t not in ocean

        return regions(tmap, land, self.geom)

    def trade_routes(self, rs, limit: int = 15, full: bool = False,
                     only_overseas: bool = False) -> dict:
        """Proponuje szlaki handlowe wg regul danego zestawu.

        Typ trasy (krajowa / zagraniczna / miedzykontynentalna) decyduje
        o procencie wartosci - w wielu zestawach trasy miedzy wlasnymi
        miastami daja 0%, a miedzykontynentalne podwojna stawke.
        """
        import os
        import collections
        from .registry import parse_file
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}

        # zasady handlu z regul
        pct: dict[str, int] = {}
        bonus: dict[str, str] = {}
        path = os.path.join(rs.path, "game.ruleset")
        if os.path.exists(path):
            g = parse_file(path, base_dir=os.path.dirname(rs.path))
            sec = g.get("trade")
            tbl = sec.table("settings") if sec else None
            for r in (tbl.dicts() if tbl else []):
                pct[str(r.get("type"))] = int(r.get("pct") or 0)
                bonus[str(r.get("type"))] = str(r.get("bonus") or "")

        max_routes = 0
        for eff in rs.effects_by_type.get("Max_Trade_Routes", []):
            if not eff.reqs:
                max_routes = max(max_routes, eff.value)

        st = s.reg.get("settings")
        stbl = st.table("set") if st else None
        settings = {str(r.get("name")): r.get("value")
                    for r in (stbl.dicts() if stbl else [])}
        mindist = int(settings.get("trademindist") or 9)

        cont = self._continents()
        my = s.cities_of(s.me.slot)
        foreign = s.known_cities()
        if full:
            seen = {(c.x, c.y) for c in foreign}
            for p in s.players.values():
                if p.slot == s.me.slot:
                    continue
                for c in s.cities_of(p.slot):
                    if (c.x, c.y) not in seen:
                        foreign.append(c)

        nations = {p.slot: p.nation for p in s.players.values()}
        dipl = s.me.diplomacy

        # ile slotow juz zajete
        sec_me = s._sections[s.me.slot]
        ctbl = sec_me.table("c")
        used = collections.Counter()
        for r in (ctbl.dicts() if ctbl else []):
            n = 0
            for i in range(8):
                v = r.get(f"traderoute{i}")
                if v not in (None, 0, "0"):
                    n += 1
            used[str(r.get("name"))] = n

        def route_type(fc) -> str:
            same_cont = cont.get((fc.x, fc.y)) == cont.get(("__", "__"))
            return ""

        def dist(a, b) -> int:
            return self.geom.real_distance(a, b)

        cands = []
        for mc in my:
            mc_cont = cont.get((mc.x, mc.y))
            for fc in foreign:
                ic = cont.get((fc.x, fc.y)) != mc_cont
                rel = dipl.get(fc.owner, "?")
                if rel in ("Alliance",):
                    kind = "AllyIC" if ic else "Ally"
                elif rel in ("War",):
                    kind = "EnemyIC" if ic else "Enemy"
                elif rel == "Team":
                    kind = "TeamIC" if ic else "Team"
                else:
                    kind = "INIC" if ic else "IN"
                value_pct = pct.get(kind, 0)
                if value_pct <= 0:
                    continue
                if only_overseas and not ic:
                    continue
                d = dist((mc.x, mc.y), (fc.x, fc.y))
                if d < mindist:
                    continue
                # przyblizenie klasycznego wzoru: (dystans+10) * (handel obu
                # miast) / 24; handlu miast nie ma w zapisie, wiec bierzemy
                # rozmiar jako przyblizenie
                score = (d + 10) * (mc.size + fc.size) * value_pct // 2400
                cands.append({
                    "moje_miasto": mc.name, "rozmiar": mc.size,
                    "partner": fc.name, "nacja": nations.get(fc.owner, "?"),
                    "rozmiar_partnera": fc.size,
                    "stan_dyplomatyczny": rel,
                    "typ_trasy": kind, "procent_wartosci": value_pct,
                    "miedzykontynentalna": ic,
                    "dystans": d, "ocena": score, "_xy": (fc.x, fc.y),
                })
        cands.sort(key=lambda c: -c["ocena"])

        # przydzial zachlanny: limit tras na miasto
        picked = []
        per_city = collections.Counter()
        taken = set()
        for c in cands:
            if max_routes and used[c["moje_miasto"]] + per_city[c["moje_miasto"]] >= max_routes:
                continue
            key = (c["moje_miasto"], c["partner"])
            if key in taken:
                continue
            taken.add(key)
            per_city[c["moje_miasto"]] += 1
            picked.append(c)
            if len(picked) >= limit:
                break

        # --- ile tur karawana tam idzie
        # Wartosc trasy bez czasu dostawy jest myląca: 200% za trzydziesci tur
        # marszu jest gorsze niz 100% za piec. Klasa kupiecka chodzi wylacznie
        # po drogach, kolei i rzekach albo plynie statkiem - wiec liczymy trzy
        # warianty: ladem, morzem z portu do portu, oraz mieszany (dojscie do
        # wlasnego portu i przeprawa).
        tmap = TerrainMap(s)
        ct = city_tiles(s)
        merch = next((u for u in rs.units.values()
                      if "HelpWonder" in u.flags or "TradeRoute" in u.flags), None)
        znane = _known_techs(s)
        ship = next((u for u in sorted(rs.units.values(),
                                       key=lambda x: -x.move_rate)
                     if rs.uclass_of(u).name == "Sea" and "NonMil" in u.flags
                     and all(t in znane for t in u.req_techs())), None)

        def coastal(tile: tuple[int, int]) -> bool:
            for nb in self.geom.neighbours(*tile):
                name = tmap.terrain(*nb)
                terr = rs.terrains.get(name) if name else None
                if terr is not None and not terr.is_land:
                    return True
            return False

        moje_porty = [(m.name, (m.x, m.y)) for m in my if coastal((m.x, m.y))]

        def ladem(a, b):
            if merch is None:
                return None
            return march_turns(rs, tmap, self.geom, merch, a, b,
                               max_nodes=40000, cities=ct)

        def morzem(a, b):
            if ship is None or not (coastal(a) and coastal(b)):
                return None
            return march_turns(rs, tmap, self.geom, ship, a, b,
                               max_nodes=60000, cities=ct)

        def dostawa(a, b):
            warianty = []
            l = ladem(a, b)
            if l is not None:
                warianty.append((l + 1, "lądem po drogach", None))
            m = morzem(a, b)
            if m is not None:
                warianty.append((m + 2, "statkiem z portu", None))  # +zaladunek
            if not coastal(a):
                for nazwa, port in moje_porty:
                    do_portu = ladem(a, port)
                    if do_portu is None:
                        continue
                    rejs = morzem(port, b)
                    if rejs is None:
                        continue
                    warianty.append((do_portu + rejs + 3,
                                     f"lądem do portu {nazwa}, dalej statkiem",
                                     nazwa))
            if not warianty:
                return None, "brak połączenia dla klasy kupieckiej", None
            best = min(warianty)
            return best[0], best[1], best[2]

        for c in picked:
            a = next(((m.x, m.y) for m in my if m.name == c["moje_miasto"]), None)
            bxy = c.pop("_xy", None)
            if a is None or bxy is None:
                continue
            tur, jak, port = dostawa(a, bxy)
            c["tur_dostawy"] = tur
            c["czym"] = jak
            if port:
                c["przez_port"] = port
            c["moje_miasto_portowe"] = coastal(a)
            c["partner_portowy"] = coastal(bxy)
            c["ocena_na_ture"] = round(c["ocena"] / tur, 2) if tur else None
        for c in cands:
            c.pop("_xy", None)

        picked.sort(key=lambda c: -(c.get("ocena_na_ture") or 0))

        martwe = sorted({k for k, v in pct.items() if v <= 0})
        return {
            "tryb_wywiadu": "pełny wgląd (świadome chity)" if full
            else "tylko moja wiedza (mgła wojny)",
            "zasady": {k: {"procent": v, "premia_jednorazowa": bonus.get(k, "")}
                       for k, v in pct.items()},
            "bez_wartosci": martwe,
            "min_dystans": mindist,
            "max_tras_na_miasto": max_routes,
            "wolnych_slotow": sum(max(0, max_routes - used[c.name]) for c in my),
            "propozycje": picked,
            "kandydatow_lacznie": len(cands),
            "uwaga": ("Ocena jest przybliżona: zapis nie zawiera handlu miasta, "
                      "więc zamiast niego bierzemy rozmiar. Kolejność jest "
                      "wiarygodna, wartości bezwzględne nie."),
        }

    # ------------------------------------------------------ rozwiazywanie

    def disband_plan(self, rs, unit_types: list[str] | None = None,
                     full: bool = False) -> dict:
        """Co da rozwiazanie jednostek: tarcze, utrzymanie, zywnosc, gdzie.

        Procent zwrotu czytamy z regul (efekt Unit_Shield_Value_Pct przy akcji
        "Disband Unit Recover"), tak samo utrzymanie wg ustroju. Kandydatow
        dobieramy sami: jednostki odciete od wszystkich celow oraz bezczynne
        jednostki cywilne.
        """
        import collections
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}

        # ile procent kosztu wraca
        pct = 100
        for eff in rs.effects_by_type.get("Unit_Shield_Value_Pct", []):
            acts = [r.name for r in eff.reqs if r.type == "Action" and r.present]
            if "Disband Unit Recover" in acts:
                pct += eff.value
        pct = max(0, pct)

        gov = s.me.government
        out_type, free_per_city, factor = _upkeep_profile(rs, gov)

        units = s.units_of(s.me.slot)
        sec = s._sections[s.me.slot]
        ctbl = sec.table("c")
        crows = list(ctbl.dicts()) if ctbl else []
        city_by_id = {int(r["id"]): r for r in crows}
        by_home = collections.Counter(u.homecity for u in units)

        # cele: obszary, do ktorych warto docierac
        tmap = TerrainMap(s)
        enemy_spots = []
        for p in s.players.values():
            if p.slot == s.me.slot:
                continue
            cs = s.cities_of(p.slot) if full else [
                c for c in s.known_cities() if c.owner == p.slot]
            enemy_spots.extend((c.x, c.y) for c in cs)

        candidates: list[dict] = []
        chosen: list = []
        wanted = {str(t) for t in (unit_types or [])}

        # 1) jednostki odciete od wszystkich celow (klasa nie dojdzie)
        by_class: dict[str, list] = collections.defaultdict(list)
        for u in units:
            ut = rs.units.get(u.type)
            if ut:
                by_class[rs.uclass_of(ut).name].append(u)
        for cls, group in by_class.items():
            ok = passability(rs, tmap, cls)
            reg = regions(tmap, ok, self.geom)
            good = {reg.get(spot, 0) for spot in enemy_spots if reg.get(spot)}
            cut = [u for u in group if reg.get((u.x, u.y), 0) not in good]
            if not cut:
                continue
            for tname, n in collections.Counter(u.type for u in cut).items():
                if wanted and tname not in wanted:
                    continue
                ut = rs.units[tname]
                if ut.attack <= 0 and ut.defense <= 0:
                    continue                      # cywilne omijamy tutaj
                candidates.append({
                    "jednostka": tname, "klasa": cls, "sztuk": n,
                    "powod": "odcięta od wszystkich celów — nie dojdzie do walki",
                    "koszt_budowy": ut.build_cost,
                    "zwrot_tarcz": ut.build_cost * pct // 100 * n,
                    "zywnosc": getattr(ut, "uk_food", 0) * n,
                })
                chosen.extend([u for u in cut if u.type == tname])

        # 2) bezczynne jednostki cywilne
        idle = [u for u in units
                if u.activity == "Idle" and rs.units.get(u.type)
                and rs.units[u.type].attack == 0 and rs.units[u.type].defense <= 1]
        for tname, n in collections.Counter(u.type for u in idle).items():
            if wanted and tname not in wanted:
                continue
            ut = rs.units[tname]
            keep = 0 if wanted else min(n, 30)   # zapas na roboty
            drop = n - keep
            if drop <= 0:
                continue
            candidates.append({
                "jednostka": tname, "klasa": rs.uclass_of(ut).name, "sztuk": drop,
                "powod": f"bezczynne ({n} sztuk stoi, zostawiam {keep} w rezerwie)",
                "koszt_budowy": ut.build_cost,
                "zwrot_tarcz": ut.build_cost * pct // 100 * drop,
                "zywnosc": getattr(ut, "uk_food", 0) * drop,
            })
            chosen.extend([u for u in idle if u.type == tname][:drop])

        # oszczednosc na utrzymaniu: przed i po
        def upkeep(counter) -> int:
            return sum(max(0, counter.get(cid, 0) - free_per_city) * max(1, factor)
                       for cid in city_by_id)
        after = by_home.copy()
        for u in chosen:
            after[u.homecity] -= 1
        before_cost, after_cost = upkeep(by_home), upkeep(after)

        # gdzie rozwiazac: miasta bez kluczowych budynkow
        KEY = ["Library", "Temple", "Marketplace", "Granary", "Harbour",
               "Colosseum", "Aqueduct", "University", "Bank"]
        spots = []
        for r in crows:
            blds = set(s._bits(r.get("improvements")))
            missing = [k for k in KEY
                       if k in rs.buildings and k not in blds
                       and all(t in _known_techs(s) for t in rs.buildings[k].req_techs())]
            if not missing:
                continue
            cheapest = min(missing, key=lambda k: rs.buildings[k].build_cost)
            here = sum(1 for u in chosen
                       if (u.x, u.y) == (int(r["x"]), int(r["y"])))
            spots.append({
                "miasto": str(r.get("name")), "rozmiar": int(r.get("size") or 0),
                "brakuje": missing,
                "najtanszy_brak": cheapest,
                "koszt": rs.buildings[cheapest].build_cost,
                "buduje_teraz": str(r.get("currently_building_name") or "") or None,
                "jednostek_do_rozwiazania_na_miejscu": here,
            })
        spots.sort(key=lambda x: (-x["jednostek_do_rozwiazania_na_miejscu"],
                                  -x["rozmiar"]))

        total = sum(c["zwrot_tarcz"] for c in candidates)
        buys = []
        for k in KEY:
            b = rs.buildings.get(k)
            if b and b.build_cost and all(t in _known_techs(s) for t in b.req_techs()):
                brakuje = sum(1 for x in spots if k in x["brakuje"])
                if brakuje:
                    buys.append({"budynek": k, "koszt": b.build_cost,
                                 "brakuje_w_miastach": brakuje,
                                 "ile_za_zwrot": total // b.build_cost})
        buys.sort(key=lambda x: -x["brakuje_w_miastach"])

        return {
            "zwrot_procent": pct,
            "ustroj": gov,
            "utrzymanie_placone_w": {"Gold": "złocie", "Shield": "tarczach"}.get(
                out_type, out_type),
            "kandydaci": sorted(candidates, key=lambda c: -c["zwrot_tarcz"]),
            "razem_tarcz": total,
            "utrzymanie_teraz": before_cost,
            "utrzymanie_po": after_cost,
            "oszczednosc_na_ture": before_cost - after_cost,
            "uwolniona_zywnosc": sum(c["zywnosc"] for c in candidates),
            "gdzie_rozwiazac": spots[:12],
            "co_za_to_kupisz": buys[:6],
            "uwaga": ("Tarcze trafiają do miasta, w którym rozwiązujesz jednostkę, "
                      "i idą w bieżącą produkcję — ustaw docelowy budynek ZANIM "
                      "rozwiążesz."),
        }

    # -------------------------------------------------------------- miasta

    def cities_audit(self, rs) -> dict:
        """Wielkosc miast, limity wzrostu i ile jednostek jeszcze wyzywia.

        W wielu zestawach regul darmowe utrzymanie ZYWNOSCIOWE rosnie razem
        z miastem (efekt Unit_Upkeep_Free_Per_City dla OutputType:Food
        z warunkami MinSize), a limit wielkosci podnosza akwedukt i kanalizacja.
        Wszystko czytamy z regul - nic nie jest tu zaszyte.
        """
        import collections
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}

        # ile jednostek na zywnosci miasto utrzyma za darmo, wg wielkosci
        base = 0
        steps: list[int] = []
        for eff in rs.effects_by_type.get("Unit_Upkeep_Free_Per_City", []):
            types = [r.name for r in eff.reqs if r.type == "OutputType"]
            if types != ["Food"]:
                continue
            sizes = [int(r.name) for r in eff.reqs
                     if r.type == "MinSize" and str(r.name).isdigit()]
            govs = [r.name for r in eff.reqs if r.type == "Gov"]
            if govs:
                continue
            if sizes:
                steps.extend([sizes[0]] * eff.value)
            else:
                base += eff.value
        steps.sort()

        def free_food(size: int) -> int:
            return base + sum(1 for m in steps if size >= m)

        # limit wielkosci: Size_Adj sumuje sie, Size_Unlimit znosi limit
        def size_cap(buildings: set[str]) -> tuple[int, bool]:
            cap = 0
            for eff in rs.effects_by_type.get("Size_Adj", []):
                need = [r for r in eff.reqs if r.type == "Building"]
                if all((r.name in buildings) == r.present for r in need):
                    cap += eff.value
            unlimited = False
            for eff in rs.effects_by_type.get("Size_Unlimit", []):
                need = [r for r in eff.reqs if r.type == "Building"]
                if need and all((r.name in buildings) == r.present for r in need):
                    unlimited = True
            return cap, unlimited

        food_cost = {}
        for name, ut in rs.units.items():
            food_cost[name] = getattr(ut, "uk_food", 0)

        sec = s._sections[s.me.slot]
        tbl = sec.table("c")
        by_home = collections.defaultdict(list)
        for u in s.units_of(s.me.slot):
            by_home[u.homecity].append(u)

        rows = []
        for r in (tbl.dicts() if tbl else []):
            cid = int(r.get("id") or 0)
            size = int(r.get("size") or 0)
            blds = set(s._bits(r.get("improvements")))
            here = by_home.get(cid, [])
            eaters = sum(1 for u in here if food_cost.get(u.type, 0) > 0)
            ff = free_food(size)
            cap, unlimited = size_cap(blds)
            rows.append({
                "miasto": str(r.get("name") or "?"),
                "rozmiar": size,
                "jednostek": len(here),
                "jednostek_na_zywnosci": eaters,
                "darmowe_utrzymanie_zywnosci": ff,
                "zapas_do_limitu": ff - eaters,
                "deficyt_zywnosci": max(0, eaters - ff),
                "limit_wielkosci": "bez limitu" if unlimited else cap,
                "zapas_zywnosci": int(r.get("food_stock") or 0),
                "buduje": str(r.get("currently_building_name") or "") or None,
            })
        rows.sort(key=lambda x: (-x["deficyt_zywnosci"], x["zapas_do_limitu"]))

        # ktore jednostki w ogole jedza
        eat = sorted(n for n, c in food_cost.items() if c > 0)
        free_eat = sorted(n for n, c in food_cost.items()
                          if c == 0 and rs.units[n].build_cost > 0)
        return {
            "zasada_darmowego_utrzymania": (
                f"{base} jednostek na żywności za darmo, +1 za każdy rozmiar "
                f"od {steps[0] if steps else '-'} do {steps[-1] if steps else '-'}"),
            "przyklad": {f"rozmiar {n}": free_food(n) for n in (4, 8, 12, 16, 20, 24)},
            "jednostki_jedzace": eat[:40],
            "jednostki_bez_zywnosci": free_eat[:20],
            "miasta": rows,
            "miast_z_deficytem": sum(1 for x in rows if x["deficyt_zywnosci"]),
            "miast_na_granicy": sum(1 for x in rows if x["zapas_do_limitu"] == 0),
        }

    # ------------------------------------------------------------ przejezdnosc

    def reachability(self, rs, unit_types: list[str], full: bool) -> dict:
        """Czy jednostki danych typow w ogole dojda do celow.

        Klasy takie jak "Big Land" (katapulty, dziala) nie wchodza na bagna,
        dzungle i gory bez drogi - to czesto wazniejsze niz sama sila ataku.
        """
        import collections
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        tmap = TerrainMap(s)
        my_units = s.units_of(s.me.slot)

        types = unit_types or sorted({u.type for u in my_units})
        out: dict = {"tryb_wywiadu": "pełny wgląd (świadome chity)" if full
                     else "tylko moja wiedza (mgła wojny)", "jednostki": {}}

        targets = []
        for p in s.players.values():
            if s.me and p.slot == s.me.slot:
                continue
            cs = s.cities_of(p.slot) if full else [
                c for c in s.known_cities() if c.owner == p.slot]
            for c in cs:
                targets.append((p.nation, c.name, c.x, c.y))

        for tname in types:
            ut = rs.units.get(tname)
            if ut is None:
                continue
            uclass = rs.uclass_of(ut).name
            ok = passability(rs, tmap, uclass)
            reg = regions(tmap, ok, self.geom)
            mine = [u for u in my_units if u.type == tname]
            where = collections.Counter(reg.get((u.x, u.y), 0) for u in mine)
            entry = {
                "klasa": uclass,
                "sztuk": len(mine),
                "wchodzi_na": sorted(t.name for t in rs.terrains.values()
                                     if uclass in t.native_to and t.is_land),
                "nie_wchodzi_bez_drogi": sorted(
                    t.name for t in rs.terrains.values()
                    if t.is_land and uclass not in t.native_to),
                "moje_sztuki_wg_obszaru": [
                    {"obszar": k, "sztuk": v} for k, v in where.most_common()],
                "cele": [],
            }
            for nation, cname, x, y in targets:
                z = reg.get((x, y), 0)
                entry["cele"].append({
                    "nacja": nation, "miasto": cname,
                    "teren": tmap.terrain(x, y),
                    "obszar": z,
                    "moich_sztuk_w_tym_obszarze": where.get(z, 0),
                    "dojda": where.get(z, 0) > 0,
                })
            # dla odcietych grup policz, ile pracy kosztuje polaczenie ich drogą
            target_regions = {c["obszar"] for c in entry["cele"] if c["obszar"]}
            main = max(target_regions, key=lambda z: sum(
                1 for c in entry["cele"] if c["obszar"] == z), default=0)
            links = []
            for z, n in where.items():
                if z == main or z == 0 or n == 0:
                    continue
                spot = next(((u.x, u.y) for u in mine
                             if reg.get((u.x, u.y), 0) == z), None)
                if spot is None:
                    continue
                link = road_link(rs, tmap, ok, spot, main, reg, geom=self.geom)
                if link:
                    links.append({"obszar": z, "odcietych_sztuk": n, **link})
            if links:
                entry["polaczenia_drogowe"] = sorted(
                    links, key=lambda l: l["lacznie_tur_pracy"])
                entry["glowny_obszar"] = main

            entry["odcietych_sztuk"] = sum(
                v for k, v in where.items()
                if not any(c["obszar"] == k for c in entry["cele"]))
            out["jednostki"][tname] = entry
        return out

    # --------------------------------------------------------------- dystans

    def front(self, target: str, full: bool, max_distance: int = 12) -> dict:
        """Ktore moje miasta i jednostki sa najblizej celu."""
        s = self.save
        slot = s.nation_slot(target)
        if slot is None or s.me is None:
            return {"blad": f"nie ma nacji {target}"}
        targets = ([(c.name, c.x, c.y, c.size, c.walls)
                    for c in s.cities_of(slot)] if full else
                   [(c.name, c.x, c.y, c.size, c.walls)
                    for c in s.known_cities() if c.owner == slot])
        if not targets:
            return {"blad": f"nie znam żadnego miasta nacji {target}"}

        my_cities = s.cities_of(s.me.slot)
        my_units = s.units_of(s.me.slot)
        rows = []
        for name, x, y, size, walls in targets:
            near_c = sorted(((self.geom.real_distance((x, y), (c.x, c.y)), c)
                             for c in my_cities),
                            key=lambda t: t[0])[:3]
            near_u = [u for u in my_units
                      if self.geom.real_distance((x, y), (u.x, u.y)) <= max_distance]
            by_type: dict[str, int] = {}
            for u in near_u:
                by_type[u.type] = by_type.get(u.type, 0) + 1
            rows.append({
                "miasto_celu": name, "x": x, "y": y, "rozmiar": size, "mury": walls,
                "moje_najblizsze_miasta": [
                    {"nazwa": c.name, "dystans": d, "x": c.x, "y": c.y}
                    for d, c in near_c],
                "moje_jednostki_w_zasiegu": sorted(
                    ({"jednostka": k, "sztuk": v} for k, v in by_type.items()),
                    key=lambda e: -e["sztuk"]),
                "jednostek_w_zasiegu": len(near_u),
            })
        return {"cel": s.players[slot].nation,
                "tryb_wywiadu": "pełny wgląd (świadome chity)" if full
                else "tylko moja wiedza (mgła wojny)",
                "promien_zasiegu": max_distance,
                "fronty": sorted(rows, key=lambda r: -r["rozmiar"])}

    # ---------------------------------------------------------- robotnicy

    def worker_plan(self, rs, limit: int = 40) -> dict:
        """Gdzie stoja bezczynni robotnicy i co maja robic.

        Bezczynny robotnik nie jest neutralny - kosztuje `uk_shield` tarczy
        na ture, wiec pietnastu stojacych to pietnascie tarcz wyrzucanych
        co ture. Dlatego narzedzie liczy najpierw koszt, a dopiero potem
        podaje prace.

        Prace sortujemy wg tego, co dane miasto naprawde potrzebuje, a nie
        wg ogolnej zasady: kopalnia daje tarcze, ale miastu ustawionemu na
        wzrost nie da nic, bo ono tych kafli nie obrabia.

        Osobno liczymy siec drog, bo w tych regulach droga to nie ozdoba:
        klasa Merchant nie jest natywna dla ZADNEGO terenu i karawana bez
        ciaglej drogi nie ruszy sie z miejsca. Miasta poza siecia nie moga
        ani wyslac, ani przyjac karawany.
        """
        import collections
        import heapq

        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        sec = s._sections[s.me.slot]
        rows = list(sec.table("c").dicts()) if sec.table("c") else []
        if not rows:
            return {"blad": "brak moich miast"}

        tmap = TerrainMap(s)
        ct = city_tiles(s)
        road_time = _road_build_times(rs)
        mine_incr, road_pct = _tile_bonuses(rs)

        # --- kto w ogole umie pracowac
        robotnicy = [u for u in s.units_of(s.me.slot)
                     if u.type in rs.units
                     and ("Settlers" in rs.units[u.type].flags
                          or "Workers" in rs.units[u.type].roles)]
        if not robotnicy:
            robotnicy = [u for u in s.units_of(s.me.slot)
                         if u.type in rs.units
                         and "Settlers" in rs.units[u.type].flags]

        po_aktywnosci = collections.Counter(str(u.activity) for u in robotnicy)
        bezczynni = [u for u in robotnicy if str(u.activity) == "Idle"]
        koszt_bezczynnych = sum(rs.units[u.type].uk_shield for u in bezczynni)

        miasta = {int(r.get("id") or 0): r for r in rows}
        xy_miast = {(int(r["x"]), int(r["y"])): str(r.get("name")) for r in rows}

        # --- siec drog: ktore miasta sa polaczone
        wezly = list(xy_miast)
        rodzic = {w: w for w in wezly}

        def znajdz(a):
            while rodzic[a] != a:
                rodzic[a] = rodzic[rodzic[a]]
                a = rodzic[a]
            return a

        def polacz(a, b):
            ra, rb = znajdz(a), znajdz(b)
            if ra != rb:
                rodzic[ra] = rb

        # przechodzimy po kaflach z droga/rzeka i sklejamy miasta, ktore
        # laczy ciagla nitka - to jest dokladnie warunek ruchu karawany
        def przejezdny(t):
            return (t in ct or tmap.has_extra("Road", *t)
                    or tmap.has_extra("River", *t))

        odwiedzone = set()
        for start in wezly:
            if start in odwiedzone:
                continue
            stos, grupa = [start], []
            odwiedzone.add(start)
            while stos:
                cur = stos.pop()
                if cur in xy_miast:
                    grupa.append(cur)
                for nb in self.geom.neighbours(*cur):
                    if nb in odwiedzone or not przejezdny(nb):
                        continue
                    odwiedzone.add(nb)
                    stos.append(nb)
            for g in grupa[1:]:
                polacz(grupa[0], g)

        skupiska = collections.defaultdict(list)
        for w in wezly:
            skupiska[znajdz(w)].append(xy_miast[w])
        sieci = sorted(skupiska.values(), key=len, reverse=True)
        glowna = set(sieci[0]) if sieci else set()

        # --- ile pracy kosztuje dociagniecie miasta do glownej sieci
        def brak_do_sieci(start):
            pq = [(0, start)]
            best = {start: 0}
            while pq:
                c, cur = heapq.heappop(pq)
                if cur in xy_miast and xy_miast[cur] in glowna and cur != start:
                    return c, xy_miast[cur]
                if c > best.get(cur, 1 << 30):
                    continue
                for nb in self.geom.neighbours(*cur):
                    nm = tmap.terrain(*nb)
                    terr = rs.terrains.get(nm) if nm else None
                    if terr is None or not terr.is_land:
                        continue
                    nxt = c + (0 if przejezdny(nb)
                               else max(1, road_time.get(nm, 2)))
                    if nxt < best.get(nb, 1 << 30):
                        best[nb] = nxt
                        heapq.heappush(pq, (nxt, nb))
            return None, None

        odciete = []
        for w in wezly:
            nazwa = xy_miast[w]
            if nazwa in glowna:
                continue
            praca, do_kogo = brak_do_sieci(w)
            odciete.append({
                "miasto": nazwa,
                "tur_pracy_do_sieci": praca,
                "najblizsze_w_sieci": do_kogo,
                "uwaga": ("brak połączenia lądowego — drogi nie da się "
                          "poprowadzić" if praca is None else
                          "karawana stąd nie wyjedzie, dopóki nie ma drogi"),
            })
        odciete.sort(key=lambda r: (r["tur_pracy_do_sieci"] is None,
                                    r["tur_pracy_do_sieci"] or 0))

        # --- co robic konkretnym bezczynnym: bierzemy najblizsza sensowna
        #     robote, wazac to, czego miasto potrzebuje
        def prace_wokol(cx, cy, promien=3):
            out = []
            for dx in range(-promien, promien + 1):
                for dy in range(-promien - 1, promien + 2):
                    nb = ((cx + dx) % self.geom.xsize,
                          (cy + dy) % self.geom.ysize)
                    d = self.geom.real_distance((cx, cy), nb)
                    if d > promien:
                        continue
                    nm = tmap.terrain(*nb)
                    terr = rs.terrains.get(nm) if nm else None
                    if terr is None or not terr.is_land:
                        continue
                    wl = tmap.owner(*nb)
                    if wl is not None and wl != s.me.slot:
                        continue
                    if (not tmap.has_extra("Irrigation", *nb)
                            and (terr.irrigation_food_incr or 0) > 0):
                        out.append({
                            "kafel": list(nb), "teren": nm, "praca": "irygacja",
                            "daje": f"+{terr.irrigation_food_incr} żywności",
                            "tur_pracy": terr.irrigation_time, "dystans": d,
                            "waga": 3 * (terr.irrigation_food_incr or 0),
                        })
                    if not tmap.has_extra("Road", *nb) and nm not in ("Lake",):
                        handel = road_pct.get(nm, 0) // 100
                        out.append({
                            "kafel": list(nb), "teren": nm, "praca": "droga",
                            "daje": (f"+{handel} handlu" if handel
                                     else "ruch i przejezdność dla karawan"),
                            "tur_pracy": road_time.get(nm, 2), "dystans": d,
                            "waga": 2 + 2 * handel,
                        })
            out.sort(key=lambda r: (-r["waga"] / max(1, r["tur_pracy"]),
                                    r["dystans"]))
            return out

        lista = []
        for u in bezczynni[:limit]:
            dom = miasta.get(int(u.homecity or 0))
            blisko = min(rows, key=lambda r: self.geom.real_distance(
                (u.x, u.y), (int(r["x"]), int(r["y"]))))
            prace = prace_wokol(u.x, u.y, 3)[:3]
            lista.append({
                "jednostka": u.type, "gdzie_stoi": [u.x, u.y],
                "miasto_macierzyste": (str(dom.get("name")) if dom
                                       else "(bez miasta)"),
                "najblizsze_miasto": str(blisko.get("name")),
                "dystans_do_niego": self.geom.real_distance(
                    (u.x, u.y), (int(blisko["x"]), int(blisko["y"]))),
                "kosztuje_tarcz": rs.units[u.type].uk_shield,
                "proponowane_prace": prace or
                    ["brak pracy w promieniu 3 — przenieś albo rozwiąż"],
            })

        # --- gdzie stoja bezczynni, miastami
        wg_miast = collections.Counter()
        for u in bezczynni:
            blisko = min(rows, key=lambda r: self.geom.real_distance(
                (u.x, u.y), (int(r["x"]), int(r["y"]))))
            wg_miast[str(blisko.get("name"))] += 1

        return {
            "robotnikow_razem": len(robotnicy),
            "wg_aktywnosci": dict(po_aktywnosci),
            "bezczynnych": len(bezczynni),
            "bezczynni_kosztuja_tarcz_na_ture": koszt_bezczynnych,
            "strata_przez_20_tur": koszt_bezczynnych * 20,
            "bezczynni_wg_miast": dict(wg_miast.most_common()),
            "siec_drog": {
                "miast_w_glownej_sieci": len(glowna),
                "miast_razem": len(wezly),
                "grupy": [sorted(g) for g in sieci],
            },
            "miasta_poza_siecia": odciete,
            "bezczynni": lista,
        }

    # ------------------------------------------------------------ zarzadca

    # Kolejnosc wyjsc w CMA jest ta sama, co w silniku (common/fc_types.h):
    # O_FOOD, O_SHIELD, O_TRADE, O_GOLD, O_LUXURY, O_SCIENCE.
    CMA_WYJSCIA = ("jedzenie", "tarcze", "handel", "zloto", "luksus", "nauka")

    # Gotowe nastawy zarzadcy. Wagi sa wzgledne - liczy sie stosunek, nie
    # wartosc bezwzgledna. `minimalna_nadwyzka` to twardy warunek: zarzadca
    # odmowi ustawienia, ktore go lamie, wiec zbyt ostry prog wylacza CMA.
    NASTAWY = {
        "wzrost": {
            "opis": "maksymalna żywność — miasto ma rosnąć",
            "wagi": (10, 1, 1, 1, 1, 1), "min": (2, 0, 0, 0, 0, 0),
            "happy": 1,
        },
        "produkcja": {
            "opis": "maksymalne tarcze przy zerowym bilansie żywności",
            "wagi": (1, 10, 1, 1, 1, 1), "min": (0, 0, 0, 0, 0, 0),
            "happy": 1,
        },
        "handel": {
            "opis": "maksymalny handel — nauka i złoto",
            "wagi": (1, 1, 8, 1, 1, 1), "min": (0, 0, 0, 0, 0, 0),
            "happy": 1,
        },
        "luksus": {
            "opis": "luksus na zadowolenie — ratunek dla miasta w niepokojach",
            "wagi": (1, 1, 1, 1, 8, 1), "min": (0, 0, 0, 0, 0, 0),
            "happy": 5,
        },
        "zbalansowany": {
            "opis": "rośnie i produkuje jednocześnie",
            "wagi": (3, 2, 2, 1, 1, 1), "min": (1, 0, 0, 0, 0, 0),
            "happy": 1,
        },
    }

    def governor_plan(self, rs, cel_rozmiar: int = 12,
                      miasto: str = "") -> dict:
        """Jaka nastawe zarzadcy (CMA) dac kazdemu miastu i co ja blokuje.

        Pytanie "chce duzo metropolii powyzej 12" rozbija sie na trzy
        niezalezne sufity, a zarzadca rusza tylko jeden z nich:

          1. LIMIT WIELKOSCI - `Size_Adj`. Bez akweduktu miasto konczy na 8
             i zaden zarzadca tego nie obejdzie.
          2. ZYWNOSC - czy w promieniu 2 w ogole jest tyle jedzenia. To
             jedyny sufit, ktory zarzadca podnosi, przestawiajac obywateli
             z tarcz na jedzenie.
          3. SZCZESCIE - `City_Unhappy_Size` mowi, od ktorego obywatela
             zaczyna sie niezadowolenie; do tego dochodzi kara za wielkosc
             panstwa (`Empire_Size_Base`/`Step`). Pokrywa to zadowolenie
             z budynkow, prawo wojenne i luksus.

        Dlatego kazde miasto dostaje trzy sufity osobno i `waskie_gardlo`
        mowi, ktory z nich wiaze pierwszy - bo tylko na ten warto wydawac.
        """
        import collections
        import itertools

        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        sec = s._sections[s.me.slot]
        rows = {str(r.get("name")): r for r in (sec.table("c").dicts()
                                                if sec.table("c") else [])}
        if not rows:
            return {"blad": "brak moich miast"}
        if miasto and miasto not in rows:
            return {"blad": f"nie mam miasta {miasto}",
                    "dostepne": sorted(rows)}

        tmap = TerrainMap(s)
        techs = _known_techs(s) - {"A_NONE"}
        gov = s.me.government or ""
        mine_blds: set[str] = set()
        for r in rows.values():
            mine_blds |= set(s._bits(r.get("improvements")))
        mine_incr, road_pct = _tile_bonuses(rs)
        zasoby = _resource_yields(rs)
        liczba_miast = len(rows)

        def efekt(etype: str, blds: set[str], size: int = 0) -> int:
            """Suma efektu przy warunkach spelnionych dla tego miasta."""
            total = 0
            for eff in rs.effects_by_type.get(etype, []):
                ok = True
                for q in eff.reqs:
                    if q.type == "Gov":
                        got = (q.name == gov)
                    elif q.type == "Tech":
                        got = q.name in techs
                    elif q.type == "Building":
                        pool = blds if q.range.lower() == "city" else mine_blds
                        got = q.name in pool
                    elif q.type == "MinSize":
                        got = (size >= int(q.name)
                               if str(q.name).isdigit() else False)
                    else:
                        got = q.present          # warunków spoza tej listy nie
                    if got != q.present:         # rozstrzygamy - nie zgadujemy
                        ok = False
                        break
                if ok:
                    total += eff.value
            return total

        prog_nieszcz = self._unhappy_size(rs)
        baza = efekt("Empire_Size_Base", set()) or 0
        krok = efekt("Empire_Size_Step", set()) or baza or 0
        # kara za rozrost panstwa: co `krok` miast powyzej `baza` to jeden
        # dodatkowy niezadowolony w KAZDYM miescie
        kara_imperium = 0
        if baza and liczba_miast > baza:
            kara_imperium = 1 + (liczba_miast - baza - 1) // max(1, krok)

        wyniki = []
        for nm in ([miasto] if miasto else list(rows)):
            r = rows[nm]
            x, y = int(r["x"]), int(r["y"])
            size = int(r.get("size") or 0)
            blds = set(s._bits(r.get("improvements")))
            jedn = [u for u in s.units_of(s.me.slot)
                    if str(u.homecity) == str(r.get("id"))]
            wojsko_w_miescie = sum(
                1 for u in jedn
                if (u.x, u.y) == (x, y) and u.type in rs.units
                and rs.units[u.type].is_military)

            area = [((x + dx) % self.geom.xsize, (y + dy) % self.geom.ysize)
                    for dx in range(-3, 4) for dy in range(-4, 5)
                    if (dx, dy) != (0, 0)
                    and self.geom.real_distance(
                        (x, y), ((x + dx) % self.geom.xsize,
                                 (y + dy) % self.geom.ysize)) <= 2]
            port = "Harbour" in blds or "Harbor" in blds

            def yld(a, b):
                t = tmap.terrain(a, b)
                terr = rs.terrains.get(t) if t else None
                if terr is None:
                    return None
                f, sh, tr = terr.food, terr.shield, terr.trade
                for zn, (zf, zs, zt) in zasoby.items():
                    if (zf or zs or zt) and tmap.has_extra(zn, a, b):
                        f += zf; sh += zs; tr += zt
                        break
                if tmap.has_extra("River", a, b):
                    tr += 1
                if tmap.has_extra("Road", a, b):
                    tr += road_pct.get(t, 0) // 100
                if tmap.has_extra("Irrigation", a, b):
                    f += terr.irrigation_food_incr or 0
                if tmap.has_extra("Mine", a, b):
                    sh += mine_incr.get(t, 0)
                if port and "Sea" in terr.flags:
                    f += 1
                return f, sh, tr

            kafle = [v for v in (yld(a, b) for a, b in area) if v]
            centrum = yld(x, y) or (0, 0, 0)

            def najlepsza(sz: int, klucz):
                """Najlepsza obsada `sz` kafli wg podanego klucza, przy jedzeniu >= 0."""
                if sz > len(kafle):
                    return None
                best = None
                for combo in itertools.combinations(kafle, sz):
                    f = sum(v[0] for v in combo) + max(centrum[0], 1) - sz * 2
                    if f < 0:
                        continue
                    sh = sum(v[1] for v in combo) + max(centrum[1], 1)
                    tr = sum(v[2] for v in combo) + centrum[2]
                    kv = klucz(f, sh, tr)
                    if best is None or kv > best[0]:
                        best = (kv, (f, sh, tr))
                return best[1] if best else None

            # --- sufit 1: limit wielkosci
            cap, unlim = 0, False
            for eff in rs.effects_by_type.get("Size_Adj", []):
                need = [q for q in eff.reqs if q.type == "Building"]
                if all((q.name in blds) == q.present for q in need):
                    cap += eff.value
            for eff in rs.effects_by_type.get("Size_Unlimit", []):
                need = [q for q in eff.reqs if q.type == "Building"]
                if need and all((q.name in blds) == q.present for q in need):
                    unlim = True
            sufit_limit = 999 if unlim else cap

            # --- sufit 2: zywnosc
            sufit_jedzenie = 0
            for sz in range(1, min(len(kafle), 21) + 1):
                if najlepsza(sz, lambda f, sh, tr: (f, sh, tr)) is not None:
                    sufit_jedzenie = sz
                else:
                    break

            # --- sufit 3: szczescie
            content_bud = efekt("Make_Content", blds, size)
            martial_each = efekt("Martial_Law_Each", blds, size)
            martial_max = efekt("Martial_Law_Max", blds, size)
            content_mil = min(wojsko_w_miescie * martial_each,
                              martial_max * martial_each) if martial_each else 0
            sufit_szczescie = 0
            for sz in range(1, 41):
                niezadowoleni = max(0, sz - prog_nieszcz) + kara_imperium
                # luksus: dwa punkty luksusu robia jednego zadowolonego
                lux = najlepsza(sz, lambda f, sh, tr: (tr, f, sh))
                z_luksusu = (lux[2] // 2 // 2) if lux else 0
                if content_bud + content_mil + z_luksusu >= niezadowoleni:
                    sufit_szczescie = sz
                else:
                    break

            realny = min(sufit_limit, sufit_jedzenie, sufit_szczescie)
            waskie = min(
                (("limit wielkości", sufit_limit),
                 ("żywność", sufit_jedzenie),
                 ("szczęście", sufit_szczescie)), key=lambda e: e[1])[0]

            # --- jaka nastawe dac
            if realny <= size and waskie != "żywność":
                nastawa = "produkcja" if sufit_szczescie > size else "luksus"
            elif sufit_jedzenie > size and realny > size:
                nastawa = "wzrost"
            else:
                nastawa = "zbalansowany"
            if nastawa == "wzrost" and size >= cel_rozmiar:
                nastawa = "zbalansowany"

            n = self.NASTAWY[nastawa]
            podglad = {}
            for kl, klucz in (("wzrost", lambda f, sh, tr: (f, sh, tr)),
                              ("produkcja", lambda f, sh, tr: (sh, f, tr)),
                              ("handel", lambda f, sh, tr: (tr, f, sh))):
                v = najlepsza(size, klucz)
                podglad[kl] = ({"jedzenie": v[0], "tarcze": v[1], "handel": v[2]}
                               if v else "miasto się nie wyżywi")

            wyniki.append({
                "miasto": nm, "rozmiar": size,
                "zarzadca_wlaczony": str(r.get("cma_enabled")).lower() == "true",
                "sufit_limit_wielkosci": sufit_limit,
                "sufit_zywnosci": sufit_jedzenie,
                "sufit_szczescia": sufit_szczescie,
                "realny_sufit": realny,
                "waskie_gardlo": waskie,
                "dojdzie_do_celu": realny >= cel_rozmiar,
                "niezadowolonych_teraz": max(0, size - prog_nieszcz) + kara_imperium,
                "zadowolenie_z_budynkow": content_bud,
                "zadowolenie_z_prawa_wojennego": content_mil,
                "zalecana_nastawa": nastawa,
                "co_daje": n["opis"],
                "wagi_cma": dict(zip(self.CMA_WYJSCIA, n["wagi"])),
                "minimalna_nadwyzka_cma": dict(zip(self.CMA_WYJSCIA, n["min"])),
                "wspolczynnik_szczescia": n["happy"],
                "podglad_obsady": podglad,
            })

        wyniki.sort(key=lambda w: (-w["realny_sufit"], -w["rozmiar"]))
        blokady = collections.Counter(w["waskie_gardlo"] for w in wyniki)
        return {
            "cel_rozmiar": cel_rozmiar,
            "ustroj": gov, "miast": liczba_miast,
            "prog_niezadowolenia": prog_nieszcz,
            "kara_za_wielkosc_panstwa": kara_imperium,
            "empire_size_base": baza, "empire_size_step": krok,
            "miast_dojdzie_do_celu": sum(1 for w in wyniki
                                         if w["dojdzie_do_celu"]),
            "co_blokuje_ile_miast": dict(blokady),
            "kolejnosc_wyjsc_cma": list(self.CMA_WYJSCIA),
            "dostepne_nastawy": {k: v["opis"] for k, v in self.NASTAWY.items()},
            "miasta": wyniki,
        }

    # -------------------------------------------------- co budowac w miescie

    def city_build_advice(self, rs, miasto: str = "", limit: int = 8) -> dict:
        """Co konkretne miasto powinno zbudowac w tej turze - i dlaczego nie co innego.

        Kazda pozycja jest wyceniona z regul i ze stanu TEGO miasta, a nie
        z ogolnej zasady "swiatynia jest dobra". Rzeczy, ktore w tej sesji
        okazaly sie pulapkami, maja tu wlasne sprawdzenia:

          * spichlerz dziala na wzrost dopiero od rozmiaru z `MinSize`
            w Growth_Food i tylko gdy miasto MA nadwyzke zywnosci;
          * port daje `Output_Add_Tile` wylacznie na terenie z flaga Sea -
            jezioro sie nie liczy - wiec zysk liczymy przez ponowna obsade
            kafli, a nie po liczbie kafli morskich;
          * tanie akwedukty maja wymog "Adjacent", a na hexie sasiadow jest
            szesc, nie osiem;
          * budynek procentowy (biblioteka, targ) mnozy to, co miasto juz
            ma - w miescie o handlu 4 nie mnozy niczego;
          * mury nie daja zadowolenia - ten `Make_Content` nalezy do
            Mausoleum i tylko SPRAWDZA obecnosc murow;
          * cud o zasiegu City dziala w jednym miescie, o zasiegu Player
            w calym panstwie - to inna klasa inwestycji;
          * karawana ma sens tylko wtedy, gdy klasa Merchant faktycznie
            dojdzie do miasta budujacego cud.

        Zwraca liste `opcje` posortowana wg oceny, kazda z polem `dlaczego`
        i - gdy trzeba - `ostrzezenie`. Do tego `nie_oplaca_sie`, zeby bylo
        widac, co zostalo rozwazone i odrzucone.
        """
        import collections
        import itertools
        import os

        from .registry import parse_file

        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        sec = s._sections[s.me.slot]
        rows = {str(r.get("name")): r for r in (sec.table("c").dicts()
                                                if sec.table("c") else [])}
        if not rows:
            return {"blad": "brak moich miast"}
        wybrane = ([miasto] if miasto else list(rows))
        if miasto and miasto not in rows:
            return {"blad": f"nie mam miasta {miasto}",
                    "dostepne": sorted(rows)}

        tmap = TerrainMap(s)
        ct = city_tiles(s)
        techs = _known_techs(s) - {"A_NONE"}
        gov = s.me.government or ""
        mine_blds: set[str] = set()
        for r in rows.values():
            mine_blds |= set(s._bits(r.get("improvements")))
        mine_incr, road_pct = _tile_bonuses(rs)
        zasoby = _resource_yields(rs)

        # ustawienia partii, ktore zmieniaja werdykty
        st = s.reg.get("settings")
        stbl = st.table("set") if st else None
        setting = {str(r.get("name")): r.get("value")
                   for r in (stbl.dicts() if stbl else [])}
        gpath = os.path.join(rs.path, "game.ruleset")
        illness_on, illness_min = False, 99
        gr_ini: list[int] = []
        if os.path.exists(gpath):
            g = parse_file(gpath, base_dir=os.path.dirname(rs.path))
            ill = g.get("illness")
            if ill is not None:
                illness_on = str(ill.str("illness_on")).upper() in ("TRUE", "1")
                illness_min = ill.int("illness_min_size") or 99
            civ = g.get("civstyle")
            if civ is not None:
                gr_ini = [int(v) for v in civ.list("granary_food_ini")] or []
        # suwak podatkow jest w sekcji gracza, nie w ustawieniach serwera -
        # bez tego kazdy budynek mnozacy zloto wychodzil na zero
        tax = int(sec.get("rates.tax") or setting.get("taxrate") or 0)

        # ile wolnych miejsc pod miasta i ile pracy czeka - dla Settlers/Workers
        try:
            wzrost = self.growth_potential(rs, limit=99)
            praca_backlog = sum(int(c.get("tur_pracy_lacznie") or 0)
                                for c in wzrost.get("miasta", []))
        except Exception:  # noqa: BLE001
            praca_backlog = 0

        # miasta budujace cud - cel dla karawan
        merch = next((u for u in rs.units.values() if "HelpWonder" in u.flags),
                     None)
        budowane_cudy = {str(r.get("currently_building_name")): nm
                         for nm, r in rows.items()
                         if str(r.get("currently_building_kind")) == "Building"
                         and rs.buildings.get(
                             str(r.get("currently_building_name"))) is not None
                         and rs.buildings[
                             str(r.get("currently_building_name"))].is_wonder}
        cudowe = [(nm, r) for nm, r in rows.items()
                  if str(r.get("currently_building_kind")) == "Building"
                  and rs.buildings.get(str(r.get("currently_building_name")))
                  is not None
                  and rs.buildings[str(r.get("currently_building_name"))].is_wonder]

        wyniki = []
        for nm in wybrane:
            r = rows[nm]
            x, y = int(r["x"]), int(r["y"])
            size = int(r.get("size") or 0)
            blds = set(s._bits(r.get("improvements")))
            zapas = int(r.get("shield_stock") or 0)
            nadw = int(r.get("last_turns_shield_surplus") or 0)
            jedn = [u for u in s.units_of(s.me.slot)
                    if str(u.homecity) == str(r.get("id"))]
            kind_now = str(r.get("currently_building_kind") or "")

            # --- kafle i obsada
            area = [((x + dx) % self.geom.xsize, (y + dy) % self.geom.ysize)
                    for dx in range(-3, 4) for dy in range(-4, 5)
                    if (dx, dy) != (0, 0)
                    and self.geom.real_distance(
                        (x, y), ((x + dx) % self.geom.xsize,
                                 (y + dy) % self.geom.ysize)) <= 2]

            def yld(a, b, port: bool):
                t = tmap.terrain(a, b)
                terr = rs.terrains.get(t) if t else None
                if terr is None:
                    return None
                f, sh, tr = terr.food, terr.shield, terr.trade
                for zn, (zf, zs, zt) in zasoby.items():
                    if (zf or zs or zt) and tmap.has_extra(zn, a, b):
                        f += zf; sh += zs; tr += zt
                        break
                if tmap.has_extra("River", a, b):
                    tr += 1
                if tmap.has_extra("Road", a, b):
                    tr += road_pct.get(t, 0) // 100
                if tmap.has_extra("Irrigation", a, b):
                    f += terr.irrigation_food_incr or 0
                if tmap.has_extra("Mine", a, b):
                    sh += mine_incr.get(t, 0)
                if port and "Sea" in terr.flags:
                    f += 1
                return f, sh, tr

            def obsada(sz: int, port: bool):
                """Najwiecej tarcz przy nieujemnym bilansie zywnosci."""
                vs = [v for v in (yld(a, b, port) for a, b in area) if v]
                c = yld(x, y, port) or (0, 0, 0)
                if len(vs) < sz:
                    return None
                best = None
                for combo in itertools.combinations(vs, min(sz, len(vs))):
                    f = sum(v[0] for v in combo) + max(c[0], 1) - sz * 2
                    if f < 0:
                        continue
                    sh = sum(v[1] for v in combo) + max(c[1], 1)
                    tr = sum(v[2] for v in combo) + c[2]
                    if best is None or (sh, f, tr) > best:
                        best = (sh, f, tr)
                return best

            def max_jedzenie(sz: int, port: bool):
                """Obsada z najwieksza iloscia zywnosci - gdy nic nie wychodzi na plus."""
                vs = [v for v in (yld(a, b, port) for a, b in area) if v]
                c = yld(x, y, port) or (0, 0, 0)
                vs.sort(key=lambda v: (-v[0], -v[1], -v[2]))
                pk = vs[:sz]
                return (sum(v[1] for v in pk) + max(c[1], 1),
                        sum(v[0] for v in pk) + max(c[0], 1) - sz * 2,
                        sum(v[2] for v in pk) + c[2])

            stolica = next(((int(q["x"]), int(q["y"])) for q in rows.values()
                            if "Palace" in set(s._bits(q.get("improvements")))),
                           None)
            dystans = (self.geom.real_distance((x, y), stolica)
                       if stolica else 0)

            ma_port = "Harbour" in blds or "Harbor" in blds
            teraz = obsada(size, ma_port)
            glodowe = teraz is None
            if glodowe:
                # zadna obsada nie wychodzi na zero - miasto sie kurczy
                teraz = max_jedzenie(size, ma_port)
            handel = teraz[2] if teraz else 0

            # --- sasiedztwo dla tanich akweduktow (hex: szesc sasiadow)
            sasiedzi = list(self.geom.neighbours(x, y))
            ma_rzeke = tmap.has_extra("River", x, y) or any(
                tmap.has_extra("River", a, b) for a, b in sasiedzi)
            ma_jezioro = any(tmap.terrain(a, b) == "Lake" for a, b in sasiedzi)
            nadmorskie = any(
                (rs.terrains.get(tmap.terrain(a, b) or "") is not None
                 and not rs.terrains[tmap.terrain(a, b)].is_land)
                for a, b in sasiedzi)

            def spelnia(bl) -> tuple[bool, list[str]]:
                braki = []
                for q in bl.reqs:
                    if q.type == "Tech":
                        if (q.name in techs) != q.present:
                            braki.append(f"technologia {q.name}")
                    elif q.type == "Extra" and q.name == "River":
                        if ma_rzeke != q.present:
                            braki.append("rzeka obok" if q.present
                                         else "brak rzeki obok")
                    elif q.type == "Terrain" and q.name == "Lake":
                        if ma_jezioro != q.present:
                            braki.append("jezioro obok" if q.present
                                         else "brak jeziora obok")
                    elif q.type == "TerrainFlag" and q.name == "Sea":
                        if nadmorskie != q.present:
                            braki.append("dostęp do morza")
                    elif q.type == "Building":
                        pool = blds if q.range.lower() == "city" else mine_blds
                        if (q.name in pool) != q.present:
                            braki.append(f"budynek {q.name}")
                    elif q.type == "MinSize":
                        if str(q.name).isdigit() and (size >= int(q.name)) != q.present:
                            braki.append(f"rozmiar {q.name}")
                return (not braki), braki

            def tur(koszt: int) -> int | None:
                brak = max(0, koszt - zapas)
                return None if nadw <= 0 else max(1, -(-brak // nadw))

            opcje, odrzucone = [], []

            def dodaj(nazwa, koszt, upkeep, ocena, czemu, rodzaj,
                      ostrzezenie=None):
                t = tur(koszt)
                rec = {"co": nazwa, "rodzaj": rodzaj, "koszt_tarcz": koszt,
                       "utrzymanie_zl": upkeep, "tur": t,
                       "ocena": round(ocena, 2), "dlaczego": czemu}
                if ostrzezenie:
                    rec["ostrzezenie"] = ostrzezenie
                opcje.append(rec)

            # ---------------- budynki i cudy
            for bnm, bl in rs.buildings.items():
                if bnm in blds:
                    continue
                # budynek moze byc PRZESTARZALY - gra go wtedy nie pokaze.
                # Mausoleum znika po zdobyciu Sanitation, a narzedzie bez tego
                # sprawdzenia polecalo cud, ktorego nie da sie postawic.
                stary = (bl.przestarzaly(techs)
                         if hasattr(bl, "przestarzaly") else None)
                if stary:
                    odrzucone.append({
                        "co": bnm,
                        "powod": f"przestarzały — unieważniony przez {stary}"})
                    continue
                ok, braki = spelnia(bl)
                if not ok:
                    odrzucone.append({"co": bnm, "powod": "; ".join(braki)})
                    continue
                if bl.is_wonder and bnm in mine_blds:
                    odrzucone.append({"co": bnm, "powod": "już mam ten cud"})
                    continue

                ocena, czemu, ostrz = 0.0, [], None

                # --- port: policz FAKTYCZNY zysk przez ponowna obsade
                if bnm in ("Harbour", "Harbor"):
                    # porownujemy na obsadzie najedzeniowej, bo pytanie brzmi
                    # "czy port wyzywi miasto", a nie "ile da przy obecnym
                    # ustawieniu obywateli"
                    bez_f = max_jedzenie(size, False)[1]
                    z_f = max_jedzenie(size, True)[1]
                    zysk_f = z_f - bez_f
                    ratuje = bez_f < 0 <= z_f
                    dalej = 0
                    for sz in range(size + 1, min(size + 5, 21)):
                        if obsada(sz, True) and not obsada(sz, False):
                            dalej += 1
                    ocena = zysk_f * 2 + dalej * 3 + (8 if ratuje else 0)
                    czemu.append(f"bilans żywności {bez_f:+} → {z_f:+}")
                    if ratuje:
                        czemu.append("bez portu miasto nie wyżywi się przy "
                                     "ŻADNEJ obsadzie i będzie się kurczyć")
                    if dalej:
                        czemu.append(f"utrzyma {dalej} rozmiarów więcej, "
                                     "których bez portu nie wyżywi")
                    if not zysk_f and not dalej:
                        ostrz = ("miasto nie obrabia kafli morskich — port "
                                 "nie doda tu nic, a kosztuje "
                                 f"{bl.upkeep} zł na turę")

                # --- spichlerz: prog MinSize w Growth_Food + musi byc nadwyzka
                elif bnm == "Granary":
                    growth = 0
                    for eff in rs.effects_by_type.get("Growth_Food", []):
                        if not any(q.type == "Building" and q.name == bnm
                                   for q in eff.reqs):
                            continue
                        okr = all(
                            (size >= int(q.name)) == q.present
                            for q in eff.reqs
                            if q.type == "MinSize" and str(q.name).isdigit())
                        if okr:
                            growth = max(growth, eff.value)
                    nadwyzka_f = teraz[1] if teraz else 0
                    ocena = (growth / 10.0) if (growth and nadwyzka_f > 0) else 0
                    if not growth:
                        ostrz = (f"przy rozmiarze {size} Growth_Food nie działa "
                                 "— spichlerz nie przyspieszy wzrostu")
                    elif nadwyzka_f <= 0:
                        ostrz = ("miasto nie ma nadwyżki żywności — nie ma "
                                 "czego zatrzymywać przy wzroście")
                    else:
                        czemu.append(f"Growth_Food +{growth}% przy rozmiarze {size}")

                # --- akwedukty: sufit i zaraza
                elif bnm.startswith("Aqueduct"):
                    cap = 0
                    for eff in rs.effects_by_type.get("Size_Adj", []):
                        need = [q for q in eff.reqs if q.type == "Building"]
                        if all((q.name in blds) == q.present for q in need):
                            cap += eff.value
                    blisko = max(0, 6 - (cap - size))
                    zaraza = illness_on and size >= illness_min
                    ocena = blisko + (4 if zaraza else 0)
                    if zaraza:
                        czemu.append(f"zaraza działa od rozmiaru {illness_min}, "
                                     f"miasto ma {size} — Health_Pct +30")
                    czemu.append(f"sufit {cap}, miasto {size}")
                    if cap - size > 6:
                        ostrz = f"do sufitu {cap} jeszcze daleko"

                # --- budynki procentowe: mnoza to, co miasto JUZ ma
                else:
                    proc = []
                    for etype in ("Output_Bonus", "Output_Bonus_2"):
                        for eff in rs.effects_by_type.get(etype, []):
                            if not any(q.type == "Building" and q.present
                                       and q.name == bnm for q in eff.reqs):
                                continue
                            out = [q.name for q in eff.reqs
                                   if q.type == "OutputType"]
                            proc.append((out[0] if out else "?", eff.value))
                    content, content_potem = 0, 0
                    for eff in rs.effects_by_type.get("Make_Content", []):
                        # efekt nalezy do budynku tylko wtedy, gdy to JEGO
                        # obecnosc jest wymogiem, a nie gdy inny budynek go
                        # sprawdza (Mausoleum vs City Walls)
                        wlasne = [q for q in eff.reqs if q.type == "Building"]
                        moj = [q for q in wlasne if q.name == bnm and q.present]
                        if not moj:
                            continue
                        # efekt liczy sie do wartosci TEGO budynku takze wtedy,
                        # gdy wymaga jeszcze innych - pod warunkiem, ze tamte
                        # juz mam. Swiatynia po zbudowaniu Temple of Artemis
                        # daje +2 wlasne I +2 z cudu; bez tego narzedzie
                        # zaniza ja o polowe.
                        reszta_ok = True
                        for q in wlasne:
                            if q.name == bnm:
                                continue
                            pool = blds if q.range.lower() == "city" else mine_blds
                            if (q.name in pool) != q.present:
                                reszta_ok = False
                                break
                        if not reszta_ok:
                            continue
                        # ...i tylko wtedy, gdy POZOSTALE warunki tez sa
                        # spelnione: drugi punkt Swiatyni wisi na Mysticism,
                        # ktorego moge nie miec
                        brak_t = [q.name for q in eff.reqs if q.type == "Tech"
                                  and (q.name in techs) != q.present]
                        zly_gov = any(q.type == "Gov" and (q.name == gov) != q.present
                                      for q in eff.reqs)
                        zly_rozm = any(
                            q.type == "MinSize" and str(q.name).isdigit()
                            and (size >= int(q.name)) != q.present
                            for q in eff.reqs)
                        if zly_gov or zly_rozm:
                            continue
                        if brak_t:
                            content_potem += eff.value
                        else:
                            content += eff.value
                    for out, pct in proc:
                        baza = handel * (tax / 100.0) if out.lower() == "gold" \
                            else (handel if out.lower() == "science" else 0)
                        zysk = baza * pct / 100.0
                        ocena += zysk
                        czemu.append(f"+{pct}% {out} od {baza:.0f} → +{zysk:.1f}/turę")
                    # budynki tnace marnotrawstwo (ratusz) - ich wartosc to
                    # NIE zadowolenie, tylko odzyskana produkcja; bez tego
                    # ratusz w odleglym miescie wychodzil na grosze
                    for out, ile in (("Trade", handel),
                                     ("Shield", max(nadw, 0))):
                        if ile <= 0:
                            continue
                        teraz_p = self._waste_pct(rs, out, blds, gov, techs,
                                                  mine_blds, size, dystans)
                        po_p = self._waste_pct(rs, out, blds | {bnm}, gov, techs,
                                               mine_blds | {bnm}, size, dystans)
                        if po_p < teraz_p:
                            zysk = ile * (teraz_p - po_p) / 100.0
                            ocena += zysk
                            czemu.append(
                                f"marnotrawstwo {out.lower()} {teraz_p}%→{po_p}%"
                                f" → +{zysk:.1f}/turę")
                    niezadowoleni = max(0, size - self._unhappy_size(rs))
                    if content or content_potem:
                        ocena += min(content, niezadowoleni) * 2
                        czemu.append(f"Make_Content +{content} teraz"
                                     + (f" (+{content_potem} po zdobyciu "
                                        "brakujących technologii)"
                                        if content_potem else "")
                                     + f", niezadowolonych {niezadowoleni}")
                        if not niezadowoleni:
                            ostrz = "w tym mieście nie ma jeszcze niezadowolonych"
                    if bl.is_wonder:
                        zasieg = self._wonder_scope(rs, bnm)
                        ocena += 6 if zasieg == "Player" else 2
                        czemu.append(f"cud o zasięgu {zasieg}"
                                     + (" — działa w KAŻDYM mieście"
                                        if zasieg == "Player" else
                                        " — działa tylko tutaj"))
                        # cud, ktorego efekty MNOZA inny budynek, jest wart
                        # tyle, ile tych budynkow stoi - Mausoleum bez murow
                        # i sadow nie daje nic
                        wymagane = self._wonder_needs(rs, bnm)
                        if wymagane:
                            mam = sum(1 for w in wymagane if w in mine_blds)
                            czemu.append("efekty liczą budynki: "
                                         + ", ".join(sorted(wymagane)))
                            if not mam:
                                ocena -= 5
                                ostrz = ("nie masz ani jednego z budynków, "
                                         "które ten cud mnoży ("
                                         + ", ".join(sorted(wymagane))
                                         + ") — dziś dałby zero")
                        inne = budowane_cudy.get(bnm)
                        if inne and inne != nm:
                            ocena -= 8
                            ostrz = (f"{inne} już to buduje "
                                     f"({rows[inne].get('shield_stock')}"
                                     f"/{bl.build_cost} tarcz) — drugie "
                                     "miasto marnuje tarcze")
                    if not proc and not content and not bl.is_wonder:
                        # tylko efekty, ktorych JEDYNYM wymogiem budynkowym
                        # jest ten budynek - inaczej mury "dostaja"
                        # Make_Content nalezacy do Mausoleum
                        efekty = []
                        for et, lst in rs.effects_by_type.items():
                            for e in lst:
                                wl = [q for q in e.reqs if q.type == "Building"]
                                if len(wl) == 1 and wl[0].name == bnm \
                                        and wl[0].present:
                                    efekty.append(et)
                                    break
                        if efekty:
                            czemu.append("efekty: " + ", ".join(sorted(efekty)[:4]))

                if bl.upkeep:
                    ocena -= bl.upkeep * 0.8
                    czemu.append(f"utrzymanie {bl.upkeep} zł/turę")
                dodaj(bnm, bl.build_cost, bl.upkeep, ocena,
                      "; ".join(czemu) or "brak wyraźnego zysku",
                      "cud" if bl.is_wonder else "budynek", ostrz)

            # ---------------- Coinage: zamiana tarcz na zloto
            # Budynek rodzaju "Convert" nie powstaje - miasto po prostu
            # przelicza nadwyzke tarcz na zloto. To jedyna sensowna odpowiedz
            # dla miasta, ktore uderzylo w swoj sufit i nie ma czego budowac:
            # dwie tarcze na ture zamienione w dwa zlote biją kazdy budynek,
            # ktory bedzie stal dwadziescia tur i doda kolejna zlotowke
            # utrzymania.
            zamiana = next((b for b in rs.buildings.values()
                            if getattr(b, "genus", "") == "Convert"
                            and "Gold" in getattr(b, "flags", ())), None)
            if zamiana is None:
                zamiana = rs.buildings.get("Coinage")
            if zamiana is not None and nadw > 0:
                # ile inne opcje realnie dają — jeśli nic nie przebija zamiany,
                # to znaczy, że miasto nie ma po co produkować tarcz
                # Zamiana nie konkuruje wartoscia z budynkiem - ona jest
                # tym, co robisz, gdy zadnego sensownego budynku nie ma.
                # Dlatego jej ocena zalezy od TEGO, ile warte sa alternatywy,
                # a nie od wielkosci produkcji: inaczej wygrywalaby w
                # najlepszym miescie, co jest odwrotnoscia prawdy.
                najlepsze = max((o["ocena"] for o in opcje), default=0.0)
                ocena = (nadw * 0.5) if najlepsze < 1.0 else 0.4
                czemu = [f"{nadw} tarcz/turę → {nadw} złota/turę, bez końca",
                         "nie kosztuje utrzymania i nigdy się nie kończy"]
                ostrz = None
                if najlepsze >= 1.0:
                    ostrz = ("miasto ma jeszcze co budować — zamiana na złoto "
                             "dopiero, gdy zabraknie sensownych celów")
                opcje.append({
                    "co": zamiana.name, "rodzaj": "zamiana",
                    "koszt_tarcz": 0, "utrzymanie_zl": 0, "tur": None,
                    "ocena": round(ocena, 2), "dlaczego": "; ".join(czemu),
                    **({"ostrzezenie": ostrz} if ostrz else {}),
                })

            # ---------------- karawana na cud
            if merch is not None and cudowe:
                cele = []
                for cnm, cr in cudowe:
                    if cnm == nm:
                        continue
                    t = march_turns(rs, tmap, self.geom, merch, (x, y),
                                    (int(cr["x"]), int(cr["y"])),
                                    max_nodes=60000, cities=ct)
                    if t is not None:
                        cele.append((cnm, t, str(cr.get("currently_building_name"))))
                if cele:
                    cnm, t, cud = min(cele, key=lambda e: e[1])
                    tb = tur(merch.build_cost)
                    dodaj(merch.name, merch.build_cost, 0,
                          8 - (t + (tb or 0)) * 0.2,
                          f"{merch.build_cost} tarcz trafi w całości do „{cud}” "
                          f"w mieście {cnm}; marsz {t} tur",
                          "jednostka",
                          ("zmiana z budynku na jednostkę kosztuje 50% zapasu "
                           f"({zapas} tarcz)") if kind_now == "Building" and zapas
                          else None)
                else:
                    odrzucone.append({
                        "co": merch.name,
                        "powod": ("żadne miasto budujące cud nie jest osiągalne "
                                  f"dla klasy {rs.uclass_of(merch).name}")})

            # ---------------- osadnicy i robotnicy
            for unm in ("Settlers", "Workers", "Migrants"):
                ut = rs.units.get(unm)
                if ut is None or not all(t in techs for t in ut.req_techs()):
                    continue
                pop = getattr(ut, "pop_cost", 0)
                if pop and size <= pop:
                    odrzucone.append({"co": unm,
                                      "powod": f"miasto musi mieć więcej niż "
                                               f"{pop} obywateli"})
                    continue
                czemu, ocena = [], 0.0
                if unm == "Workers":
                    ocena = 3 if praca_backlog > 40 else 1
                    czemu.append(f"w państwie czeka {praca_backlog} tur pracy "
                                 "polowej")
                    czemu.append(f"kosztuje {ut.uk_shield} tarczy utrzymania")
                elif unm == "Settlers":
                    nadwyzka_f = teraz[1] if teraz else 0
                    ocena = 4 if nadwyzka_f > 1 else 1.5
                    czemu.append(f"zdejmuje {pop} obywateli z miasta "
                                 f"(bilans żywności {nadwyzka_f:+})")
                elif unm == "Migrants":
                    # migrant przenosi obywatela tam, gdzie jest go czym
                    # wyzywic - ma sens tylko z miasta, ktore juz nie rosnie
                    nadwyzka_f = teraz[1] if teraz else 0
                    stoi = obsada(size + 1, ma_port) is None
                    ocena = 2.5 if stoi else 0.5
                    czemu.append(
                        f"przenosi 1 obywatela do innego miasta; to miasto "
                        + ("nie wyżywi już kolejnego" if stoi
                           else f"jeszcze rośnie (bilans {nadwyzka_f:+})"))
                dodaj(unm, ut.build_cost, 0, ocena, "; ".join(czemu),
                      "jednostka",
                      ("zmiana z budynku na jednostkę kosztuje 50% zapasu"
                       if kind_now == "Building" and zapas else None))

            # Ocena mowi, ILE cos daje; nie mowila dotad, KIEDY. Cud wart 6.0
            # w miescie, ktore budowaloby go sto tur, jest wart tyle, co nic -
            # dlatego dzielimy ocene przez czas, gdy ten przekracza rozsadek.
            for o in opcje:
                tt = o.get("tur")
                if tt and tt > 25:
                    o["ocena"] = round(o["ocena"] * 25.0 / tt, 2)
                    o["dlaczego"] = (f"stałoby {tt} tur — "
                                     + o["dlaczego"])
            opcje.sort(key=lambda o: (-o["ocena"], o["tur"] or 999))
            wyniki.append({
                "miasto": nm, "rozmiar": size,
                "buduje_teraz": r.get("currently_building_name"),
                "zapas_tarcz": zapas, "nadwyzka_tarcz": nadw,
                "bilans_zywnosci": (teraz[1] if teraz else None),
                "kurczy_sie": glodowe,
                "handel": handel,
                "budynki": sorted(blds),
                "jednostek_na_utrzymaniu": len(jedn),
                "tarcz_na_utrzymanie_jednostek": sum(
                    rs.units[u.type].uk_shield for u in jedn
                    if u.type in rs.units),
                "rzeka_obok": ma_rzeke, "jezioro_obok": ma_jezioro,
                "nadmorskie": nadmorskie,
                "opcje": opcje[:limit],
                "nie_oplaca_sie": [o for o in opcje[limit:] if o["ocena"] <= 0][:6],
                "nie_da_sie_zbudowac": odrzucone[:12],
            })

        wyniki.sort(key=lambda w: -w["rozmiar"])
        return {"ustroj": gov, "suwak_podatkow": tax,
                "zaraza_od_rozmiaru": illness_min if illness_on else "wyłączona",
                "miasta": wyniki}

    def _waste_pct(self, rs, output: str, blds: set[str], gov: str,
                   techs: set[str], mine: set[str], size: int,
                   dystans: int) -> int:
        """Ile procent danej produkcji miasto traci - jak city_waste()."""
        baza = self._city_effect(rs, "Output_Waste", output, blds, gov,
                                 techs, mine, size)
        dyst = self._city_effect(rs, "Output_Waste_By_Distance", output, blds,
                                 gov, techs, mine, size)
        redukcja = self._city_effect(rs, "Output_Waste_Pct", output, blds, gov,
                                     techs, mine, size)
        poziom = baza + dyst * dystans // 100
        poziom -= poziom * redukcja // 100
        return max(0, min(100, poziom))

    def _unhappy_size(self, rs) -> int:
        """Od ktorego obywatela zaczyna sie niezadowolenie."""
        vals = [e.value for e in rs.effects_by_type.get("City_Unhappy_Size", [])
                if not e.reqs]
        return vals[0] if vals else 4

    def _wonder_needs(self, rs, name: str) -> set[str]:
        """Budynki, ktorych obecnosc warunkuje efekty tego cudu.

        Mausoleum daje Make_Content za KAZDE City Walls i za KAZDY Courthouse;
        bez nich jest to dwiescie tarcz w nic. Temple of Artemis tak samo
        zalezy od Swiatyn.
        """
        out: set[str] = set()
        for lst in rs.effects_by_type.values():
            for eff in lst:
                wl = [q for q in eff.reqs if q.type == "Building" and q.present]
                if any(q.name == name for q in wl) and len(wl) > 1:
                    out |= {q.name for q in wl if q.name != name}
        return out

    def _wonder_scope(self, rs, name: str) -> str:
        """Czy cud dziala w swoim miescie, czy w calym panstwie."""
        zasiegi = set()
        for lst in rs.effects_by_type.values():
            for eff in lst:
                for q in eff.reqs:
                    if q.type == "Building" and q.present and q.name == name:
                        zasiegi.add(q.range)
        for pref in ("World", "Player"):
            if pref in zasiegi:
                return pref
        return "City"

    # ------------------------------------------------------------ karawany

    def caravan_plan(self, rs, limit: int = 12, max_turns: int = 60,
                     full: bool = False) -> dict:
        """Gdzie budowac karawany, dokad je wyslac i czym tam dotrzec.

        Trzy pytania, ktore trzeba rozstrzygnac razem, bo osobno kazde daje
        zla odpowiedz:

        1. Czy trasa jest legalna - o tym decyduje tabela `[trade] settings`
           z regul. W sandboksie trasy krajowe daja 0%, wiec liczy sie tylko
           zagranica, a wojna zeruje wartosc.
        2. Ile trasa da - liczymy doslownie wzorami z `common/traderoutes.c`
           w wariancie CLASSIC (oba style sa w ustawieniach partii), a nie
           przyblizeniem po rozmiarze miasta.
        3. Czy karawana tam DOJDZIE - i to jest w sandboksie rozstrzygajace.
           Klasa Merchant nie jest natywna dla zadnego terenu; jest natywna
           wylacznie dla drogi, kolei i rzeki. Bez ciaglej drogi karawana nie
           ruszy sie z miejsca i jedyna droga jest transport morski.

        Dlatego kazdy kandydat dostaje trase ladem i trase morzem, a wynik
        podaje szybsza z nich razem z czasem produkcji - trasa warta 200%
        osiagalna za trzydziesci tur jest gorsza niz warta 100% za osiem.
        """
        import collections
        import os

        from .registry import parse_file

        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        my = s.cities_of(s.me.slot)
        if not my:
            return {"blad": "brak moich miast"}

        # --- reguly handlu i ustawienia partii
        pct: dict[str, int] = {}
        bonus_kind: dict[str, str] = {}
        path = os.path.join(rs.path, "game.ruleset")
        if os.path.exists(path):
            g = parse_file(path, base_dir=os.path.dirname(rs.path))
            sec = g.get("trade")
            tbl = sec.table("settings") if sec else None
            for r in (tbl.dicts() if tbl else []):
                pct[str(r.get("type"))] = int(r.get("pct") or 0)
                bonus_kind[str(r.get("type"))] = str(r.get("bonus") or "")

        st = s.reg.get("settings")
        stbl = st.table("set") if st else None
        setting = {str(r.get("name")): r.get("value")
                   for r in (stbl.dicts() if stbl else [])}
        mindist = int(setting.get("trademindist") or 9)
        rel_pct = int(setting.get("tradeworldrelpct") or 0)
        rev_style = str(setting.get("trade_revenue_style") or "CLASSIC")
        bon_style = str(setting.get("caravan_bonus_style") or "CLASSIC")
        xsize = int(setting.get("xsize") or self.geom.xsize)
        ysize = int(setting.get("ysize") or self.geom.ysize)
        big = max(xsize, ysize) or 1

        max_routes = 0
        for eff in rs.effects_by_type.get("Max_Trade_Routes", []):
            if not eff.reqs:
                max_routes = max(max_routes, eff.value)

        def weighted(d: int) -> int:
            return ((100 - rel_pct) * d + rel_pct * (d * 40 // big)) // 100

        # --- jednostki: karawana i prom, ktory ja uniesie
        techs = _known_techs(s)
        merch = next((u for u in rs.units.values()
                      if "HelpWonder" in u.flags or "TradeRoute" in u.flags),
                     None)
        if merch is None:
            return {"blad": "ten zestaw regul nie ma jednostki handlowej"}
        mclass = rs.uclass_of(merch).name
        promy = [u for u in rs.units.values()
                 if mclass in getattr(u, "cargo", ())
                 and all(t in techs for t in u.req_techs())]
        prom = max(promy, key=lambda u: u.move_rate) if promy else None
        prom_brak = sorted({t for u in rs.units.values()
                            if mclass in getattr(u, "cargo", ())
                            for t in u.req_techs() if t not in techs})

        # --- po czym Merchant w ogole chodzi (to jest sedno w sandboksie)
        natywne_tereny = sorted(n for n, t in rs.terrains.items()
                                if mclass in t.native_to)

        tmap = TerrainMap(s)
        ct = city_tiles(s)
        cont = self._continents()

        def coastal(tile) -> bool:
            for nb in self.geom.neighbours(*tile):
                nm = tmap.terrain(*nb)
                terr = rs.terrains.get(nm) if nm else None
                if terr is not None and not terr.is_land:
                    return True
            return False

        # --- ile handlu daje miasto (potrzebne do obu wzorow)
        handel_cache: dict[tuple[int, int], int] = {}

        def trade_of(x: int, y: int, size: int, blds=()) -> int:
            key = (x, y)
            if key in handel_cache:
                return handel_cache[key]
            val = _city_trade(rs, tmap, self.geom, x, y, size, set(blds))
            handel_cache[key] = val
            return val

        # --- zajete sloty tras
        sec_me = s._sections[s.me.slot]
        ctbl = sec_me.table("c")
        rows = {str(r.get("name")): r for r in (ctbl.dicts() if ctbl else [])}
        used = collections.Counter()
        for nm, r in rows.items():
            used[nm] = sum(1 for i in range(8)
                           if r.get(f"traderoute{i}") not in (None, 0, "0"))

        foreign = list(s.known_cities())
        if full:
            seen = {(c.x, c.y) for c in foreign}
            for p in s.players.values():
                if p.slot != s.me.slot:
                    for c in s.cities_of(p.slot):
                        if (c.x, c.y) not in seen:
                            foreign.append(c)
        nations = {p.slot: p.nation for p in s.players.values()}
        dipl = s.me.diplomacy

        # --- kandydaci
        cands, odrzucone = [], collections.Counter()
        za_blisko: dict[str, dict] = {}
        for mc in my:
            r = rows.get(mc.name, {})
            mc_trade = trade_of(mc.x, mc.y, mc.size, s._bits(r.get("improvements")))
            for fc in foreign:
                ic = cont.get((fc.x, fc.y)) != cont.get((mc.x, mc.y))
                rel = dipl.get(fc.owner, "?")
                if rel == "Alliance":
                    kind = "AllyIC" if ic else "Ally"
                elif rel == "War":
                    kind = "EnemyIC" if ic else "Enemy"
                elif rel == "Team":
                    kind = "TeamIC" if ic else "Team"
                else:
                    kind = "INIC" if ic else "IN"
                value_pct = pct.get(kind, 0)
                if value_pct <= 0:
                    odrzucone[f"{kind}: reguly daja 0% wartosci"] += 1
                    continue
                d = self.geom.real_distance((mc.x, mc.y), (fc.x, fc.y))
                if d < mindist:
                    odrzucone[f"blizej niz trademindist={mindist}"] += 1
                    # sasiad zza miedzy to typowy kandydat na "zbudujmy tam
                    # droge" - a wlasnie do niego trasy zalozyc SIE NIE DA
                    prev = za_blisko.get(fc.name)
                    if prev is None or d < prev["dystans"]:
                        za_blisko[fc.name] = {
                            "partner": fc.name, "nacja": nations.get(fc.owner, "?"),
                            "rozmiar": fc.size, "najblizsze_moje": mc.name,
                            "dystans": d, "wymagany_dystans": mindist,
                            "stan_dyplomatyczny": rel,
                            "powod": "za blisko — trasy handlowej założyć się nie da",
                        }
                    continue
                fc_trade = trade_of(fc.x, fc.y, fc.size)

                if rev_style.upper().startswith("CLASSIC"):
                    baza = weighted(d) + mc.size + fc.size
                else:
                    baza = (mc_trade + fc_trade + 4) * 3
                na_ture = baza * value_pct // 100 // 12

                if bon_style.upper().startswith("CLASSIC"):
                    tb = (weighted(d) + 10) * (mc_trade + fc_trade) // 24
                else:
                    tb = 0
                jednorazowo = tb if bonus_kind.get(kind) != "None" else 0

                cands.append({
                    "moje_miasto": mc.name, "partner": fc.name,
                    "nacja": nations.get(fc.owner, "?"),
                    "stan_dyplomatyczny": rel, "typ_trasy": kind,
                    "procent_wartosci": value_pct,
                    "miedzykontynentalna": ic, "dystans": d,
                    "handel_moj": mc_trade, "handel_partnera": fc_trade,
                    "handel_na_ture": na_ture,
                    "zloto_jednorazowo": jednorazowo,
                    "premia": bonus_kind.get(kind, ""),
                    "_od": (mc.x, mc.y), "_do": (fc.x, fc.y),
                })

        # --- osiagalnosc: liczymy tylko dla najlepiej rokujacych, bo kazda
        #     trasa to osobne przeszukiwanie mapy
        cands.sort(key=lambda c: -(c["handel_na_ture"] * 10
                                   + c["zloto_jednorazowo"]))
        badane = cands[:max(limit * 4, 40)]

        porty = {c.name: coastal((c.x, c.y)) for c in my}

        def ladem(a, b):
            return march_turns(rs, tmap, self.geom, merch, a, b,
                               max_nodes=60000, cities=ct)

        def morzem(a, b):
            if prom is None or not (coastal(a) and coastal(b)):
                return None
            return march_turns(rs, tmap, self.geom, prom, a, b,
                               max_nodes=60000, cities=ct)

        # ile tur pracy robotnika brakuje, zeby DOBUDOWAC droge do celu:
        # istniejaca droga, rzeka i kafel miasta sa darmowe, reszta kosztuje
        # `road_time` danego terenu. To odpowiada na pytanie "czy budowac
        # droge do tego miasta" liczba, a nie przeczuciem.
        road_time = _road_build_times(rs)

        def brak_drogi(start, goal):
            import heapq
            pq = [(0, start)]
            best = {start: 0}
            while pq:
                c, cur = heapq.heappop(pq)
                if cur == goal:
                    return c
                if c > best.get(cur, 1 << 30):
                    continue
                for nb in self.geom.neighbours(*cur):
                    nm = tmap.terrain(*nb)
                    terr = rs.terrains.get(nm) if nm else None
                    if terr is None or not terr.is_land:
                        continue
                    darmowy = (nb in ct or tmap.has_extra("Road", *nb)
                               or tmap.has_extra("River", *nb))
                    nxt = c + (0 if darmowy else max(1, road_time.get(nm, 2)))
                    if nxt < best.get(nb, 1 << 30):
                        best[nb] = nxt
                        heapq.heappush(pq, (nxt, nb))
            return None

        wynik = []
        for c in badane:
            l = ladem(c["_od"], c["_do"])
            m = morzem(c["_od"], c["_do"])
            drogi = []
            if l is not None:
                drogi.append(("drogą/rzeką", l))
            if m is not None:
                drogi.append((f"morzem ({prom.name})", m + 1))
            if not drogi:
                c["osiagalne"] = False
                c["czym"] = ("nie dojdzie: klasa " + mclass +
                             " chodzi tylko po drogach i rzekach, "
                             "a promu nie mam" if prom is None else
                             "nie dojdzie: brak drogi i brak trasy morskiej")
            else:
                czym, tur = min(drogi, key=lambda p: p[1])
                c["osiagalne"] = True
                c["czym"] = czym
                c["tur_marszu"] = tur
                c["warianty"] = {k: v for k, v in drogi}
            c["tur_pracy_na_droge"] = 0 if l is not None else None
            wynik.append(c)

        # Analiza drogowa idzie osobnym przebiegiem po WSZYSTKICH kandydatach
        # z mojego kontynentu, a nie po czolowce rankingu: trasa morska przez
        # pol swiata bije w punktach sasiada zza miedzy, a to wlasnie do
        # sasiada oplaca sie dosypac kilka kafli drogi.
        laczone = {(c["moje_miasto"], c["partner"]): c for c in wynik}
        droga_do = []
        for c in cands:
            if c["miedzykontynentalna"]:
                continue
            juz = laczone.get((c["moje_miasto"], c["partner"]))
            if juz is not None and juz.get("osiagalne") and \
                    juz.get("czym", "").startswith("drogą"):
                continue                     # droga juz jest
            praca = brak_drogi(c["_od"], c["_do"])
            if praca is None or praca <= 0:
                continue
            droga_do.append({
                "partner": c["partner"], "nacja": c["nacja"],
                "z_miasta": c["moje_miasto"], "dystans": c["dystans"],
                "tur_pracy_na_droge": praca,
                "handel_na_ture": c["handel_na_ture"],
                "zloto_jednorazowo": c["zloto_jednorazowo"],
                "typ_trasy": c["typ_trasy"],
                "stan_dyplomatyczny": c["stan_dyplomatyczny"],
            })
        # najtansza droga do kazdego partnera
        naj: dict[str, dict] = {}
        for r in sorted(droga_do, key=lambda r: r["tur_pracy_na_droge"]):
            naj.setdefault(r["partner"], r)

        osiagalne = [c for c in wynik if c.get("osiagalne")]

        # --- produkcja: gdzie karawana powstanie najszybciej
        koszt = merch.build_cost
        produkcja = {}
        for mc in my:
            r = rows.get(mc.name, {})
            nadw = int(r.get("last_turns_shield_surplus") or 0)
            zapas = int(r.get("shield_stock") or 0)
            kind = str(r.get("currently_building_kind") or "")
            brak = max(0, koszt - zapas)
            tur = None if nadw <= 0 else -(-brak // nadw)
            produkcja[mc.name] = {
                "nadwyzka_tarcz": nadw, "zapas_tarcz": zapas,
                "tur_na_karawane": tur,
                "buduje_teraz": r.get("currently_building_name"),
                "kara_za_zmiane": ("50% zapasu — zmiana budynek→jednostka"
                                   if kind == "Building" else "brak"),
                "port": porty.get(mc.name, False),
                "wolnych_slotow": (max_routes - used[mc.name]
                                   if max_routes else None),
            }

        # --- plan: najlepsza trasa dla kazdego miasta, ktore ma jeszcze slot
        # limit tras obowiazuje OBA miasta - bez pilnowania slotow partnera
        # plan wysyla piec karawan do tego samego miasta, a przyjmie dwie
        plan, zajete = [], collections.Counter()
        u_partnera = collections.Counter()
        for c in sorted(osiagalne,
                        key=lambda c: (-(c["handel_na_ture"] * 10
                                         + c["zloto_jednorazowo"])
                                       / max(1, c["tur_marszu"]))):
            nm = c["moje_miasto"]
            p = produkcja[nm]
            if max_routes and used[nm] + zajete[nm] >= max_routes:
                continue
            if max_routes and u_partnera[c["partner"]] >= max_routes:
                continue
            if p["tur_na_karawane"] is None:
                continue
            razem = p["tur_na_karawane"] + c["tur_marszu"]
            if razem > max_turns:
                continue
            zajete[nm] += 1
            u_partnera[c["partner"]] += 1
            plan.append({
                "buduj_w": nm,
                "tur_produkcji": p["tur_na_karawane"],
                "wyslij_do": c["partner"], "nacja": c["nacja"],
                "stan_dyplomatyczny": c["stan_dyplomatyczny"],
                "czym": c["czym"], "tur_marszu": c["tur_marszu"],
                "tur_razem": razem,
                "tur_pracy_na_droge": c.get("tur_pracy_na_droge"),
                "dystans": c["dystans"],
                "typ_trasy": c["typ_trasy"],
                "procent_wartosci": c["procent_wartosci"],
                "handel_na_ture": c["handel_na_ture"],
                "zloto_jednorazowo": c["zloto_jednorazowo"],
                "kara_za_zmiane_produkcji": p["kara_za_zmiane"],
            })
            if len(plan) >= limit:
                break

        nieosiagalne = [
            {"moje_miasto": c["moje_miasto"], "partner": c["partner"],
             "nacja": c["nacja"], "dystans": c["dystans"],
             "handel_na_ture": c["handel_na_ture"], "powod": c["czym"],
             "tur_pracy_na_droge": c.get("tur_pracy_na_droge")}
            for c in wynik if not c.get("osiagalne")][:limit]

        warto_droge = sorted(naj.values(),
                             key=lambda r: r["tur_pracy_na_droge"])[:limit]

        return {
            "jak_chodzi_karawana": {
                "klasa": mclass,
                "natywne_tereny": natywne_tereny or
                    "ŻADEN — tylko drogi, koleje i rzeki",
                "natywne_ulepszenia": sorted(
                    n for n in _road_move_costs(rs)
                    if n in ("Road", "Railroad", "Maglev", "River")),
                "prom": (f"{prom.name} ({prom.build_cost} tarcz, ruch "
                         f"{prom.move_rate}, ładowność {prom.transport_cap})"
                         if prom else "brak — brakuje: " + ", ".join(prom_brak)),
            },
            "zasady_handlu": {
                "styl_wartosci": rev_style, "styl_premii": bon_style,
                "trademindist": mindist, "tradeworldrelpct": rel_pct,
                "max_tras_na_miasto": max_routes or "bez limitu",
                "typy_bez_wartosci": sorted(k for k, v in pct.items() if v <= 0),
            },
            "plan": plan,
            "produkcja_w_miastach": produkcja,
            "warto_dobudowac_droge": warto_droge,
            "za_blisko_na_szlak": sorted(za_blisko.values(),
                                        key=lambda r: r["dystans"]),
            "nieosiagalne_mimo_wartosci": nieosiagalne,
            "odrzucone_powody": dict(odrzucone),
            "zbadanych_par": len(badane),
            "kandydatow_razem": len(cands),
        }


# --------------------------------------------------------- wspolne narzedzia

class IntelMixin:
    """Narzedzia wywiadu wspolne dla okna aplikacji i trybu bez Qt.

    Klasa uzywajaca musi udostepnic metode `_intel_apply_ruleset(nazwa)`.
    """

    _intel: Intel | None = None
    _intel_full: bool = False

    def _load_save(self, path: str | None) -> Intel:
        if path:
            target = os.path.expanduser(path)
        else:
            found = find_saves()
            if not found:
                raise FileNotFoundError(
                    "nie znalazłem żadnego zapisu w ~/.freeciv/saves")
            target = found[0]
        self._intel = Intel(Save(target))
        return self._intel

    def _need_intel(self) -> Intel:
        if self._intel is None:
            return self._load_save(None)
        return self._intel

    def ai_savegame(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad"))
        intel = self._load_save(args.get("sciezka"))
        self._intel_full = full
        out = intel.summary(full)
        ruleset = intel.save.ruleset
        try:
            applied = self._intel_apply_ruleset(ruleset)
            out["zestaw_regul_ustawiony"] = applied
        except Exception as exc:  # noqa: BLE001
            out["zestaw_regul_ustawiony"] = f"nie udało się: {exc}"
        # od razu bierzemy jego prawdziwe drzewo zamiast progu z suwaka
        try:
            known = _known_techs(intel.save) - {"A_NONE"}
            if known:
                self._set_tech_override(known)
                out["technologie_z_zapisu"] = len(known)
        except Exception:  # noqa: BLE001
            pass
        return out

    def ai_army(self, args: dict) -> dict:
        return self._need_intel().my_army()

    def ai_nation(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        return self._need_intel().nation(str(args.get("nacja", "")), full)

    def ai_growth(self, args: dict) -> dict:
        return self._need_intel().growth_potential(
            self._intel_ruleset(), str(args.get("miasto", "")),
            int(args.get("limit") or 10))

    def ai_providers(self, args: dict) -> dict:
        """Ktorzy dostawcy sa skonfigurowani - BEZ ujawniania kluczy."""
        from . import providers
        return providers.status()

    def ai_diplomacy(self, args: dict) -> dict:
        return self._need_intel().diplomacy(self._intel_ruleset())

    def ai_alerts(self, args: dict) -> dict:
        return self._need_intel().alerts(self._intel_ruleset())

    def ai_city_defense(self, args: dict) -> dict:
        return self._need_intel().city_defense(
            self._intel_ruleset(), str(args.get("miasto", "")),
            str(args.get("napastnik", "")), int(args.get("limit") or 8))

    def ai_mobility(self, args: dict) -> dict:
        return self._need_intel().mobility(
            self._intel_ruleset(), int(args.get("tury") or 2),
            str(args.get("jednostka", "")))

    def ai_threats(self, args: dict) -> dict:
        return self._need_intel().threat_assessment(
            self._intel_ruleset(), int(args.get("tury") or 8))

    def ai_production_plan(self, args: dict) -> dict:
        return self._need_intel().production_plan(
            self._intel_ruleset(), str(args.get("strategia", "auto")))

    def ai_research_plan(self, args: dict) -> dict:
        return self._need_intel().research_plan(
            self._intel_ruleset(), str(args.get("strategia", "gospodarka")),
            int(args.get("limit") or 8))

    def ai_turn_plan(self, args: dict) -> dict:
        return self._need_intel().turn_plan(
            self._intel_ruleset(), str(args.get("nastawienie", "auto")))

    def ai_campaign(self, args: dict) -> dict:
        return self._need_intel().campaign_plan(
            self._intel_ruleset(), int(args.get("tury") or 2),
            int(args.get("rezerwa") if args.get("rezerwa") is not None else 1))

    def ai_war_readiness(self, args: dict) -> dict:
        nations = args.get("nacje") or args.get("nations") or []
        if isinstance(nations, str):
            nations = [nations]
        return self._need_intel().war_readiness(
            self._intel_ruleset(), list(nations), int(args.get("tury") or 2))

    def ai_build_plan(self, args: dict) -> dict:
        return self._need_intel().build_plan(
            self._intel_ruleset(), str(args.get("metropolia", "")))

    def ai_corruption(self, args: dict) -> dict:
        return self._need_intel().corruption(self._intel_ruleset())

    def ai_techs(self, args: dict) -> dict:
        rs = self._intel_ruleset()
        out = self._need_intel().tech_tree(rs, int(args.get("limit") or 12))
        if "blad" in out:
            return out
        if args.get("zastosuj") is not None:
            self._set_tech_override(out["znane"] if args["zastosuj"] else None)
        out["filtr_z_zapisu"] = bool(getattr(self, "_tech_override", None))
        return out

    def _set_tech_override(self, known) -> None:
        """Podmienia filtr technologii: suwak -> faktycznie zbadane."""
        self._tech_override = set(known) if known else None
        hook = getattr(self, "_intel_tech_filter_changed", None)
        if hook:
            hook()

    def ai_eras(self, args: dict) -> dict:
        rs = self._intel_ruleset()
        depth = args.get("prog")
        depth = rs.max_tech_depth() if depth is None else int(depth)
        eras = rs.eras()
        out = {
            "zestaw_regul": rs.name,
            **({"uwaga": "nazwy epok są etykietami progów drzewa, nie "
                          "chronologią — w tym zestawie część wypada nie tam, "
                          "gdzie w historii",
                "nietypowa_kolejnosc": _kolejnosc}
               if (_kolejnosc := rs.eras_out_of_order()) else {}),
            "maks_prog": rs.max_tech_depth(),
            "prog": depth,
            "epoka": rs.era_at(depth)["nazwa"],
            "epoki": [{**e, "nowe": rs.unlocked_at(e["prog"])} for e in eras],
            "nowe_na_tym_progu": rs.unlocked_at(depth),
        }
        nxt = next((e for e in eras if e["prog"] > depth), None)
        if nxt:
            out["nastepna_epoka"] = {"nazwa": nxt["nazwa"], "prog": nxt["prog"],
                                     "technologia": nxt["technologia"]}
        return out

    def ai_trade(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        return self._need_intel().trade_routes(
            self._intel_ruleset(), int(args.get("limit") or 15), full,
            bool(args.get("tylko_miedzykontynentalne")))

    def ai_workers(self, args: dict) -> dict:
        return self._need_intel().worker_plan(
            self._intel_ruleset(), int(args.get("limit") or 40))

    def ai_governor(self, args: dict) -> dict:
        return self._need_intel().governor_plan(
            self._intel_ruleset(), int(args.get("cel_rozmiar") or 12),
            str(args.get("miasto") or ""))

    def ai_city_build(self, args: dict) -> dict:
        return self._need_intel().city_build_advice(
            self._intel_ruleset(), str(args.get("miasto") or ""),
            int(args.get("limit") or 8))

    def ai_caravans(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        return self._need_intel().caravan_plan(
            self._intel_ruleset(), int(args.get("limit") or 12),
            int(args.get("max_tur") or 60), full)

    def ai_disband(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        types = args.get("jednostki") or None
        return self._need_intel().disband_plan(
            self._intel_ruleset(),
            [str(t) for t in types] if types else None, full)

    def ai_cities(self, args: dict) -> dict:
        return self._need_intel().cities_audit(self._intel_ruleset())

    def ai_reach(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        types = args.get("jednostki") or []
        return self._need_intel().reachability(
            self._intel_ruleset(), [str(t) for t in types], full)

    def ai_governments(self, args: dict) -> dict:
        rs = self._intel_ruleset()
        govs = args.get("ustroje") or None
        if govs is not None:
            govs = [str(g) for g in govs]
        return government_comparison(rs, self._intel, govs)

    def ai_front(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        return self._need_intel().front(
            str(args.get("nacja", "")), full,
            int(args.get("promien") or 12))


# ------------------------------------------------------------------ ustroje

# Efekty, ktore realnie decyduja o oplacalnosci ustroju. Wartosci czytamy
# z regul - nic nie jest tu zaszyte na sztywno poza doborem, co pokazac.
GOV_EFFECTS = {
    "Max_Rates": ("maks. suwak podatków/nauki (%)", "wyżej lepiej"),
    "Empire_Size_Base": ("próg wielkości imperium", "wyżej lepiej"),
    "Empire_Size_Step": ("co ile miast kolejna kara", "wyżej lepiej"),
    "Martial_Law_Each": ("stan wojenny: ilu niezadowolonych uspokaja jednostka",
                         "wyżej lepiej"),
    "Martial_Law_Max": ("stan wojenny: maks. jednostek", "wyżej lepiej"),
    "Make_Content_Mil": ("darmowe jednostki w polu (bez niezadowolenia)",
                         "wyżej lepiej"),
    "Revolution_Unhappiness": ("niezadowolenie za każdą jednostkę w polu",
                               "niżej lepiej"),
    "Unit_Upkeep_Free_Per_City": ("darmowe utrzymanie jednostek na miasto",
                                  "wyżej lepiej"),
    "Upkeep_Factor": ("mnożnik utrzymania jednostki", "niżej lepiej"),
    "Output_Waste": ("marnotrawstwo produkcji/handlu (%)", "niżej lepiej"),
    "Output_Bonus": ("premia do wytwarzania (%)", "wyżej lepiej"),
    "Output_Inc_Tile": ("dodatkowy surowiec z kafla", "wyżej lepiej"),
    "Civil_War_Chance": ("ryzyko wojny domowej (%)", "niżej lepiej"),
    "Happiness_To_Gold": ("budynki szczęścia dają złoto zamiast zadowolenia", ""),
    "Fanatics": ("dostęp do fanatyków (bez utrzymania)", ""),
}


def _gov_effect_values(rs, gov: str) -> dict[str, list[dict]]:
    """Wartosci efektow obowiazujace pod danym ustrojem.

    Uwzglednia zarowno efekty wymagajace tego ustroju, jak i te, ktore
    wykluczaja inne ustroje (czyli obowiazuja rowniez ten).
    """
    all_govs = set(rs.governments)
    out: dict[str, list[dict]] = {}
    for eff in rs.effects:
        if eff.type not in GOV_EFFECTS:
            continue
        pos = [r.name for r in eff.reqs if r.type == "Gov" and r.present]
        neg = [r.name for r in eff.reqs if r.type == "Gov" and not r.present]
        if pos and gov not in pos:
            continue
        if neg and gov in neg:
            continue
        if not pos and not neg:
            continue                      # efekt globalny, nie rozroznia ustrojow
        if neg and not pos and not (all_govs - set(neg)):
            continue
        extra = [f"{r.type}:{r.name}" for r in eff.reqs
                 if r.type not in ("Gov",)]
        out.setdefault(eff.type, []).append(
            {"wartosc": eff.value, "warunki": extra})
    return out


def government_comparison(rs, intel: "Intel | None", govs: list[str] | None = None
                          ) -> dict:
    """Porownuje ustroje wg regul, a jesli jest wczytany zapis - takze wg
    konkretnej sytuacji gracza (liczba miast, jednostek, znane technologie)."""
    import collections

    names = govs or list(rs.governments)
    unknown = [g for g in names if g not in rs.governments]
    names = [g for g in names if g in rs.governments]
    out: dict = {"zestaw_regul": rs.name, "ustroje": {}}
    if unknown:
        out["nieznane_ustroje"] = unknown

    # jakiej technologii wymaga ustroj
    reqs: dict[str, list[str]] = {}
    from .registry import parse_file
    import os
    path = os.path.join(rs.path, "governments.ruleset")
    if os.path.exists(path):
        reg = parse_file(path, base_dir=os.path.dirname(rs.path))
        for sec in reg.prefixed("government_"):
            from .model import clean_name
            gname = clean_name(sec.str("name"))
            tbl = sec.table("reqs")
            reqs[gname] = [str(r.get("name")) for r in tbl.dicts()
                           if str(r.get("type", "")).lower() == "tech"] if tbl else []

    for g in names:
        entry: dict = {"wymaga_technologii": reqs.get(g, []),
                       "efekty": {}}
        for etype, rows in sorted(_gov_effect_values(rs, g).items()):
            opis, kierunek = GOV_EFFECTS[etype]
            entry["efekty"][etype] = {"opis": opis, "kierunek": kierunek,
                                      "wartosci": rows}
        out["ustroje"][g] = entry

    if intel is None or intel.save.me is None:
        out["uwaga"] = ("brak wczytanego zapisu — porównanie jest ogólne, "
                        "bez liczb z twojej partii")
        return out

    save = intel.save
    me = save.me
    units = save.units_of(me.slot)
    sec = save._sections[me.slot]
    tbl = sec.table("c")
    city_ids = {int(r["id"]) for r in tbl.dicts()} if tbl else set()
    by_home = collections.Counter(u.homecity for u in units)

    known = set()
    research = save.reg.get("research")
    sf = save.reg.get("savefile")
    if research and sf:
        rtbl = research.table("r")
        vector = sf.list("technology_vector")
        if rtbl and len(rtbl):
            done = str(list(rtbl.dicts())[0].get("done") or "")
            known = {vector[i] for i, ch in enumerate(done)
                     if ch == "1" and i < len(vector)}

    out["moja_sytuacja"] = {
        "tura": save.turn, "obecny_ustroj": me.government,
        "miast": len(city_ids), "jednostek": len(units),
        "zloto": me.gold,
    }

    # --- co kazdy ustroj oznacza dla TEGO panstwa, a nie w oderwaniu
    #
    # Marnotrawstwo zalezy od odleglosci do stolicy, wiec liczymy je na
    # faktycznym rozkladzie miast. Bez tego porownanie ustrojow jest
    # bezuzyteczne dla rozleglego panstwa - a wlasnie dla takiego jest wazne.
    intel = Intel(save) if not isinstance(save, Intel) else save
    sec_me = save._sections[me.slot]
    rows = list(sec_me.table("c").dicts()) if sec_me.table("c") else []
    stolica = next(((int(r["x"]), int(r["y"])) for r in rows
                    if "Palace" in set(save._bits(r.get("improvements")))), None)
    dysty = [intel.geom.real_distance((int(r["x"]), int(r["y"])), stolica)
             for r in rows] if stolica else []
    mine_blds: set[str] = set()
    for r in rows:
        mine_blds |= set(save._bits(r.get("improvements")))
    w_polu = sum(1 for u in save.units_of(me.slot)
                 if (ut := rs.units.get(u.type)) and getattr(ut, "uk_happy", 0) > 0
                 and (u.x, u.y) not in {(int(r["x"]), int(r["y"])) for r in rows})

    def srednie_marnotrawstwo(gov: str, output: str) -> int | None:
        if not dysty:
            return None
        total = 0
        for d in dysty:
            base = intel._city_effect(rs, "Output_Waste", output, {"Courthouse"},
                                      gov, known, mine_blds, 8)
            bd = intel._city_effect(rs, "Output_Waste_By_Distance", output,
                                    {"Courthouse"}, gov, known, mine_blds, 8)
            pc = intel._city_effect(rs, "Output_Waste_Pct", output, {"Courthouse"},
                                    gov, known, mine_blds, 8)
            lvl = base + bd * d // 100
            lvl -= lvl * pc // 100
            total += max(0, min(100, lvl))
        return round(total / len(dysty))

    for g, entry in out["ustroje"].items():
        ml = intel._city_effect(rs, "Martial_Law_Each", "", set(), g, known,
                                mine_blds, 0)
        mlmax = intel._city_effect(rs, "Martial_Law_Max", "", set(), g, known,
                                   mine_blds, 0)
        rev = intel._city_effect(rs, "Revolution_Unhappiness", "", set(), g,
                                 known, mine_blds, 0)
        uf = max(1, intel._city_effect(rs, "Unhappy_Factor", "", set(), g, known,
                                       mine_blds, 0))
        mcm = intel._city_effect(rs, "Make_Content_Mil", "", set(), g, known,
                                 mine_blds, 0)
        entry["dla_mojego_panstwa"] = {
            "marnotrawstwo_tarcz_srednio_proc": srednie_marnotrawstwo(g, "Shield"),
            "marnotrawstwo_handlu_srednio_proc": srednie_marnotrawstwo(g, "Trade"),
            "stan_wojenny": (f"{ml} za jednostkę, maks {mlmax}" if ml
                             else "brak — garnizon nie uspokaja miasta"),
            "jednostek_w_polu_bez_kosztu": mcm // uf,
            "moje_jednostki_w_polu": w_polu,
            "anarchia_po_turach_zamieszek": rev or "nigdy",
        }

    for g, entry in out["ustroje"].items():
        need = entry["wymaga_technologii"]
        entry["dostepny_teraz"] = all(t in known for t in need) if known else None
        entry["brakujace_technologie"] = [t for t in need if t not in known] \
            if known else []

        eff = entry["efekty"]

        def first(etype: str, default: int = 0) -> int:
            rows = eff.get(etype, {}).get("wartosci", [])
            plain = [r["wartosc"] for r in rows if not r["warunki"]]
            return plain[0] if plain else default

        free = 0
        factor = 0
        koszt_typ = "—"
        for row in eff.get("Unit_Upkeep_Free_Per_City", {}).get("wartosci", []):
            for w in row["warunki"]:
                if w.startswith("OutputType:"):
                    free = row["wartosc"]
                    koszt_typ = w.split(":", 1)[1]
        for row in eff.get("Upkeep_Factor", {}).get("wartosci", []):
            for w in row["warunki"]:
                if w == f"OutputType:{koszt_typ}":
                    factor += row["wartosc"]
        if koszt_typ != "—" and city_ids:
            koszt = sum(max(0, by_home.get(c, 0) - free) * max(1, factor)
                        for c in city_ids)
            entry["utrzymanie_wojsk"] = {
                "darmowych_na_miasto": free,
                "mnoznik": max(1, factor),
                "placisz_w": {"Gold": "złocie", "Shield": "tarczach"}.get(
                    koszt_typ, koszt_typ),
                "koszt_na_ture": koszt,
            }

        base = first("Empire_Size_Base")
        step = first("Empire_Size_Step", base)
        if base:
            n = len(city_ids)
            kary = 0 if n <= base else 1 + (n - base - 1) // max(1, step)
            entry["kara_za_wielkosc"] = {
                "prog": base, "krok": step,
                "poziomow_kary_przy_twoich_miastach": kary,
                "miast_do_kolejnej_kary": max(0, base + kary * step - n + 1)
                if n > base else base - n + 1,
            }
    return out
