"""School Hack – FastAPI app (teacher + student portals)."""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import (agent, chat_llm, db, i18n, learning_profile, llm, math_agent, math_tutor,
               pdf_text, subtopic_quizzes, subtopics_data, tts, vectorstore)
from .chat_text import clean_answer, clean_topic, is_refusal, is_soft_refusal
from .questions import (PASS_MARK, SKILLS, VISIBLE_SKILLS, VISIBLE_TOPICS,
                        SUBTOPIC_TITLES, SUBTOPIC_COVERS, SUBTOPIC_ORDER,
                        CURRICULUM_UNITS, build_test, generate, grade, note_for)

BASE = Path(__file__).resolve().parent
app = FastAPI(title="School Hack")
# Keep the signed login cookie stable across page navigation and app restarts.
# The demo session lasts 30 days. A hosted copy sets SESSION_SECRET (render.yaml
# generates one), because the fallback below is public in the repository.
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "chemtutor-hackathon-demo"),
                   max_age=60 * 60 * 24 * 30,
                   same_site="lax")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
tpl = Jinja2Templates(directory=BASE / "templates")

# Demo credentials (same for both portals, as requested). Replace before real use.
USERS = {"student": {"admin": "admin"}, "teacher": {"admin": "admin"}}

SUBJECTS = [
    {"slug": "chemie", "name": "Chemie", "icon": "⚗️", "active": True, "desc": "Säuren & Laugen, Mol, Alkane"},
    {"slug": "mathematik", "name": "Mathematik", "icon": "∑", "active": True,
     "desc": "Gleichungen, Parabeln, Ungleichungen"},
    {"slug": "biologie", "name": "Biologie", "icon": "🧬", "active": False, "desc": "Bald verfügbar"},
    {"slug": "physik", "name": "Physik", "icon": "⚛", "active": False, "desc": "Bald verfügbar"},
]


@app.on_event("startup")
def _startup() -> None:
    db.init()


@app.middleware("http")
async def _language(request: Request, call_next):
    """Make the chosen language (cookie) available to the code of this request."""
    i18n.set_current(request.cookies.get(i18n.COOKIE))
    return await call_next(request)


def role(request: Request) -> str | None:
    return request.session.get("role")


def need(request: Request, r: str):
    if role(request) != r:
        return RedirectResponse(f"/login/{r}", status_code=303)
    return None


def render(request: Request, name: str, **ctx):
    return tpl.TemplateResponse(request, name, {
        "role": role(request), "user": request.session.get("user"), "llm": llm.available(),
        "lang": i18n.current(), **ctx
    })


# ---------------------------------------------------------------------------
# learning overview on the student home page (own progress only)
# ---------------------------------------------------------------------------
GRAPH_TOPICS = VISIBLE_TOPICS
GRAPH_TOPIC_SHORT = {
    "Stoffmenge und Mol": "Stoffmenge & Mol",
    "Säuren und Laugen": "Säuren & Laugen",
    "Kohlenwasserstoffe": "Kohlenwasserstoffe",
    "Alkohole": "Alkohole",
    "Organische Säuren": "Organische Säuren",
    "Ester": "Ester",
}

# Official Berlin-Brandenburg curriculum labels (Chemie 9/10).
# The chemistry page renders these above the skills of each topic.
TOPIC_META = {
    unit["topic"]: {
        "code": unit["code"],
        "official": unit["title"],
        "sub": unit.get("sub", ""),
    }
    for unit in CURRICULUM_UNITS
}
RADAR_POS = {"chemie": "top", "mathematik": "right", "biologie": "bottom", "physik": "left"}
RADAR_ANGLES = {"chemie": -90, "mathematik": 0, "biologie": 90, "physik": 180}
RADAR_W = RADAR_H = 640
RADAR_CX = RADAR_CY = 320
RADAR_R = 250
RADAR_RINGS = (0.2, 0.4, 0.6, 0.8, 1.0)
RADAR_SWEEP = 26


def _tone(p: float) -> tuple[str, str, str]:
    return learning_profile.tone(p)


def _radar_point(angle: float, radius: float) -> tuple[float, float]:
    a = math.radians(angle)
    return round(RADAR_CX + radius * math.cos(a), 1), round(RADAR_CY + radius * math.sin(a), 1)


def _learning_network(mastery: dict[str, float]) -> dict:
    """Build the diamond radar of all four subjects (own progress only)."""
    values = [max(0.0, min(1.0, mastery.get(s, 0.3))) for s in VISIBLE_SKILLS]
    topic_rows = []
    for topic in GRAPH_TOPICS:
        ids = [s for s in VISIBLE_SKILLS if VISIBLE_SKILLS[s]["topic"] == topic]
        ps = [max(0.0, min(1.0, mastery.get(s, 0.3))) for s in ids]
        pc = round(100 * sum(ps) / len(ps))
        topic_rows.append({
            "name": topic, "short": GRAPH_TOPIC_SHORT[topic], "pc": pc,
            "color": _tone(pc / 100)[2],
        })

    overall = round(100 * sum(values) / len(values))
    rings = []
    for frac in RADAR_RINGS:
        r = RADAR_R * frac
        pts = [_radar_point(a, r) for a in (-90, 0, 90, 180)]
        rings.append({"r": round(r, 1), "points": " ".join(f"{x},{y}" for x, y in pts)})

    axes = []
    for subject in SUBJECTS:
        angle = RADAR_ANGLES[subject["slug"]]
        x2, y2 = _radar_point(angle, RADAR_R)
        pc = overall if subject["active"] else None
        value_r = round(RADAR_R * overall / 100, 1) if pc is not None else 0.0
        value_x, value_y = _radar_point(angle, value_r)
        sector = None
        if pc is not None and value_r > 0:
            x1, y1 = _radar_point(angle - RADAR_SWEEP, value_r)
            x3, y3 = _radar_point(angle + RADAR_SWEEP, value_r)
            sector = (f"M {RADAR_CX} {RADAR_CY} L {x1} {y1} "
                      f"A {value_r} {value_r} 0 0 1 {x3} {y3} Z")
        units = topic_rows if subject["active"] else []
        aria = f"{subject['name']}, {'aktiv, ' + str(overall) + ' Prozent' if subject['active'] else 'bald verfügbar'}"
        if units:
            aria += ". Hauptbereiche: " + ", ".join(f"{u['short']} {u['pc']} Prozent" for u in units)
        axes.append({
            **subject, "pos": RADAR_POS[subject["slug"]], "angle": angle,
            "x2": x2, "y2": y2, "pc": pc, "value_r": value_r,
            "value_x": value_x, "value_y": value_y, "sector": sector,
            "units": units, "aria": aria,
        })

    return {
        "w": RADAR_W, "h": RADAR_H, "cx": RADAR_CX, "cy": RADAR_CY,
        "rings": rings, "axes": axes, "overall": overall,
        "secure": sum(1 for p in values if p >= 0.7),
        "total": len(VISIBLE_SKILLS),
    }


