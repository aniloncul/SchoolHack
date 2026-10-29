"""Studdy-Methodik für Mathematik: das Modell führt den Unterricht.

Warum diese Datei?
    Die Vorlage (Studdy, Session 364768) arbeitet nicht mit einem Vorlesungstext,
    sondern mit einer Kette von Türmen: pro Turm genau EIN Gedanke, die passenden
    Tafelbefehle und am Ende genau EINE Frage an die Schülerin. Die Antwort der
    Schülerin geht in den nächsten Turm ein. Genau so ist dieser Client gebaut.

Abgrenzung zu ``math_agent``
    ``math_agent`` rechnet exakt und ist der Offline-Fallback, wenn kein Modell
    erreichbar ist. Hier rechnet das Modell; die App prüft nur die Form der
    Ausgabe und zeichnet die Tafel. Jede Zahl, die das Modell nennt, stammt aus
    seinem eigenen Lösungstext — deshalb steht im Prompt ausdrücklich: erst
    prüfen, dann behaupten.

Öffentliche API
    available()                  -> bool
    next_turn(task, history, answer, turn) -> dict
    models()                     -> list[str]
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request

from . import math_agent
from .chat_llm import api_key, available as _key_available, env_value

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Kostenlose Modelle auf OpenRouter, nach Mathematik-Leistung geordnet.
# Der erste Eintrag lässt sich mit MATH_MODEL überschreiben; die Liste ist
# bewusst konservativ: große Reasoning-Modelle zuerst, Router zuletzt.
DEFAULT_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "thinkingmachines/inkling:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
    "openrouter/free",
]

MAX_HISTORY = 8
MAX_TURNS = 10


def _nim() -> dict | None:
    """NVIDIA NIM (OpenAI-kompatibel) — der bevorzugte Anbieter für Mathe."""
    key = env_value("NVIDIA_NIM_API_KEY")
    if not key:
        return None
    base = env_value("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    model = env_value("NVIDIA_NIM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    return {"name": "nim", "model": model, "url": base + "/chat/completions", "key": key}


def _nim_model(model: str) -> dict | None:
    """Zweiter NIM-Eintrag mit anderem Modell (gleicher Schlüssel)."""
    base = env_value("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    key = env_value("NVIDIA_NIM_API_KEY")
    if not key or not model:
        return None
    return {"name": "nim", "model": model, "url": base + "/chat/completions", "key": key}


def _providers() -> list[dict]:
    """NIM zuerst; OpenRouter nur als kurze Rückfallebene.

    Die kostenlosen OpenRouter-Modelle antworten oft erst nach 30-90 s oder
    gar nicht. Zwei Versuche reichen: danach übernimmt der Rechenkern.
    """
    out: list[dict] = []
    nim = _nim()
    if nim:
        out.append(nim)
        hedge = env_value("NVIDIA_NIM_HEDGE_MODEL", "")
        if hedge and hedge != nim["model"]:
            extra = _nim_model(hedge)
            if extra:
                out.append(extra)
    if _key_available():
        for model in models()[:1]:
            out.append({"name": "openrouter", "model": model,
                        "url": OPENROUTER_URL, "key": api_key()})
    return out


def models() -> list[str]:
    configured = env_value("MATH_MODEL")
    ordered = ([configured] if configured else []) + DEFAULT_MODELS
    out: list[str] = []
    for name in ordered:
        if name and name not in out:
            out.append(name)
    return out


def available() -> bool:
    return bool(_providers())


def _timeout() -> float:
    try:
        return float(env_value("MATH_TIMEOUT", "120"))
    except ValueError:
        return 120.0


def _budget() -> float:
    """Gesamtzeit fuer einen Turm, danach antwortet der Rechenkern."""
    try:
        return float(env_value("MATH_BUDGET", "48"))
    except ValueError:
        return 48.0


def _hedge_delay() -> float:
    """Nach so vielen Sekunden startet das schnellere Ersatzmodell mit."""
    try:
        return float(env_value("MATH_HEDGE_DELAY", "8"))
    except ValueError:
        return 8.0


def _run_one(provider: dict, messages: list[dict], timeout: float, out) -> None:
    """Ein Modellaufruf im Hintergrund; Ergebnis geht in die Warteschlange."""
    for attempt in range(2):
        try:
            text = _post(provider, messages, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt == 0:
                time.sleep(1.0)
                continue
            out.put((provider, "", "HTTP " + str(exc.code)))
            return
        except Exception as exc:
            out.put((provider, "", str(exc)[:120]))
            return
        out.put((provider, text, None))
        return


def _provider_timeout(provider: dict) -> float:
    if provider.get("name") == "nim":
        try:
            return float(env_value("MATH_TIMEOUT_NIM", "22"))
        except ValueError:
            return 22.0
    return 8.0


# ---------------------------------------------------------------------------
# Prompt — die Regeln der Vorlage, auf Deutsch
# ---------------------------------------------------------------------------

SYSTEM = """Du bist der Mathematik-Tutor der App "School Hack" für Klasse 9 in Berlin.
Du bist wie ein guter Freund, der Mathe erklärt: locker, direkt, ermutigend.

