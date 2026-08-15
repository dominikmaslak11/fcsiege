"""Webowy interfejs serwowany przez API - do obslugi z telefonu przez tailnet.

Jedna strona bez zadnych zewnetrznych zasobow: silnik zostaje na komputerze,
przy zapisach gry, a telefon jest cienkim klientem. Kolory bierzemy z `theme`,
zeby to wygladalo na te sama aplikacje, co okno.

Strona rozmawia wylacznie z tym samym serwerem, z ktorego zostala pobrana, wiec
token trzyma w `localStorage` i dokleja go do kazdego zadania.
"""

from __future__ import annotations

from . import theme

TEXTS = {
    "pl": {
        "title": "FCSiege",
        "sub": "Doradca do bieżącej partii",
        "tab_game": "Partia",
        "tab_calc": "Kalkulator",
        "tab_chat": "Asystent",
        "load": "Wczytaj najnowszy zapis",
        "cheat": "Pełny wgląd (świadomie chituję)",
        "reports": "Raporty",
        "r_waste": "Korupcja",
        "r_plan": "Plan budowy",
        "r_tech": "Technologie",
        "r_cities": "Miasta",
        "r_army": "Moje wojska",
        "r_trade": "Szlaki handlowe",
        "compute": "Policz",
        "ruleset": "Zestaw reguł",
        "city_terrain": "Teren miasta",
        "my_unit": "Moja jednostka",
        "enemy": "Wróg",
        "count": "sztuk",
        "ask": "Opisz sytuację…",
        "send": "Wyślij",
        "stop": "Przerwij",
        "clear": "Wyczyść",
        "token": "Token API",
        "token_hint": "Zostawiasz puste, jeśli serwer działa bez tokenu.",
        "save": "Zapisz",
        "connected": "połączono",
        "offline": "brak połączenia",
        "no_save": "Nie wczytano zapisu.",
        "thinking": "Myślę…",
        "settings": "Ustawienia",
        "alerts": "Ostrzeżenia",
        "watching": "nasłuchuję zapisów",
        "newsave": "Nowy zapis",
        "dismiss": "Zamknij",
        "tab_an": "Analiza",
        "refresh": "Odśwież analizę",
        "running": "Liczę…",
        "auto": "odświeża się sama po każdej turze",
        "diplo": "Układy",
        "growth": "Wzrost i prace",
        "waste": "Korupcja",
        "logi": "Logistyka",
        "providers": "Modele i klucze",
        "provider": "Dostawca",
        "apikey": "Klucz API",
        "setkey": "Zapisz klucz",
        "makeactive": "Ustaw jako domyślny",
        "nokey": "brak klucza",
        "fromenv": "ze zmiennej środowiskowej",
        "fromfile": "zapisany",
        "engine": "Silnik",
        "compare": "Porównaj silniki",
    },
    "en": {
        "title": "FCSiege",
        "sub": "Adviser for the game in progress",
        "tab_game": "Game",
        "tab_calc": "Calculator",
        "tab_chat": "Assistant",
        "load": "Load the newest savegame",
        "cheat": "Full intel (deliberate cheating)",
        "reports": "Reports",
        "r_waste": "Waste",
        "r_plan": "Build plan",
        "r_tech": "Technologies",
        "r_cities": "Cities",
        "r_army": "My forces",
        "r_trade": "Trade routes",
        "compute": "Compute",
        "ruleset": "Ruleset",
        "city_terrain": "City terrain",
        "my_unit": "My unit",
        "enemy": "Enemy",
        "count": "count",
        "ask": "Describe the situation…",
        "send": "Send",
        "stop": "Stop",
        "clear": "Clear",
        "token": "API token",
        "token_hint": "Leave empty if the server runs without a token.",
        "save": "Save",
        "connected": "connected",
        "offline": "no connection",
        "no_save": "No savegame loaded.",
        "thinking": "Thinking…",
        "settings": "Settings",
        "alerts": "Alerts",
        "watching": "watching savegames",
        "newsave": "New savegame",
        "dismiss": "Dismiss",
        "tab_an": "Analysis",
        "refresh": "Refresh analysis",
        "running": "Computing…",
        "auto": "refreshes itself after every turn",
        "diplo": "Treaties",
        "growth": "Growth and works",
        "waste": "Waste",
        "logi": "Logistics",
        "providers": "Models and keys",
        "provider": "Provider",
        "apikey": "API key",
        "setkey": "Save key",
        "makeactive": "Make default",
        "nokey": "no key",
        "fromenv": "from environment",
        "fromfile": "saved",
        "engine": "Engine",
        "compare": "Compare engines",
    },
}


def page(lang: str = "pl") -> str:
    t = TEXTS.get(lang, TEXTS["pl"])
    return _TEMPLATE.format(
        lang=lang, other=("EN" if lang == "pl" else "PL"), t=t, bg=theme.BG, surface=theme.SURFACE, border=theme.BORDER,
        border_soft=theme.BORDER_SOFT, text=theme.TEXT, dim=theme.TEXT_DIM,
        faint=theme.TEXT_FAINT, accent=theme.ACCENT, good=theme.GOOD,
        warn=theme.WARN, bad=theme.BAD, attack=theme.ATTACK,
        defend=theme.DEFEND, grid=theme.GRID,
    )