# ---------------------------------------------------------------------------
# public + auth
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return render(request, "landing.html")


@app.get("/archify", response_class=HTMLResponse)
def archify(request: Request):
    """How the app works, as two live diagrams (public, no login)."""
    return render(request, "archify.html")


@app.get("/login/{who}", response_class=HTMLResponse)
def login_page(request: Request, who: str):
    if who not in USERS:
        return RedirectResponse("/", 303)
    return render(request, "login.html", who=who, error=None)


@app.post("/login/{who}")
def login(request: Request, who: str, username: str = Form(...), password: str = Form(...)):
    if who in USERS and USERS[who].get(username) == password:
        request.session.clear()
        request.session.update(role=who, user=username)
        return RedirectResponse("/teacher" if who == "teacher" else "/student/home", 303)
    return render(request, "login.html", who=who, error="Benutzername oder Passwort falsch.")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", 303)


@app.get("/lang/{code}")
def set_language(code: str, next: str = "/"):
    """Switch the app between German and English, then go back to the page."""
    # Only local paths, so the link cannot be used to redirect elsewhere.
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(target, 303)
    if code in i18n.LANGS:
        resp.set_cookie(i18n.COOKIE, code, max_age=i18n.COOKIE_MAX_AGE, samesite="lax")
    return resp


# ---------------------------------------------------------------------------
# student portal
# ---------------------------------------------------------------------------
@app.get("/student", response_class=HTMLResponse)
def student_home(request: Request):
    if (r := need(request, "student")):
        return r
    return render(request, "student_home.html", subjects=SUBJECTS)


@app.get("/student/home", response_class=HTMLResponse)
def student_dashboard(request: Request):
    if (r := need(request, "student")):
        return r
    network = _learning_network(db.get_mastery(db.student_id()))
    username = request.session.get("user")
    display_name = "Hanna" if username == "admin" else username
    return render(request, "home.html", subjects=SUBJECTS, network=network, display_name=display_name)


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    if role(request) not in {"student", "teacher"}:
        return RedirectResponse("/login/student", 303)
    return render(request, "settings.html")


@app.get("/student/profile", response_class=HTMLResponse)
def student_profile(request: Request):
    """The student's own learning profile (only their own data)."""
    if (r := need(request, "student")):
        return r
    profile = learning_profile.build(_tutor_sid(request))
    if not profile:
        return RedirectResponse("/student/home", 303)
    return render(request, "profile.html", profile=profile, viewer="student")


@app.get("/teacher/student/{sid}", response_class=HTMLResponse)
def teacher_student_profile(request: Request, sid: int):
    """One student's learning profile for the teacher."""
    if (r := need(request, "teacher")):
        return r
    profile = learning_profile.build(sid)
    if not profile:
        return RedirectResponse("/teacher", 303)
    return render(request, "profile.html", profile=profile, viewer="teacher")


@app.get("/student/chemie", response_class=HTMLResponse)
def chemie(request: Request):
    if (r := need(request, "student")):
        return r
    sid = db.student_id()
    m = db.get_mastery(sid)
    nodes = [{"id": k, "name": SUBTOPIC_TITLES.get(k, v["name"]),
              "covers": SUBTOPIC_COVERS.get(k, []),
              "topic": v["topic"], "pre": v["pre"], "p": round(m.get(k, 0.3), 2)}
             for k, v in VISIBLE_SKILLS.items()]
    nodes.sort(key=lambda n: SUBTOPIC_ORDER.get(n["id"], len(SUBTOPIC_ORDER)))
    return render(request, "chemie.html", nodes=nodes, topics=VISIBLE_TOPICS, topic_meta=TOPIC_META,
                  tests=db.student_tests(sid), pass_mark=PASS_MARK,
                  network=_learning_network(m))


# ---------------------------------------------------------------------------
# AI tutor (general science, conversation history, optional practice questions)
# ---------------------------------------------------------------------------
TUTOR_SUBJECTS = {"science", "chemie", "biologie", "physik", "mathematik"}
TUTOR_SUBJECT_LABELS = {
    "science": "Alle Naturwissenschaften",
    "chemie": "Chemie",
    "biologie": "Biologie",
    "physik": "Physik",
    "mathematik": "Mathematik",
}
_TUTOR_ALIASES = {
    "chemistry": "chemie", "bio": "biologie", "biology": "biologie",
    "physics": "physik", "mathe": "mathematik", "math": "mathematik",
}
_QUIZ_WORDS = (
    "test", "quiz", "übe", "übung", "aufgabe", "abfrage", "abfragen",
    "prüfe", "prüf", "frag mich", "stelle mir", "practice",
    "noch eine", "nächste frage",
    # English (the app can be switched to English)
    "practise", "exercise", "ask me", "another one", "next question",
)
_SUBJECT_WORDS = {
    "biologie": ("bio", "zelle", "dna", "gen", "photosynthese", "enzym", "evolution",
                 "pflanze", "tier", "virus", "bakter", "mensch", "organ", "nerv", "blut",
                 "cell", "photosynthesis", "enzyme", "plant", "animal", "human", "blood"),
    "physik": ("physik", "kraft", "energie", "strom", "spannung", "licht", "newton",
               "geschwindigkeit", "elektron", "magnet", "wärme", "druck", "schall", "optik",
               "physics", "force", "energy", "current", "voltage", "light", "speed", "velocity",
               "heat", "pressure", "sound", "optics"),
    "mathematik": ("mathe", "mathematik", "gleichung", "ableitung", "funktion", "geometrie",
                   "prozent", "wahrscheinlichkeit", "rechne", "zahl",
                   "math", "equation", "derivative", "function", "geometry", "percent",
                   "probability", "calculate", "solve"),
}