════════════════════════════════════════
KERNPRINZIP — FLOW-MODUS (nicht Quiz-Modus):
════════════════════════════════════════
• Du führst den Unterricht aktiv. Eine mathematische Operation besteht aus 2–4 kleinen,
  interaktiven Teilschritten ("steps").
• JEDER TEILSCHRITT WIRD ZUERST VOM TUTOR MIT EINEM LOCKEREN SATZ ERKLÄRT ("say"),
  UND DANN ERSCHEINT DIE DAZUGEHÖRIGE FORMEL AUF DER TAFEL ("board").
  Beispiel bei Schnittpunkten:
    1. Teilschritt say: "İlk başta iki denklemi birbirine eşitliyoruz." -> board: f(x) = g(x)
    2. Teilschritt say: "Şimdi bunları tek tarafta toplamamız lazım." -> board: ax² + bx + c = 0
    3. Teilschritt say: "Şimdi x'i yalnız bırakmak için Mitternachtsformel kullanıyoruz." -> board: x_{1,2} = ...
    4. Teilschritt say: "x değerlerini bulduk, şimdi kesişim noktalarının y değerlerini buluyoruz." -> board in place="result"
• Du fragst die Schülerin NUR an GROSSEN Übergängen:
    – Am Ende einer ganzen Phase (z.B. nach x-Werte → "Jetzt die y-Werte dazu?")
    – Wenn du eine Formel brauchst, die sie kennen sollte ("Kennst du die Mitternachtsformel?")
    – Am Ende einer Teilaufgabe ("b. angehen?")
  Nur dann: ask setzen. Sonst immer: ask = null.
• Sagt die Schülerin "ok", "devam et", "weiter", "ja", "evet", "tamam" o.ä.:
  NICHT nochmal fragen — direkt weitermachen. ask = null.
• Sagt sie "weiß nicht" / "hatırlamıyorum": Formel DIREKT zeigen + erklären + weitermachen.
  Kein Hinweis ohne Lösung — das bremst den Fluss.
• Antwortet sie falsch: kurz korrigieren, richtige Zahl nennen, sofort weitermachen.
• Die Schülerin steuert das Tempo mit "ok/devam" — du lieferst den Stoff.

════════════════════════════════════════
TON:
════════════════════════════════════════
• Locker, direkt, wie ein Freund: "Tamamdır!", "Hemen hallediyoruz!",
  "Das ist der Trick hier:", "Jetzt wird's spannend:", "Schau mal..."
• Jedes "say" in einem step hat genau 1 kurzen Satz. Nie formal, nie langweilig.

