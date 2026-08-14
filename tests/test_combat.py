"""Testy silnika walki. Uruchom: python3 tests/test_combat.py"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fcsiege.advisor import (counter_advice, max_wave_stopped, min_defenders,
                             rank_defenders, rank_staging_terrain, rank_units)
from fcsiege.combat import (Side, Situation, _duel_probabilities,
                            defense_stand, duel, heals_fully_in_city, siege,
                            veteran_build_level)
from fcsiege.model import Ruleset, discover_rulesets

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "rulesets")

failures = []


def check(name, ok, detail=""):
    print(("  OK   " if ok else "  BLAD ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def brute_force_duel(p, hp_a, fp_a, hp_d, fp_d, n=200000, seed=1):
    """Niezalezna symulacja runda po rundzie - punkt odniesienia dla wzoru."""
    rng = np.random.default_rng(seed)
    a = np.full(n, hp_a, dtype=np.int32)
    d = np.full(n, hp_d, dtype=np.int32)
    while True:
        live = (a > 0) & (d > 0)
        if not live.any():
            break
        hit = rng.random(n) < p
        d[live & hit] -= fp_a
        a[live & ~hit] -= fp_d
    return float((d <= 0).mean())


def test_duel_formula():
    """Wzor zamkniety musi zgadzac sie z symulacja runda po rundzie."""
    print("\nWzor na pojedynek kontra symulacja runda po rundzie:")
    cases = [(0.40, 10, 1, 10, 1), (0.31, 30, 1, 10, 1), (0.576, 10, 1, 10, 1),
             (0.25, 20, 2, 30, 1), (0.70, 20, 1, 20, 2), (0.50, 10, 3, 10, 1)]
    for p, hpa, fpa, hpd, fpd in cases:
        exact, _ = _duel_probabilities(p, hpa, fpa, hpd, fpd)
        sim = brute_force_duel(p, hpa, fpa, hpd, fpd)
        check(f"p={p} {hpa}/{fpa} vs {hpd}/{fpd}", abs(exact - sim) < 0.004,
              f"wzor {exact:.4f} / symulacja {sim:.4f}")


def test_classic_numbers():
    """Recznie policzone wartosci dla zestawu classic."""
    print("\nWartosci sil bojowych (classic):")
    rs = Ruleset.load(os.path.join(DATA, "classic"))
    sit = Situation(terrain=rs.terrains["Hills"], in_city=True,
                    buildings={"City Walls"}, fortified=True, city_size=8)
    d = duel(rs, Side(rs.units["Catapult"]), Side(rs.units["Warriors"]), sit)
    # 1 obrony x 10 -> wzgorza x2 -> mury x3 -> okopanie x1.5 = 90
    check("Wojownicy w miescie na wzgorzu z murami: obrona 9.0",
          abs(d.defense_power - 90) < 1e-6, f"jest {d.defense_power / 10:.1f}")
    check("Katapulta: atak 6.0", abs(d.attack_power - 60) < 1e-6)
    check("szansa trafienia 6/(6+9) = 0.4", abs(d.p_round - 0.4) < 1e-9)

    # pikinierzy kontra konnica: bonus jednostkowy x2
    plain = Situation(terrain=rs.terrains["Grassland"], in_city=False,
                      fortified=False)
    vs_knight = duel(rs, Side(rs.units["Knights"]), Side(rs.units["Pikemen"]), plain)
    vs_legion = duel(rs, Side(rs.units["Legion"]), Side(rs.units["Pikemen"]), plain)
    check("Pikinierzy maja podwojna obrone kontra Rycerzy",
          abs(vs_knight.defense_power - 2 * vs_legion.defense_power) < 1e-6,
          f"{vs_knight.defense_power / 10:.1f} kontra {vs_legion.defense_power / 10:.1f}")

    # haubica ignoruje mury
    how = duel(rs, Side(rs.units["Howitzer"]), Side(rs.units["Warriors"]), sit)
    check("Haubica ignoruje mury (obrona 3.0 zamiast 9.0)",
          abs(how.defense_power - 30) < 1e-6, f"jest {how.defense_power / 10:.1f}")


def test_ruleset_differences():
    """Rozne zestawy regul musza dawac rozne wyniki."""
    print("\nRoznice miedzy zestawami regul:")
    vals = {}
    for name in ("classic", "civ2civ3", "sandbox", "civ2"):
        rs = Ruleset.load(os.path.join(DATA, name))
        sit = Situation(terrain=rs.terrains["Hills"], in_city=True,
                        buildings={"City Walls"}, fortified=True, city_size=8)
        d = duel(rs, Side(rs.units["Catapult"]), Side(rs.units["Warriors"]), sit)
        vals[name] = d.defense_power / 10
    check("classic 9.0", abs(vals["classic"] - 9.0) < 1e-6, str(vals["classic"]))
    check("civ2 6.0 (bez darmowego okopania przy murach)",
          abs(vals["civ2"] - 6.0) < 1e-6, str(vals["civ2"]))
    check("sandbox 5.5 (premie Defend_Bonus sie sumuja)",
          abs(vals["sandbox"] - 5.5) < 1e-6, str(vals["sandbox"]))
    check("civ2civ3 rowna sandboxowi", abs(vals["civ2civ3"] - vals["sandbox"]) < 1e-6)


def test_siege_invariants():
    """Wlasnosci, ktore musza zachodzic w kazdym scenariuszu."""
    print("\nNiezmienniki oblezenia (wszystkie zestawy):")
    import random
    random.seed(11)
    bad = 0
    total = 0
    for d in discover_rulesets(DATA):
        rs = Ruleset.load(d)
        atts = [u for u in rs.units.values() if u.attack > 0]
        defs = [u for u in rs.units.values() if u.defense > 0]
        terrs = list(rs.terrains.values())
        blds = [b.name for b in rs.defensive_buildings()]
        for _ in range(25):
            total += 1
            sit = Situation(
                terrain=random.choice(terrs),
                in_city=random.random() < 0.7,
                city_size=random.randint(1, 20),
                buildings=set(random.sample(blds, k=min(2, len(blds)))) if blds else set(),
                fortified=random.random() < 0.5,
                gov=rs.governments[0] if rs.governments else "Despotism",
                techs=set(rs.techs), units_on_tile=3)
            garrison = [Side(random.choice(defs), 0, random.randint(1, 5))]
            try:
                r = siege(rs, Side(random.choice(atts)), garrison, sit, trials=1200)
                assert np.all(np.diff(r.p_success_by_attacks) >= -1e-9)
                assert 0.0 <= r.p_with(999) <= 1.0 + 1e-9
                if np.isfinite(r.mean_attacks):
                    assert r.mean_attacks >= r.n_defenders - 1e-9
                counter_advice(rs, r, Side(random.choice(atts)), garrison, sit)
                rank_staging_terrain(rs, Side(random.choice(atts)), garrison, sit)
            except Exception as exc:  # noqa: BLE001
                bad += 1
                print(f"    {rs.name}: {type(exc).__name__}: {exc}")
    check(f"{total} losowych scenariuszy bez bledow", bad == 0, f"bledow: {bad}")


def test_missile_losses():
    """Rakiety gina takze wtedy, gdy wygraja starcie."""
    print("\nZuzycie jednostek jednorazowych:")
    rs = Ruleset.load(os.path.join(DATA, "classic"))
    sit = Situation(terrain=rs.terrains["Hills"], in_city=True,
                    buildings={"City Walls"}, fortified=True)
    g = [Side(rs.units["Warriors"], 0, 5)]
    m = siege(rs, Side(rs.units["Cruise Missile"]), g, sit, trials=8000)
    c = siege(rs, Side(rs.units["Cannon"]), g, sit, trials=8000)
    check("Pocisk manewrujacy traci tyle jednostek, ile wykonal atakow",
          abs(m.mean_losses - m.mean_attacks) < 1e-9,
          f"{m.mean_losses:.2f} z {m.mean_attacks:.2f}")
    check("Dziala tracimy tylko przy przegranych starciach",
          abs(c.mean_losses - (c.mean_attacks - 5)) < 1e-9)


def test_fortify_in_city():
    """Okopanie w miescie: w wiekszosci zestawow nic nie daje."""
    print("\nCzy rozkaz fortify w miescie cokolwiek zmienia:")
    expect = {"classic": False, "sandbox": False, "civ2civ3": False, "civ2": True}
    for name, should_matter in expect.items():
        rs = Ruleset.load(os.path.join(DATA, name))
        vals = []
        for fort in (False, True):
            sit = Situation(terrain=rs.terrains["Hills"], in_city=True,
                            buildings=set(), fortified=fort, city_size=6)
            vals.append(duel(rs, Side(rs.units["Warriors"]),
                             Side(rs.units["Phalanx"]), sit).defense_power)
        differs = abs(vals[0] - vals[1]) > 1e-9
        check(f"{name}: fortify {'zmienia' if should_matter else 'nic nie daje'}",
              differs == should_matter, f"{vals[0] / 10:.1f} -> {vals[1] / 10:.1f}")


def test_barracks_veteran():
    """Koszary nadaja stopien weterana i pelne leczenie."""
    print("\nKoszary:")
    rs = Ruleset.load(os.path.join(DATA, "classic"))
    ph = rs.units["Phalanx"]
    bare = Situation(terrain=rs.terrains["Hills"], in_city=True, buildings=set())
    with_b = Situation(terrain=rs.terrains["Hills"], in_city=True,
                       buildings={"Barracks"})
    check("bez koszar jednostka jest zielona",
          veteran_build_level(rs, bare, ph) == 0)
    check("z koszarami startuje o stopien wyzej",
          veteran_build_level(rs, with_b, ph) == 1)
    check("koszary daja pelne leczenie w miescie",
          heals_fully_in_city(rs, with_b, ph)
          and not heals_fully_in_city(rs, bare, ph))
    check("Koszary sa na liscie budowli obronnych",
          "Barracks" in [b.name for b in rs.defensive_buildings()])


def test_defense_monotonic():
    """Wiecej obroncow nie moze zmniejszyc szansy utrzymania miasta."""
    print("\nMonotonicznosc obrony:")
    rs = Ruleset.load(os.path.join(DATA, "classic"))
    sit = Situation(terrain=rs.terrains["Hills"], in_city=True,
                    buildings={"City Walls"}, fortified=True, city_size=6)
    enemy = [Side(rs.units["Catapult"], 0, 8)]
    prev = -1.0
    ok = True
    seq = []
    for m in range(1, 7):
        r = defense_stand(rs, enemy, [Side(rs.units["Phalanx"], 0, m)], sit,
                          trials=6000)
        seq.append(round(r.p_hold, 3))
        if r.p_hold < prev - 0.02:
            ok = False
        prev = r.p_hold
    check("szansa utrzymania rosnie z liczba obroncow", ok, str(seq))

    mc, p = min_defenders(rs, enemy, Side(rs.units["Phalanx"]), sit,
                          confidence=0.95, trials=6000)
    check("min_defenders zwraca liczbe spelniajaca prog",
          mc is not None and p >= 0.95, f"{mc} obroncow, p={p:.3f}")


def test_defense_matches_siege():
    """Obrona i szturm to ten sam model widziany z dwoch stron."""
    print("\nZgodnosc trybu obrony z trybem szturmu:")
    rs = Ruleset.load(os.path.join(DATA, "classic"))
    sit = Situation(terrain=rs.terrains["Hills"], in_city=True,
                    buildings={"City Walls"}, fortified=True, city_size=6)
    garrison = [Side(rs.units["Warriors"], 0, 3)]
    att = Side(rs.units["Catapult"])
    for k in (4, 8, 12):
        s_res = siege(rs, att, garrison, sit, trials=30000)
        d_res = defense_stand(rs, [Side(rs.units["Catapult"], 0, k)],
                              garrison, sit, trials=30000)
        # szansa zdobycia przy k atakach = 1 - szansa utrzymania przy k napastnikach
        diff = abs(s_res.p_with(k) - (1.0 - d_res.p_hold))
        check(f"{k} atakow: zdobycie {s_res.p_with(k):.3f} "
              f"= 1 - utrzymanie {d_res.p_hold:.3f}", diff < 0.02,
              f"roznica {diff:.4f}")


def test_defender_ranking():
    """Ranking obroncow zwraca sensowne minimum."""
    print("\nRanking obroncow:")
    rs = Ruleset.load(os.path.join(DATA, "classic"))
    sit = Situation(terrain=rs.terrains["Hills"], in_city=True,
                    buildings={"City Walls", "Barracks"}, fortified=True,
                    city_size=6)
    enemy = [Side(rs.units["Warriors"], 1, 5)]
    opts = rank_defenders(rs, enemy, sit, rs.techs_up_to(4), trials=3000)
    check("sa jakiekolwiek opcje obrony", len(opts) > 0)
    check("kazda opcja ma wyliczone minimum",
          all(o.min_count is not None for o in opts))
    ph = next((o for o in opts if o.name == "Phalanx"), None)
    wa = next((o for o in opts if o.name == "Warriors"), None)
    check("Falanga wytrzymuje wieksza fale niz Wojownicy",
          ph is not None and wa is not None and ph.stops_alone > wa.stops_alone,
          f"falanga {ph.stops_alone if ph else '?'} kontra "
          f"wojownicy {wa.stops_alone if wa else '?'}")
    check("jednostki z koszar startuja jako weterani",
          all(o.vet_on_build >= 1 for o in opts))


if __name__ == "__main__":
    test_duel_formula()
    test_classic_numbers()
    test_ruleset_differences()
    test_missile_losses()
    test_fortify_in_city()
    test_barracks_veteran()
    test_defense_monotonic()
    test_defense_matches_siege()
    test_defender_ranking()
    test_siege_invariants()
    print("\n" + "=" * 60)
    if failures:
        print(f"NIEPOWODZENIA ({len(failures)}): " + ", ".join(failures))
        sys.exit(1)
    print("Wszystkie testy przeszly.")
