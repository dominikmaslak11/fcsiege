# FCSiege

Kalkulator walki o miasto dla [Freeciva](https://www.freeciv.org/). Odpowiada na
dwa pytania:

* **Szturm** — ile jednostek muszę poświęcić, żeby zdobyć to miasto?
* **Obrona** — ile jednostek i jakich muszę zostawić, żeby wróg go nie zajął?

Wszystko liczone z oryginalnych plików `.ruleset`, więc wynik naprawdę zależy od
zestawu reguł, na którym grasz.

![Tryb szturmu](docs/01-szturm.png)

## Uruchomienie

```bash
git clone https://github.com/dominikmaslak11/fcsiege.git
cd fcsiege
pip install PySide6 numpy      # jeśli jeszcze ich nie masz
python3 fcsiege.py
```

Wymaga Pythona 3.10+.

## Skąd biorą się liczby

Aplikacja **nie ma zaszytej ani jednej statystyki**. Przy starcie parsuje pliki
`.ruleset` (`units`, `terrain`, `buildings`, `effects`, `techs`, `game`,
`governments`) i z nich buduje model walki. Ten sam scenariusz — 5 wojowników
w mieście na wzgórzu z murami — wygląda tak:

| ruleset | obrona | dlaczego |
|---|---|---|
| classic | **9.0** | wzgórze ×2, mury ×3, okopanie ×1.5 |
| sandbox / civ2civ3 | **5.5** | wzgórze ×1.5, a miasto +50% i mury +100% **sumują się** do ×2.5 |
| civ2 | **6.0** | mury kasują premię za okopanie |

Dołączone zestawy: `classic`, `sandbox`, `civ2civ3`, `multiplayer`, `civ1`,
`civ2`, `alien`. Aplikacja szuka też zestawów w `~/.local/share/freeciv`,
`/usr/share/freeciv` i w katalogach ze zmiennej `FREECIV_DATA_PATH`, więc widzi
również Twoje własne mody.

## Tryb szturmu

Wybierasz jednostkę, garnizon wroga, teren i budowle — dostajesz liczbę jednostek
potrzebnych na 90% pewności, oczekiwane straty i pełną krzywą prawdopodobieństwa.

Zakładka **Czym uderzyć** szereguje wszystkie dostępne jednostki według realnego
kosztu strat i inwestycji w tarczach:

![Czym uderzyć](docs/02-czym-uderzyc.png)

Zakładka **Rozbicie sił** pokazuje, skąd wzięła się każda liczba — z jawnym
rozbiciem na poszczególne efekty:

![Rozbicie sił](docs/03-rozbicie-sil.png)

### Teren, z którego atakujesz

We Freecivie teren atakującego **nie zmienia siły ataku** — liczy się wyłącznie
kafel obrońcy. Zakładka **Skąd atakować** pokazuje więc to, na co ten kafel
naprawdę wpływa: koszt wejścia, Twoją obronę i ryzyko śmierci przy kontrataku.

![Skąd atakować](docs/04-skad-atakowac.png)

## Tryb obrony

Role się odwracają: opisujesz siły wroga i swoje miasto, a aplikacja podaje
**minimalny garnizon**, który je utrzyma.

![Tryb obrony](docs/05-obrona.png)

Zakładka **Czym bronić** szereguje jednostki od najtańszego garnizonu, który
wystarczy — z kolumną „1 sztuka zatrzyma”, czyli miarą zapasu bezpieczeństwa:

![Czym bronić](docs/06-czym-bronic.png)

Zakładka **Wytrzymałość** to najbardziej praktyczna tabela w całym programie:
ilu napastników każdego typu odeprze garnizon danej wielkości.

![Wytrzymałość](docs/07-wytrzymalosc.png)

Wskazówki odpowiadają też na pytania, które łatwo przeoczyć — na przykład czy
rozkaz `fortify` w mieście cokolwiek daje (w `classic`, `sandbox` i `civ2civ3`
**nie daje nic**, bo kafel miasta przyznaje tę samą premię automatycznie):

![Wskazówki](docs/08-wskazowki.png)

## Model walki

Za `common/combat.c`:

```
siła ataku  = attack  × 10 × mnożnik_weterana [× zmęczenie przy tired_attack]

siła obrony = defense × 10 × mnożnik_weterana
              × (100 + bonus terenu)/100          (klasy z flagą TerrainDefense)
              × bonusy jednostkowe                 (np. pikinierzy kontra konnica)
              × (100 + Σ Defend_Bonus)/100         (mury, Wielki Mur, SAM, SDI…)
              × (100 + Σ bonusów ulepszeń)/100     (rzeka, forteca…)
              × (100 + Fortify_Defense_Bonus)/100  (okopanie / kafel miasta)
```

Efekty tego samego typu **sumują procenty**, a dopiero suma mnoży obronę.

W rundzie atakujący trafia z prawdopodobieństwem `A/(A+D)` i zadaje obrażenia
równe swojej sile ognia. Szansa wygrania pojedynku liczona jest **dokładnym
wzorem** (rozkład ujemny dwumianowy), nie losowaniem rund.

Obsługiwane są reguły siły ognia z `game.ruleset`: `BadWallAttacker`,
`BadCityDefender` (Pearl Harbour), bonus `LowFirepower` i atak spoza własnego
żywiołu, a także `Veteran_Build` (koszary, Sun Tzu) oraz `HP_Regen`.

### Model starcia o miasto

Rozliczenie jednej tury, symulacją Monte Carlo:

* każda jednostka atakuje raz,
* obrońcy **nie leczą się** w trakcie szturmu i zachowują odniesione rany,
* do obrony staje zawsze ten obrońca, który ma największe szanse przeżyć — przy
  jednakowych obrońcach ten najmniej ranny (tak działa `get_defender()`),
* obrońca po wygranej obronie może awansować (można wyłączyć).

Ponieważ każdy pojedynek kończy się śmiercią jednej ze stron,
**straty = liczba ataków − liczba obrońców** (dla rakiet, które giną także po
wygranej, straty = liczba ataków).

Oba tryby to ten sam model widziany z dwóch stron; test
`test_defense_matches_siege` sprawdza, że `P(zdobycia przy k atakach)` równa się
`1 − P(utrzymania przy k napastnikach)`.

## Testy

```bash
python3 tests/test_combat.py
```

Sprawdzają m.in.: zgodność wzoru na pojedynek z niezależną symulacją runda po
rundzie (200 tys. prób), ręcznie policzone wartości dla `classic`, różnice
między zestawami reguł, zużycie rakiet, działanie koszar i `fortify`,
monotoniczność obrony oraz 175 losowych scenariuszy na wszystkich zestawach.

## Ograniczenia

* Wymagania efektów, których kalkulator nie modeluje (rzadkie typy `reqs`), są
  traktowane jako niespełnione, a ich lista trafia do „Uwag silnika”.
* Zestaw `alien` używa efektu `Combat_Rounds` (limit rund) — wyniki są tam
  przybliżone i aplikacja o tym ostrzega.
* Kalkulator nie wie, czy miasto jest nadbrzeżne ani czy lotnictwo doleci —
  dlatego filtr „tylko jednostki, które mogą samodzielnie zająć miasto” jest
  domyślnie włączony.
* Model obejmuje **jedną turę** szturmu. To zwykle wystarcza, bo w mieście
  z koszarami obrońcy odzyskują między turami 100% życia.

## Układ kodu

| plik | rola |
|---|---|
| `fcsiege/registry.py` | parser formatu `.ruleset` (sekcje, listy, tabele, `*include`) |
| `fcsiege/model.py` | model danych zestawu reguł + drzewo technologii |
| `fcsiege/combat.py` | siły bojowe, ewaluator wymagań efektów, pojedynek, szturm, obrona |
| `fcsiege/advisor.py` | rankingi jednostek, ocena kafli, wskazówki |
| `fcsiege/widgets.py` | rysowane ręcznie: karta odpowiedzi, wykres, paski sił |
| `fcsiege/theme.py` | paleta i arkusz stylów |
| `fcsiege/app.py` | okno główne |
| `tools/screenshots.py` | generuje zrzuty do `docs/` (działa bez ekranu) |

## Licencja

GNU GPL w wersji 2 lub późniejszej — patrz [LICENSE](LICENSE).

Katalog `data/rulesets/` zawiera niezmienione pliki reguł z projektu Freeciv
(GPL-2.0-or-later); szczegóły i atrybucja w [NOTICE](NOTICE).