════════════════════════════════════════
RECHNEN:
════════════════════════════════════════
• Rechne selbst und exakt. Dezimalzahlen auf Deutsch mit Komma (22{,}65).
• Prüfe jede Zahl. Beachte Definitionsbereich — schneide die Lösungsmenge.
• Benutze ausschließlich die verifizierten Zahlen (falls im Prompt angegeben).
• Nach spätestens 10 Türmen: Ergebnis zusammenfassen, done=true.
• Nur die gestellte Aufgabe bearbeiten.

════════════════════════════════════════
TAFEL (Multi-Spalten):
════════════════════════════════════════
1. "header": Aufgabenstellung, Funktionen, Definitionsbereich.
2. "step": Rechenschritte — hier erscheint der Großteil der Arbeit (linke Spalte).
   Wichtige Umformungen in color="#0085CF" (Blau). Kreise Zwischenergebnisse mit "circle" ein.
3. "reference": Formeln am rechten Rand (color="#C73C3C" Rot), z.B. Mitternachtsformel.
4. "notes": Zusammenfassung am Ende.
5. "result": Endergebnis / Punkteberechnung (wandert in die Nebenspalte rechts).

ERLAUBTE TAFEL-ELEMENTE:
  {"op":"latex", "place":"header|step|reference|notes|result", "id":"...", "latex":"...", "color":"#222222|#0085CF|#C73C3C"}
  {"op":"text",  "place":"header|step|reference|notes|result", "id":"...", "text":"..."}
  {"op":"circle", "target":"<elementId>", "color":"blue|red"}
  {"op":"highlight", "target":"<elementId>", "color":"yellow|cyan"}
  {"op":"plot",  "curves":[{"label":"f","expr":"2x^2+3x+7"}], "points":[[-1,6,"P1"]], "roots":[[-1,""]]}