_TEMPLATE = """<!doctype html>
<html lang="{lang}" data-lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="{bg}">
<title>{t[title]} — {t[sub]}</title>
<style>
:root {{
  --bg:{bg}; --surface:{surface}; --border:{border}; --border-soft:{border_soft};
  --text:{text}; --dim:{dim}; --faint:{faint}; --accent:{accent};
  --good:{good}; --warn:{warn}; --bad:{bad}; --attack:{attack}; --defend:{defend};
  --grid:{grid};
  --mono: "JetBrains Mono","Fira Code","DejaVu Sans Mono",ui-monospace,monospace;
  --sans: "Inter","Segoe UI",system-ui,"Noto Sans",sans-serif;
  --safe-b: env(safe-area-inset-bottom, 0px);
}}
* {{ box-sizing:border-box; -webkit-tap-highlight-color:transparent; }}
html,body {{ margin:0; height:100%; }}
body {{
  background:var(--bg); color:var(--text); font-family:var(--sans);
  font-size:16px; line-height:1.5; overscroll-behavior-y:none;
  display:flex; flex-direction:column;
}}
button,input,select,textarea {{ font:inherit; color:inherit; }}

/* ── pasek ─────────────────────────────────────────────────────────── */
header {{
  flex:none; display:flex; align-items:center; gap:.6rem;
  padding:.6rem .8rem; padding-top:calc(.6rem + env(safe-area-inset-top,0px));
  background:var(--surface); border-bottom:1px solid var(--border);
}}
.brand {{ font-weight:700; letter-spacing:-.01em; }}
.brand span {{ color:var(--accent); }}
.dot {{ width:.55rem; height:.55rem; border-radius:50%; background:var(--faint); flex:none; }}
.dot.on {{ background:var(--good); }}
.dot.off {{ background:var(--bad); }}
.state {{ font-family:var(--mono); font-size:.68rem; color:var(--faint);
         text-transform:uppercase; letter-spacing:.08em; }}
header .grow {{ flex:1; }}
.iconbtn {{
  background:transparent; border:1px solid var(--border); border-radius:6px;
  padding:.3rem .55rem; font-family:var(--mono); font-size:.72rem;
  color:var(--dim); cursor:pointer;
}}
.iconbtn:active {{ background:var(--border-soft); }}
.iconbtn[aria-pressed="true"] {{ background:var(--accent); border-color:var(--accent); color:#0B0F1A; }}

/* ── panele ────────────────────────────────────────────────────────── */
main {{ flex:1; overflow-y:auto; -webkit-overflow-scrolling:touch; }}
.panel {{ display:none; padding:.9rem .8rem 1.4rem; }}
.panel.on {{ display:block; }}
.card {{
  background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:.85rem .9rem; margin-bottom:.7rem;
}}
.card h2 {{ margin:0 0 .6rem; font-size:.72rem; letter-spacing:.1em;
            text-transform:uppercase; color:var(--faint); font-family:var(--mono); }}
.row {{ display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; }}
.row > * {{ min-width:0; }}
label.check {{ display:flex; gap:.5rem; align-items:center; font-size:.9rem; color:var(--dim); }}
input[type=checkbox] {{ width:1.1rem; height:1.1rem; accent-color:var(--accent); }}

.btn {{
  background:var(--accent); color:#0B0F1A; border:0; border-radius:8px;
  padding:.62rem 1rem; font-weight:600; cursor:pointer; flex:1;
}}
.btn:disabled {{ opacity:.5; }}
.btn.ghost {{ background:transparent; border:1px solid var(--border); color:var(--dim); font-weight:500; }}
.btn.danger {{ background:var(--bad); }}

.grid2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(9rem,1fr)); gap:.5rem; }}
select, input[type=text], input[type=number], input[type=password], textarea {{
  width:100%; background:var(--bg); border:1px solid var(--border);
  border-radius:8px; padding:.55rem .6rem; color:var(--text);
}}
textarea {{ resize:none; }}
.hint {{ font-size:.78rem; color:var(--faint); margin:.35rem 0 0; }}

/* ── kafelki statystyk ─────────────────────────────────────────────── */
.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(6.2rem,1fr)); gap:.45rem; }}
.tile {{ background:var(--bg); border:1px solid var(--border-soft); border-radius:8px; padding:.5rem .6rem; }}
.tile b {{ display:block; font-family:var(--mono); font-size:1.15rem; font-variant-numeric:tabular-nums; }}
.tile span {{ display:block; font-size:.64rem; text-transform:uppercase;
              letter-spacing:.07em; color:var(--faint); margin-top:.1rem; }}

/* ── wyniki ────────────────────────────────────────────────────────── */
pre.out {{
  margin:0; font-family:var(--mono); font-size:.76rem; line-height:1.55;
  white-space:pre; overflow-x:auto; color:var(--dim);
  background:var(--bg); border:1px solid var(--border-soft);
  border-radius:8px; padding:.6rem;
}}
table.out {{ width:100%; border-collapse:collapse; font-family:var(--mono); font-size:.74rem; }}
table.out th, table.out td {{ padding:.28rem .45rem; text-align:right; white-space:nowrap;
  border-bottom:1px solid var(--border-soft); font-variant-numeric:tabular-nums; }}
table.out th:first-child, table.out td:first-child {{ text-align:left; }}
table.out thead th {{ color:var(--faint); font-size:.62rem; text-transform:uppercase;
  letter-spacing:.06em; border-bottom:1px solid var(--border); }}
.scroll {{ overflow-x:auto; }}

/* ── czat ──────────────────────────────────────────────────────────── */
#chatlog {{ display:flex; flex-direction:column; gap:.55rem; }}
.msg {{ border-radius:10px; padding:.6rem .75rem; max-width:92%; white-space:pre-wrap;
        overflow-wrap:anywhere; }}
.msg.me {{ align-self:flex-end; background:var(--border-soft); }}
.msg.ai {{ align-self:flex-start; background:var(--surface); border:1px solid var(--border); }}
.msg.err {{ align-self:stretch; background:rgba(255,93,108,.12); border:1px solid var(--bad);
            color:var(--bad); font-size:.86rem; }}
.think {{ align-self:flex-start; font-size:.8rem; color:var(--faint); font-style:italic;
          border-left:2px solid var(--border); padding-left:.6rem; white-space:pre-wrap; }}
.toolchip {{
  align-self:flex-start; font-family:var(--mono); font-size:.68rem;
  background:var(--bg); border:1px solid var(--border); border-radius:999px;
  padding:.2rem .6rem; color:var(--accent);
}}
.toolchip.done {{ color:var(--good); border-color:var(--grid); }}
#composer {{
  flex:none; display:flex; gap:.5rem; padding:.6rem .8rem;
  padding-bottom:calc(.6rem + var(--safe-b));
  background:var(--surface); border-top:1px solid var(--border);
}}
#composer textarea {{ flex:1; max-height:7rem; }}
#composer .btn {{ flex:none; padding:.62rem .9rem; }}

/* ── nawigacja dolna ───────────────────────────────────────────────── */
nav {{
  flex:none; display:flex; background:var(--surface); border-top:1px solid var(--border);
  padding-bottom:var(--safe-b);
}}
nav button {{
  flex:1; background:transparent; border:0; padding:.65rem .3rem .55rem;
  color:var(--faint); font-size:.74rem; cursor:pointer; border-top:2px solid transparent;
}}
nav button[aria-selected="true"] {{ color:var(--accent); border-top-color:var(--accent); }}

/* ── powiadomienia ─────────────────────────────────────────────────── */
#toasts {{
  position:fixed; left:.6rem; right:.6rem; bottom:calc(3.6rem + var(--safe-b));
  z-index:30; display:flex; flex-direction:column; gap:.45rem; pointer-events:none;
}}
.toast {{
  pointer-events:auto; background:var(--surface); border:1px solid var(--border);
  border-left:3px solid var(--accent); border-radius:8px; padding:.6rem .75rem;
  box-shadow:0 8px 24px -12px rgba(0,0,0,.8); animation:rise .18s ease-out;
}}
.toast.krytyczne {{ border-left-color:var(--bad); }}
.toast.pilne {{ border-left-color:var(--warn); }}
.toast b {{ display:block; font-size:.86rem; }}
.toast small {{ display:block; color:var(--faint); font-size:.74rem; margin-top:.1rem; }}
.toast p {{ margin:.35rem 0 0; font-size:.84rem; color:var(--dim); }}
.toast .x {{ float:right; background:none; border:0; color:var(--faint); cursor:pointer;
             font-size:1rem; line-height:1; padding:0 0 0 .5rem; }}
@keyframes rise {{ from {{ opacity:0; transform:translateY(6px); }} to {{ opacity:1; }} }}
nav button .badge {{
  display:inline-block; min-width:1.05rem; padding:0 .25rem; margin-left:.25rem;
  border-radius:999px; background:var(--bad); color:#0B0F1A;
  font-family:var(--mono); font-size:.62rem; font-weight:700;
}}
dialog {{
  border:1px solid var(--border); border-radius:12px; background:var(--surface);
  color:var(--text); padding:1rem; width:min(22rem,92vw);
}}
dialog::backdrop {{ background:rgba(0,0,0,.6); }}
@media (prefers-reduced-motion:reduce) {{ * {{ transition:none!important; animation:none!important; }} }}
</style>
</head>
<body>

<header>
  <span class="dot" id="dot"></span>
  <span class="brand">FC<span>Siege</span></span>
  <span class="state" id="state">…</span>
  <span class="grow"></span>
  <button class="iconbtn" id="langbtn" type="button">{other}</button>
  <button class="iconbtn" id="setbtn" type="button" aria-label="{t[settings]}">⚙</button>
</header>

<main>
  <section class="panel on" id="p-game">
    <div class="card">
      <h2>{t[tab_game]}</h2>
      <label class="check"><input type="checkbox" id="cheat"> {t[cheat]}</label>
      <div class="row" style="margin-top:.6rem">
        <button class="btn" id="btn-load">{t[load]}</button>
      </div>
      <div class="stats" id="gamestats" style="margin-top:.7rem"></div>
      <p class="hint" id="gamehint">{t[no_save]}</p>
    </div>
    <div class="card">
      <h2>{t[reports]}</h2>
      <div class="grid2">
        <button class="btn ghost" data-tool="korupcja">{t[r_waste]}</button>
        <button class="btn ghost" data-tool="plan_budowy">{t[r_plan]}</button>
        <button class="btn ghost" data-tool="moje_technologie">{t[r_tech]}</button>
        <button class="btn ghost" data-tool="audyt_miast">{t[r_cities]}</button>
        <button class="btn ghost" data-tool="moje_wojska">{t[r_army]}</button>
        <button class="btn ghost" data-tool="szlaki_handlowe">{t[r_trade]}</button>
      </div>
    </div>
    <div class="card" id="alertcard" hidden>
      <h2>{t[alerts]}</h2>
      <div id="alertlist"></div>
    </div>
    <div class="card" id="reportcard" hidden><h2 id="reporttitle"></h2><div id="report"></div></div>
  </section>

  <section class="panel" id="p-calc">
    <div class="card">
      <h2>{t[tab_calc]}</h2>
      <div class="grid2">
        <div><label class="hint">{t[ruleset]}</label><select id="f-ruleset"></select></div>
        <div><label class="hint">{t[city_terrain]}</label><select id="f-terrain"></select></div>
        <div><label class="hint">{t[my_unit]}</label><select id="f-unit"></select></div>
        <div><label class="hint">{t[enemy]}</label><select id="f-enemy"></select></div>
        <div><label class="hint">{t[count]}</label><input type="number" id="f-n" value="5" min="1" max="40"></div>
      </div>
      <div class="row" style="margin-top:.7rem">
        <button class="btn" id="btn-compute">{t[compute]}</button>
      </div>
    </div>
    <div class="card" id="calccard" hidden><h2>{t[compute]}</h2><div id="calcout"></div></div>
  </section>

  <section class="panel" id="p-an">
    <div class="card">
      <h2>{t[tab_an]}</h2>
      <div class="row">
        <button class="btn" id="btn-an">{t[refresh]}</button>
      </div>
      <p class="hint" id="anhint">{t[auto]}</p>
    </div>
    <div id="anout"></div>
  </section>

  <section class="panel" id="p-chat">
    <div id="chatlog"></div>
  </section>
</main>

<div id="composer" hidden>
  <select id="engine" title="{t[engine]}" style="flex:none;width:6.5rem"></select>
  <textarea id="ask" rows="1" placeholder="{t[ask]}"></textarea>
  <button class="btn" id="btn-send">{t[send]}</button>
  <button class="btn ghost" id="btn-cmp" title="{t[compare]}">⇄</button>
  <button class="btn danger" id="btn-stop" hidden>{t[stop]}</button>
</div>

<div id="toasts" aria-live="polite"></div>

<nav role="tablist">
  <button role="tab" aria-selected="true" data-panel="p-game">{t[tab_game]}</button>
  <button role="tab" aria-selected="false" data-panel="p-calc">{t[tab_calc]}</button>
  <button role="tab" aria-selected="false" data-panel="p-an">{t[tab_an]}</button>
  <button role="tab" aria-selected="false" data-panel="p-chat">{t[tab_chat]}</button>
</nav>

<dialog id="settings">
  <h2 style="margin:0 0 .6rem;font-size:1rem">{t[settings]}</h2>
  <label class="hint">{t[token]}</label>
  <input type="password" id="tokenin" autocomplete="off">
  <p class="hint">{t[token_hint]}</p>

  <h2 style="margin:1rem 0 .5rem;font-size:1rem">{t[providers]}</h2>
  <div id="provlist" style="font-size:.82rem"></div>
  <label class="hint">{t[provider]}</label>
  <select id="provsel"></select>
  <label class="hint">{t[apikey]}</label>
  <input type="password" id="provkey" autocomplete="off" placeholder="sk-…">
  <label class="check" style="margin-top:.5rem">
    <input type="checkbox" id="provactive"> {t[makeactive]}</label>
  <div class="row" style="margin-top:.5rem">
    <button class="btn ghost" id="btn-provsave">{t[setkey]}</button>
  </div>
  <div class="row" style="margin-top:.8rem">
    <button class="btn" id="btn-savetoken">{t[save]}</button>
    <button class="btn ghost" id="btn-closeset">×</button>
  </div>
</dialog>

<script>
const LANG = "{lang}";
const T = {{ thinking: "{t[thinking]}", offline: "{t[offline]}", connected: "{t[connected]}" }};
const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => {{
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
}};

let token = localStorage.getItem("fcsiege-token") || "";

// token mozna podac raz w adresie (wygodne przy kodzie QR); od razu go
// chowamy w schowku przegladarki i wycieramy z paska adresu, zeby nie
// zostawal w historii
(function bootstrapToken() {{
  const params = new URLSearchParams(location.search);
  const fromUrl = params.get("token");
  if (!fromUrl) return;
  token = fromUrl;
  localStorage.setItem("fcsiege-token", token);
  params.delete("token");
  const rest = params.toString();
  history.replaceState(null, "", location.pathname + (rest ? "?" + rest : ""));
}})();

function headers(extra) {{
  const h = Object.assign({{ "Content-Type": "application/json" }}, extra || {{}});
  if (token) h["Authorization"] = "Bearer " + token;
  return h;
}}
function url(path) {{
  return path + (path.includes("?") ? "&" : "?") + "lang=" + LANG;
}}

async function call(tool, args) {{
  const r = await fetch(url("/narzedzie/" + tool), {{
    method: "POST", headers: headers(), body: JSON.stringify(args || {{}})
  }});
  const data = await r.json();
  if (!r.ok) throw new Error(data.blad || data.error || ("HTTP " + r.status));
  return data;
}}

/* ── stan połączenia ─────────────────────────────────────────────── */
async function ping() {{
  try {{
    const r = await fetch(url("/zdrowie"), {{ headers: headers() }});
    const d = await r.json();
    $("#dot").className = "dot " + (r.ok ? "on" : "off");
    $("#state").textContent = r.ok ? (d.zrodlo || d.source || T.connected) : "HTTP " + r.status;
  }} catch (e) {{
    $("#dot").className = "dot off";
    $("#state").textContent = T.offline;
  }}
}}

/* ── zakładki ────────────────────────────────────────────────────── */
document.querySelectorAll("nav button").forEach((b) => {{
  b.addEventListener("click", () => {{
    document.querySelectorAll("nav button").forEach((x) =>
      x.setAttribute("aria-selected", String(x === b)));
    document.querySelectorAll(".panel").forEach((p) =>
      p.classList.toggle("on", p.id === b.dataset.panel));
    $("#composer").hidden = b.dataset.panel !== "p-chat";
  }});
}});

/* ── ustawienia ──────────────────────────────────────────────────── */
async function loadProviders() {{
  try {{
    const r = await fetch(url("/dostawcy"), {{ headers: headers() }});
    if (!r.ok) return;
    const d = await r.json();
    const lista = d.dostawcy || d.providers || [];
    const box = $("#provlist");
    box.textContent = "";
    const sel = $("#provsel");
    sel.textContent = "";
    lista.forEach((p) => {{
      const id = p.dostawca || p.provider;
      const ma = p.ma_klucz ?? p.has_key;
      const skad = p.skad_klucz || p.key_source;
      const row = el("div");
      row.style.cssText = "display:flex;gap:.4rem;padding:.15rem 0";
      row.append(el("span", null, ma ? "✓" : "·"));
      row.append(el("b", null, p.nazwa || p.name || id));
      row.append(el("span", "hint", ma
        ? (skad === "env" ? "{t[fromenv]}" : "{t[fromfile]}") : "{t[nokey]}"));
      if ((d.aktywny || d.active) === id) row.append(el("span", "hint", "★"));
      box.append(row);
      const o = el("option", null, (p.nazwa || p.name || id));
      o.value = id;
      sel.append(o);
    }});
  }} catch (e) {{ /* panel kluczy zostaje pusty */ }}
}}

$("#btn-provsave").addEventListener("click", async () => {{
  const id = $("#provsel").value;
  const klucz = $("#provkey").value.trim();
  if (!id) return;
  try {{
    const r = await fetch(url("/dostawcy/" + id), {{
      method: "POST", headers: headers(),
      body: JSON.stringify({{ klucz: klucz, aktywny: $("#provactive").checked }})
    }});
    if (!r.ok) throw new Error("HTTP " + r.status);
    $("#provkey").value = "";
    loadProviders();
  }} catch (e) {{ $("#provlist").textContent = String(e.message); }}
}});

$("#setbtn").addEventListener("click", () => {{
  $("#tokenin").value = token;
  loadProviders();
  $("#settings").showModal();
}});
$("#btn-closeset").addEventListener("click", () => $("#settings").close());
$("#btn-savetoken").addEventListener("click", () => {{
  token = $("#tokenin").value.trim();
  localStorage.setItem("fcsiege-token", token);
  $("#settings").close();
  ping();
}});
$("#langbtn").addEventListener("click", () => {{
  location.search = "?lang=" + (LANG === "pl" ? "en" : "pl");
}});

/* ── partia ──────────────────────────────────────────────────────── */
const STAT_FIELDS = [
  ["tura", "turn"], ["zloto", "gold"], ["miast", "cities"], ["jednostek", "units"]
];
$("#btn-load").addEventListener("click", async () => {{
  const btn = $("#btn-load");
  btn.disabled = true;
  try {{
    const d = await call("wczytaj_zapis", {{ pelny_wglad: $("#cheat").checked }});
    const me = d.ja || d.me || {{}};
    const stats = $("#gamestats");
    stats.textContent = "";
    const pairs = [
      [d.tura ?? d.turn, "tura/turn"],
      [me.zloto ?? me.gold, "gold"],
      [me.miast ?? me.cities, "cities"],
      [me.jednostek ?? me.units, "units"]
    ];
    for (const [v, label] of pairs) {{
      if (v === undefined) continue;
      const tile = el("div", "tile");
      tile.append(el("b", null, String(v)), el("span", null, label));
      stats.append(tile);
    }}
    $("#gamehint").textContent =
      (d.plik || d.file || "") + " · " + (d.tryb_wywiadu || d.intel_mode || "");
  }} catch (e) {{
    $("#gamehint").textContent = String(e.message);
  }} finally {{
    btn.disabled = false;
  }}
}});

/* ── raporty ─────────────────────────────────────────────────────── */
function renderTable(rows) {{
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))]
    .filter((c) => rows.some((r) => r[c] !== null && typeof r[c] !== "object"));
  const table = el("table", "out");
  const thead = el("thead"), tr = el("tr");
  cols.forEach((c) => tr.append(el("th", null, c.replace(/_/g, " "))));
  thead.append(tr);
  const tbody = el("tbody");
  rows.forEach((r) => {{
    const line = el("tr");
    cols.forEach((c) => {{
      const v = r[c];
      line.append(el("td", null, v === null || v === undefined ? "—" : String(v)));
    }});
    tbody.append(line);
  }});
  table.append(thead, tbody);
  const wrap = el("div", "scroll");
  wrap.append(table);
  return wrap;
}}

function renderResult(data) {{
  const box = el("div");
  // najdluzsza lista slownikow to zwykle sedno raportu - pokaz ja tabela
  let best = null;
  for (const [k, v] of Object.entries(data)) {{
    if (Array.isArray(v) && v.length && typeof v[0] === "object" && !Array.isArray(v[0])) {{
      if (!best || v.length > best[1].length) best = [k, v];
    }}
  }}
  if (best) {{
    box.append(el("p", "hint", best[0].replace(/_/g, " ")));
    box.append(renderTable(best[1]));
  }}
  const rest = {{}};
  for (const [k, v] of Object.entries(data)) if (!best || k !== best[0]) rest[k] = v;
  if (Object.keys(rest).length) {{
    const pre = el("pre", "out", JSON.stringify(rest, null, 1));
    pre.style.marginTop = ".6rem";
    box.append(pre);
  }}
  return box;
}}

document.querySelectorAll("[data-tool]").forEach((b) => {{
  b.addEventListener("click", async () => {{
    $("#reportcard").hidden = false;
    $("#reporttitle").textContent = b.textContent;
    $("#report").textContent = "…";
    try {{
      const d = await call(b.dataset.tool, {{}});
      $("#report").textContent = "";
      $("#report").append(renderResult(d));
    }} catch (e) {{
      $("#report").textContent = String(e.message);
    }}
  }});
}});

/* ── kalkulator ──────────────────────────────────────────────────── */
async function fillSelects() {{
  try {{
    const state = await call("pokaz_stan", {{}});
    const units = await call("spis", {{ czego: "jednostki" }});
    const terr = await call("spis", {{ czego: "tereny" }});
    const rules = await call("spis", {{ czego: "zestawy" }});
    const put = (sel, list, cur) => {{
      const node = $(sel);
      node.textContent = "";
      (list || []).forEach((name) => {{
        const o = el("option", null, name);
        o.value = name;
        if (name === cur) o.selected = true;
        node.append(o);
      }});
    }};
    const pick = (o) => Array.isArray(o) ? o : (o.jednostki || o.units || o.tereny ||
      o.terrains || o.zestawy || o.rulesets || o.spis || o.list || []);
    put("#f-unit", pick(units), (state.moja_jednostka || state.my_unit || {{}}).jednostka);
    put("#f-enemy", pick(units), null);
    put("#f-terrain", pick(terr), state.teren_miasta || state.city_terrain);
    put("#f-ruleset", pick(rules), state.zestaw_regul || state.ruleset);
  }} catch (e) {{ /* selecty zostaja puste; komunikat pokaze przycisk */ }}
}}

$("#btn-compute").addEventListener("click", async () => {{
  $("#calccard").hidden = false;
  $("#calcout").textContent = "…";
  try {{
    if ($("#f-ruleset").value) await call("ustaw_scenariusz", {{
      zestaw_regul: $("#f-ruleset").value, teren_miasta: $("#f-terrain").value
    }});
    await call("ustaw_moja_jednostke", {{ jednostka: $("#f-unit").value }});
    await call("ustaw_sily_wroga", {{
      jednostki: [{{ jednostka: $("#f-enemy").value, liczba: Number($("#f-n").value) }}]
    }});
    const d = await call("policz", {{}});
    $("#calcout").textContent = "";
    const stats = el("div", "stats");
    const wanted = ["szansa_pojedynku_proc", "duel_win_pct", "potrzeba_90proc",
                    "needed_for_90pct", "srednie_straty", "average_losses",
                    "koszt_strat_tarcze", "loss_cost_shields"];
    wanted.forEach((k) => {{
      if (d[k] === undefined) return;
      const tile = el("div", "tile");
      tile.append(el("b", null, String(d[k])), el("span", null, k.replace(/_/g, " ")));
      stats.append(tile);
    }});
    $("#calcout").append(stats, renderResult(d));
  }} catch (e) {{
    $("#calcout").textContent = String(e.message);
  }}
}});

/* ── czat (strumień SSE) ─────────────────────────────────────────── */
let controller = null;

function addMsg(cls, text) {{
  const n = el("div", "msg " + cls, text || "");
  $("#chatlog").append(n);
  n.scrollIntoView({{ block: "end" }});
  return n;
}}

async function send() {{
  const text = $("#ask").value.trim();
  if (!text || controller) return;
  $("#ask").value = "";
  $("#ask").style.height = "auto";
  addMsg("me", text);
  $("#btn-send").hidden = true;
  $("#btn-stop").hidden = false;
  controller = new AbortController();

  let bubble = null, think = null;
  try {{
    const r = await fetch(url("/czat"), {{
      method: "POST", headers: headers(),
      body: JSON.stringify({{ tekst: text, dostawca: $("#engine").value || undefined }}),
      signal: controller.signal
    }});
    if (!r.ok || !r.body) {{
      const d = await r.json().catch(() => ({{}}));
      throw new Error(d.blad || d.error || ("HTTP " + r.status));
    }}
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {{
      const {{ done, value }} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {{ stream: true }});
      let cut;
      while ((cut = buf.indexOf("\\n\\n")) >= 0) {{
        const chunk = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        const line = chunk.split("\\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.typ === "delta") {{
          if (!bubble) bubble = addMsg("ai", "");
          bubble.textContent += ev.tekst;
          bubble.scrollIntoView({{ block: "end" }});
        }} else if (ev.typ === "thinking") {{
          if (!think) {{ think = el("div", "think", ""); $("#chatlog").append(think); }}
          think.textContent += ev.tekst;
        }} else if (ev.typ === "tool_start") {{
          const chip = el("div", "toolchip", "▸ " + ev.nazwa);
          chip.dataset.tool = ev.nazwa;
          $("#chatlog").append(chip);
          chip.scrollIntoView({{ block: "end" }});
        }} else if (ev.typ === "tool_end") {{
          const chips = [...document.querySelectorAll(".toolchip")]
            .filter((c) => c.dataset.tool === ev.nazwa && !c.classList.contains("done"));
          if (chips.length) {{
            chips[chips.length - 1].classList.add("done");
            chips[chips.length - 1].textContent = "✓ " + ev.nazwa;
          }}
        }} else if (ev.typ === "error") {{
          addMsg("err", ev.tekst);
        }}
      }}
    }}
  }} catch (e) {{
    if (e.name !== "AbortError") addMsg("err", String(e.message));
  }} finally {{
    controller = null;
    $("#btn-send").hidden = false;
    $("#btn-stop").hidden = true;
  }}
}}

async function fillEngines() {{
  try {{
    const r = await fetch(url("/dostawcy"), {{ headers: headers() }});
    if (!r.ok) return;
    const d = await r.json();
    const sel = $("#engine");
    sel.textContent = "";
    (d.dostawcy || d.providers || []).forEach((p) => {{
      if (!(p.ma_klucz ?? p.has_key)) return;
      const id = p.dostawca || p.provider;
      const o = el("option", null, id);
      o.value = id;
      if ((d.aktywny || d.active) === id) o.selected = true;
      sel.append(o);
    }});
  }} catch (e) {{ /* zostaje domyślny silnik serwera */ }}
}}

async function compare() {{
  const text = $("#ask").value.trim();
  if (!text || controller) return;
  $("#ask").value = "";
  addMsg("me", text);
  const czekam = addMsg("ai", "…");
  try {{
    const r = await fetch(url("/porownaj"), {{
      method: "POST", headers: headers(), body: JSON.stringify({{ tekst: text }})
    }});
    const d = await r.json();
    czekam.remove();
    (d.odpowiedzi || d.answers || []).forEach((w) => {{
      const b = addMsg(w.blad ? "err" : "ai", "");
      b.append(el("b", null, w.dostawca + " · " + w.model));
      b.append(el("div", null, w.blad || w.odpowiedz));
      if ((w.uzyte_narzedzia || []).length) {{
        b.append(el("div", "toolchip", "✓ " + w.uzyte_narzedzia.join(", ")));
      }}
    }});
    const zgodne = d.liczby_zgodne_u_wszystkich || d.numbers_agreed_by_all || [];
    if (zgodne.length) {{
      addMsg("ai", "⇄ zgodne liczby: " + zgodne.join(", "));
    }}
  }} catch (e) {{
    czekam.remove();
    addMsg("err", String(e.message));
  }}
}}

$("#btn-cmp").addEventListener("click", compare);
$("#btn-send").addEventListener("click", send);
$("#btn-stop").addEventListener("click", () => controller && controller.abort());
$("#ask").addEventListener("input", (e) => {{
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, 112) + "px";
}});
$("#ask").addEventListener("keydown", (e) => {{
  if (e.key === "Enter" && !e.shiftKey) {{ e.preventDefault(); send(); }}
}});

/* ── pełna analiza: przyciskiem i po każdej turze ────────────────── */
function sekcja(tytul, tresc) {{
  const card = el("div", "card");
  card.append(el("h2", null, tytul));
  card.append(tresc);
  return card;
}}

function listaProstych(obj, pola) {{
  const box = el("div", "stats");
  pola.forEach(([klucz, etykieta]) => {{
    const v = obj[klucz];
    if (v === undefined || v === null || typeof v === "object") return;
    const tile = el("div", "tile");
    tile.append(el("b", null, String(v)), el("span", null, etykieta));
    box.append(tile);
  }});
  return box;
}}

async function runAnalysis() {{
  const btn = $("#btn-an");
  btn.disabled = true;
  $("#anhint").textContent = "{t[running]}";
  try {{
    const r = await fetch(url("/analiza"), {{ headers: headers() }});
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    const out = $("#anout");
    out.textContent = "";

    const al = d.alerty || d.alerts || {{}};
    renderAlerts(al.alerty || al.alerts || []);

    const dip = d.uklady_dyplomatyczne || d.treaties || {{}};
    if (dip.uklady || dip.treaties) {{
      out.append(sekcja("{t[diplo]}", renderTable(dip.uklady || dip.treaties)));
    }}
    const gr = d.potencjal_wzrostu || d.growth_potential || {{}};
    if (gr.plan_robot || gr.worker_plan) {{
      const box = el("div");
      box.append(listaProstych(gr, [
        ["prac_lacznie", "prac"], ["jobs_total", "jobs"],
        ["zywnosci_do_zyskania", "+ żywności"], ["food_to_gain", "+ food"],
        ["tur_pracy_lacznie", "tur pracy"], ["worker_turns_total", "worker turns"]
      ]));
      box.append(renderTable((gr.plan_robot || gr.worker_plan).slice(0, 15)));
      out.append(sekcja("{t[growth]}", box));
    }}
    const ko = d.korupcja || d.waste || {{}};
    if (ko.miasta || ko.cities) {{
      out.append(sekcja("{t[waste]}", renderTable((ko.miasta || ko.cities).slice(0, 12))));
    }}
    const mo = d.mobilnosc || d.mobility || {{}};
    if (mo.punkty_zborne || mo.rally_points) {{
      out.append(sekcja("{t[logi]}",
        renderTable((mo.punkty_zborne || mo.rally_points).slice(0, 10))));
    }}
    $("#anhint").textContent = "{t[auto]}";
  }} catch (e) {{
    $("#anhint").textContent = String(e.message);
  }} finally {{
    btn.disabled = false;
  }}
}}

$("#btn-an").addEventListener("click", runAnalysis);

/* ── powiadomienia o nowym zapisie ───────────────────────────────── */
function toast(a) {{
  const box = el("div", "toast " + (a.waga || a.severity || ""));
  const x = el("button", "x", "×");
  x.addEventListener("click", () => box.remove());
  box.append(x);
  box.append(el("b", null, (a.miasto || a.city || "") + " — " + (a.rodzaj || a.kind || "")));
  const tur = a.tur_do_szkody ?? a.turns_to_harm;
  box.append(el("small", null, (a.waga || a.severity || "") +
    (tur !== null && tur !== undefined ? " · " + tur + " tur" : "")));
  box.append(el("p", null, a.rada || a.advice || ""));
  $("#toasts").append(box);
  setTimeout(() => box.remove(), 20000);
}}

function renderAlerts(list) {{
  const box = $("#alertlist");
  box.textContent = "";
  (list || []).forEach((a) => {{
    const row = el("div", "toast " + (a.waga || a.severity || ""));
    row.style.marginBottom = ".4rem";
    row.append(el("b", null, (a.miasto || a.city || "") + " — " + (a.rodzaj || a.kind || "")));
    const tur = a.tur_do_szkody ?? a.turns_to_harm;
    row.append(el("small", null, (a.waga || a.severity || "") +
      (tur !== null && tur !== undefined ? " · " + tur + " tur" : "")));
    row.append(el("p", null, (a.co_sie_dzieje || a.what_is_happening || "") +
      " → " + (a.rada || a.advice || "")));
    box.append(row);
  }});
  $("#alertcard").hidden = !(list && list.length);
  const pilne = (list || []).filter((a) =>
    ["krytyczne", "pilne", "critical", "urgent"].includes(a.waga || a.severity)).length;
  const tab = document.querySelector('nav button[data-panel="p-game"]');
  tab.textContent = "{t[tab_game]}";
  if (pilne) tab.append(el("span", "badge", String(pilne)));
}}

async function watchSaves() {{
  for (;;) {{
    try {{
      const r = await fetch(url("/zdarzenia"), {{ headers: headers() }});
      if (!r.ok || !r.body) throw new Error("HTTP " + r.status);
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {{
        const {{ done, value }} = await reader.read();
        if (done) break;
        buf += dec.decode(value, {{ stream: true }});
        let cut;
        while ((cut = buf.indexOf("\\n\\n")) >= 0) {{
          const chunk = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          const line = chunk.split("\\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          const ev = JSON.parse(line.slice(5));
          if (ev.typ !== "nowy_zapis" && ev.type !== "new_savegame") continue;
          const alerts = ev.alerty || ev.alerts || [];
          renderAlerts(alerts);
          alerts.filter((a) => ["krytyczne", "pilne", "critical", "urgent"]
            .includes(a.waga || a.severity)).slice(0, 3).forEach(toast);
          $("#state").textContent = (ev.plik || ev.file || "{t[newsave]}").slice(0, 28);
          runAnalysis();          // nowa tura -> przelicz wszystko od nowa
        }}
      }}
    }} catch (e) {{ /* zerwane połączenie — próbujemy dalej */ }}
    await new Promise((r) => setTimeout(r, 5000));
  }}
}}

ping();
fillSelects();
fillEngines();
setInterval(ping, 20000);
watchSaves();
</script>
</body>
</html>
"""