def _tutor_sid(request: Request) -> int:
    user = request.session.get("user", "admin")
    try:
        return db.student_id(user)
    except Exception:
        return 1


def _normalise_tutor_subject(value: object) -> str:
    text = str(value or "").strip().lower()
    text = _TUTOR_ALIASES.get(text, text)
    return text if text in TUTOR_SUBJECTS else ""


def _tutor_subject(value: object) -> str:
    subject = _normalise_tutor_subject(value)
    return subject or "science"


def _wants_quiz(message: str) -> bool:
    low = message.lower()
    return any(word in low for word in _QUIZ_WORDS)


def _detect_subject(message: str) -> str:
    low = message.lower()
    for subject, words in _SUBJECT_WORDS.items():
        if any(word in low for word in words):
            return subject
    return "chemie"


def _pick_chemistry_skill(sid: int, requested: object, topic: str, message: str,
                          fallback: bool = False) -> str | None:
    requested = str(requested or "").strip()
    if requested in SKILLS:
        return requested
    requested_low = requested.lower()
    if requested_low:
        for skill, info in SKILLS.items():
            if requested_low in {skill.lower(), info["name"].lower()}:
                return skill

    text = f"{topic} {message}".lower()
    best, best_score = "", 0
    for skill, info in SKILLS.items():
        hay = f"{skill} {info['name']} {info['topic']}".lower()
        tokens = {t for t in re.findall(r"[a-zäöüß0-9]+", hay) if len(t) > 2}
        score = sum(1 for token in tokens if token in text)
        if score > best_score:
            best, best_score = skill, score
    if best and best_score >= 2:
        return best
    if fallback:
        mastery = db.get_mastery(sid)
        return min(VISIBLE_SKILLS, key=lambda skill: mastery.get(skill, 0.3))
    return None


def _title_from(message: str) -> str:
    text = " ".join(str(message or "").split())
    if not text:
        return "Neue Unterhaltung"
    return text[:48] + ("…" if len(text) > 48 else "")


def _tutor_question(q, subject: str) -> dict:
    data = q.to_dict()
    data["subject"] = subject
    data["topic"] = q.skill
    data["origin"] = "engine"
    data["hint"] = "Lies die Frage genau und überlege, welche Formel oder Regel passt."
    data["explanation"] = clean_answer(q.explanation or "", max_sentences=3, max_words=100)
    return data


def _valid_ai_question(data: object, subject: str, topic: str) -> dict | None:
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "").lower()
    prompt = str(data.get("prompt") or "").strip()
    answer = str(data.get("answer") or "").strip()
    if kind not in {"mc", "num"} or not prompt or not answer:
        return None
    options = data.get("options") if isinstance(data.get("options"), list) else []
    options = [str(o).strip() for o in options if str(o).strip()]
    if kind == "mc":
        options = list(dict.fromkeys(options))[:4]
        if len(options) < 2 or answer not in options:
            return None
        random.shuffle(options)
    else:
        options = []
        try:
            float(answer.replace(",", ".").replace("−", "-"))
        except ValueError:
            return None
    try:
        tolerance = min(0.2, max(0.0, float(data.get("tolerance") or 0.02)))
    except (TypeError, ValueError):
        tolerance = 0.02
    return {
        "id": f"tutor-{random.randrange(10**9)}",
        "kind": kind,
        "prompt": prompt[:500],
        "options": options,
        "answer": answer[:120],
        "unit": str(data.get("unit") or "").strip()[:20],
        "tolerance": tolerance,
        "explanation": clean_answer(str(data.get("explanation") or ""), max_sentences=3, max_words=100),
        "hint": str(data.get("hint") or "").strip()[:300] or
                "Lies die Frage genau und überlege, welcher Schritt zuerst kommt.",
        "skill": subject,
        "subject": subject,
        "topic": topic[:200],
        "origin": "ai",
        "traps": {},
    }


@app.get("/student/tutor", response_class=HTMLResponse)
def student_tutor(request: Request):
    if (r := need(request, "student")):
        return r
    sid = _tutor_sid(request)
    return render(request, "tutor.html",
                  conversations=db.list_tutor_conversations(sid),
                  initial_subject=_tutor_subject(request.query_params.get("subject")),
                  tutor_labels=TUTOR_SUBJECT_LABELS,
                  curriculum=learning_profile.curriculum(sid))


def _skill_opening(sid: int, skill: str, focus: dict) -> tuple[str, dict]:
    """First tutor message of a subtopic chat: explanation + a code-made exercise."""
    tail = " Probier diese Übung – nach einem Fehlversuch kannst du dir den Lösungsweg zeigen lassen."
    intro = chat_llm.skill_intro(learning_profile.focus_brief(focus))
    if intro:
        # The model already writes in the student's language; match it.
        tail = i18n.pick(tail, " Try this exercise – after a wrong attempt you can show the worked solution.")
    else:
        # No key / model down: still useful, from code-derived facts only.
        # Kept German as a whole so the page can translate it (static/i18n/en.js).
        status = (f"Du stehst bei {focus['pc']} % ({focus['correct']} von {focus['attempts']} richtig)."
                  if focus["practised"] else "Diesen Baustein hast du noch nicht geübt.")
        intro = f"Hier üben wir „{focus['title']}“. {status}"
    question = _tutor_question(generate(skill, random.Random(), f"tutor-{random.randrange(10**9)}"), "chemie")
    return f"{intro}{tail}", question


@app.post("/api/tutor/skill/{skill}")
def tutor_skill_open(request: Request, skill: str):
    """Open (or start) the student's chat for one chemistry subtopic."""
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    if skill not in VISIBLE_SKILLS:
        return JSONResponse({"error": "unknown skill"}, 404)
    sid = _tutor_sid(request)
    cid = db.find_skill_conversation(sid, skill)
    if cid is None:
        focus = learning_profile.skill_focus(sid, skill)
        if not focus:
            return JSONResponse({"error": "unknown skill"}, 404)
        cid = db.create_tutor_conversation(sid, "chemie", focus["title"][:80], skill=skill)
        message, question = _skill_opening(sid, skill, focus)
        db.add_tutor_message(cid, "assistant", message, question)
    conversation = db.get_tutor_conversation(cid, sid)
    return conversation


