"""Petla rozmowy z Claude - bez Qt, wspolna dla okna i dla webowego UI.

Petle prowadzimy recznie, a nie przez SDK-owy tool runner, bo narzedzia moga
wymagac wykonania w cudzym watku (okno Qt), a tekst ma splywac token po tokenie.

Petla jest generatorem zdarzen. Kto ja konsumuje, decyduje, co z nimi zrobic:
okno zamienia je na sygnaly Qt, serwer HTTP na strumien SSE. Dzieki temu istnieje
jedna implementacja, a nie dwie, ktore po miesiacu sie rozjada.

    ("delta",      tekst)      kolejny kawalek odpowiedzi
    ("thinking",   tekst)      podsumowanie rozumowania
    ("tool_start", nazwa, argumenty_json)
    ("tool_end",   nazwa)
    ("done",)                  tura zakonczona normalnie
    ("error",      komunikat)  koniec z bledem
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .aitools import SYSTEM_PROMPT, TOOL_SPECS, localized_specs, result_to_text
from .i18n import _

MODEL = "claude-opus-5"
MAX_TOKENS = 16000
MAX_TOOL_ROUNDS = 24
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Conversation:
    """Historia rozmowy w formacie API."""
    messages: list = field(default_factory=list)

    def clear(self) -> None:
        self.messages.clear()


def system_prompt(context_note: str = "", lang: str = "pl") -> list[dict]:
    text = SYSTEM_PROMPT
    if lang == "en":
        text += ("\n\nAnswer in English. Tool names and result keys are in "
                 "English in this session.")
    if context_note:
        text += f"\n\nAktualny stan aplikacji:\n{context_note}"
    # stabilny prefiks - warto go zbuforowac miedzy turami
    return [{"type": "text", "text": text,
             "cache_control": {"type": "ephemeral"}}]


def stream_reply(client, conversation: Conversation, user_text: str,
                 run_tool, context_note: str = "", should_stop=None,
                 lang: str = "pl"):
    """Jedna tura rozmowy. Zwraca generator zdarzen opisanych w naglowku.

    `run_tool(nazwa, argumenty) -> obiekt` moze blokowac; to woalajacy decyduje,
    gdzie narzedzie faktycznie sie wykona.
    """
    stop = should_stop or (lambda: False)
    system = system_prompt(context_note, lang)
    tools = localized_specs() if lang == "en" else TOOL_SPECS
    conversation.messages.append({"role": "user", "content": user_text})

    try:
        for _round in range(MAX_TOOL_ROUNDS):
            if stop():
                yield ("error", _("Przerywam…"))
                return

            response = None
            for event in _one_turn(client, conversation, system, tools, stop):
                if event[0] == "final":
                    response = event[1]
                else:
                    yield event
            if response is None:
                return

            conversation.messages.append(
                {"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                cat = getattr(getattr(response, "stop_details", None),
                              "category", None)
                yield ("error", _("Model odmówił odpowiedzi")
                       + (f" ({cat})" if cat else "") + ".")
                return

            if response.stop_reason == "pause_turn":
                continue        # narzedzie serwerowe potrzebuje kolejnej rundy

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                yield ("done",)
                return

            results = []
            for block in tool_uses:
                args = block.input if isinstance(block.input, dict) else {}
                yield ("tool_start", block.name,
                       json.dumps(args, ensure_ascii=False))
                try:
                    text, is_error = result_to_text(run_tool(block.name, args)), False
                except Exception as exc:  # noqa: BLE001
                    text, is_error = f"błąd narzędzia: {exc}", True
                yield ("tool_end", block.name)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": text,
                    **({"is_error": True} if is_error else {}),
                })
            # wszystkie wyniki musza wrocic w JEDNEJ wiadomosci uzytkownika
            conversation.messages.append({"role": "user", "content": results})

        yield ("error", "Przekroczono limit rund narzędziowych.")
    except Exception as exc:  # noqa: BLE001
        yield ("error", f"{type(exc).__name__}: {exc}")


def _one_turn(client, conversation, system, tools, stop):
    """Jedno wywolanie API ze strumieniowaniem; na koncu ('final', wiadomosc)."""
    import anthropic
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=system,
            tools=tools,
            thinking={"type": "adaptive", "display": "summarized"},
            messages=conversation.messages,
        ) as stream:
            for event in stream:
                if stop():
                    break
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield ("delta", event.delta.text)
                    elif event.delta.type == "thinking_delta":
                        yield ("thinking", event.delta.thinking)
            yield ("final", stream.get_final_message())
    except anthropic.AuthenticationError:
        yield ("error", "Klucz API został odrzucony. Sprawdź go w ustawieniach czatu.")
    except anthropic.PermissionDeniedError:
        yield ("error", "Klucz nie ma uprawnień do modelu " + MODEL + ".")
    except anthropic.RateLimitError as exc:
        after = exc.response.headers.get("retry-after", "kilkadziesiąt")
        yield ("error", f"Limit zapytań wyczerpany. Spróbuj za {after} s.")
    except anthropic.APIConnectionError:
        yield ("error", "Brak połączenia z api.anthropic.com.")
    except anthropic.APIStatusError as exc:
        yield ("error", f"Błąd API {exc.status_code}: {exc.message}")