════════════════════════════════════════
AUSGABE — ausschließlich gültiges JSON mit "steps", kein Markdown:
════════════════════════════════════════
{
  "steps": [
    {
      "say": "1. Teilschritt gesprochen (1 lockerer, direkter Satz)",
      "board": [ {"op":"latex", "place":"step", "latex":"..."} ]
    },
    {
      "say": "2. Teilschritt gesprochen (1 lockerer, direkter Satz)",
      "board": [ {"op":"latex", "place":"step", "latex":"..."} ]
    }
  ],
  "ask": {"question":"...", "kind":"text|choice", "choices":["..."], "answer":"...", "hint":"..."},
  "done": false
}
- ask = null wenn kein echter Übergangspunkt (Normalfall: die meisten Türme!).
- Bei kind="choice" max. 3 Antworten.
- Fertig: ask = null, done = true.
"""


def _history_text(history: list[dict]) -> str:
    lines = []
    for item in history[-MAX_HISTORY:]:
        say = re.sub(r"\s+", " ", str(item.get("say") or ""))[:240]
        question = re.sub(r"\s+", " ", str(item.get("question") or ""))[:160]
        answer = re.sub(r"\s+", " ", str(item.get("answer") or ""))[:160]
        line = "Tutor: " + say
        if question:
            line += " | Frage: " + question
        if answer:
            line += " | Schülerin: " + answer
        lines.append(line)
    return "\n".join(lines)


def verified_path(expr: str) -> str:
    """Der exakt gerechnete Rechenweg als Wahrheits-Anker fuer das Modell.

    Das Modell fuehrt das Gespraech, aber die Zahlen kommen aus dem Rechenkern.
    Ohne diesen Block hat das Modell in Tests x1 = 8 statt x1 = 5 gerechnet.
    """
    expr = (expr or "").strip()
    if not expr:
        return ""
    try:
        result = math_agent.analyse(expr)
    except Exception:
        return ""
    if not result or not result.get("ok"):
        return ""

    lines: list[str] = []
    for cmd in result.get("commands") or []:
        if cmd.get("commandName") != "write_latex":
            continue
        args = (cmd.get("actionItem") or {}).get("args") or {}
        latex = str(args.get("latex") or "").strip()
        if latex and latex not in lines:
            lines.append(latex)
    if not lines:
        return ""

    out = ["Geprüfter Rechenweg (exakt gerechnet — diese Zeilen und Zahlen stimmen):"]
    out += ["- " + line for line in lines[:12]]

    facts = result.get("facts") or {}
    if facts.get("points"):
        out.append("- Punkte: " + " und ".join("(" + p + ")" for p in facts["points"]))
    if facts.get("interval_text"):
        out.append("- Lösungsmenge: " + facts["interval_text"])
    if facts.get("vertex"):
        out.append("- Scheitelpunkt: S(" + facts["vertex"] + ")")
    if facts.get("above"):
        out.append("- Lage: " + facts["above"])
    return "\n".join(out)


def _user_prompt(task: str, history: list[dict], answer: str, turn: int,
                 verified: str = "") -> str:
    parts = ["Aufgabe:", task.strip()[:1200], ""]
    if verified:
        parts += [verified, "",
                  "Benutze diese Zeilen als Wahrheit. Schreibe pro Turm trotzdem nur "
                  "den EINEN nächsten Schritt an die Tafel und weiche bei keiner Zahl ab. "
                  "Schreibe auf die Tafel nur Zeilen aus dieser Liste (oder eine direkte "
                  "Umformung davon) — keine eigenen Zwischenschritte mit neuen Zahlen.", ""]
    if history:
        parts += ["Bisheriger Unterricht:", _history_text(history), ""]
    if answer:
        parts += ["Letzte Antwort der Schülerin:", answer.strip()[:200], ""]
    if turn == 0:
        parts.append(
            "Beginne jetzt: Schreibe die Aufgabe als LaTeX auf die Tafel (f und g getrennt, "
            "ohne 'Aufgabe 1:'), erkläre in einem kurzen Satz was das Ziel ist "
            "und starte sofort mit dem ersten Lösungsschritt — ohne zu fragen ob die Schülerin bereit ist. "
            "Turm 1."
        )
    elif turn >= MAX_TURNS:
        parts.append(
            "Das ist der letzte Turm. Fasse das Ergebnis in einem lockeren Satz zusammen, "
            "stelle keine Frage mehr und setze done=true."
        )
    else:
        # Flow-Modus: Antwort der Schülerin kurz aufgreifen, dann direkt weitermachen
        answer_lower = (answer or "").lower().strip()
        simple_continue = any(w in answer_lower for w in [
            "ok", "devam", "weiter", "ja", "evet", "tamam", "gut", "alright",
            "stimmt", "richtig", "mhm", "hmm", "jo", "yep", "anladim", "anladım",
            "okay", "natürlich", "sicher", "klar"
        ])
        if simple_continue:
            parts.append(
                f"Turm {turn + 1}: Die Schülerin hat mit '{answer}' bestätigt — "
                "direkt mit dem nächsten Schritt weitermachen. ask = null. "
                "Zeige 2–4 Tafelschritte und erkläre sie kurz."
            )
        else:
            parts.append(
                f"Turm {turn + 1}: Reagiere kurz auf die letzte Antwort und mache "
                "danach direkt 2–4 Tafelschritte weiter. "
                "Nur wenn es ein echter Übergangspunkt ist: ask setzen. Sonst ask = null."
            )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _post(provider: dict, messages: list[dict], timeout: float | None = None) -> str:
    payload = {
        "model": provider["model"],
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1200,
        "stream": False,
    }
    if provider.get("name") == "nim":
        # Ohne Denk-Spur antwortet das Modell in ~9 s statt in 20-40 s; die
        # Pruefung steht ohnehin im Prompt (erst rechnen, dann behaupten).
        payload["chat_template_kwargs"] = {"thinking": False}
    req = urllib.request.Request(
        provider["url"],
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + provider["key"],
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "School Hack - Mathematik",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout or _timeout()) as resp:
        data = json.loads(resp.read())
    return str(data["choices"][0]["message"].get("content") or "").strip()


def _parse_json(text: str) -> dict | None:
    """Erstes vollständiges JSON-Objekt aus der Antwort schneiden."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = text.find("{")
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------------
# Tafel
# ---------------------------------------------------------------------------

_PLACE = {
    "task": ("loesung", "header"),
    "header": ("loesung", "header"),
    "step": ("loesung", "schritte"),
    "steps": ("loesung", "schritte"),
    "reference": ("loesung", "reference_material"),
    "reference_material": ("loesung", "reference_material"),
    "formula": ("loesung", "reference_material"),
    "notes": ("loesung", "final_notes"),
    "summary": ("loesung", "final_notes"),
    "final_notes": ("loesung", "final_notes"),
    "result": ("loesung", "ergebnis"),
    "ergebnis": ("loesung", "ergebnis"),
}

_COLORS = (math_agent.BLUE, math_agent.RED)


def _num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _plot(command: dict) -> str | None:
    curves = []
    for i, curve in enumerate(command.get("curves") or []):
        if not isinstance(curve, dict):
            continue
        expr = str(curve.get("expr") or "").replace("y=", "").replace(" ", "")
        if not expr:
            continue
        try:
            poly = math_agent.parse_expr(expr)
        except math_agent.ParseError:
            continue
        label = str(curve.get("label") or "f")[:6]
        curves.append((label, poly, _COLORS[i % len(_COLORS)]))
    if not curves:
        return None

    points = []
    for point in command.get("points") or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        x, y = _num(point[0]), _num(point[1])
        if x is None or y is None:
            continue
        points.append((x, y, str(point[2] if len(point) > 2 else "")[:10]))

    roots = []
    for root in command.get("roots") or []:
        if isinstance(root, (list, tuple)) and root:
            x = _num(root[0])
            if x is not None:
                roots.append((x, str(root[1] if len(root) > 1 else "")[:10]))
        else:
            x = _num(root)
            if x is not None:
                roots.append((x, ""))

    try:
        return math_agent.svg_plot(curves, points, roots)
    except Exception:
        return None


def _commands(board: object, turn: int, offset: int = 0, step_idx: int = 0) -> list[dict]:
    """JSON-Tafel -> Kommandoprotokoll der Seite."""
    commands: list[dict] = []
    if not isinstance(board, list):
        return commands
    for i, item in enumerate(board[:6]):
        if not isinstance(item, dict):
            continue
        op = str(item.get("op") or "").lower()
        place = _PLACE.get(str(item.get("place") or "step"), _PLACE["step"])
        custom_id = str(item.get("id") or "").strip()
        eid = custom_id or ("m" + str(turn) + "_" + str(step_idx) + "_" + str(i))
        color = item.get("color")
        order = len(commands) + offset
        if op == "latex":
            latex = str(item.get("latex") or "").strip()
            if latex:
                commands.append(math_agent._tex(order, eid, place[0], place[1], latex, color=color))
        elif op == "text":
            text = str(item.get("text") or "").strip()
            if text:
                commands.append(math_agent._txt(order, eid, place[0], place[1], text, color=color))
        elif op in ("circle", "circle_annotate"):
            target = str(item.get("target") or item.get("targetElementId") or "").strip()
            if target:
                commands.append(math_agent._circle(order, eid, target, color=color or "blue"))
        elif op == "highlight":
            target = str(item.get("target") or item.get("targetElementId") or "").strip()
            if target:
                commands.append(math_agent._highlight(order, eid, target, color=color or "yellow"))
        elif op == "plot":
            svg = _plot(item)
            if svg:
                commands.append(math_agent._draw(order, eid, "grafik", "schaubild", svg,
                                                 "Schaubild"))
    return commands


def _final_guard(commands: list[dict], expr: str, turn: int) -> list[dict]:
    """Im letzten Turm duerfen nur geprüfte Ergebnisse auf der Tafel stehen.

    Das Modell rechnet in langen Wegen gelegentlich falsch (x1 = 8 statt 5,
    S1(-1|5) statt (-1|6)). Beim Abschluss ersetzt der Rechenkern deshalb die
    Ergebniszeilen — der Weg davor bleibt der des Modells.
    """
    expr = (expr or "").strip()
    if not expr:
        return commands
    try:
        core = math_agent.analyse(expr)
    except Exception:
        return commands
    if not core or not core.get("ok"):
        return commands

    is_inequality = core.get("facts", {}).get("op") in ("<", ">", "<=", ">=")
    results = [c for c in (core.get("commands") or [])
               if c.get("commandName") in ("write_latex", "write_text")
               and (c.get("position") or {}).get("columnId") == "ergebnis"]
    
    # Bei Ungleichungen niemals irrtuemliche Schnittpunkte P_i dranhaengen!
    if is_inequality:
        results = [c for c in results if not ("P_{" in str((c.get("actionItem") or {}).get("args", {}).get("latex") or "")
                                          or "P_" in str((c.get("actionItem") or {}).get("args", {}).get("latex") or ""))]

    if not results:
        return commands

    kept = [c for c in commands
            if (c.get("position") or {}).get("columnId") != "ergebnis"]
    for i, cmd in enumerate(results):
        cmd = json.loads(json.dumps(cmd))
        cmd["orderIndex"] = len(kept) + i
        old_id = cmd.get("elementId") or "res"
        cmd["elementId"] = "g" + str(turn) + "_" + old_id
        args = (cmd.get("actionItem") or {}).get("args") or {}
        args["id"] = cmd["elementId"]
        kept.append(cmd)
    return kept


def _ask(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    question = re.sub(r"\s+", " ", str(raw.get("question") or "")).strip()[:300]
    if not question:
        return None
    kind = str(raw.get("kind") or "text").lower()
    if kind not in ("number", "choice", "text"):
        kind = "text"
    choices = [str(c)[:40] for c in (raw.get("choices") or []) if str(c).strip()][:4]
    if kind == "choice" and len(choices) < 2:
        kind = "text"
    answer = str(raw.get("answer") or "").strip()[:80]
    hint = re.sub(r"\s+", " ", str(raw.get("hint") or "")).strip()[:240]
    accept = math_agent._answer_variants(answer) if answer else []
    return {"question": question, "kind": kind, "choices": choices,
            "answer": answer, "accept": accept, "hint": hint}


# ---------------------------------------------------------------------------
# Öffentliche API
# ---------------------------------------------------------------------------

def next_turn(task: str, history: list[dict] | None = None, answer: str = "",
              turn: int = 0, expr: str = "") -> dict:
    """Einen Unterrichtsturm erzeugen.

    Rückgabe::

        {"ok": True, "say": "...", "commands": [...],
         "ask": {...}|None, "done": False, "model": "..."}

    Bei Modellfehlern steht ``ok=False`` und ``error``; der Aufrufer spielt
    dann den deterministischen Plan aus ``math_agent.build_plan``.
    """
    task = (task or "").strip()
    if not task:
        return {"ok": False, "error": "empty", "detail": "Aufgabe fehlt."}
    if not available():
        return {"ok": False, "error": "no-key",
                "detail": "Kein Modell konfiguriert (NIM oder OpenRouter)."}

    history = history or []
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": _user_prompt(task, history, answer, turn,
                                                        verified_path(expr))}]

    # Ein Turm darf nicht ewig dauern. Das Hauptmodell startet sofort; ist es
    # nach ein paar Sekunden noch nicht fertig (oder kurz ueberlastet), laeuft
    # ein zweites, schnelleres NIM-Modell parallel. Die erste brauchbare
    # Antwort gewinnt. Danach uebernimmt der Rechenkern.
    deadline = time.monotonic() + _budget()
    providers = _providers()
    results: queue.Queue = queue.Queue()
    started = 0
    answered = 0
    nim_attempts = 0
    last_error = ""
    index = 0
    nim_hedge_at = None

    def launch(provider):
        nonlocal started, nim_attempts
        if provider["name"] == "nim":
            nim_attempts += 1
        remaining = max(2.0, deadline - time.monotonic())
        threading.Thread(
            target=_run_one,
            args=(provider, messages, min(_provider_timeout(provider), remaining), results),
            daemon=True).start()
        started += 1

    launch(providers[0])
    index = 1
    if index < len(providers) and providers[index]["name"] == "nim":
        nim_hedge_at = time.monotonic() + _hedge_delay()

    while True:
        now = time.monotonic()
        if now >= deadline:
            last_error = last_error or "Zeitbudget aufgebraucht."
            break
        if nim_hedge_at is not None and now >= nim_hedge_at:
            launch(providers[index])
            index += 1
            nim_hedge_at = None
            continue

        wait = deadline - now
        if nim_hedge_at is not None:
            wait = min(wait, nim_hedge_at - now)
        try:
            provider, text, exc = results.get(timeout=max(0.05, wait))
        except queue.Empty:
            continue

        answered += 1
        label = provider["name"] + ":" + provider["model"]
        if exc is not None:
            last_error = label + ": " + str(exc)
        else:
            data = _parse_json(text)
            if data:
                steps_raw = data.get("steps")
                parsed_steps = []
                all_commands = []
                say_parts = []

                if isinstance(steps_raw, list) and steps_raw:
                    for s_idx, s in enumerate(steps_raw):
                        if not isinstance(s, dict):
                            continue
                        s_say = re.sub(r"\s+", " ", str(s.get("say") or "")).strip()
                        s_cmds = _commands(s.get("board"), turn, offset=len(all_commands), step_idx=s_idx)
                        if s_say or s_cmds:
                            parsed_steps.append({
                                "say": s_say,
                                "commands": s_cmds
                            })
                            if s_say:
                                say_parts.append(s_say)
                            all_commands.extend(s_cmds)

                if not parsed_steps:
                    top_say = re.sub(r"\s+", " ", str(data.get("say") or "")).strip()
                    top_cmds = _commands(data.get("board"), turn)
                    all_commands = top_cmds
                    say_parts = [top_say] if top_say else []
                    if top_say or top_cmds:
                        parsed_steps = [{"say": top_say, "commands": top_cmds}]

                if parsed_steps:
                    if data.get("done"):
                        all_commands = _final_guard(all_commands, expr, turn)
                        if parsed_steps:
                            # Ensure the final step contains the verified results
                            parsed_steps[-1]["commands"] = [
                                c for c in all_commands
                                if (c.get("position") or {}).get("columnId") == "ergebnis"
                            ] or parsed_steps[-1]["commands"]

                    full_say = " ".join(say_parts)[:600]
                    return {
                        "ok": True,
                        "say": full_say,
                        "commands": all_commands,
                        "steps": parsed_steps,
                        "ask": None if data.get("ask") is None else _ask(data.get("ask")),
                        "done": bool(data.get("done")),
                        "model": label,
                    }
            last_error = label + ": keine gueltige JSON-Antwort"

        if answered >= started:
            # Ein kurzer Aussetzer (NIM ueberlastet, Lesetimeout) ist haeufiger
            # als ein echter Ausfall: einmal denselben Anbieter wiederholen,
            # bevor die kostenlose Rueckfallebene drankommt.
            if provider["name"] == "nim" and nim_attempts < 2 and deadline - time.monotonic() > 4:
                launch(provider)
            elif index < len(providers):
                launch(providers[index])
                index += 1
            else:
                break

    return {"ok": False, "error": "model",
            "detail": last_error or "Kein Modell erreichbar."}
