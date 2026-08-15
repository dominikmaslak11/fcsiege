"""Poswiadczenia do API Anthropica - bez Qt.

Wydzielone z `aiclient`, bo z tego samego kodu korzysta serwer HTTP, ktory
nie ma prawa wymagac PySide6. Kolejnosc szukania klucza jest swiadoma:
zmienna srodowiskowa, potem wlasny plik z prawami 0600, na koncu profil
oficjalnego CLI Anthropica.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass

CONFIG_DIR = os.path.expanduser("~/.config/fcsiege")
CRED_FILE = os.path.join(CONFIG_DIR, "credentials.json")
ANTHROPIC_PROFILE_DIR = os.path.expanduser("~/.config/anthropic/credentials")


# ------------------------------------------------------------- poswiadczenia

@dataclass
class Credentials:
    """Skad wziac klucz i czy w ogole go mamy."""
    source: str          # "env" | "plik" | "profil" | "brak"
    api_key: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.source != "brak"


def saved_key() -> str | None:
    try:
        with open(CRED_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("api_key") or None
    except (OSError, ValueError):
        return None


def save_key(api_key: str) -> None:
    """Zapisuje klucz tylko do odczytu dla wlasciciela (0600)."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CRED_FILE, "w", encoding="utf-8") as fh:
        json.dump({"api_key": api_key}, fh)
    os.chmod(CRED_FILE, stat.S_IRUSR | stat.S_IWUSR)


def forget_key() -> None:
    try:
        os.remove(CRED_FILE)
    except OSError:
        pass


def has_anthropic_profile() -> bool:
    """Czy istnieje profil OAuth zalozony przez CLI Anthropica."""
    try:
        return any(f.endswith(".json") for f in os.listdir(ANTHROPIC_PROFILE_DIR))
    except OSError:
        return False


def detect_credentials() -> Credentials:
    """Kolejnosc jak w SDK: zmienna srodowiskowa, nasz plik, profil OAuth."""
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return Credentials("env", env, "zmienna środowiskowa ANTHROPIC_API_KEY")
    key = saved_key()
    if key:
        return Credentials("plik", key, CRED_FILE)
    if has_anthropic_profile():
        return Credentials("profil", None, "profil OAuth z ~/.config/anthropic")
    return Credentials("brak")


def anthropic_cli_present() -> bool:
    """Czy w PATH jest CLI Anthropica (a nie Apache Ant o tej samej nazwie)."""
    import shutil
    import subprocess
    path = shutil.which("ant")
    if not path:
        return False
    try:
        out = subprocess.run([path, "auth", "--help"], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    blob = (out.stdout + out.stderr).lower()
    return "anthropic" in blob or "login" in blob and "buildfile" not in blob


def make_client(creds: Credentials):
    """Tworzy klienta SDK. Bez klucza SDK sam znajdzie profil OAuth."""
    import anthropic
    if creds.api_key:
        return anthropic.Anthropic(api_key=creds.api_key)
    return anthropic.Anthropic()


# ----------------------------------------------------------------- rozmowa