@app.post("/student/test/new")
def new_test(request: Request):
    if (r := need(request, "student")):
        return r
    sid = db.student_id()
    qs = build_test(random.randrange(10**9), 30, db.get_mastery(sid))
    tid = db.create_test(sid, [q.to_dict() for q in qs])
    return RedirectResponse(f"/student/test/{tid}", 303)


@app.post("/student/subtopic/{sub_id}/test")
def new_subtopic_test(request: Request, sub_id: str):
    """Start a five-question check for one subtopic in the normal test screen."""
    if (r := need(request, "student")):
        return r
    quiz = subtopic_quizzes.get_quiz(sub_id)
    if not quiz:
        return RedirectResponse("/student/chemie", 303)
    questions = [{**q, "subtopic": sub_id} for q in quiz["questions"]]
    tid = db.create_test(db.student_id(), questions, kind=f"subtopic:{sub_id}")
    return RedirectResponse(f"/student/test/{tid}", 303)


def _round_state(test: dict) -> tuple[int, list[dict], list[dict]]:
    """Return (current_round, questions_for_round, answers)."""
    ans = db.answers_for(test["id"])
    qs = {q["id"]: q for q in test["questions"]}
    if not ans:
        return 1, test["questions"], ans
    last = max(a["round"] for a in ans)
    wrong = [a["qid"] for a in ans if a["round"] == last and not a["correct"]]
    return last + 1, [qs[q] for q in wrong], ans


@app.get("/student/test/{tid}", response_class=HTMLResponse)
def take_test(request: Request, tid: int):
    if (r := need(request, "student")):
        return r
    test = db.get_test(tid)
    if not test:
        return RedirectResponse("/student/chemie", 303)
    rnd, qs, ans = _round_state(test)
    if not qs:
        return RedirectResponse(f"/student/test/{tid}/result", 303)
    # in retry rounds, show the previous wrong answer + error feedback
    prev = {}
    if rnd > 1:
        for a in ans:
            if a["round"] == rnd - 1:
                q = next(q for q in test["questions"] if q["id"] == a["qid"])
                prev[a["qid"]] = {"given": a["given"], "feedback": grade(q, a["given"])[1]}
    return render(request, "test.html", test=test, round=rnd, questions=qs, prev=prev, skills=VISIBLE_SKILLS)


@app.post("/student/test/{tid}")
async def submit_test(request: Request, tid: int):
    if (r := need(request, "student")):
        return r
    test = db.get_test(tid)
    rnd, qs, _ = _round_state(test)
    form = await request.form()
    sid = db.student_id()
    rows = []
    for q in qs:
        given = str(form.get(q["id"], ""))
        ok, _ = grade(q, given)
        rows.append((q["id"], q["skill"], given, ok))
        db.update_mastery(sid, q["skill"], ok)
    db.save_answers(tid, rnd, rows)
    if rnd == 1:
        db.update_test(tid, first_score=sum(ok for *_, ok in rows), status="done")
    db.update_test(tid, final_round=rnd)
    return RedirectResponse(f"/student/test/{tid}/result", 303)


@app.get("/student/test/{tid}/result", response_class=HTMLResponse)
def result(request: Request, tid: int):
    if (r := need(request, "student")):
        return r
    test = db.get_test(tid)
    ans = db.answers_for(tid)
    if not ans:
        return RedirectResponse(f"/student/test/{tid}", 303)
    qs = {q["id"]: q for q in test["questions"]}
    last = max(a["round"] for a in ans)
    last_rows = [a for a in ans if a["round"] == last]
    wrong = [a for a in last_rows if not a["correct"]]
    first = test["first_score"] or 0
    by_skill: dict[str, list[int]] = {}
    for a in ans:
        if a["round"] == 1:
            by_skill.setdefault(a["skill"], []).append(a["correct"])
    skill_rows = sorted(({"name": SKILLS[s]["name"], "correct": sum(v), "total": len(v)} for s, v in by_skill.items()),
                        key=lambda x: x["correct"] / x["total"])
    review = [{"q": qs[a["qid"]], "given": a["given"], "feedback": grade(qs[a["qid"]], a["given"])[1]} for a in wrong]
    kind = test.get("kind") or "test"
    is_subtopic = kind.startswith("subtopic:")
    total = len(test["questions"])
    pass_mark = 3 if is_subtopic else PASS_MARK
    # The example grade key is made for the 30-question test; a 5-question
    # subtopic check passes at 3/5, which would scale to a misleading Note 5.
    note = None if is_subtopic else note_for(first, total)
    return render(request, "result.html", test=test, first=first, passed=first >= pass_mark,
                  pass_mark=pass_mark, total=total, round=last, remaining=len(wrong),
                  last_total=len(last_rows), review=review, skill_rows=skill_rows, note=note)


@app.post("/api/hint")
async def hint(request: Request):
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    body = await request.json()
    test = db.get_test(int(body["test_id"]))
    q = next((q for q in test["questions"] if q["id"] == body["qid"]), None)
    if not q:
        return JSONResponse({"error": "not found"}, 404)
    given = body.get("given") or None
    err = grade(q, given)[1] if given else None
    return {"hint": llm.tutor_hint(q, given, int(body.get("attempt", 1)), err)}


# ---------------------------------------------------------------------------
# Mathematik-Agent: deterministic solver, the model only narrates
# ---------------------------------------------------------------------------
MAX_MATH_CHARS = 400

MATH_EXPLAIN_SYSTEM = (
    "Du bist ein geduldiger Mathematik-Tutor für Klasse 9 in Berlin. "
    "Erkläre in höchstens drei kurzen Sätzen auf Deutsch, WARUM das Ergebnis so ist. "
    "Benutze ausschließlich die Zahlen aus den gelieferten Fakten und erfinde keine neuen. "
    "Fehlt eine Zahl, bleib allgemein. Keine Begrüßung, keine Floskeln."
)


