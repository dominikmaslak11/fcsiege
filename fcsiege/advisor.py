"""Rekomendacje: czym uderzyc i skad, zeby stracic jak najmniej."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .combat import (Side, Situation, SiegeResult, can_attack_tile,
                     duel, siege)
from .model import Ruleset, Terrain, UnitType


@dataclass
class UnitOption:
    """Ocena jednego typu jednostki jako narzedzia szturmu."""
    utype: UnitType
    p_single: float          # szansa wygranej w pojedynczym starciu
    mean_attacks: float      # ile atakow potrzeba srednio
    mean_losses: float       # ile jednostek srednio zginie
    shields_lost: float      # koszt strat w tarczach
    attacks_90: int | None   # ile jednostek na 90% pewnosci
    shields_90: int | None
    can_occupy: bool
    tech_depth: int
    req_techs: list[str]
    invest_90: int | None = None   # tarcze potrzebne, by wystawic taka armie

    @property
    def name(self) -> str:
        return self.utype.name


@dataclass
class TerrainOption:
    """Ocena kafla, z ktorego mozna poprowadzic natarcie."""
    terrain: Terrain
    move_cost: int
    own_defense: float       # sila obrony naszej jednostki na tym kaflu
    p_counter: float         # szansa, ze kontratak obroncy nas zabije


def rank_units(rs: Ruleset, defenders: list[Side], sit: Situation,
               known_techs: set[str] | None, attacker_vet: int = 0,
               promotions: bool = True, trials: int = 6000,
               limit: int = 0, occupiers_only: bool = False) -> list[UnitOption]:
    """Szereguje dostepne jednostki od najtanszego w stratach szturmu."""
    out: list[UnitOption] = []
    rng = np.random.default_rng(4242)

    for ut in rs.units_available(known_techs):
        if ut.attack <= 0 or "NonMil" in ut.flags:
            continue
        uc = rs.uclass_of(ut)
        if not can_attack_tile(rs, ut, sit.terrain):
            continue
        if occupiers_only and not uc.can_occupy_city:
            continue

        att = Side(ut, attacker_vet, 1)
        d = duel(rs, att, defenders[0], sit)
        if d.attack_power <= 0:
            continue

        res = siege(rs, att, defenders, sit, promotions=promotions,
                    trials=trials, rng=rng)
        if not np.isfinite(res.mean_attacks):
            continue

        a90 = res.attacks_for(0.90)
        occupy_extra = 0 if rs.uclass_of(ut).can_occupy_city else 1
        out.append(UnitOption(
            utype=ut,
            p_single=d.p_win,
            mean_attacks=res.mean_attacks,
            mean_losses=res.mean_losses,
            shields_lost=res.mean_shields_lost,
            attacks_90=a90,
            shields_90=(a90 * ut.build_cost) if a90 is not None else None,
            invest_90=((a90 + occupy_extra) * ut.build_cost)
            if a90 is not None else None,
            can_occupy=uc.can_occupy_city,
            tech_depth=rs.unit_tech_depth(ut),
            req_techs=ut.req_techs(),
        ))

    # najpierw najmniejsze straty, a przy remisie najtansza armia do wystawienia
    out.sort(key=lambda o: (round(o.shields_lost, 1),
                            o.invest_90 if o.invest_90 is not None else 10**9,
                            o.mean_attacks))
    return out[:limit] if limit else out


def rank_staging_terrain(rs: Ruleset, att: Side, defenders: list[Side],
                         sit: Situation) -> list[TerrainOption]:
    """Ocenia kafle, z ktorych mozna uderzyc.

    We Freecivie teren atakujacego NIE zmienia sily jego ataku. Ma jednak
    znaczenie praktyczne: kosztuje ruch (a przy tired_attack brak pelnego
    punktu ruchu oslabia atak) i decyduje, jak przezyjemy kontratak.
    """
    out: list[TerrainOption] = []
    best_def = max(defenders, key=lambda d: d.utype.attack) if defenders else None
    uc_name = rs.uclass_of(att.utype).name

    for terr in rs.terrains.values():
        # tylko kafle, na ktorych nasza jednostka moze w ogole stanac
        if terr.native_to and uc_name not in terr.native_to:
            continue
        staging = Situation(
            terrain=terr, extras=set(sit.attacker_extras), in_city=False,
            city_size=0, buildings=set(), player_buildings=set(),
            fortified=True, gov=sit.gov, techs=sit.techs, units_on_tile=1)

        own_def = 0.0
        p_counter = 0.0
        if best_def is not None and best_def.utype.attack > 0:
            counter = duel(rs, Side(best_def.utype, best_def.vet), att, staging)
            own_def = counter.defense_power
            p_counter = counter.p_win

        out.append(TerrainOption(
            terrain=terr,
            move_cost=terr.movement_cost,
            own_defense=own_def,
            p_counter=p_counter,
        ))

    out.sort(key=lambda o: (o.p_counter, o.move_cost))
    return out


def counter_advice(rs: Ruleset, res: SiegeResult, att: Side,
                   defenders: list[Side], sit: Situation) -> list[str]:
    """Krotkie, konkretne wskazowki taktyczne do biezacego scenariusza."""
    tips: list[str] = []
    d = res.duel

    if d.attack_power <= 0:
        return [f"{att.utype.name} nie ma sily ataku - tą jednostką nie zdobędziesz miasta."]

    # co najbardziej podbija obrone
    mods = sorted(d.defense_bd.modifiers, key=lambda m: -m.factor)
    lead = ["Najmocniej broni ich", "Drugi w kolejności jest"]
    for i, m in enumerate(mods[:2]):
        if m.factor >= 1.5:
            pct = (m.factor - 1) * 100
            tips.append(f"{lead[i]} <b>{m.label}</b> (+{pct:.0f}%) — bez tego "
                        f"obrona spadłaby z {d.defense_power / 10:.1f} do "
                        f"{d.defense_power / m.factor / 10:.1f}.")

    # czy istnieje jednostka ignorujaca mury
    walls_active = any(m.source == "Defend_Bonus" and m.factor > 1.0
                       for m in d.defense_bd.modifiers)
    if walls_active:
        ignorers = [u.name for u in rs.units.values()
                    if u.attack > 0 and _ignores_walls(rs, u, defenders[0], sit)]
        if ignorers:
            tips.append("Umocnienia miasta omijają: " + ", ".join(ignorers[:4]) + ".")

    # weterani
    if att.vet + 1 < len(att.utype.vet_levels):
        nxt = att.utype.vet_levels[att.vet + 1]
        cur = att.utype.vet_levels[att.vet].power_fact
        tips.append(f"Awans na poziom „{nxt.name}” podniósłby siłę ataku o "
                    f"{(nxt.power_fact / cur - 1) * 100:.0f}% "
                    f"(koszary / Sun Tzu dają go od razu przy budowie).")

    if not rs.uclass_of(att.utype).can_occupy_city:
        tips.append(f"{att.utype.name} nie może zająć miasta - po wybiciu "
                    f"obrońców musisz mieć w pobliżu jednostkę lądową.")

    if res.mean_attacks > 0 and np_isfinite(res.mean_attacks):
        tips.append(f"Zabierz zapas: średnia to {res.mean_attacks:.1f} ataku, "
                    f"ale na 90% pewności potrzeba {res.attacks_for(0.90)} "
                    f"jednostek, a na wejście do miasta jeszcze jedną.")

    return tips


def _ignores_walls(rs: Ruleset, attacker: UnitType, defender: Side,
                   sit: Situation) -> bool:
    """Czy dany atakujacy nie dostaje po nosie od efektow obronnych miasta."""
    from .combat import ReqEvaluator
    ev = ReqEvaluator(rs, sit)
    total = 0
    for eff in rs.effects_by_type.get("Defend_Bonus", []):
        if ev.effect_active(eff, attacker, 0):
            total += eff.value
    return total == 0


def np_isfinite(x: float) -> bool:
    return x == x and x not in (float("inf"), float("-inf"))


# ------------------------------------------------------------------- obrona

from .combat import DefenseResult, defense_stand, heals_fully_in_city, \
    veteran_build_level  # noqa: E402


@dataclass
class DefenderOption:
    """Ocena typu jednostki jako obroncy miasta."""
    utype: UnitType
    vet_on_build: int
    vet_name: str
    min_count: int | None      # ile sztuk, by utrzymac miasto z zadana pewnoscia
    p_at_min: float
    p_single: float            # szansa utrzymania przy jednej sztuce
    stops_alone: int           # ilu takich napastnikow zatrzyma jedna sztuka
    shields: int | None        # koszt budowy min_count sztuk
    upkeep: int                # laczne utrzymanie w tarczach na ture
    defense_power: float
    heals_fully: bool
    req_techs: list[str]

    @property
    def name(self) -> str:
        return self.utype.name


MAX_GARRISON = 12
MAX_WAVE = 40


def min_defenders(rs: Ruleset, attackers: list[Side], defender: Side,
                  sit: Situation, confidence: float = 0.95,
                  promotions: bool = True, trials: int = 8000,
                  rng=None) -> tuple[int | None, float]:
    """Najmniejsza liczba takich obroncow, ktora utrzyma miasto."""
    lo, hi = 1, MAX_GARRISON
    best: tuple[int | None, float] = (None, 0.0)
    while lo <= hi:
        mid = (lo + hi) // 2
        r = defense_stand(rs, attackers, [Side(defender.utype, defender.vet, mid)],
                          sit, promotions=promotions, trials=trials, rng=rng)
        if r.p_hold >= confidence:
            best = (mid, r.p_hold)
            hi = mid - 1
        else:
            lo = mid + 1
    return best


def wave_is_capped(k: int) -> bool:
    """Czy wynik oparl sie o gorny limit sprawdzanej fali."""
    return k >= MAX_WAVE


def max_wave_stopped(rs: Ruleset, attacker: Side, defenders: list[Side],
                     sit: Situation, confidence: float = 0.95,
                     promotions: bool = True, trials: int = 6000,
                     rng=None) -> int:
    """Ilu napastnikow tego typu garnizon odeprze z zadana pewnoscia."""
    lo, hi = 0, MAX_WAVE
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if mid == 0:
            lo = 1
            best = 0
            continue
        r = defense_stand(rs, [Side(attacker.utype, attacker.vet, mid)],
                          defenders, sit, promotions=promotions,
                          trials=trials, rng=rng)
        if r.p_hold >= confidence:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def rank_defenders(rs: Ruleset, attackers: list[Side], sit: Situation,
                   known_techs: set[str] | None, confidence: float = 0.95,
                   promotions: bool = True, trials: int = 6000,
                   from_barracks: bool = True) -> list[DefenderOption]:
    """Szereguje jednostki obronne od najtanszej, ktora utrzyma miasto."""
    out: list[DefenderOption] = []
    rng = np.random.default_rng(909)
    main_att = max(attackers, key=lambda a: a.utype.attack * a.vet_fact()) \
        if attackers else None

    for ut in rs.units_available(known_techs):
        if ut.defense <= 0 or "NonMil" in ut.flags:
            continue
        uc = rs.uclass_of(ut)
        # obroncy miasta to jednostki, ktore moga w nim stac
        if sit.terrain.native_to and uc.name not in sit.terrain.native_to:
            continue

        vet = veteran_build_level(rs, sit, ut) if from_barracks else 0
        side = Side(ut, vet, 1)
        mc, p_at = min_defenders(rs, attackers, side, sit, confidence,
                                 promotions, trials, rng)
        single = defense_stand(rs, attackers, [Side(ut, vet, 1)], sit,
                               promotions=promotions, trials=trials, rng=rng)
        stops = max_wave_stopped(rs, main_att, [Side(ut, vet, 1)], sit,
                                 confidence, promotions, trials, rng) \
            if main_att else 0
        d = duel(rs, main_att or side, Side(ut, vet, 1), sit) if main_att else None

        out.append(DefenderOption(
            utype=ut,
            vet_on_build=vet,
            vet_name=side.vet_name(),
            min_count=mc,
            p_at_min=p_at,
            p_single=single.p_hold,
            stops_alone=stops,
            shields=(mc * ut.build_cost) if mc is not None else None,
            upkeep=(mc or 0) * ut.uk_shield,
            defense_power=d.defense_power if d else 0.0,
            heals_fully=heals_fully_in_city(rs, sit, ut),
            req_techs=ut.req_techs(),
        ))

    out.sort(key=lambda o: (o.min_count if o.min_count is not None else 99,
                            o.shields if o.shields is not None else 10 ** 9,
                            -o.stops_alone))
    return out


def defense_advice(rs: Ruleset, res: DefenseResult, attackers: list[Side],
                   defenders: list[Side], sit: Situation,
                   stops: int | None = None) -> list[str]:
    """Wskazowki dla obroncy."""
    tips: list[str] = []
    if not res.duels:
        return tips
    att_name, def_name, d = res.duels[0]

    tips.append(f"Jeden {att_name} kontra {def_name}: siła {d.attack_power / 10:.1f} "
                f"przeciw {d.defense_power / 10:.1f}, więc napastnik wygrywa "
                f"starcie z prawdopodobieństwem {d.p_win * 100:.1f}%.")

    mods = sorted(d.defense_bd.modifiers, key=lambda m: -m.factor)
    if mods:
        m = mods[0]
        tips.append(f"Najwięcej daje ci <b>{m.label}</b> (×{m.factor:.2f}) — bez "
                    f"tego obrona spadłaby do {d.defense_power / m.factor / 10:.1f}.")

    dut = defenders[0].utype if defenders else None
    if dut is not None:
        if heals_fully_in_city(rs, sit, dut):
            tips.append("Ranni obrońcy odzyskują w tym mieście <b>100% życia co "
                        "turę</b>, więc odparcie jednej tury szturmu w pełni "
                        "odbudowuje garnizon — koszary są tu warte więcej niż "
                        "dodatkowa jednostka.")
        vb = veteran_build_level(rs, sit, dut)
        if vb > 0:
            lv = dut.vet_levels[min(vb, len(dut.vet_levels) - 1)]
            tips.append(f"Jednostki budowane w tym mieście od razu mają stopień "
                        f"„{lv.name}” (×{lv.power_fact / 100:g} do siły) — "
                        f"buduj obrońców właśnie tutaj.")

    if stops:
        cap = "+" if stops >= MAX_WAVE else ""
        tips.append(f"Ten garnizon odeprze <b>{stops}{cap}</b> takich napastników "
                    f"w jednej turze przy 95% pewności.")

    # czy jawne okopanie w miescie cokolwiek daje
    if sit.in_city and defenders:
        from .combat import defense_power
        base = Situation(**{**sit.__dict__, "fortified": False})
        forted = Situation(**{**sit.__dict__, "fortified": True})
        a0 = attackers[0] if attackers else Side(defenders[0].utype)
        d_off, _ = defense_power(rs, a0, defenders[0], base)
        d_on, _ = defense_power(rs, a0, defenders[0], forted)
        if abs(d_off.total - d_on.total) < 1e-9:
            tips.append("Rozkaz „fortify” w mieście <b>nic tu nie zmienia</b> — "
                        "kafel miasta i tak daje tę samą premię automatycznie "
                        "(patrz „Rozbicie sił”).")
        else:
            tips.append(f"Rozkaz „fortify” <b>ma tu znaczenie</b>: obrona rośnie "
                        f"z {d_off.total / 10:.1f} do {d_on.total / 10:.1f}. "
                        f"Trzymaj obrońców okopanych.")

    if res.mean_att_losses >= res.n_attacks - 0.01 and res.p_hold > 0.99:
        tips.append("Przy takim stosunku sił szturm jest dla wroga czystą stratą "
                    "— cała jego grupa ginie pod murami.")

    # czy warto wyjsc i uderzyc
    if attackers and defenders and sit.attacker_terrain is not None:
        field = Situation(terrain=sit.attacker_terrain, in_city=False,
                          fortified=True, gov=sit.gov, techs=sit.techs)
        sortie = duel(rs, Side(defenders[0].utype, defenders[0].vet),
                      Side(attackers[0].utype, attackers[0].vet), field)
        tips.append(f"Wypad z miasta: twój {defenders[0].utype.name} atakujący "
                    f"{attackers[0].utype.name} na kaflu „{sit.attacker_terrain.name}” "
                    f"wygrywa tylko w {sortie.p_win * 100:.0f}% — poza murami "
                    f"tracisz całą premię obronną.")
    return tips
