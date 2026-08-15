"""Rejestr dostawcow modeli i przechowywanie kluczy.

Jeden plik `~/.config/fcsiege/credentials.json` z prawami 0600 trzyma klucze
wszystkich dostawcow. Zmienna srodowiskowa zawsze ma pierwszenstwo przed
plikiem - dzieki temu da sie uruchomic sesje z innym kluczem bez ruszania
konfiguracji.

Claude rozmawia przez oficjalne SDK Anthropica. Pozostali (OpenAI, DeepSeek,
Gemini) wystawiaja zgodny z OpenAI punkt `/chat/completions` z obsluga funkcji,
wiec dla nich starcza jedna implementacja po HTTP ze standardowej biblioteki -
zero zaleznosci.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field

CONFIG_DIR = os.path.expanduser("~/.config/fcsiege")
CRED_FILE = os.path.join(CONFIG_DIR, "credentials.json")


@dataclass(frozen=True)
class Provider:
    """Opis dostawcy: skad klucz, gdzie API, jakim protokolem."""
    key: str                     # identyfikator wewnetrzny
    label: str                   # nazwa dla czlowieka
    env: tuple[str, ...]         # zmienne srodowiskowe z kluczem
    protocol: str                # "anthropic" | "openai"
    base_url: str = ""
    default_model: str = ""
    models: tuple[str, ...] = field(default_factory=tuple)
    # OpenAI odrzuca `max_tokens` dla nowszych modeli i chce
    # `max_completion_tokens`; DeepSeek i Gemini przyjmuja stara nazwe
    token_param: str = "max_tokens"
    key_hint: str = ""
    console: str = ""


PROVIDERS: dict[str, Provider] = {
    "claude": Provider(
        key="claude", label="Claude (Anthropic)",
        env=("ANTHROPIC_API_KEY",),
        protocol="anthropic",
        default_model="claude-opus-5",
        models=("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"),
        key_hint="sk-ant-...",
        console="https://console.anthropic.com/settings/keys",
    ),
    "openai": Provider(
        key="openai", label="OpenAI (GPT / Codex)",
        env=("OPENAI_API_KEY",),
        protocol="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-5",
        models=("gpt-5", "gpt-5-mini"),
        token_param="max_completion_tokens",
        key_hint="sk-...",
        console="https://platform.openai.com/api-keys",
    ),
    "gemini": Provider(
        key="gemini", label="Gemini (Google)",
        env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        protocol="openai",       # Google wystawia zgodny punkt OpenAI
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-3.1-pro-preview",
        models=("gemini-3.1-pro-preview", "gemini-3.7-flash",
                "gemini-3.5-flash", "gemini-2.5-flash"),
        key_hint="AIza...",
        console="https://aistudio.google.com/apikey",
    ),
    "deepseek": Provider(
        key="deepseek", label="DeepSeek",
        env=("DEEPSEEK_API_KEY",),
        protocol="openai",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        models=("deepseek-chat", "deepseek-reasoner"),
        key_hint="sk-...",
        console="https://platform.deepseek.com/api_keys",
    ),
}

DEFAULT = "claude"


# ------------------------------------------------------------ przechowywanie

def _read() -> dict:
    try:
        with open(CRED_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # starszy format trzymal jeden klucz pod "api_key" - traktujemy go jak Claude
    if "api_key" in data and "providers" not in data:
        return {"providers": {"claude": {"api_key": data["api_key"]}}}
    return data


def _write(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CRED_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.chmod(CRED_FILE, stat.S_IRUSR | stat.S_IWUSR)


def saved_key(provider: str) -> str | None:
    return (_read().get("providers", {}).get(provider, {}) or {}).get("api_key")


def saved_model(provider: str) -> str | None:
    return (_read().get("providers", {}).get(provider, {}) or {}).get("model")


def save_key(provider: str, api_key: str, model: str = "") -> None:
    """Zapisuje klucz dostawcy; plik zostaje czytelny tylko dla wlasciciela."""
    if provider not in PROVIDERS:
        raise KeyError(provider)
    data = _read()
    data.setdefault("providers", {})[provider] = {
        "api_key": api_key.strip(),
        **({"model": model} if model else {}),
    }
    _write(data)


def forget_key(provider: str) -> None:
    data = _read()
    data.get("providers", {}).pop(provider, None)
    _write(data)


def active_provider() -> str:
    want = os.environ.get("FCSIEGE_PROVIDER") or _read().get("aktywny") or DEFAULT
    return want if want in PROVIDERS else DEFAULT


def set_active(provider: str) -> str:
    if provider not in PROVIDERS:
        raise KeyError(provider)
    data = _read()
    data["aktywny"] = provider
    _write(data)
    return provider


# ------------------------------------------------------------------ odczyt

@dataclass
class Key:
    provider: str
    source: str                  # "env" | "plik" | "brak"
    api_key: str | None = None
    model: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.api_key)


def resolve(provider: str) -> Key:
    """Klucz dostawcy: najpierw srodowisko, potem zapisany plik."""
    p = PROVIDERS.get(provider)
    if p is None:
        return Key(provider, "brak", detail=f"nie znam dostawcy {provider}")
    for var in p.env:
        val = os.environ.get(var)
        if val:
            return Key(provider, "env", val,
                       saved_model(provider) or p.default_model, var)
    val = saved_key(provider)
    if val:
        return Key(provider, "plik", val,
                   saved_model(provider) or p.default_model, CRED_FILE)
    return Key(provider, "brak", None, p.default_model,
               "ustaw " + " albo ".join(p.env) + f" lub zapisz klucz ({p.console})")


def status() -> dict:
    """Przeglad wszystkich dostawcow - bez ujawniania kluczy."""
    out = []
    for name, p in PROVIDERS.items():
        k = resolve(name)
        out.append({
            "dostawca": name, "nazwa": p.label, "protokol": p.protocol,
            "skad_klucz": k.source, "ma_klucz": k.ok,
            "model": k.model or p.default_model,
            "modele": list(p.models),
            "zmienne_srodowiskowe": list(p.env),
            "gdzie_wziac_klucz": p.console,
            "format_klucza": p.key_hint,
        })
    return {"aktywny": active_provider(), "plik": CRED_FILE, "dostawcy": out}
