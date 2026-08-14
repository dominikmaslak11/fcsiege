"""Silnik obliczen walki Freeciva.

Odwzorowuje mechanike z common/combat.c:

  * sila ataku  = attack  * POWER_FACTOR * mnoznik_weterana [* zmeczenie]
  * sila obrony = defense * POWER_FACTOR * mnoznik_weterana
                  * bonus terenu * bonusy jednostkowe
                  * efekty "Defend_Bonus" (mury itd.)
                  * bonusy ulepszen terenu (rzeka, forteca)
                  * efekt "Fortify_Defense_Bonus"
  * w kazdej rundzie atakujacy trafia z prawdopodobienstwem A/(A+D)
    i zadaje obrazenia rowne swojej siле ognia; inaczej obrywa sam.

Oblezenie miasta liczymy symulacja Monte Carlo, bo Freeciv w kazdym ataku
wystawia do obrony jednostke o najwyzszej szansie przetrwania (czyli przy
identycznych obroncach - te najmniej ranna). Pojedynczy pojedynek liczony
jest jednak dokladnym wzorem, nie runda po rundzie.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .model import POWER_FACTOR, Effect, Req, Ruleset, Terrain, UnitType

# Typy wymagan, ktore potrafimy ocenic. Reszta oznacza efekt jako niepewny.
SUPPORTED_REQS = {
    "Building", "Wonder", "BuildingFlag", "BuildingGenus",
    "UnitClass", "UnitClassFlag", "UnitFlag", "UnitType",
    "CityTile", "Extra", "ExtraFlag", "Activity", "DiplRel",
    "MinSize", "MaxUnitsOnTile", "Gov", "Tech", "Advance",
    "Terrain", "TerrainClass", "TerrainFlag", "TerrainAlter",
    "Action", "MinMoveFrags", "UnitState", "Age", "MinHitPoints",
}


@dataclass
class Side:
    """Jedna strona walki."""
    utype: UnitType
    vet: int = 0                      # indeks poziomu weterana
    count: int = 1
    moves_frags: int | None = None    # pozostale ulamki ruchu (zmeczony atak)

    def vet_fact(self) -> int:
        levels = self.utype.vet_levels
        if not levels:
            return 100
        idx = max(0, min(self.vet, len(levels) - 1))
        return levels[idx].power_fact

    def vet_name(self) -> str:
        levels = self.utype.vet_levels
        if not levels:
            return "green"
        idx = max(0, min(self.vet, len(levels) - 1))
        return levels[idx].name


@dataclass
class Situation:
    """Okolicznosci obrony: teren, miasto, budynki, ulepszenia."""
    terrain: Terrain
    extras: set[str] = field(default_factory=set)
    in_city: bool = True
    city_size: int = 8
    buildings: set[str] = field(default_factory=set)       # budynki w miescie
    player_buildings: set[str] = field(default_factory=set)  # cuda obroncy
    fortified: bool = True
    gov: str = "Despotism"
    techs: set[str] = field(default_factory=set)
    units_on_tile: int = 1
    # teren, z ktorego atakujemy - nie wplywa na walke, ale liczymy z niego
    # koszt ruchu i wlasna obrone w razie kontrataku
    attacker_terrain: Terrain | None = None
    attacker_extras: set[str] = field(default_factory=set)


@dataclass
class Modifier:
    """Pojedynczy skladnik mnoznika, do pokazania w rozbiciu wyniku."""
    label: str
    factor: float
    source: str = ""
    details: list[str] = field(default_factory=list)


@dataclass
class PowerBreakdown:
    base: float
    total: float
    modifiers: list[Modifier] = field(default_factory=list)


@dataclass
class DuelResult:
    """Wynik pojedynczego starcia jedna jednostka kontra jedna."""
    attack_power: float
    defense_power: float
    p_round: float           # szansa atakujacego na trafienie w rundzie
    p_win: float             # szansa atakujacego na wygranie calego pojedynku
    attacker_fp: int
    defender_fp: int
    attacker_hp: int
    defender_hp: int
    rounds_needed_att: int
    rounds_needed_def: int
    attack_bd: PowerBreakdown
    defense_bd: PowerBreakdown
    warnings: list[str] = field(default_factory=list)


@dataclass
class SiegeResult:
    """Wynik szturmu na cale miasto."""
    n_defenders: int
    p_success_by_attacks: np.ndarray   # p[k] = szansa zdobycia przy k atakach
    attacks_pmf: np.ndarray            # rozklad liczby atakow
    mean_attacks: float
    mean_losses: float
    mean_shields_lost: float
    duel: DuelResult
    defender_duels: list[DuelResult]
    failure_rate: float                # udzial prob, gdzie szturm sie nie udal
    notes: list[str] = field(default_factory=list)
    consumed_on_attack: bool = False   # np. rakiety gina przy kazdym ataku

    def attacks_for(self, confidence: float) -> int | None:
        """Ile atakow potrzeba, by zdobyc miasto z zadana pewnoscia."""
        idx = np.searchsorted(self.p_success_by_attacks, confidence, side="left")
        if idx >= len(self.p_success_by_attacks):
            return None
        return int(idx)

    def p_with(self, attacks: int) -> float:
        if attacks < 0:
            return 0.0
        if attacks >= len(self.p_success_by_attacks):
            return float(self.p_success_by_attacks[-1])
        return float(self.p_success_by_attacks[attacks])


# --------------------------------------------------------------- wymagania

class ReqEvaluator:
    """Sprawdza wymagania efektow w zadanym kontekscie."""

    def __init__(self, rs: Ruleset, sit: Situation):
        self.rs = rs
        self.sit = sit
        self.unsupported: set[str] = set()

    def _buildings_in_scope(self, rng: str) -> set[str]:
        if rng in ("City", "Local", "Tile"):
            return self.sit.buildings
        return self.sit.buildings | self.sit.player_buildings

    def check(self, req: Req, unit: UnitType | None, vet: int = 0) -> bool:
        val = self._value(req, unit, vet)
        if val is None:
            self.unsupported.add(req.type)
            # nieznanego wymagania nie umiemy potwierdzic - efekt nie dziala
            return not req.present
        return val if req.present else not val

    def _value(self, req: Req, unit: UnitType | None, vet: int) -> bool | None:
        t, name, rng = req.type, req.name, req.range
        rs = self.rs
        sit = self.sit

        if t in ("Building", "Wonder"):
            return name in self._buildings_in_scope(rng)
        if t == "BuildingFlag":
            scope = self._buildings_in_scope(rng)
            return any(name in rs.buildings[b].flags for b in scope if b in rs.buildings)
        if t == "BuildingGenus":
            scope = self._buildings_in_scope(rng)
            return any(rs.buildings[b].genus == name for b in scope if b in rs.buildings)

        if unit is not None:
            if t == "UnitType":
                return unit.name == name
            if t == "UnitFlag":
                return name in unit.flags
            if t == "UnitClass":
                return unit.uclass_id == name
            if t == "UnitClassFlag":
                return name in rs.uclass_of(unit).flags

        if t == "CityTile":
            if name == "Center":
                return sit.in_city
            if name in ("Claimed", "Extras Owned"):
                return True
            return None
        if t == "Extra":
            return name in sit.extras
        if t == "ExtraFlag":
            return any(name in rs.extras[e].flags for e in sit.extras if e in rs.extras)
        if t == "Activity":
            if name == "Fortified":
                return sit.fortified
            return False
        if t == "DiplRel":
            if name in ("Foreign", "War", "Is foreign", "Has real embassy"):
                return name != "Has real embassy"
            if name in ("Alliance", "Team", "Peace", "Cease-fire", "Armistice",
                        "Never met", "Host", "Team or alliance"):
                return False
            return None
        if t == "MinSize":
            try:
                return sit.city_size >= int(name)
            except (TypeError, ValueError):
                return None
        if t == "MaxUnitsOnTile":
            try:
                return sit.units_on_tile <= int(name)
            except (TypeError, ValueError):
                return None
        if t == "Gov":
            return sit.gov == name
        if t in ("Tech", "Advance"):
            return name in sit.techs
        if t == "Terrain":
            return sit.terrain.name == name
        if t == "TerrainClass":
            return sit.terrain.tclass == name
        if t == "TerrainFlag":
            return name in sit.terrain.flags
        if t == "MinHitPoints":
            return True
        if t == "Age":
            return True
        return None

    def effect_active(self, eff: Effect, unit: UnitType | None, vet: int = 0) -> bool:
        for r in eff.reqs:
            if not self.check(r, unit, vet):
                return False
        if eff.nreqs and all(self.check(r, unit, vet) for r in eff.nreqs):
            return False
        return True

    def sum_effects(self, etype: str, unit: UnitType | None,
                    vet: int = 0) -> tuple[int, list[str]]:
        """Sumuje wartosci aktywnych efektow danego typu.

        We Freecivie procenty efektow tego samego typu sie DODAJA, a dopiero
        ich suma mnozy sile obrony - dlatego zwracamy jedna liczbe i liste
        opisow skladowych.
        """
        total = 0
        parts: list[str] = []
        for eff in self.rs.effects_by_type.get(etype, []):
            if self.effect_active(eff, unit, vet):
                total += eff.value
                parts.append(f"{_effect_label(eff, etype)} +{eff.value}%")
        return total, parts


def _effect_label(eff: Effect, etype: str) -> str:
    """Czytelna nazwa efektu na podstawie jego wymagan."""
    parts = []
    for r in eff.reqs:
        if not r.present:
            continue
        if r.type in ("Building", "Wonder"):
            parts.append(r.name)
        elif r.type == "Extra":
            parts.append(r.name)
        elif r.type == "Activity" and r.name == "Fortified":
            parts.append("okopanie")
        elif r.type == "CityTile" and r.name == "Center":
            parts.append("w miescie")
        elif r.type == "MinSize":
            parts.append(f"miasto od {r.name} mieszk.")
    if not parts:
        parts.append(etype)
    return " + ".join(dict.fromkeys(parts))


def can_attack_tile(rs: Ruleset, ut: UnitType, terrain: Terrain) -> bool:
    """Czy jednostka tego typu moze w ogole zaatakowac cel na takim kaflu."""
    if ut.attack <= 0:
        return False
    uc = rs.uclass_of(ut)
    if not terrain.native_to or uc.name in terrain.native_to:
        return True
    if "Only_Native_Attack" in ut.flags:
        return False
    return "AttackNonNative" in uc.flags


# ------------------------------------------------------------ sily bojowe

def attack_power(rs: Ruleset, att: Side, sit: Situation) -> PowerBreakdown:
    """Sila ataku w jednostkach POWER_FACTOR."""
    mods: list[Modifier] = []
    power = att.utype.attack * POWER_FACTOR

    vf = att.vet_fact()
    if vf != 100:
        mods.append(Modifier(f"weteran ({att.vet_name()})", vf / 100.0, "veteran"))
    power = power * vf // 100

    if rs.combat.tired_attack and att.moves_frags is not None:
        full = rs.move_fragments
        if 0 < att.moves_frags < full:
            mods.append(Modifier(
                f"zmeczony atak ({att.moves_frags}/{full} ruchu)",
                att.moves_frags / full, "tired"))
            power = power * att.moves_frags // full

    return PowerBreakdown(base=att.utype.attack * POWER_FACTOR,
                          total=float(power), modifiers=mods)


def defense_power(rs: Ruleset, att: Side, dfn: Side, sit: Situation,
                  ev: ReqEvaluator | None = None) -> tuple[PowerBreakdown, ReqEvaluator]:
    """Sila obrony w jednostkach POWER_FACTOR, z pelnym rozbiciem mnoznikow."""
    ev = ev or ReqEvaluator(rs, sit)
    mods: list[Modifier] = []
    dut, aut = dfn.utype, att.utype
    power = dut.defense * POWER_FACTOR

    vf = dfn.vet_fact()
    if vf != 100:
        mods.append(Modifier(f"weteran ({dfn.vet_name()})", vf / 100.0, "veteran"))
    power = power * vf // 100

    # bonus terenu - tylko dla klas z flaga TerrainDefense
    if rs.uclass_of(dut).terrain_defense and sit.terrain.defense_bonus:
        f = 100 + sit.terrain.defense_bonus
        mods.append(Modifier(f"teren: {sit.terrain.name}", f / 100.0, "terrain"))
        power = power * f // 100

    # bonusy jednostkowe: obronca kontra flagi atakujacego
    dm = dut.bonus_value("DefenseMultiplier", aut.flags) * 100 \
        + dut.bonus_value("DefenseMultiplierPct", aut.flags)
    if dm:
        f = 100 + dm
        mods.append(Modifier(f"{dut.name} kontra {aut.name}", f / 100.0, "bonus"))
        power = power * f // 100

    dd = aut.bonus_value("DefenseDivider", dut.flags) * 100 \
        + aut.bonus_value("DefenseDividerPct", dut.flags)
    if dd:
        f = 100 + dd
        mods.append(Modifier(f"{aut.name} przebija obrone {dut.name}",
                             100.0 / f, "bonus"))
        power = power * 100 // f

    # efekty Defend_Bonus - kontekstem jest jednostka ATAKUJACA
    city_pct = dut.bonus_value("CityDefensePct", aut.flags)
    if city_pct and sit.in_city:
        f = 100 + city_pct
        mods.append(Modifier(f"{dut.name}: obrona miasta", f / 100.0, "citydef"))
        power = power * f // 100
        defend_pct = city_pct
    else:
        defend_pct, dparts = ev.sum_effects("Defend_Bonus", aut, att.vet)
        if defend_pct:
            mods.append(Modifier(
                "umocnienia i pozycja", 1.0 + defend_pct / 100.0,
                "Defend_Bonus", dparts))
            power = power * (100 + defend_pct) // 100

    # ulepszenia terenu (rzeka, forteca...) - bonusy sie sumuja
    extra_pct = 0
    extra_parts: list[str] = []
    for name in sorted(sit.extras):
        e = rs.extras.get(name)
        if e and e.defense_bonus:
            extra_pct += e.defense_bonus
            extra_parts.append(f"{e.name} +{e.defense_bonus}%")
    if extra_pct:
        mods.append(Modifier("ulepszenia terenu", 1.0 + extra_pct / 100.0,
                             "extra", extra_parts))
        power = power * (100 + extra_pct) // 100

    # okopanie / darmowa fortyfikacja w miescie
    fort_pct, fparts = ev.sum_effects("Fortify_Defense_Bonus", dut, dfn.vet)
    if fort_pct:
        mods.append(Modifier("okopanie", 1.0 + fort_pct / 100.0,
                             "Fortify_Defense_Bonus", fparts))
        power = power * (100 + fort_pct) // 100

    return PowerBreakdown(base=dut.defense * POWER_FACTOR,
                          total=float(power), modifiers=mods), ev


def firepowers(rs: Ruleset, att: Side, dfn: Side, sit: Situation,
               defend_bonus_active: bool) -> tuple[int, int, list[str]]:
    """Sila ognia obu stron po uwzglednieniu regul specjalnych."""
    notes: list[str] = []
    afp = att.utype.firepower
    dfp = dfn.utype.firepower
    cr = rs.combat
    aut, dut = att.utype, dfn.utype

    if aut.has_bonus("LowFirepower", dut.flags):
        if dfp > cr.low_firepower_combat_bonus:
            dfp = cr.low_firepower_combat_bonus
            notes.append(f"{aut.name} tlumi sile ognia obroncy do {dfp}")
    if dut.has_bonus("LowFirepower", aut.flags):
        if afp > cr.low_firepower_combat_bonus:
            afp = cr.low_firepower_combat_bonus
            notes.append(f"{dut.name} tlumi sile ognia atakujacego do {afp}")

    if "BadWallAttacker" in aut.flags and defend_bonus_active:
        if afp > cr.low_firepower_badwallattacker:
            afp = cr.low_firepower_badwallattacker
            notes.append(f"{aut.name} zle radzi sobie z umocnieniami: "
                         f"sila ognia spada do {afp}")

    # atak z zywiolu, ktory nie jest wlasciwy dla kafla obroncy
    # (np. okret ostrzeliwujacy piechote na ladzie)
    a_class = rs.uclass_of(aut)
    if ("AttackNonNative" in a_class.flags
            and "NonNatBombardTgt" in rs.uclass_of(dut).flags
            and sit.terrain.native_to
            and a_class.name not in sit.terrain.native_to):
        limit = cr.low_firepower_nonnat_bombard
        if afp > limit or dfp > limit:
            afp, dfp = min(afp, limit), min(dfp, limit)
            notes.append(f"{aut.name} atakuje spoza swojego zywiolu - sila ognia "
                         f"obu stron spada do {limit}")

    if "BadCityDefender" in dut.flags and sit.in_city:
        afp *= 2
        if dfp > cr.low_firepower_pearl_harbour:
            dfp = cr.low_firepower_pearl_harbour
        notes.append(f"{dut.name} to kiepski obronca miasta: atakujacy ma "
                     f"podwojona sile ognia, obronca tylko {dfp}")

    return max(0, afp), max(0, dfp), notes


# --------------------------------------------------------------- pojedynek

def _duel_probabilities(p: float, hp_a: int, fp_a: int,
                        hp_d: int, fp_d: int) -> tuple[float, np.ndarray]:
    """Dokladny rozklad wyniku pojedynku.

    Zwraca (szansa wygranej atakujacego, rozklad liczby trafien atakujacego
    w przegranych pojedynkach). Uzywamy rozkladu ujemnego dwumianowego:
    atakujacy wygrywa, gdy zbierze ra trafien zanim obronca zbierze rd.
    """
    if fp_a <= 0:
        # atakujacy nie zadaje obrazen - nie moze wygrac
        return 0.0, np.array([1.0])
    ra = math.ceil(hp_d / fp_a)
    if fp_d <= 0:
        return 1.0, np.zeros(ra)
    rd = math.ceil(hp_a / fp_d)
    q = 1.0 - p

    if p <= 0.0:
        lose = np.zeros(ra)
        lose[0] = 1.0
        return 0.0, lose
    if p >= 1.0:
        return 1.0, np.zeros(ra)

    # log-przestrzen dla stabilnosci przy duzych HP
    log_p, log_q = math.log(p), math.log(q)
    lg = math.lgamma

    p_win = 0.0
    for k in range(rd):
        lc = lg(ra + k) - lg(ra) - lg(k + 1)
        p_win += math.exp(lc + ra * log_p + k * log_q)

    lose = np.zeros(ra)
    for j in range(ra):
        lc = lg(rd + j) - lg(rd) - lg(j + 1)
        lose[j] = math.exp(lc + rd * log_q + j * log_p)

    total_lose = lose.sum()
    p_win = min(1.0, max(0.0, p_win))
    if total_lose > 0:
        lose = lose / total_lose
    return p_win, lose


def duel(rs: Ruleset, att: Side, dfn: Side, sit: Situation,
         defender_hp: int | None = None) -> DuelResult:
    """Pelne rozliczenie jednego starcia."""
    abd = attack_power(rs, att, sit)
    dbd, ev = defense_power(rs, att, dfn, sit)
    defend_active = any(m.source == "Defend_Bonus" or m.source == "citydef"
                        for m in dbd.modifiers)
    afp, dfp, fnotes = firepowers(rs, att, dfn, sit, defend_active)

    A, D = abd.total, dbd.total
    if D <= 0:
        p_round = 1.0
    elif A <= 0:
        p_round = 0.0
    else:
        p_round = A / (A + D)

    hp_a = att.utype.hitpoints
    hp_d = defender_hp if defender_hp is not None else dfn.utype.hitpoints
    p_win, _ = _duel_probabilities(p_round, hp_a, afp, hp_d, dfp)

    warnings = list(fnotes)
    if ev.unsupported:
        warnings.append("Pominieto wymagania efektow, ktorych kalkulator nie "
                        "modeluje: " + ", ".join(sorted(ev.unsupported)))
    if rs.effects_by_type.get("Combat_Rounds"):
        warnings.append("Ten zestaw regul uzywa efektu Combat_Rounds "
                        "(limit rund) - wyniki sa przyblizone.")
    if att.utype.attack == 0:
        warnings.append(f"{att.utype.name} ma zerowy atak - nie moze atakowac.")
    if not can_attack_tile(rs, att.utype, sit.terrain):
        warnings.append(f"{att.utype.name} nie moze atakowac celu na kaflu typu "
                        f"{sit.terrain.name} (obcy zywiol).")

    return DuelResult(
        attack_power=A, defense_power=D, p_round=p_round, p_win=p_win,
        attacker_fp=afp, defender_fp=dfp, attacker_hp=hp_a, defender_hp=hp_d,
        rounds_needed_att=math.ceil(hp_d / afp) if afp > 0 else 0,
        rounds_needed_def=math.ceil(hp_a / dfp) if dfp > 0 else 0,
        attack_bd=abd, defense_bd=dbd, warnings=warnings,
    )


# ---------------------------------------------------------------- oblezenie

MAX_ATTACKS = 400


def siege(rs: Ruleset, att: Side, defenders: list[Side], sit: Situation,
          promotions: bool = True, trials: int = 40000,
          rng: np.random.Generator | None = None) -> SiegeResult:
    """Symuluje szturm na miasto bronione przez podany garnizon.

    Zalozenia (opisane tez w interfejsie):
      * kazdy atak wykonuje swieza jednostka atakujaca (jeden atak na turę),
      * obroncy nie leca sie w trakcie szturmu,
      * do obrony staje zawsze jednostka o najwyzszej szansie na przetrwanie,
        czyli przy jednakowych obroncach ta najmniej ranna.
    """
    rng = rng or np.random.default_rng(20260814)
    notes: list[str] = []

    # rozwiniecie garnizonu na pojedyncze jednostki
    flat: list[Side] = []
    for d in defenders:
        for _ in range(max(0, d.count)):
            flat.append(Side(d.utype, d.vet, 1))
    n_def = len(flat)
    if n_def == 0:
        empty = duel(rs, att, Side(att.utype), sit)
        return SiegeResult(0, np.ones(2), np.array([1.0, 0.0]), 0.0, 0.0, 0.0,
                           empty, [], 0.0,
                           ["Miasto nie ma obroncow - wystarczy jedna jednostka, "
                            "by do niego wejsc."])

    max_vet = max(len(d.utype.vet_levels) for d in flat)
    max_hp = max(d.utype.hitpoints for d in flat)

    # tablice pojedynkow dla kazdego typu obroncy, poziomu weterana i HP
    types: list[UnitType] = []
    type_idx: dict[str, int] = {}
    for d in flat:
        if d.utype.name not in type_idx:
            type_idx[d.utype.name] = len(types)
            types.append(d.utype)

    n_types = len(types)
    p_win_tab = np.zeros((n_types, max_vet, max_hp + 1))
    lose_cdf = np.zeros((n_types, max_vet, max_hp + 1, max_hp + 2))
    duels_repr: list[DuelResult] = []
    all_warnings: list[str] = []

    for ti, ut in enumerate(types):
        for vet in range(max(1, len(ut.vet_levels))):
            side = Side(ut, vet, 1)
            d0 = duel(rs, att, side, sit)
            if vet == 0:
                duels_repr.append(d0)
                for w in d0.warnings:
                    if w not in all_warnings:
                        all_warnings.append(w)
            for hp in range(1, ut.hitpoints + 1):
                pw, lose = _duel_probabilities(d0.p_round, d0.attacker_hp,
                                               d0.attacker_fp, hp, d0.defender_fp)
                p_win_tab[ti, vet, hp] = pw
                # lose[j] = obronca otrzymal j trafien -> zostaje mu hp - j*fp
                rem = np.maximum(1, hp - np.arange(len(lose)) * max(1, d0.attacker_fp))
                acc = np.zeros(max_hp + 2)
                for j, prob in enumerate(lose):
                    acc[rem[j]] += prob
                lose_cdf[ti, vet, hp] = np.cumsum(acc)

    # szanse awansu obroncy po wygranej obronie
    promo = np.zeros((n_types, max_vet))
    if promotions:
        ev = ReqEvaluator(rs, sit)
        for ti, ut in enumerate(types):
            vc, _ = ev.sum_effects("Veteran_Combat", ut, 0)
            for vet in range(max(1, len(ut.vet_levels))):
                if vet < len(ut.vet_levels):
                    base = ut.vet_levels[vet].raise_chance
                else:
                    base = 0
                if vet + 1 >= len(ut.vet_levels):
                    base = 0
                promo[ti, vet] = min(1.0, base * (100 + vc) / 10000.0)

    # ---- symulacja
    hp = np.zeros((trials, n_def), dtype=np.int32)
    vet = np.zeros((trials, n_def), dtype=np.int32)
    tidx = np.zeros(n_def, dtype=np.int32)
    for i, d in enumerate(flat):
        hp[:, i] = d.utype.hitpoints
        vet[:, i] = min(d.vet, max_vet - 1)
        tidx[i] = type_idx[d.utype.name]

    alive = np.ones((trials, n_def), dtype=bool)
    attacks = np.zeros(trials, dtype=np.int32)
    done = np.zeros(trials, dtype=bool)

    # sila obrony kazdego typu/weterana - do wyboru najlepszego obroncy
    def_strength = np.zeros((n_types, max_vet))
    for ti, ut in enumerate(types):
        for v in range(max(1, len(ut.vet_levels))):
            def_strength[ti, v] = duel(rs, att, Side(ut, v, 1), sit).defense_power

    for _step in range(MAX_ATTACKS):
        active = ~done
        if not active.any():
            break
        idx = np.flatnonzero(active)

        # Freeciv wystawia obronce o najwyzszej szansie przetrwania:
        # najpierw sila obrony, przy remisie - wiecej punktow zycia.
        strength = def_strength[tidx[None, :], vet[idx]]
        score = strength * 1000.0 + hp[idx]
        score = np.where(alive[idx], score, -1.0)
        pick = np.argmax(score, axis=1)

        rows = idx
        d_t = tidx[pick]
        d_v = vet[rows, pick]
        d_hp = hp[rows, pick]

        pw = p_win_tab[d_t, d_v, d_hp]
        roll = rng.random(len(rows))
        att_wins = roll < pw

        attacks[rows] += 1

        # atakujacy wygrywa -> obronca ginie
        win_rows = rows[att_wins]
        win_pick = pick[att_wins]
        if len(win_rows):
            alive[win_rows, win_pick] = False
            hp[win_rows, win_pick] = 0
            done[win_rows] = ~alive[win_rows].any(axis=1)

        # atakujacy ginie -> obronca zostaje ranny i moze awansowac
        lose_mask = ~att_wins
        lose_rows = rows[lose_mask]
        if len(lose_rows):
            lose_pick = pick[lose_mask]
            lt, lv, lhp = d_t[lose_mask], d_v[lose_mask], d_hp[lose_mask]
            u = rng.random(len(lose_rows))
            cdf = lose_cdf[lt, lv, lhp]
            new_hp = (cdf < u[:, None]).sum(axis=1)
            new_hp = np.clip(new_hp, 1, max_hp)
            hp[lose_rows, lose_pick] = new_hp
            if promotions:
                pr = promo[lt, lv]
                up = rng.random(len(lose_rows)) < pr
                vet[lose_rows[up], lose_pick[up]] = np.minimum(
                    lv[up] + 1, max_vet - 1)

    failure_rate = float((~done).mean())
    if failure_rate > 0.001:
        notes.append(f"W {failure_rate * 100:.1f}% symulacji miasto nie padlo "
                     f"nawet po {MAX_ATTACKS} atakach - ten atakujacy jest "
                     f"praktycznie bez szans.")

    finished = attacks[done]
    pmf = np.zeros(MAX_ATTACKS + 2)
    if len(finished):
        counts = np.bincount(finished, minlength=MAX_ATTACKS + 2)
        pmf = counts[:MAX_ATTACKS + 2] / trials
    cdf = np.cumsum(pmf)

    # rakiety i podobne jednostki gina takze wtedy, gdy wygraja starcie
    consumed = "Missile" in rs.uclass_of(att.utype).flags
    mean_attacks = float(finished.mean()) if len(finished) else float("inf")
    if not len(finished):
        mean_losses = float("inf")
    elif consumed:
        mean_losses = mean_attacks
    else:
        mean_losses = mean_attacks - n_def
    mean_shields = mean_losses * att.utype.build_cost if len(finished) else float("inf")

    return SiegeResult(
        n_defenders=n_def,
        p_success_by_attacks=cdf,
        attacks_pmf=pmf,
        mean_attacks=mean_attacks,
        mean_losses=mean_losses,
        mean_shields_lost=mean_shields,
        duel=duels_repr[0],
        defender_duels=duels_repr,
        failure_rate=failure_rate,
        notes=notes + all_warnings,
        consumed_on_attack=consumed,
    )


# ------------------------------------------------------------------- obrona

@dataclass
class DefenseResult:
    """Wynik odparcia szturmu: czy miasto sie utrzyma."""
    p_hold: float                    # szansa, ze miasto sie obroni
    mean_def_losses: float           # ilu obroncow zginie
    mean_att_losses: float           # ilu napastnikow zginie
    survivors_pmf: np.ndarray        # rozklad liczby ocalalych obroncow
    n_attacks: int                   # ile atakow wykona wrog
    n_defenders: int
    duels: list[tuple[str, str, DuelResult]]   # (napastnik, obronca, pojedynek)
    notes: list[str] = field(default_factory=list)


def veteran_build_level(rs: Ruleset, sit: Situation, ut: UnitType) -> int:
    """Na jakim poziomie weterana rodzi sie jednostka zbudowana w tym miescie.

    Sumuje efekty "Veteran_Build" (koszary, Sun Tzu itd.).
    """
    ev = ReqEvaluator(rs, sit)
    total, _ = ev.sum_effects("Veteran_Build", ut, 0)
    return max(0, min(total, len(ut.vet_levels) - 1))


def heals_fully_in_city(rs: Ruleset, sit: Situation, ut: UnitType) -> bool:
    """Czy jednostka odzyskuje w tym miescie 100% zycia na ture."""
    ev = ReqEvaluator(rs, sit)
    for eff in rs.effects_by_type.get("HP_Regen", []):
        if eff.value >= 100 and ev.effect_active(eff, ut, 0):
            return True
    return False


def _defender_tables(rs: Ruleset, profiles: list[Side], def_types: list[UnitType],
                     sit: Situation, max_hp: int):
    """Tablice pojedynkow dla kazdej pary (profil napastnika, typ obroncy)."""
    n_prof, n_def = len(profiles), len(def_types)
    max_vet = max((len(u.vet_levels) for u in def_types), default=1)
    p_win = np.zeros((n_prof, n_def, max_vet, max_hp + 1))
    lose_cdf = np.zeros((n_prof, n_def, max_vet, max_hp + 1, max_hp + 2))
    strength = np.zeros((n_prof, n_def, max_vet))
    duels: list[tuple[str, str, DuelResult]] = []

    for pi, att in enumerate(profiles):
        for di, dut in enumerate(def_types):
            for vet in range(max(1, len(dut.vet_levels))):
                d0 = duel(rs, att, Side(dut, vet, 1), sit)
                strength[pi, di, vet] = d0.defense_power
                if vet == 0:
                    duels.append((att.utype.name, dut.name, d0))
                for hp in range(1, dut.hitpoints + 1):
                    pw, lose = _duel_probabilities(
                        d0.p_round, d0.attacker_hp, d0.attacker_fp, hp, d0.defender_fp)
                    p_win[pi, di, vet, hp] = pw
                    rem = np.maximum(
                        1, hp - np.arange(len(lose)) * max(1, d0.attacker_fp))
                    acc = np.zeros(max_hp + 2)
                    for j, prob in enumerate(lose):
                        acc[rem[j]] += prob
                    lose_cdf[pi, di, vet, hp] = np.cumsum(acc)
    return p_win, lose_cdf, strength, duels


def defense_stand(rs: Ruleset, attackers: list[Side], defenders: list[Side],
                  sit: Situation, promotions: bool = True, trials: int = 30000,
                  rng: np.random.Generator | None = None) -> DefenseResult:
    """Czy garnizon odeprze szturm skonczonej grupy napastnikow.

    Model jednej tury: kazda jednostka wroga atakuje raz, w kolejnosci od
    najsilniejszej. Miasto pada, gdy zginie ostatni obronca.
    """
    rng = rng or np.random.default_rng(31337)
    notes: list[str] = []

    flat_def: list[Side] = []
    for d in defenders:
        for _ in range(max(0, d.count)):
            flat_def.append(Side(d.utype, d.vet, 1))
    n_def = len(flat_def)

    # napastnicy: rozwiniecie na pojedyncze jednostki, najsilniejsi pierwsi
    slots: list[Side] = []
    for a in attackers:
        for _ in range(max(0, a.count)):
            slots.append(Side(a.utype, a.vet, 1, a.moves_frags))
    slots = [s for s in slots if can_attack_tile(rs, s.utype, sit.terrain)]
    slots.sort(key=lambda s: -(s.utype.attack * s.vet_fact()))
    n_att = len(slots)

    if n_def == 0:
        return DefenseResult(0.0, 0.0, 0.0, np.array([1.0]), n_att, 0, [],
                             ["Puste miasto wróg zajmie bez walki."])
    if n_att == 0:
        return DefenseResult(1.0, 0.0, 0.0, np.eye(n_def + 1)[n_def], 0, n_def, [],
                             ["Żaden z wskazanych wrogów nie może zaatakować "
                              "tego kafla."])

    # unikalne profile napastnikow i typy obroncow
    prof_key: dict[tuple[str, int], int] = {}
    profiles: list[Side] = []
    slot_prof: list[int] = []
    for s in slots:
        key = (s.utype.name, s.vet)
        if key not in prof_key:
            prof_key[key] = len(profiles)
            profiles.append(s)
        slot_prof.append(prof_key[key])

    def_key: dict[str, int] = {}
    def_types: list[UnitType] = []
    for d in flat_def:
        if d.utype.name not in def_key:
            def_key[d.utype.name] = len(def_types)
            def_types.append(d.utype)

    max_hp = max(u.hitpoints for u in def_types)
    max_vet = max((len(u.vet_levels) for u in def_types), default=1)
    p_win, lose_cdf, strength, duels = _defender_tables(
        rs, profiles, def_types, sit, max_hp)

    promo = np.zeros((len(def_types), max_vet))
    if promotions:
        ev = ReqEvaluator(rs, sit)
        for di, ut in enumerate(def_types):
            vc, _ = ev.sum_effects("Veteran_Combat", ut, 0)
            for vet in range(max(1, len(ut.vet_levels))):
                base = ut.vet_levels[vet].raise_chance if vet < len(ut.vet_levels) else 0
                if vet + 1 >= len(ut.vet_levels):
                    base = 0
                promo[di, vet] = min(1.0, base * (100 + vc) / 10000.0)

    hp = np.zeros((trials, n_def), dtype=np.int32)
    vet = np.zeros((trials, n_def), dtype=np.int32)
    didx = np.zeros(n_def, dtype=np.int32)
    for i, d in enumerate(flat_def):
        hp[:, i] = d.utype.hitpoints
        vet[:, i] = min(d.vet, max_vet - 1)
        didx[i] = def_key[d.utype.name]

    alive = np.ones((trials, n_def), dtype=bool)
    att_losses = np.zeros(trials, dtype=np.int32)
    fallen = np.zeros(trials, dtype=bool)

    for slot in range(n_att):
        pi = slot_prof[slot]
        live = ~fallen
        if not live.any():
            break
        rows = np.flatnonzero(live)

        # do obrony staje jednostka o najwiekszej szansie przetrwania
        score = strength[pi, didx[None, :], vet[rows]] * 1000.0 + hp[rows]
        score = np.where(alive[rows], score, -1.0)
        pick = np.argmax(score, axis=1)

        d_t = didx[pick]
        d_v = vet[rows, pick]
        d_hp = hp[rows, pick]

        att_wins = rng.random(len(rows)) < p_win[pi, d_t, d_v, d_hp]

        win_rows, win_pick = rows[att_wins], pick[att_wins]
        if len(win_rows):
            alive[win_rows, win_pick] = False
            hp[win_rows, win_pick] = 0
            fallen[win_rows] = ~alive[win_rows].any(axis=1)

        lose_rows = rows[~att_wins]
        if len(lose_rows):
            lose_pick = pick[~att_wins]
            lt, lv, lhp = d_t[~att_wins], d_v[~att_wins], d_hp[~att_wins]
            u = rng.random(len(lose_rows))
            cdf = lose_cdf[pi, lt, lv, lhp]
            new_hp = np.clip((cdf < u[:, None]).sum(axis=1), 1, max_hp)
            hp[lose_rows, lose_pick] = new_hp
            att_losses[lose_rows] += 1
            if promotions:
                up = rng.random(len(lose_rows)) < promo[lt, lv]
                vet[lose_rows[up], lose_pick[up]] = np.minimum(lv[up] + 1, max_vet - 1)

    survivors = alive.sum(axis=1)
    p_hold = float((survivors > 0).mean())
    pmf = np.bincount(survivors, minlength=n_def + 1) / trials

    if rs.effects_by_type.get("Combat_Rounds"):
        notes.append("Ten zestaw reguł używa efektu Combat_Rounds (limit rund) "
                     "- wyniki są przybliżone.")

    return DefenseResult(
        p_hold=p_hold,
        mean_def_losses=float((n_def - survivors).mean()),
        mean_att_losses=float(att_losses.mean()),
        survivors_pmf=pmf,
        n_attacks=n_att,
        n_defenders=n_def,
        duels=duels,
        notes=notes,
    )