@app.get("/student/mathematik", response_class=HTMLResponse)
def mathematik(request: Request):
    if (r := need(request, "student")):
        return r
    return render(request, "mathematik.html")


@app.post("/api/math/agent")
async def math_agent_stream(request: Request):
    """NDJSON: meta -> command* -> done. Jede Zahl kommt aus math_agent."""
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)

    message = str(body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, 400)
    if len(message) > MAX_MATH_CHARS:
        return JSONResponse({"error": f"Die Aufgabe ist zu lang (max. {MAX_MATH_CHARS} Zeichen)."}, 400)

    result = await run_in_threadpool(math_agent.build_lesson, message)

    async def stream():
        meta = {"type": "meta", "ok": result["ok"], "title": result["title"],
                "note": result["note"]}
        yield json.dumps(meta, ensure_ascii=False) + "\n"

        for cmd in result["commands"]:
            yield json.dumps({"type": "command", "command": cmd}, ensure_ascii=False) + "\n"
            pause = 0.6 if cmd.get("commandName") == "draw" else 0.22
            await asyncio.sleep(pause)

        payload = {"type": "done", "ok": result["ok"]}
        if result["ok"]:
            payload["facts"] = {"title": result["title"], "problem": message,
                                "facts": result.get("facts") or {}}
        yield json.dumps(payload, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/math/explain")
async def math_explain(request: Request):
    """Optional narration: the prompt carries the computed facts, never new numbers."""
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)

    facts = body.get("facts") if isinstance(body.get("facts"), dict) else {}
    title = str(body.get("title") or "")[:120]
    problem = str(body.get("problem") or "")[:400]

    lines = []
    for key in ("roots", "points", "above", "interval_text", "D"):
        value = facts.get(key)
        if value:
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            lines.append(f"{key}: {value}")

    prompt = (f"Aufgabe: {problem}\nArt: {title}\nBerechnete Fakten:\n"
              + ("\n".join(lines) or "- keine -"))
    text = await run_in_threadpool(llm.ask, MATH_EXPLAIN_SYSTEM, prompt, 260)
    if not text:
        return JSONResponse({"text": _math_fallback(facts, title), "model": False})
    return JSONResponse({"text": text, "model": True})


MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_TUTOR_CHARS = 1200


@app.post("/api/math/pdf")
async def math_pdf(request: Request, file: UploadFile = File(...)):
    """PDF lesen und mathematische Aufgaben daraus schneiden.

    Der Text wird mit der Standardbibliothek gelesen (kein OCR). Scans werden
    klar abgelehnt, statt Zahlen zu raten.
    """
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    name = (file.filename or "").lower()
    if name and not name.endswith(".pdf"):
        return JSONResponse({"error": "Bitte lade eine PDF-Datei hoch."}, 400)
    data = await file.read(MAX_PDF_BYTES + 1)
    if not data:
        return JSONResponse({"error": "Die Datei ist leer."}, 400)
    if len(data) > MAX_PDF_BYTES:
        return JSONResponse({"error": "Das PDF ist zu groß (max. 12 MB)."}, 400)

    try:
        doc = await run_in_threadpool(pdf_text.extract, data)
        tasks = await run_in_threadpool(pdf_text.split_tasks, doc["text"])
    except pdf_text.PdfError as exc:
        return JSONResponse({"error": str(exc)}, 400)
    except Exception as exc:  # kaputte Datei darf die Seite nicht abschiessen
        return JSONResponse({"error": "Das PDF konnte nicht gelesen werden (" +
                             str(exc)[:80] + ")."}, 400)

    has_math = any(t.get("ok") for t in tasks)
    return {
        "ok": True,
        "page_count": doc["page_count"],
        "shown_pages": doc["shown_pages"],
        "chars": doc["chars"],
        "truncated": doc["truncated"],
        "pages": [{"no": p["no"], "chars": len(p["text"])} for p in doc["pages"]],
        "tasks": tasks,
        "has_math": has_math,
        "preview": doc["text"][:600],
    }


@app.post("/api/math/tutor")
async def math_tutor_turn(request: Request):
    """Ein Unterrichtsturm wie bei der Vorlage: ein Schritt, dann eine Frage."""
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)

    task = str(body.get("task") or "").strip()
    if not task:
        return JSONResponse({"error": "task required"}, 400)
    if len(task) > MAX_TUTOR_CHARS:
        return JSONResponse({"error": f"Die Aufgabe ist zu lang (max. {MAX_TUTOR_CHARS} Zeichen)."}, 400)

    raw_history = body.get("history")
    history = []
    if isinstance(raw_history, list):
        for item in raw_history[-12:]:
            if isinstance(item, dict):
                history.append({"say": str(item.get("say") or "")[:600],
                                "question": str(item.get("question") or "")[:300],
                                "answer": str(item.get("answer") or "")[:200]})

    try:
        turn = int(body.get("turn") or 0)
    except (TypeError, ValueError):
        turn = 0
    answer = str(body.get("answer") or "")[:200]

    expr = str(body.get("expr") or "").strip()[:MAX_MATH_CHARS]

    result = await run_in_threadpool(math_tutor.next_turn, task, history, answer, turn, expr)
    if not result.get("ok"):
        return JSONResponse(result, 503)
    return result


@app.post("/api/math/plan")
async def math_plan(request: Request):
    """Offline-Unterricht: derselbe Ablauf, aber alle Zahlen aus dem Rechenkern."""
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)
    message = str(body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, 400)
    if len(message) > MAX_MATH_CHARS:
        return JSONResponse({"error": f"Die Aufgabe ist zu lang (max. {MAX_MATH_CHARS} Zeichen)."}, 400)

    plan = await run_in_threadpool(math_agent.build_plan, message)
    return plan


