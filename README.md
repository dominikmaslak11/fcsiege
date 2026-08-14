# FCSiege

Kalkulator walki o miasto dla [Freeciva](https://www.freeciv.org/). Odpowiada na
dwa pytania:

* **Szturm** — ile jednostek muszę poświęcić, żeby zdobyć to miasto?
* **Obrona** — ile jednostek i jakich muszę zostawić, żeby wróg go nie zajął?

Można je zadać klikając w panelach albo **napisać zwykłym zdaniem do wbudowanego
asystenta**, który sam ustawi scenariusz i policzy.

Wszystko liczone z oryginalnych plików `.ruleset`, więc wynik naprawdę zależy od
zestawu reguł, na którym grasz.

![Tryb szturmu](docs/01-szturm.png)

## Uruchomienie

```bash
git clone https://github.com/dominikmaslak11/fcsiege.git
cd fcsiege
pip install PySide6 numpy      # jeśli jeszcze ich nie masz
pip install anthropic mcp      # opcjonalnie: asystent i serwer MCP
python3 fcsiege.py
```

Wymaga Pythona 3.10+. Bez `anthropic` działa wszystko poza czatem, bez `mcp` —
wszystko poza serwerem MCP.

Cztery sposoby uruchomienia:

```bash
python3 fcsiege.py             # okno aplikacji
python3 fcsiege.py --control   # okno + gniazdo sterujące (dla MCP i API)
python3 fcsiege.py mcp         # serwer MCP po stdio
python3 fcsiege.py api         # API HTTP na 127.0.0.1:8765
```

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

## Asystent

Przycisk **Asystent** w nagłówku otwiera czat. Opisujesz sytuację zwykłym
zdaniem, a model ustawia scenariusz i uruchamia obliczenia — zmiany widać na
żywo w panelach po lewej, bo asystent klika w ten sam interfejs co Ty.

![Asystent](docs/09-asystent.png)

**Model:** `claude-opus-5`, adaptacyjne myślenie, odpowiedź strumieniowana.
Włączony jest serwerowy `fallbacks: "default"` — gdy klasyfikator bezpieczeństwa
odrzuci zapytanie, API automatycznie przenosi je na model zapasowy.

**Logowanie.** Aplikacja szuka poświadczeń w kolejności: zmienna
`ANTHROPIC_API_KEY` → klucz zapisany w `~/.config/fcsiege/credentials.json`
(prawa 0600) → profil OAuth z `~/.config/anthropic` (zakładany przez CLI
Anthropica poleceniem `ant auth login`). Jeśli nic nie znajdzie, panel czatu
poprosi o klucz z [console.claude.com](https://console.claude.com/settings/keys).

> Uwaga: w wielu dystrybucjach polecenie `ant` to **Apache Ant**, nie CLI
> Anthropica. Aplikacja to wykrywa i mówi wprost, której drogi logowania
> możesz użyć.

**Czym asystent steruje** (15 narzędzi): `pokaz_stan`, `ustaw_scenariusz`,
`ustaw_moja_jednostke`, `ustaw_sily_wroga`, `policz`, `ranking`,
`tabela_wytrzymalosci`, `dane_jednostki`, `spis`, oraz wywiad z zapisu gry:
`wczytaj_zapis`, `moje_wojska`, `wywiad_o_nacji`, `linia_frontu`,
`porownaj_ustroje`, `przejezdnosc`.

Wszystkie liczby pochodzą z tego samego silnika co panele — prompt systemowy
zabrania modelowi szacowania wyników walki z pamięci. Narzędzia wykonują się
w wątku interfejsu, więc czat i klikanie nigdy nie rozjeżdżają się ze sobą.

**Co jest wysyłane:** treść rozmowy i ustawienia scenariusza (jednostki, teren,
budowle) trafiają do API Anthropic. Bez otwarcia czatu aplikacja nie łączy się
z siecią w ogóle.

## Serwer MCP

Ten sam kalkulator jako serwer [MCP](https://modelcontextprotocol.io/) — Claude
Code, Claude Desktop czy dowolny inny klient MCP może liczyć scenariusze bez
otwierania aplikacji.

```bash
claude mcp add fcsiege -- python3 /ścieżka/do/fcsiege/fcsiege.py mcp --ruleset sandbox
```

Konfiguracja Claude Desktop (`~/.config/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "fcsiege": {
      "command": "python3",
      "args": ["/ścieżka/do/fcsiege/fcsiege.py", "mcp", "--ruleset", "sandbox"]
    }
  }
}
```

Domyślnie (`--attach auto`) serwer liczy we własnym stanie. Jeśli jednak
aplikacja działa z `--control`, narzędzia idą do niej — wtedy **Claude przestawia
kontrolki w oknie, które masz przed sobą**, a wynik mówi, skąd pochodzi
(pole `zrodlo`). `--attach nigdy` wymusza tryb lokalny.

## API HTTP

Dla skryptów, botów i wszystkiego, co nie mówi po MCP. Tylko biblioteka
standardowa, domyślnie nasłuchuje wyłącznie na `127.0.0.1`.

```bash
python3 fcsiege.py api --port 8765 --token tajne
```

```bash
curl -s localhost:8765/policz -H "Authorization: Bearer tajne" \
  -H 'Content-Type: application/json' -d '{"scenariusz":{
    "tryb":"szturm","teren_miasta":"Hills","budowle":["City Walls"],
    "moja_jednostka":{"jednostka":"Catapult"},
    "sily_wroga":[{"jednostka":"Warriors","liczba":5}]}}'
```

| ścieżka | co robi |
|---|---|
| `GET /zdrowie` | czy żyje i skąd liczy |
| `GET /narzedzia` | definicje narzędzi (te same, co w MCP) |
| `GET /openapi.json` | schemat OpenAPI 3.1 wygenerowany z definicji |
| `GET /stan` | obecny scenariusz |
| `POST /narzedzie/<nazwa>` | wywołanie narzędzia, ciało = argumenty |
| `POST /policz` | skrót: ustawia scenariusz i od razu liczy |

Token jest opcjonalny lokalnie, ale wymagany, jeśli wystawiasz `--host` poza
localhost — serwer ostrzega, gdy tego nie zrobisz.

## Jeden silnik, trzy powierzchnie

Okno, MCP i API liczą tym samym kodem. Narzędzia są zdefiniowane raz
(`aitools.TOOL_SPECS`) i realizowane przez dwa „mosty”: okno aplikacji operuje
na kontrolkach Qt, a `HeadlessBridge` na zwykłym obiekcie stanu. Test
`test_headless_matches_gui` przepuszcza ten sam scenariusz przez oba i porównuje
wyniki, więc nie mogą się rozjechać.

## Bieżąca partia (czytanie zapisów gry)

Zapisy Freeciva (`.sav`, `.sav.gz`, `.sav.xz`, `.sav.bz2`, `.sav.zst`) to **ten sam
format secfile co pliki reguł**, więc czyta je ten sam parser. Zakładka
**Bieżąca partia** wczytuje najnowszy zapis z `~/.freeciv/saves` i pokazuje turę,
twoją nację, złoto, miasta, wojska i dyplomację — a asystent w czacie korzysta
z tych samych danych, więc nie musisz mu opisywać sytuacji.

### Mgła wojny

Zapis zawiera stan **wszystkich** graczy, także tego, czego twoja cywilizacja nie
widzi. Domyślnie narzędzie pokazuje wyłącznie twoją wiedzę:

* twoje miasta i jednostki,
* obce miasta, które masz odkryte (rozmiar, mury, czy obsadzone),
* stany dyplomatyczne.

Pole **„Pełny wgląd — świadomie chituję"** (i parametr `pelny_wglad=True`
w narzędziach) ujawnia cudze wojska, garnizony i nieodkryte miasta. Każda
odpowiedź mówi, w którym trybie powstała — nie da się przypadkiem zerknąć
w karty przeciwnika i o tym zapomnieć.

Narzędzia wywiadu: `wczytaj_zapis`, `moje_wojska`, `wywiad_o_nacji`,
`linia_frontu`, `porownaj_ustroje`, `przejezdnosc`.

### Przejezdność terenu

`przejezdnosc` odpowiada na pytanie, które łatwo przeoczyć: **czy jednostka
w ogóle dojdzie do celu**. W `sandbox` i `civ2civ3` katapulty i działa należą do
klasy **Big Land**, która nie wchodzi na bagna, dżunglę ani góry — chyba że leży
tam droga, kolej albo **rzeka** (wszystkie mają flagę `NativeTile`), a każde
miasto ma drogę z automatu.

Narzędzie czyta warstwy dróg i rzek wprost z zapisu, wyznacza spójne obszary
przejezdne dla danej klasy i mówi, ile twoich jednostek stoi w którym obszarze
oraz do których miast wroga faktycznie dotrą. Najsilniejsza jednostka jest
bezużyteczna, jeśli utknie po drugiej stronie bagna.

### Porównanie ustrojów

`porownaj_ustroje` czyta efekty ustrojów wprost z reguł (maksymalne suwaki,
utrzymanie wojsk, kary za wielkość imperium, stan wojenny, niezadowolenie od
wojsk w polu, marnotrawstwo, premie) i — jeśli jest wczytany zapis — przelicza
je na twoją partię: ile realnie kosztowałoby utrzymanie **twoich** jednostek
przy **twoim** rozkładzie na miasta, ile masz poziomów kary za wielkość
i których technologii ci brakuje.

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
python3 tests/test_combat.py    # silnik walki
python3 tests/test_chat.py      # asystent (bez sieci, klient podstawiony)
python3 tests/test_surfaces.py  # MCP, API HTTP, gniazdo sterujące
```

`test_surfaces.py` sprawdza zgodność silnika bez Qt z oknem, uruchamia serwer MCP
i rozmawia z nim **prawdziwym klientem MCP** po stdio, wysyła **prawdziwe żądania
HTTP** do API (włącznie z odmową bez tokenu) oraz steruje otwartym oknem przez
gniazdo. Żaden test nie wychodzi do sieci.

`test_chat.py` sprawdza schematy narzędzi, most do interfejsu (czy narzędzie
naprawdę przestawia kontrolkę i czy zwraca te same liczby co karta odpowiedzi),
pełną pętlę `tool_use → tool_result → odpowiedź` na podstawionym kliencie oraz
obsługę odmowy modelu.

`test_combat.py` sprawdza m.in.: zgodność wzoru na pojedynek z niezależną symulacją runda po
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
* Asystent liczy wyłącznie tym silnikiem, ale sam dobór scenariusza to jego
  interpretacja Twojego opisu — sprawdź w panelach, czy ustawił to, co miałeś
  na myśli.
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
| `fcsiege/app.py` | okno główne + most dla asystenta |
| `fcsiege/headless.py` | ten sam kalkulator bez Qt (rdzeń dla MCP i API) |
| `fcsiege/mcp_server.py` | serwer MCP (stdio) |
| `fcsiege/http_api.py` | API HTTP + schemat OpenAPI |
| `fcsiege/control.py` | gniazdo sterujące uruchomionym oknem |
| `fcsiege/savegame.py` | czytanie zapisów gry + filtr mgły wojny |
| `fcsiege/aitools.py` | definicje narzędzi i prompt systemowy asystenta |
| `fcsiege/aiclient.py` | poświadczenia i pętla rozmowy ze strumieniowaniem |
| `fcsiege/chatpanel.py` | interfejs czatu i logowania |
| `tools/screenshots.py` | generuje zrzuty do `docs/` (działa bez ekranu) |

## Licencja

GNU GPL w wersji 2 lub późniejszej — patrz [LICENSE](LICENSE).

Katalog `data/rulesets/` zawiera niezmienione pliki reguł z projektu Freeciv
(GPL-2.0-or-later); szczegóły i atrybucja w [NOTICE](NOTICE).
