"""Petla rozmowy dla dostawcow mowiacych protokolem OpenAI.

OpenAI, DeepSeek i Gemini wystawiaja ten sam punkt `/chat/completions`
z obsluga wywolan funkcji, wiec obsluguje ich jedna implementacja. Claude ma
osobna sciezke przez wlasne SDK - swiadomie, bo jego protokol jest bogatszy
(bloki mysli, buforowanie promptu, serwerowy fallback) i nie warto go
splaszczac do wspolnego mianownika.

Zdarzenia sa te same, co w `chat.stream_reply`, wiec okno, serwer i terminal
konsumuja jedno i drugie bez rozroznienia.

Uwaga: tej sciezki nie testowalem przeciw zywym API - nie mam kluczy tych
dostawcow. Protokol jest odwzorowany z dokumentacji; blad sieciowy albo zmiana
formatu zglosi sie jako zdarzenie ("error", ...), a nie jako cichy zly wynik.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .aitools import TOOL_SPECS, localized_specs, result_to_text
from .chat import MAX_TOKENS, MAX_TOOL_ROUNDS, system_prompt

TIMEOUT = 180


def tools_for_openai(lang: str = "pl") -> list[dict]:
    """Nasze definicje narzedzi w formacie funkcji OpenAI."""
    specs = localized_specs() if lang == "en" else TOOL_SPECS
    return [{
        "type": "function",
        "function": {
            "name": s["name"],
            "description": s["description"],
            "parameters": s["input_schema"],
        },
    } for s in specs]


def _post_stream(url: str, api_key: str, payload: dict):
    """Strumien SSE z punktu /chat/completions."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}",
                 "Accept": "text/event-stream"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def stream_reply(provider, key, conversation, user_text: str, run_tool,
                 context_note: str = "", should_stop=None, lang: str = "pl"):
    """Jedna tura rozmowy; zdarzenia jak w `chat.stream_reply`."""
    stop = should_stop or (lambda: False)
    url = provider.base_url.rstrip("/") + "/chat/completions"
    model = key.model or provider.default_model

    if not conversation.messages:
        conversation.messages.append(
            {"role": "system",
             "content": system_prompt(context_note, lang)[0]["text"]})
    conversation.messages.append({"role": "user", "content": user_text})

    try:
        for _round in range(MAX_TOOL_ROUNDS):
            if stop():
                yield ("error", "Przerwano.")
                return

            tekst_parts: list[str] = []
            wywolania: dict[int, dict] = {}
            skonczono = None
            try:
                with _post_stream(url, key.api_key, {
                    "model": model,
                    provider.token_param: MAX_TOKENS,
                    "messages": conversation.messages,
                    "tools": tools_for_openai(lang),
                    "stream": True,
                }) as resp:
                    for raw in resp:
                        if stop():
                            break
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        body = line[5:].strip()
                        if body == "[DONE]":
                            break
                        try:
                            chunk = json.loads(body)
                        except ValueError:
                            continue
                        choices = chunk.get("choices") or [{}]
                        delta = choices[0].get("delta") or {}
                        if choices[0].get("finish_reason"):
                            skonczono = choices[0]["finish_reason"]
                        if delta.get("content"):
                            tekst_parts.append(delta["content"])
                            yield ("delta", delta["content"])
                        if delta.get("reasoning_content"):     # DeepSeek
                            yield ("thinking", delta["reasoning_content"])
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = wywolania.setdefault(
                                idx, {"id": "", "name": "", "args": "",
                                      "extra": None})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            # Gemini 3.x wymaga odeslania swojej sygnatury
                            # rozumowania razem z wywolaniem funkcji, inaczej
                            # kolejna runda konczy sie bledem 400
                            if tc.get("extra_content"):
                                slot["extra"] = tc["extra_content"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                yield ("error", f"{provider.label}: HTTP {exc.code} — {detail}")
                return
            except urllib.error.URLError as exc:
                yield ("error", f"{provider.label}: brak połączenia ({exc.reason})")
                return

            tekst = "".join(tekst_parts)
            if not wywolania:
                if tekst:
                    conversation.messages.append(
                        {"role": "assistant", "content": tekst})
                yield ("done",)
                return

            calls = [wywolania[i] for i in sorted(wywolania)]
            conversation.messages.append({
                "role": "assistant",
                "content": tekst or None,
                "tool_calls": [{
                    "id": c["id"] or f"call_{i}",
                    "type": "function",
                    "function": {"name": c["name"], "arguments": c["args"] or "{}"},
                    **({"extra_content": c["extra"]} if c.get("extra") else {}),
                } for i, c in enumerate(calls)],
            })

            for i, c in enumerate(calls):
                try:
                    args = json.loads(c["args"] or "{}")
                except ValueError:
                    args = {}
                yield ("tool_start", c["name"],
                       json.dumps(args, ensure_ascii=False))
                try:
                    tresc = result_to_text(run_tool(c["name"], args))
                except Exception as exc:  # noqa: BLE001
                    tresc = f"błąd narzędzia: {exc}"
                yield ("tool_end", c["name"])
                conversation.messages.append({
                    "role": "tool",
                    "tool_call_id": c["id"] or f"call_{i}",
                    "content": tresc,
                })
            if skonczono == "stop" and not calls:
                yield ("done",)
                return

        yield ("error", "Przekroczono limit rund narzędziowych.")
    except Exception as exc:  # noqa: BLE001
        yield ("error", f"{type(exc).__name__}: {exc}")