def _math_fallback(facts: dict, title: str) -> str:
    """Begruendung ohne Modell: nur die bereits geprueften Zahlen."""
    parts = []
    points = facts.get("points") or []
    roots = facts.get("roots") or []
    interval = facts.get("interval_text") or facts.get("interval")
    if points:
        parts.append("Die Schnittpunkte sind " + " und ".join(str(p) for p in points) + ".")
    elif interval:
        parts.append("Die Lösungsmenge ist " + str(interval) + ".")
    elif roots:
        parts.append("Die Lösungen sind x = " + ", ".join(str(r) for r in roots) + ".")
    if facts.get("above"):
        parts.append(str(facts["above"]))
    if roots and (points or interval):
        parts.append("Die Nullstellen der Differenz liegen bei x = " + ", ".join(str(r) for r in roots) + ".")
    if facts.get("D") is not None:
        parts.append("Die Diskriminante ist D = " + str(facts["D"]) + ".")
    tail = ("Das Sprachmodell antwortet gerade nicht; die Begründung bleibt deshalb knapp. "
            "Die Zahlen oben sind exakt berechnet und geprüft.")
    return ((" ".join(parts) + " ") if parts else "") + tail


@app.get("/api/math/tts")
@app.post("/api/math/tts")
async def math_tts(request: Request, text: str = "", lang: str = "de"):
    """Azure Neural Voice / Edge TTS stream endpoint for math tutor speech."""
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)

    req_text = text
    req_lang = lang
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict):
                req_text = str(body.get("text") or req_text)
                req_lang = str(body.get("lang") or req_lang)
        except Exception:
            pass

    req_text = req_text.strip()
    if not req_text:
        return JSONResponse({"error": "text required"}, 400)

    # Cap length to prevent abuse (e.g. 1500 chars per speech turn)
    if len(req_text) > 1500:
        req_text = req_text[:1500]

    audio_bytes = await tts.synthesize_speech(req_text, lang=req_lang)
    if not audio_bytes:
        return JSONResponse({"error": "synthesis failed"}, 500)

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "public, max-age=86400",
            "Content-Disposition": 'inline; filename="speech.mp3"',
        },
    )


# ---------------------------------------------------------------------------
# Chat answers (model) + optional RAG extras (figures, quizzes)
# ---------------------------------------------------------------------------
@app.get("/api/rag-context")
def rag_context(request: Request, skill: str = "", query: str = ""):
    """Return relevant Chemie book passages and images for a skill or free-text query.

    Query params:
      - skill: skill id from SKILLS (e.g. "ph_skala") — auto-expands to the skill name
      - query: free-text query (overrides skill name if both are given)
    Either skill or query must be provided.
    """
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    search_query = query or (SKILLS.get(skill, {}).get("name", skill) if skill else "")
    if not search_query:
        return JSONResponse({"error": "skill or query required"}, 400)
    return vectorstore.rag_context_for_skill(search_query)


@app.post("/api/rag-chat")
async def rag_chat(request: Request):
    """Chat answer straight from the model (same as the KI-Tutor path)."""
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    body = await request.json()
    question = body.get("question", "").strip()
    if not question:
        return JSONResponse({"error": "question required"}, 400)
    return await run_in_threadpool(chat_llm.chat, question)


@app.get("/api/rag-image/{filename}")
def rag_image(request: Request, filename: str):
    """Proxy textbook figures from the optional RAG server (works from phones too)."""
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    fetched = vectorstore.fetch_image(filename)
    if not fetched:
        return JSONResponse({"error": "image not found"}, 404)
    body, media_type = fetched
    return Response(body, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/subtopic/{skill}")
def get_subtopic(request: Request, skill: str):
    """Get core knowledge flashcards, practice exercises, and chat history for a subtopic."""
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    skill_info = SKILLS.get(skill, {"name": skill, "topic": "Chemie"})
    data = subtopics_data.get_subtopic_data(skill) or {}
    user = request.session.get("user", "admin")
    try:
        sid = db.student_id(user)
    except Exception:
        sid = 1

    history = db.get_chat_history(sid, skill)
    return {
        "skill": skill,
        "name": SUBTOPIC_TITLES.get(skill) or skill_info.get("name", skill),
        "topic": skill_info.get("topic", "Chemie"),
        "core_principle": data.get("core_principle", ""),
        "flashcards": data.get("flashcards", []),
        "practice": data.get("practice", {"true_false": [], "sentence_choice": [], "sentence_split": []}),
        "chat_history": history,
    }


MAX_CHAT_CHARS = 600


@app.post("/api/subtopic/chat")
async def subtopic_chat(request: Request):
    """Save the student's question, answer it with the model, and store both.

    The model answers directly; the RAG service is never consulted here, so a
    subtopic chat works with no second server running.
    """
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)

    skill = clean_topic(body.get("skill"), fallback="", max_len=80)
    msg = str(body.get("message") or "").strip()
    if not skill or not msg:
        return JSONResponse({"error": "skill and message required"}, 400)
    if len(msg) > MAX_CHAT_CHARS:
        return JSONResponse({"error": f"Die Frage ist zu lang (max. {MAX_CHAT_CHARS} Zeichen)."}, 400)

    skill_info = SKILLS.get(skill, {})
    # The chemistry page uses its own curriculum ids (e.g. "ph_wert"), so it
    # sends the human-readable title too. Clean it instead of trusting raw
    # prompt text; it is only a topic hint, never the question itself.
    page_title = clean_topic(body.get("skill_name"), fallback="")
    skill_name = clean_topic(
        page_title or SUBTOPIC_TITLES.get(skill) or skill_info.get("name") or skill,
        fallback=skill, max_len=200,
    )

    user = request.session.get("user", "admin")
    try:
        sid = db.student_id(user)
    except Exception:
        sid = 1

    # 1. Save user question in DB
    db.save_chat_message(sid, skill, "user", msg)

    # 2. One short answer from the model. The skill name is passed as a topic
    #    hint, never concatenated into the question.
    sub_info = subtopics_data.get_subtopic_data(skill) or {}
    # The page keeps its core principle in the browser (subtopics_data is
    # intentionally empty now); accept it as a grounded fallback.
    core = clean_topic(body.get("core_principle"), fallback="", max_len=1200)
    if not core:
        core = str(sub_info.get("core_principle") or "")[:1200]
    chat_resp = await run_in_threadpool(chat_llm.chat, msg, skill_name, core)

    answer = ""
    sources: list[dict] = []
    images: list[dict] = []
    if isinstance(chat_resp, dict) and isinstance(chat_resp.get("answer"), str):
        answer = clean_answer(chat_resp["answer"])
        if answer and not is_refusal(answer):
            if is_soft_refusal(answer):
                # Safety/generic refusal: keep the text, but no sources/images.
                sources, images = [], []
            else:
                raw_sources = chat_resp.get("sources") or []
                raw_images = chat_resp.get("images") or []
                sources = ([s for s in raw_sources if isinstance(s, dict)]
                           if isinstance(raw_sources, list) else [])
                images = ([i for i in raw_images if isinstance(i, dict) and i.get("url")]
                          if isinstance(raw_images, list) else [])
        else:
            # Never show "that is not in this book section" to a student.
            answer = ""

    if not answer:
        sources, images = [], []

    if not answer and core:
        answer = clean_answer(f"Merksatz: {core}")

    if not answer:
        answer = ("Der Chemie-Tutor ist gerade nicht erreichbar. "
                  "Bitte versuche es in einem Moment noch einmal.")

    # 4. Save assistant answer in DB
    db.save_chat_message(sid, skill, "assistant", answer, sources, images)

    return {
        "answer": answer,
        "sources": sources,
        "images": images[:1],
    }


