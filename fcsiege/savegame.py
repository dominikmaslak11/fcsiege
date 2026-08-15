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
    cities = cities or set()

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
            if not (to in cities and native_near(to)):
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
            return {"blad": f"nie mam jednostek bojowych{' typu ' + jednostka if jednostka else ''}"}

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
            return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

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
                    "dystans": d, "ocena": score,
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

    def ai_mobility(self, args: dict) -> dict:
        return self._need_intel().mobility(
            self._intel_ruleset(), int(args.get("tury") or 2),
            str(args.get("jednostka", "")))

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