@app.post("/api/subtopic/chat/clear")
async def subtopic_chat_clear(request: Request):
    """Clear chat history for a skill."""
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)
    skill = clean_topic(body.get("skill") if isinstance(body, dict) else None,
                        fallback="", max_len=80)
    if not skill:
        return JSONResponse({"error": "skill required"}, 400)
    user = request.session.get("user", "admin")
    try:
        sid = db.student_id(user)
    except Exception:
        sid = 1
    db.clear_chat_history(sid, skill)
    return {"ok": True}


MAX_TUTOR_CHARS = 1000


@app.get("/api/tutor/conversations")
def tutor_conversations(request: Request):
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    return {"conversations": db.list_tutor_conversations(_tutor_sid(request))}


@app.post("/api/tutor/conversations")
async def tutor_conversation_new(request: Request):
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    subject = _tutor_subject((body or {}).get("subject"))
    cid = db.create_tutor_conversation(_tutor_sid(request), subject, "Neue Unterhaltung")
    return {"id": cid, "subject": subject, "title": "Neue Unterhaltung"}


@app.post("/api/tutor/conversations/reorder")
async def tutor_conversations_reorder(request: Request):
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw_ids = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(raw_ids, list):
        return JSONResponse({"error": "ids required"}, 400)
    ids: list[int] = []
    for value in raw_ids[:200]:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    db.reorder_tutor_conversations(_tutor_sid(request), ids)
    return {"ok": True}


@app.get("/api/tutor/conversations/{cid}")
def tutor_conversation_get(request: Request, cid: int):
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    conversation = db.get_tutor_conversation(cid, _tutor_sid(request))
    if not conversation:
        return JSONResponse({"error": "not found"}, 404)
    return conversation


@app.patch("/api/tutor/conversations/{cid}")
async def tutor_conversation_update(request: Request, cid: int):
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    subject = _normalise_tutor_subject(body.get("subject") if isinstance(body, dict) else "")
    if not subject:
        return JSONResponse({"error": "invalid subject"}, 400)
    sid = _tutor_sid(request)
    conversation = db.get_tutor_conversation(cid, sid)
    if not conversation:
        return JSONResponse({"error": "not found"}, 404)
    db.update_tutor_conversation(cid, sid, subject=subject)
    return {"ok": True, "id": cid, "subject": subject}


@app.delete("/api/tutor/conversations/{cid}")
def tutor_conversation_delete(request: Request, cid: int):
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    db.delete_tutor_conversation(cid, _tutor_sid(request))
    return {"ok": True}


@app.post("/api/tutor/chat")
async def tutor_chat(request: Request):
    if role(request) != "student":
        return JSONResponse({"error": "login"}, 401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Ungültige Anfrage."}, 400)

    message = str(body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, 400)
    if len(message) > MAX_TUTOR_CHARS:
        return JSONResponse({"error": f"Die Frage ist zu lang (max. {MAX_TUTOR_CHARS} Zeichen)."}, 400)

    subject = _tutor_subject(body.get("subject"))
    sid = _tutor_sid(request)
    cid = body.get("conversation_id")
    is_new = not cid
    conversation = None
    if cid:
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid conversation"}, 400)
        conversation = db.get_tutor_conversation(cid, sid)
        if not conversation:
            return JSONResponse({"error": "not found"}, 404)

    # A subtopic chat (sidebar "Chemie") stays on its curriculum building block.
    # Everything else is a free chat and lands under "Allgemein".
    focus_skill = (conversation or {}).get("skill") or ""
    focus = learning_profile.skill_focus(sid, focus_skill) if focus_skill else None
    if focus:
        subject = "chemie"

    if is_new:
        cid = db.create_tutor_conversation(sid, subject, _title_from(message))

    history = db.get_tutor_messages(cid, sid, limit=12)
    model_history = []
    for item in history:
        content = str(item.get("message") or "").strip()
        q = item.get("question")
        if q:
            content += f"\n[Übungsfrage: {q.get('prompt', '')}"
            if focus:
                # The engine's answer is reliable (code-computed); it lets the
                # model explain the real solution instead of re-deriving one.
                content += (f" | Lösung vom Code, nur zum Erklären, nicht ungefragt verraten: "
                            f"{q.get('answer', '')} {q.get('unit', '')}".rstrip())
            content += "]"
        model_history.append({"role": item["role"], "content": content})

    db.add_tutor_message(cid, "user", message)

    chemistry_skills = [{"id": skill, "name": info["name"], "topic": info["topic"]}
                        for skill, info in SKILLS.items()]
    # Always one direct request to the OpenRouter model — no retrieval step in
    # front of it, so chat cannot depend on another server being up.
    response = await run_in_threadpool(
        chat_llm.tutor_chat, message, model_history,
        subject if subject != "science" else "", chemistry_skills,
        learning_profile.focus_brief(focus) if focus else "", focus_skill if focus else "",
    )

    answer = ""
    quiz_request = None
    sources: list[dict] = []
    images: list[dict] = []
    if isinstance(response, dict):
        answer = clean_answer(str(response.get("answer") or ""))
        quiz_request = response.get("quiz") if isinstance(response.get("quiz"), dict) else None
        raw_sources = response.get("sources") or []
        raw_images = response.get("images") or []
        sources = ([s for s in raw_sources if isinstance(s, dict)]
                   if isinstance(raw_sources, list) else [])
        images = ([i for i in raw_images if isinstance(i, dict) and i.get("url")]
                  if isinstance(raw_images, list) else [])

    wants_quiz = _wants_quiz(message)
    quiz_subject = subject
    quiz_topic = message
    quiz_skill = ""
    if quiz_request:
        requested_subject = _normalise_tutor_subject(quiz_request.get("subject"))
        if requested_subject and requested_subject != "science":
            quiz_subject = requested_subject
        if quiz_request.get("topic"):
            quiz_topic = str(quiz_request["topic"])[:200]
        if quiz_request.get("skill"):
            quiz_skill = str(quiz_request["skill"])[:80]
    if wants_quiz and quiz_subject == "science":
        quiz_subject = _detect_subject(message)
    if focus:
        # In a subtopic chat every exercise is for that subtopic, whatever the
        # model suggested.
        quiz_subject, quiz_skill = "chemie", focus_skill

    question = None
    if quiz_request or wants_quiz:
        if quiz_subject == "chemie":
            skill = _pick_chemistry_skill(sid, quiz_skill, quiz_topic, message,
                                          fallback=wants_quiz)
            if skill:
                engine_question = generate(skill, random.Random(), f"tutor-{random.randrange(10**9)}")
                question = _tutor_question(engine_question, "chemie")
        elif quiz_subject in {"biologie", "physik", "mathematik"}:
            generated = await run_in_threadpool(
                chat_llm.science_question, quiz_subject, quiz_topic
            )
            question = _valid_ai_question(generated, quiz_subject, quiz_topic)

    if question is None and (quiz_request or wants_quiz) and not is_soft_refusal(answer):
        # Do not leave a promise of a question card that could not be created.
        answer = re.sub(
            r"(?i)\s*(?:hier ist (?:eine|deine) (?:kurze )?übungsfrage[^.!?]*[.!?]?|"
            r"ich (?:erstelle|stelle) dir (?:eine|die) (?:kurze )?(?:übungsfrage|aufgabe)[^.!?]*[.!?]?|"
            r"here(?:'s| is) (?:a|your) (?:short )?(?:practice )?(?:question|exercise)[^.!?]*[.!?]?)\s*",
            " ", answer,
        ).strip()
        if not answer:
            answer = "Dazu habe ich gerade keine sichere Übungsfrage. Frag mich gern nach einer Erklärung."

    if is_refusal(answer):
        answer, question = "", None
    if is_soft_refusal(answer):
        sources, images, question = [], [], None
    if not answer:
        answer = ("Hier ist eine Übungsfrage." if question else
                  "Der KI-Tutor ist gerade nicht erreichbar. Bitte versuche es in einem Moment noch einmal.")

    db.add_tutor_message(cid, "assistant", answer, question, sources, images)
    db.update_tutor_conversation(cid, sid, subject=subject,
                                 title=_title_from(message) if is_new else None)
    conversation = db.get_tutor_conversation(cid, sid)

    return {
        "conversation_id": cid,
        "title": conversation["title"] if conversation else _title_from(message),
        "subject": subject,
        "skill": (conversation or {}).get("skill"),
        "answer": answer,
        "question": question,
        "sources": sources,
        "images": images[:1],
    }


class GenerateRequest(BaseModel):
    skill: str
    count: int | None = None


def _skill_topic(req: GenerateRequest) -> str | None:
    info = SKILLS.get(req.skill)
    return info["name"] if info else None


@app.post("/api/subtopic/quiz")
def subtopic_quiz(request: Request, req: GenerateRequest):
    """Generate a RAG quiz for one subtopic (book-grounded multiple choice)."""
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    topic = _skill_topic(req)
    if not topic:
        return JSONResponse({"error": "unknown skill"}, 404)
    n = max(2, min(10, req.count or 5))
    return vectorstore.rag_quiz(topic, n)


@app.post("/api/subtopic/statements")
def subtopic_statements(request: Request, req: GenerateRequest):
    """Generate RAG true/false statements for one subtopic."""
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    topic = _skill_topic(req)
    if not topic:
        return JSONResponse({"error": "unknown skill"}, 404)
    n = max(3, min(12, req.count or 6))
    return vectorstore.rag_statements(topic, n)


@app.post("/api/subtopic/flashcards")
def subtopic_flashcards(request: Request, req: GenerateRequest):
    """Generate RAG flashcards for one subtopic."""
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    topic = _skill_topic(req)
    if not topic:
        return JSONResponse({"error": "unknown skill"}, 404)
    n = max(3, min(15, req.count or 8))
    return vectorstore.rag_flashcards(topic, n)


@app.post("/api/subtopic/explore")
def subtopic_explore(request: Request, req: GenerateRequest):
    """Generate a short RAG overview ("Entdecken") for one subtopic."""
    if role(request) not in {"student", "teacher"}:
        return JSONResponse({"error": "login"}, 401)
    topic = _skill_topic(req)
    if not topic:
        return JSONResponse({"error": "unknown skill"}, 404)
    return vectorstore.rag_explore(topic)



# ---------------------------------------------------------------------------
# teacher portal
# ---------------------------------------------------------------------------
@app.get("/teacher", response_class=HTMLResponse)
def teacher(request: Request):
    if (r := need(request, "teacher")):
        return r
    return render(request, "teacher.html", skills=VISIBLE_SKILLS, pass_mark=PASS_MARK)


@app.get("/api/teacher/overview")
def overview(request: Request, klasse: str | None = None):
    if role(request) != "teacher":
        return JSONResponse({"error": "login"}, 401)
    data = db.class_overview(klasse or None)
    data["skills"] = {k: v["name"] for k, v in VISIBLE_SKILLS.items()}
    data["pass_mark"] = PASS_MARK
    return data


@app.post("/api/agent")
def run_agent(request: Request, klasse: str | None = None):
    if role(request) != "teacher":
        return JSONResponse({"error": "login"}, 401)
    return agent.run(klasse or None)


# ---------------------------------------------------------------------------
# PWA: installable on laptop + phone
# ---------------------------------------------------------------------------
@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(BASE / "static" / "sw.js", media_type="application/javascript")
