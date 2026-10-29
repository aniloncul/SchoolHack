"""Mathematik-Agent — deterministischer Rechenkern + Befehlsprotokoll.

Aufbau (bewusst anders als ein reiner Chatbot):

    Eingabe -> Parser -> Rechner (exakt, Fraction) -> Befehle fuer die Tafel
                                                       |
                                                       +-> optional: Modell formuliert nur den Satz

Das Modell erzeugt **keine** Zahl. Jede Zahl kommt aus diesem Modul. Fehlt der
LLM-Key, bleibt der Unterricht vollstaendig funktionsfaehig (Vorlagen-Saetze).

Unterstuetzte Eingaben
    f(x)=2x^2+3x+7 ; g(x)=4x^2-5x-3      Schnittpunkte zweier Parabeln
    2x^2+3x+7 = 4x^2-5x-3                Gleichung loesen (+ Schnittpunkte)
    x^2-9x+8                             Funktion analysieren (Nullstellen, Scheitel)
    x^2-9x+8=0                           Nullstellen
    3x+5=20                              lineare Gleichung
    -0.1x^2+3x-6 > -0.2x^2+5x, 0<=x<=24  Ungleichung mit Definitionsbereich
    -4x^2+15ax = 2x^2-9ax+18a^2          parametrisch (Faktorisierung in a)

Oeffentliche API
    build_lesson(text) -> {"ok", "title", "note", "commands", "facts"}
    analyse(text)      -> dasselbe, ohne Diagramm
"""
from __future__ import annotations

import math
import re
from fractions import Fraction

MAX_DEG = 2

# ---------------------------------------------------------------------------
# Farben (aus der Studdy-Ausgabe abgeleitet, siehe Produktionsbericht §5)
# ---------------------------------------------------------------------------

INK = "#222222"
AXIS = "#ABABAB"
GRID = "#E2E8F0"
BLUE = "#0085CF"
RED = "#C73C3C"
POINT_FILL = "#D7D0F6"
POINT_STROKE = "#7B61E1"
AREA = "#B4E8FE"


class ParseError(ValueError):
    """Der Ausdruck liess sich nicht sicher lesen."""


# ---------------------------------------------------------------------------
# Polynome in a  (Koeffizienten exakt, Index = Potenz von a)
# ---------------------------------------------------------------------------

def _ap_trim(c):
    c = [Fraction(x) for x in c]
    while len(c) > 1 and c[-1] == 0:
        c.pop()
    return c or [Fraction(0)]


def _ap(terms: dict[int, Fraction]) -> list[Fraction]:
    n = max(terms) + 1 if terms else 1
    return _ap_trim([terms.get(i, Fraction(0)) for i in range(n)])


def _ap_zero():
    return [Fraction(0)]


def _ap_is_zero(p) -> bool:
    return all(x == 0 for x in p)


def _ap_is_const(p) -> bool:
    return len(_ap_trim(p)) == 1


def _ap_const(p) -> Fraction:
    p = _ap_trim(p)
    return p[0]


def _ap_eval_num(p, x):
    """Polynom in a an einer Zahl auswerten (exakt)."""
    out = Fraction(0)
    for i, v in enumerate(p):
        out += v * (Fraction(x) ** i)
    return out


def _ap_deg(p) -> int:
    p = _ap_trim(p)
    return len(p) - 1


def _ap_add(x, y, sy=Fraction(1)):
    n = max(len(x), len(y))
    return _ap_trim([(x[i] if i < len(x) else 0) + sy * (y[i] if i < len(y) else 0)
                     for i in range(n)])


def _ap_scale(x, s):
    return _ap_trim([v * s for v in x])


def _ap_mul(x, y):
    out = [Fraction(0)] * (len(x) + len(y) - 1)
    for i, v in enumerate(x):
        if v == 0:
            continue
        for j, w in enumerate(y):
            out[i + j] += v * w
    return _ap_trim(out)


def _ap_div(x, s: Fraction):
    if s == 0:
        raise ZeroDivisionError
    return _ap_trim([v / s for v in x])


def _sqrt_frac(q: Fraction):
    """Exakte Wurzel eines Bruchs, sonst None."""
    q = Fraction(q)
    if q < 0:
        return None
    rn, rd = math.isqrt(q.numerator), math.isqrt(q.denominator)
    if rn * rn == q.numerator and rd * rd == q.denominator:
        return Fraction(rn, rd)
    return None


def _ap_sqrt(x):
    """Wurzel eines Polynoms in a, wenn sie exakt aufgeht."""
    x = _ap_trim(x)
    if len(x) == 1:
        r = _sqrt_frac(x[0])
        return None if r is None else [r]
    if len(x) == 3:
        m, n = _sqrt_frac(x[2]), _sqrt_frac(x[0])
        if m is None or n is None:
            return None
        if 2 * m * n == x[1]:
            return _ap_trim([n, m])
        if -2 * m * n == x[1]:
            return _ap_trim([-n, m])
    return None


def _ap_tex(p, var="a") -> str:
    """Polynom in a als LaTeX: ``3a``, ``a^{2}``, ``2 - a``."""
    p = _ap_trim(p)
    parts = []
    for i in range(len(p) - 1, -1, -1):
        v = p[i]
        if v == 0:
            continue
        sign = "-" if v < 0 else "+"
        av = abs(v)
        if i == 0:
            body = _num(av)
        else:
            body = var if i == 1 else var + "^{" + str(i) + "}"
            if av != 1:
                body = _num(av) + body
        parts.append((sign, body))
    if not parts:
        return "0"
    out = ("-" if parts[0][0] == "-" else "") + parts[0][1]
    for sign, body in parts[1:]:
        out += (" - " if sign == "-" else " + ") + body
    return out


def _ap_plain(p, var="a") -> str:
    p = _ap_trim(p)
    parts = []
    for i in range(len(p) - 1, -1, -1):
        v = p[i]
        if v == 0:
            continue
        sign = "-" if v < 0 else "+"
        av = abs(v)
        if i == 0:
            body = _de(av, 4)
        else:
            body = var if i == 1 else var + "^" + str(i)
            if av != 1:
                body = _de(av, 4) + body
        parts.append((sign, body))
    if not parts:
        return "0"
    out = ("-" if parts[0][0] == "-" else "") + parts[0][1]
    for sign, body in parts[1:]:
        out += (" - " if sign == "-" else " + ") + body
    return out


def _num(q: Fraction) -> str:
    """Bruch als LaTeX, deutsche Dezimalschreibweise wenn endlich."""
    q = Fraction(q)
    if q.denominator == 1:
        return str(q.numerator)
    d = q.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    if d == 1:
        return _de_tex(q, 6)
    return "\\frac{" + str(q.numerator) + "}{" + str(q.denominator) + "}"


def _de(v, nd=2) -> str:
    """Deutsche Dezimalzahl fuer reinen Text."""
    if isinstance(v, str):
        v = v.replace(",", ".")
    s = ("%." + str(nd) + "f") % float(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s.replace(".", ",")


def _de_tex(v, nd=2) -> str:
    """Deutsche Dezimalzahl fuer LaTeX: Komma als {,} damit kein Zwischenabstand entsteht."""
    if isinstance(v, str):
        v = v.replace("{,}", ".").replace(",", ".")
    s = ("%." + str(nd) + "f") % float(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s.replace(".", "{,}")


def _tick(v, step) -> str:
    if step >= 1:
        return _de(v, 0)
    if step >= 0.1:
        return _de(v, 1)
    return _de(v, 2)


def _bnd(v) -> str:
    """Intervallgrenze: ganze Zahlen exakt, sonst zwei Nachkommastellen (LaTeX-sicher)."""
    if isinstance(v, Fraction) and v.denominator == 1:
        return str(v.numerator)
    return _de_tex(v)


# ---------------------------------------------------------------------------
# Polynome in x mit Koeffizienten in a:  {(x_pow, a_pow): Fraction}
# ---------------------------------------------------------------------------

def _p_add(p, q, s=Fraction(1)):
    out = dict(p)
    for k, v in q.items():
        out[k] = out.get(k, Fraction(0)) + s * v
    return {k: v for k, v in out.items() if v != 0}


def _p_mul(p, q):
    out = {}
    for (dx1, da1), v1 in p.items():
        for (dx2, da2), v2 in q.items():
            k = (dx1 + dx2, da1 + da2)
            out[k] = out.get(k, Fraction(0)) + v1 * v2
    return {k: v for k, v in out.items() if v != 0}


def _p_scale(p, s):
    if s == 0:
        return {}
    return {k: v * s for k, v in p.items()}


def _p_pow(p, n):
    r = {(0, 0): Fraction(1)}
    for _ in range(n):
        r = _p_mul(r, p)
    return r


def _p_deg_x(p) -> int:
    return max([k[0] for k in p] or [-1])


def _p_coef_x(p) -> dict[int, list[Fraction]]:
    buckets: dict[int, dict[int, Fraction]] = {}
    for (dx, da), v in p.items():
        buckets.setdefault(dx, {})[da] = buckets.setdefault(dx, {}).get(da, Fraction(0)) + v
    return {dx: _ap(terms) for dx, terms in buckets.items()}


def _p_eval_num(p, x: Fraction) -> Fraction:
    out = Fraction(0)
    for (dx, da), v in p.items():
        if da != 0:
            raise ValueError("Koeffizient haengt von a ab")
        out += v * (x ** dx)
    return out


def _p_eval_param(p, X: list[Fraction]) -> list[Fraction]:
    """Polynom in x an der Stelle X (Polynom in a) auswerten."""
    xc = _p_coef_x(p)
    top = max(xc) if xc else 0
    out = _ap_zero()
    for dx in range(top, -1, -1):
        out = _ap_add(_ap_mul(out, X), xc.get(dx, _ap_zero()))
    return out


def _ap_single_term(p):
    """Genau ein Term? -> (Vorzeichen, Betragspolynom), sonst None."""
    nz = [i for i, v in enumerate(p) if v != 0]
    if len(nz) != 1:
        return None
    i = nz[0]
    v = p[i]
    mag = [Fraction(0)] * (i + 1)
    mag[i] = abs(v)
    return ("-" if v < 0 else "+"), _ap_trim(mag)


def _p_tex(p) -> str:
    """Polynom in x als LaTeX, Koeffizienten duerfen a enthalten."""
    xc = _p_coef_x(p)
    if not xc or all(_ap_is_zero(c) for c in xc.values()):
        return "0"
    parts = []
    for dx in sorted(xc, reverse=True):
        coef = xc[dx]
        if _ap_is_zero(coef):
            continue
        mono = _ap_single_term(coef)
        if mono is None:
            parts.append(("+", "\\left(" + _ap_tex(coef) + "\\right)" + _xpow(dx)))
            continue
        sign, mag = mono
        if _ap_is_const(mag):
            v = mag[0]
            if dx == 0:
                body = _num(v)
            else:
                body = _xpow(dx) if v == 1 else _num(v) + _xpow(dx)
        else:
            body = _ap_tex(mag) + _xpow(dx)
        parts.append((sign, body))
    if not parts:
        return "0"
    out = ("-" if parts[0][0] == "-" else "") + parts[0][1]
    for sign, body in parts[1:]:
        out += (" - " if sign == "-" else " + ") + body
    return out


def _xpow(n: int) -> str:
    if n == 0:
        return ""
    if n == 1:
        return "x"
    return "x^{" + str(n) + "}"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"\d+(?:\.\d+)?|[xa]|[\^\*/()+\-]")


def _normalise(text: str) -> str:
    t = text
    t = (t.replace("\u00b2", "^2").replace("\u00b3", "^3").replace("\u2074", "^4")
          .replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
          .replace("\u00b7", "*").replace("\u00d7", "*").replace("\u00f7", "/")
          .replace("\u2264", "<=").replace("\u2265", ">="))
    t = re.sub(r"\\(left|right|,|;|!)", "", t)
    t = t.replace("\\cdot", "*").replace("\\times", "*").replace("$", "")
    t = t.replace("{", "").replace("}", "").replace("\\", "")
    t = re.sub(r"(?<=\d),(?=\d)", ".", t)     # deutsches Dezimalkomma
    t = t.replace(",", ";")                   # alles andere ist eine Trennung
    return t


def _tokenise(s: str) -> list[str]:
    s = re.sub(r"\s+", "", s).lower()
    toks, pos = [], 0
    for m in _TOKEN.finditer(s):
        if m.start() != pos:
            raise ParseError(s[pos:m.start()])
        toks.append(m.group(0))
        pos = m.end()
    if pos != len(s):
        raise ParseError(s[pos:])
    if not toks:
        raise ParseError("leer")
    return toks


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = _tokenise(text)
        self.i = 0

    # -- Hilfen ------------------------------------------------------------
    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    # -- Grammatik ---------------------------------------------------------
    def parse(self):
        node = self.expr()
        if self.peek() is not None:
            raise ParseError(self.peek())
        return node

    def expr(self):
        node = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            node = _p_add(node, self.term(), Fraction(1) if op == "+" else Fraction(-1))
        return node

    def term(self):
        node = self.factor()
        while True:
            t = self.peek()
            if t in ("*", "/"):
                op = self.take()
                rhs = self.factor()
                if op == "*":
                    node = _p_mul(node, rhs)
                else:
                    if _p_deg_x(rhs) != 0 or any(k[1] for k in rhs):
                        raise ParseError("/" + self.text)
                    node = _p_scale(node, Fraction(1) / rhs.get((0, 0), Fraction(1)))
            elif t is not None and (t[0].isdigit() or t in ("x", "a", "(")):
                node = _p_mul(node, self.power())      # implizite Multiplikation
            else:
                break
        return node

    def factor(self):
        t = self.peek()
        if t == "-":
            self.take()
            return _p_scale(self.factor(), Fraction(-1))
        if t == "+":
            self.take()
            return self.factor()
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek() == "^":
            self.take()
            e = self.take()
            if e is None or not e.isdigit():
                raise ParseError("Exponent")
            if int(e) > 12:
                raise ParseError("Exponent zu gross")
            return _p_pow(base, int(e))
        return base

    def atom(self):
        t = self.take()
        if t is None:
            raise ParseError("Ausdruck endet zu frueh")
        if t == "(":
            node = self.expr()
            if self.take() != ")":
                raise ParseError("Klammer")
            return node
        if t == "x":
            return {(1, 0): Fraction(1)}
        if t == "a":
            return {(0, 1): Fraction(1)}
        if t[0].isdigit():
            return {(0, 0): Fraction(t)}
        raise ParseError(t)


def parse_expr(text: str):
    """Eine Seite einer Gleichung -> Polynom in x (Koeffizienten in a)."""
    poly = _Parser(text).parse()
    return {k: v for k, v in poly.items() if v != 0}


# ---------------------------------------------------------------------------
# Loeser
# ---------------------------------------------------------------------------

def _surd(D: Fraction):
    """D = k^2 * m  ->  (k, m) fuer ganze D, sonst None."""
    if D.denominator != 1 or D <= 0:
        return None
    n = D.numerator
    out, inner, i = 1, n, 2
    while i * i <= inner:
        while inner % (i * i) == 0:
            inner //= i * i
            out *= i
        i += 1
    return Fraction(out), inner


def solve_quadratic(c2, c1, c0) -> dict:
    """c2 x^2 + c1 x + c0 = 0 mit c* als Polynom in a."""
    if _ap_deg(c2) < 0 or _ap_is_zero(c2):
        raise ParseError("kein quadratischer Term")

    if _ap_is_const(c2) and _ap_is_const(c1) and _ap_is_const(c0):
        a, b, c = _ap_const(c2), _ap_const(c1), _ap_const(c0)
        D = b * b - 4 * a * c
        base = {"D": D, "a": a, "b": b, "c": c}
        if D < 0:
            return {**base, "kind": "none"}
        if D == 0:
            r = -b / (2 * a)
            return {**base, "kind": "double", "roots": [r], "exact": True}
        sq = _sqrt_frac(D)
        if sq is not None:
            r1, r2 = (-b + sq) / (2 * a), (-b - sq) / (2 * a)
            return {**base, "kind": "two", "roots": sorted([r1, r2]), "exact": True}
        r1 = (-float(b) + math.sqrt(float(D))) / (2 * float(a))
        r2 = (-float(b) - math.sqrt(float(D))) / (2 * float(a))
        return {**base, "kind": "two", "roots": [min(r1, r2), max(r1, r2)],
                "exact": False, "surd": _surd(D)}

    if not _ap_is_const(c2):
        raise ParseError("Leitkoeffizient haengt von a ab")
    A = _ap_const(c2)
    B = _ap_div(c1, A)
    C = _ap_div(c0, A)
    disc = _ap_add(_ap_mul(B, B), _ap_scale(C, Fraction(-4)))
    sq = _ap_sqrt(disc)
    if sq is None:
        raise ParseError("keine exakte Faktorisierung in a")
    u = _ap_div(_ap_add(_ap_scale(B, -1), sq), Fraction(2))
    v = _ap_div(_ap_add(_ap_scale(B, -1), _ap_scale(sq, -1)), Fraction(2))
    roots = sorted([u, v], key=lambda p: (float(p[1]) if len(p) > 1 else 0.0, float(p[0])))
    return {"kind": "param", "a": c2, "b": c1, "c": c0,
            "roots_p": roots, "disc": disc, "sqrt": sq}


def _factor_tex(r: Fraction) -> str:
    """Linearfaktor fuer eine exakte Nullstelle."""
    if r == 0:
        return "x"
    if r > 0:
        return "\\left(x - " + _num(r) + "\\right)"
    return "\\left(x + " + _num(-r) + "\\right)"


def _factor_ap_tex(u) -> str:
    """Linearfaktor fuer eine Nullstelle, die von a abhaengt."""
    if _ap_is_const(u):
        return _factor_tex(_ap_const(u))
    single = _ap_single_term(u)
    if single and single[0] == "-":
        return "\\left(x + " + _ap_tex(single[1]) + "\\right)"
    return "\\left(x - \\left(" + _ap_tex(u) + "\\right)\\right)"


def _quad_formula_tex(a: Fraction, b: Fraction, D: Fraction, surd=None) -> str:
    """Mitternachtsformel als LaTeX (ohne verschachtelte f-Strings)."""
    p = -b
    den = 2 * a
    if surd:
        k, m = surd
        if den > 0:
            try:
                g = math.gcd(math.gcd(abs(int(p)), int(k)), abs(int(den)))
            except (ValueError, OverflowError):
                g = 1
            if g > 1:
                p, k, den = p / g, k / g, den / g
        root = "\\sqrt{" + str(m) + "}" if k == 1 else _num(k) + "\\sqrt{" + str(m) + "}"
    else:
        root = "\\sqrt{" + _num(D) + "}"
    num = _num(p) + " \\pm " + root
    if den == 1:
        return "x_{1,2} = " + num
    return "x_{1,2} = \\frac{" + num + "}{" + _num(den) + "}"


# ---------------------------------------------------------------------------
# Tafel-Befehle (Protokoll aus dem Produktionsbericht §3)
# ---------------------------------------------------------------------------

def _cmd(order, name, element_id, space, column, **args):
    pos = {"spaceId": space, "columnId": column}
    cmd_dict = {
        "orderIndex": order,
        "commandName": name,
        "elementId": element_id,
        "position": pos,
        "actionItem": {"name": name, "args": {"type": name, "id": element_id, **args,
                                              "position": pos}},
    }
    if "color" in args and args["color"]:
        cmd_dict["color"] = args["color"]
    return cmd_dict


def _txt(order, eid, space, col, html, color=None):
    kwargs = {"text": html}
    if color:
        kwargs["color"] = color
    return _cmd(order, "write_text", eid, space, col, **kwargs)


def _tex(order, eid, space, col, latex, color=None):
    kwargs = {"latex": latex}
    if color:
        kwargs["color"] = color
    return _cmd(order, "write_latex", eid, space, col, **kwargs)


def _highlight(order, eid, target_id, color="yellow"):
    return {
        "orderIndex": order,
        "commandName": "highlight",
        "elementId": eid,
        "annotationId": eid,
        "targetElementId": target_id,
        "color": color,
        "actionItem": {
            "name": "highlight",
            "args": {"type": "highlight", "id": eid, "targetElementId": target_id, "color": color}
        }
    }


def _circle(order, eid, target_id, color="blue"):
    return {
        "orderIndex": order,
        "commandName": "circle_annotate",
        "elementId": eid,
        "annotationId": eid,
        "targetElementId": target_id,
        "color": color,
        "actionItem": {
            "name": "circle_annotate",
            "args": {"type": "circle_annotate", "id": eid, "targetElementId": target_id, "color": color}
        }
    }


def _draw(order, eid, space, col, svg, caption=""):
    return _cmd(order, "draw", eid, space, col, svg=svg, caption=caption)


def _say(order, text):
    return {"orderIndex": order, "commandName": "speak",
            "actionItem": {"name": "speak", "args": {"type": "speak", "text": text}}}


class _Board:
    def __init__(self):
        self.commands = []
        self.n = 0

    def text(self, eid, space, col, html, color=None):
        self.commands.append(_txt(self.n, eid, space, col, html, color=color))
        self.n += 1
        return self

    def tex(self, eid, space, col, latex, color=None):
        self.commands.append(_tex(self.n, eid, space, col, latex, color=color))
        self.n += 1
        return self

    def highlight(self, eid, target_id, color="yellow"):
        self.commands.append(_highlight(self.n, eid, target_id, color))
        self.n += 1
        return self

    def circle(self, eid, target_id, color="blue"):
        self.commands.append(_circle(self.n, eid, target_id, color))
        self.n += 1
        return self

    def draw(self, eid, space, col, svg, caption):
        self.commands.append(_draw(self.n, eid, space, col, svg, caption))
        self.n += 1
        return self

    def say(self, text):
        self.commands.append(_say(self.n, text))
        self.n += 1
        return self


# ---------------------------------------------------------------------------
# Problem erkennen
# ---------------------------------------------------------------------------

_DOMAIN = re.compile(r"(-?\d+(?:\.\d+)?)\s*<=\s*x\s*<=\s*(-?\d+(?:\.\d+)?)")
_DEF = re.compile(r"[fgh]\s*\(\s*x\s*\)\s*=")
_REL = re.compile(r"<=|>=|<|>|=")


def _split_domain(text: str):
    m = _DOMAIN.search(text)
    if not m:
        return text, None
    lo, hi = Fraction(m.group(1)), Fraction(m.group(2))
    return (text[:m.start()] + " " + text[m.end():]), (lo, hi)


def _parse_problem(text: str) -> dict:
    """Erkennt Aufgabe und liefert F, G, Operator, Definitionsbereich."""
    raw = _normalise(text)
    body, domain = _split_domain(raw)

    # 1) Funktionsdefinitionen:  f(x)=... ; g(x)=...
    defs = []
    for m in _DEF.finditer(body):
        start = m.end()
        chunk = body[start:]
        for sep in (";", "\n", "a.", "b.", "c.", "soru", "frage", "welche", "hangi", "wann", "("):
            k = chunk.lower().find(sep)
            if k != -1:
                chunk = chunk[:k]
        defs.append(chunk.strip(" ;\n\t"))
    if len(defs) >= 2:
        lower_raw = raw.lower()
        if "g>f" in lower_raw.replace(" ", "") or "g(x)>f(x)" in lower_raw.replace(" ", "") or "b>a" in lower_raw.replace(" ", "") or "b şehri daha sıcak" in lower_raw or "b sehir" in lower_raw:
            return {"mode": "relation", "F": defs[1], "G": defs[0], "op": ">",
                    "domain": domain, "raw": text.strip()}
        if "f>g" in lower_raw.replace(" ", "") or "f(x)>g(x)" in lower_raw.replace(" ", "") or "a>b" in lower_raw.replace(" ", "") or "a şehri daha sıcak" in lower_raw:
            return {"mode": "relation", "F": defs[0], "G": defs[1], "op": ">",
                    "domain": domain, "raw": text.strip()}
        return {"mode": "intersect", "F": defs[0], "G": defs[1], "op": "=",
                "domain": domain, "raw": text.strip()}
    if len(defs) == 1:
        body1, domain1 = _split_domain(defs[0])
        rel = _REL.search(body1)
        if rel:
            left, op, right = body1[:rel.start()], rel.group(0), body1[rel.end():]
            return {"mode": "relation", "F": left, "G": right, "op": op,
                    "domain": domain1 or domain, "raw": text.strip()}
        return {"mode": "roots", "F": body1, "G": "0", "op": "=",
                "domain": domain1 or domain, "raw": text.strip()}

    # 2) Definitionsbereich abtrennen, dann Relation suchen
    body, domain = _split_domain(raw)
    body = body.strip(" ;\n\t")
    m = _REL.search(body)
    if m:
        left = body[:m.start()].strip(" ;\n\t")
        right = body[m.end():].strip(" ;\n\t")
        op = m.group(0)
        if not left or not right:
            raise ParseError(body)
        mode = "relation" if op != "=" else "intersect"
        return {"mode": mode, "F": left, "G": right, "op": op,
                "domain": domain, "raw": text.strip()}

    # 3) Keine Relation: Ausdruck = 0  ->  Nullstellen
    body = body.strip(" ;\n\t")
    if not body:
        raise ParseError("leer")
    return {"mode": "roots", "F": body, "G": "0", "op": "=",
            "domain": domain, "raw": text.strip()}


# ---------------------------------------------------------------------------
# Unterricht bauen
# ---------------------------------------------------------------------------

def analyse(text: str) -> dict:
    try:
        spec = _parse_problem(text or "")
        F = parse_expr(spec["F"])
        G = parse_expr(spec["G"])
        return _lesson(spec, F, G)
    except ParseError as exc:
        detail = str(exc)
        return _fail("Ich konnte den Ausdruck nicht lesen: „" + detail + "“. "
                     "Erlaubt sind Zahlen, x, a sowie + - * / ^ und Klammern. "
                     "Ich rate lieber nicht, als dir etwas Falsches beizubringen.")
    except (ValueError, ZeroDivisionError) as exc:
        return _fail("Diese Form kann ich noch nicht sicher lösen (" + str(exc) + "). "
                     "Ich rate lieber nicht, als dir etwas Falsches beizubringen.")


def _fail(note: str) -> dict:
    return {"ok": False, "title": "Nicht lösbar", "note": note,
            "commands": [_say(0, note)], "facts": {}}


def _normalise_diff(diff: dict) -> dict:
    """Ganzzahlige Koeffizienten (Multiplikation mit einer positiven Zahl)."""
    if not diff:
        return diff
    den = 1
    for v in diff.values():
        den = math.lcm(den, v.denominator)
    return {k: v * den for k, v in diff.items() if v != 0}


def _lesson(spec, F, G) -> dict:
    op = spec["op"]
    domain = spec["domain"]
    diff = _normalise_diff(_p_add(F, G, Fraction(-1)))
    dx = _p_deg_x(diff)

    if dx > MAX_DEG:
        return _fail("Grad " + str(dx) + " — dafür fehlt mir ein sicherer Lösungsweg. "
                                        "Ich unterstütze Geraden und Parabeln.")
    if dx < 0:
        board = _Board()
        board.text("task", "aufgabe", "eingabe", "<h2>Gleichung</h2>")
        board.tex("res", "loesung", "ergebnis", "\\text{wahre Aussage}\\ \\Rightarrow\\ "
                                               "\\mathbb{L} = \\mathbb{R}")
        board.say("Beide Seiten sind identisch. Jede Zahl ist Lösung.")
        return _pack("Identische Gleichung", board, spec, F, G, None)

    if dx == 1:
        return _linear(spec, F, G, diff)
    if dx == 0:
        return _fail("Die Gleichung enthält kein x mehr — da gibt es nichts zu lösen.")
    return _quadratic(spec, F, G, diff)


def _pack(title, board, spec, F, G, plot, facts=None) -> dict:
    facts = dict(facts or {})
    facts.setdefault("op", spec.get("op"))
    facts.setdefault("mode", spec.get("mode"))
    if spec.get("domain"):
        facts.setdefault("domain", _bnd(spec["domain"][0]) + " <= x <= " + _bnd(spec["domain"][1]))
    return {"ok": True, "title": title, "note": "", "commands": board.commands,
            "facts": facts, "plot": plot, "spec": spec,
            "F": F, "G": G}


def _linear(spec, F, G, diff):
    c1 = _p_coef_x(diff).get(1, _ap_zero())
    c0 = _p_coef_x(diff).get(0, _ap_zero())
    if not (_ap_is_const(c1) and _ap_is_const(c0)):
        return _fail("Lineare Gleichung mit Parameter a — dafür fehlt mir die Fallunterscheidung.")
    b, c = _ap_const(c1), _ap_const(c0)
    root = -c / b
    board = _Board()
    board.text("task", "aufgabe", "eingabe",
               "<h2>Lineare Gleichung</h2>")
    board.tex("f", "aufgabe", "eingabe", "f(x) = " + _p_tex(F))
    board.tex("g", "aufgabe", "eingabe", "g(x) = " + _p_tex(G))
    board.tex("set", "loesung", "schritte", _p_tex(F) + " = " + _p_tex(G))
    board.tex("norm", "loesung", "schritte", _p_tex(diff) + " = 0")
    board.tex("root", "loesung", "ergebnis", "x = " + _num(root))
    y = _p_eval_num(F, root)
    board.tex("pt", "loesung", "ergebnis",
              "P\\left(" + _num(root) + " \\mid " + _num(y) + "\\right)")
    board.say("Die Lösung ist x = " + _de(root, 4) + ". Zur Kontrolle: in f eingesetzt "
              "kommt " + _de(y, 4) + " heraus.")
    plot = {"curves": [("f", F, BLUE), ("g", G, RED)],
            "points": [(root, y, "P")], "roots": []}
    return _pack("Lineare Gleichung", board, spec, F, G, plot,
                 {"roots": [_de(root, 4)], "points": [_de(root, 4) + " | " + _de(y, 4)],
                  "method": "linear"})


def _quadratic(spec, F, G, diff):
    op = spec["op"]
    domain = spec["domain"]
    xc = _p_coef_x(diff)
    c2, c1, c0 = xc.get(2, _ap_zero()), xc.get(1, _ap_zero()), xc.get(0, _ap_zero())
    sol = solve_quadratic(c2, c1, c0)

    if sol["kind"] == "param":
        return _parametric(spec, F, G, diff, sol)

    single = not G or spec["mode"] == "roots"
    roots_only = single and op == "="
    if op != "=":
        title = "Ungleichung"
    elif single:
        title = "Nullstellen"
    else:
        title = "Schnittpunkte"

    board = _Board()
    board.text("task", "aufgabe", "eingabe", "<h2>" + title + "</h2>")
    if single:
        board.tex("f", "aufgabe", "eingabe", "f(x) = " + _p_tex(F))
    else:
        board.tex("f", "aufgabe", "eingabe", "f(x) = " + _p_tex(F))
        board.tex("g", "aufgabe", "eingabe", "g(x) = " + _p_tex(G))

    a, b, c = sol["a"], sol["b"], sol["c"]
    D = sol["D"]

    if single:
        board.tex("set", "loesung", "schritte",
                  "f(x) = 0\\ \\Rightarrow\\ " + _p_tex(F) + " = 0" if op == "=" else
                  "f(x) " + op + " 0\\ \\Rightarrow\\ " + _p_tex(F) + " " + op + " 0")
    else:
        if op == "=":
            board.tex("set", "loesung", "schritte", _p_tex(F) + " = " + _p_tex(G))
        else:
            board.tex("set", "loesung", "schritte", _p_tex(F) + " " + op + " " + _p_tex(G))
        board.tex("norm", "loesung", "schritte", _p_tex(diff) + " = 0")
    board.tex("disc", "loesung", "schritte",
              "D = \\left(" + _num(b) + "\\right)^{2} - 4\\cdot\\left(" + _num(a) +
              "\\right)\\cdot\\left(" + _num(c) + "\\right) = " + _num(D))

    # -- keine / eine / zwei Loesungen -----------------------------------
    if sol["kind"] == "none":
        board.tex("res", "loesung", "ergebnis",
                  "D < 0\\ \\Rightarrow\\ \\mathbb{L} = \\{\\}")
        board.say("Die Diskriminante ist negativ, also gibt es keine reelle Lösung. "
                  "Der Graph berührt die x-Achse nie.")
        plot = {"curves": [("f", F, BLUE), ("g", G, RED)] if not single
                else [("f", F, BLUE)], "points": [], "roots": []}
        return _pack(title, board, spec, F, G, plot, {"roots": [], "D": _de(D, 4),
                                                      "method": "none"})

    if sol["kind"] == "double":
        r = sol["roots"][0]
        board.tex("roots", "loesung", "schritte", "x_{1} = x_{2} = " + _num(r))
        board.say("Die Diskriminante ist null: es gibt genau eine Lösung, x = "
                  + _de(r, 4) + ". Der Scheitel liegt auf der x-Achse.")
        y = _p_eval_num(F, r)
        plot = {"curves": [("f", F, BLUE), ("g", G, RED)] if not single
                else [("f", F, BLUE)],
                "points": [(r, y, "S")], "roots": [(r, "x")]}
        return _pack(title, board, spec, F, G, plot,
                     {"roots": [_de(r, 4)], "points": [_de(r, 4) + " | " + _de(y, 4)],
                      "D": _de(D, 4), "method": "double",
                      "vertex": _de(r, 4) + " | " + _de(y, 4)})

    # -- zwei Loesungen ---------------------------------------------------
    exact = sol.get("exact", False)
    if exact:
        r1, r2 = sol["roots"]
        board.tex("formula", "loesung", "schritte",
                  ("-" if a == -1 else ("" if a == 1 else _num(a))) + _factor_tex(r1) + _factor_tex(r2) + " = 0")
        board.tex("roots", "loesung", "schritte",
                  "x_{1} = " + _num(r1) + ",\\quad x_{2} = " + _num(r2))
        roots_f = [float(r1), float(r2)]
        root_txt = [_de(r1, 4), _de(r2, 4)]
    else:
        r1, r2 = sol["roots"]
        board.tex("formula", "loesung", "schritte",
                  _quad_formula_tex(a, b, D, sol.get("surd")))
        board.tex("roots", "loesung", "schritte",
                  "x_{1} \\approx " + _de_tex(r1, 2) + ",\\quad x_{2} \\approx " + _de_tex(r2, 2))
        roots_f = [r1, r2]
        root_txt = [_de(r1, 2), _de(r2, 2)]

    # Scheitelpunkt bei einer einzelnen Funktion
    vertex = None
    vertex_txt = None
    if roots_only:
        xv = -b / (2 * a)
        yv = _p_eval_num(F, xv)
        vertex = (float(xv), float(yv), "S")
        vertex_txt = _de(xv, 4) + " | " + _de(yv, 4)
        board.tex("vertex", "loesung", "ergebnis",
                  "S\\left(" + _num(xv) + " \\mid " + _num(yv) + "\\right)")

    # -- Kontrolle (Guardrail: nie eine Behauptung ungeprueft uebernehmen)
    y1 = _p_eval_num(F, Fraction(r1).limit_denominator(10 ** 9))
    y2 = _p_eval_num(F, Fraction(r2).limit_denominator(10 ** 9))
    g1 = _p_eval_num(G, Fraction(r1).limit_denominator(10 ** 9))
    g2 = _p_eval_num(G, Fraction(r2).limit_denominator(10 ** 9))
    verified = abs(y1 - g1) < Fraction(1, 10 ** 6) and abs(y2 - g2) < Fraction(1, 10 ** 6)

    pts = [(roots_f[0], float(y1)), (roots_f[1], float(y2))]
    relation = None
    if op == "=":
        for i, (px, py) in enumerate(pts, start=1):
            board.tex("pt" + str(i), "loesung", "ergebnis",
                      "P_{" + str(i) + "}\\left(" + _de_tex(px, 2) + " \\mid " +
                      _de_tex(py, 4) + "\\right)")
        if roots_only:
            board.say("Die Nullstellen sind x = " + root_txt[0] + " und x = " + root_txt[1] +
                      ". Zur Kontrolle: in f eingesetzt kommt beide Male 0 heraus.")
        else:
            sentence = ("Die Lösungen sind x = " + root_txt[0] + " und x = " + root_txt[1] + "."
                        + (" Kontrolle: in g eingesetzt kommt dasselbe y heraus, die Punkte stimmen."
                           if verified else
                           " Achtung: die Kontrolle in g geht nicht auf — bitte nachrechnen."))
            relation = _which_is_above(diff, roots_f, root_txt)
            if relation:
                sentence += " " + relation[0]
            board.say(sentence)
        facts = {"roots": root_txt,
                 "points": [root_txt[i] + " | " + _de(pts[i][1], 4) for i in range(2)],
                 "D": _de(D, 4),
                 "method": "factor" if exact else "formula"}
        if vertex_txt:
            facts["vertex"] = vertex_txt
        if relation:
            facts["above"] = relation[0]
            facts["above_graph"] = relation[1]
        labels = ("N1", "N2") if roots_only else ("P1", "P2")
        plot_points = [(pts[0][0], pts[0][1], labels[0]),
                       (pts[1][0], pts[1][1], labels[1])]
        if vertex:
            plot_points.insert(1, vertex)
        return _pack(title, board, spec, F, G,
                     {"curves": [("f", F, BLUE), ("g", G, RED)] if not single
                      else [("f", F, BLUE)],
                      "points": plot_points,
                      "roots": []}, facts)

    # -- Ungleichung -------------------------------------------------------
    segments, sign_desc = _solution_set(roots_f, float(a), op, domain)
    probe_x, probe_holds = _inequality_probe(segments, roots_f, diff, op)
    probe = {"x": _de(probe_x, 2), "holds": bool(probe_holds)}
    if not segments:
        board.tex("interval", "loesung", "ergebnis", "\\mathbb{L} = \\{\\}")
        board.say("Im erlaubten Bereich erfüllt keine Zahl die Ungleichung.")
        return _pack(title, board, spec, F, G,
                     {"curves": [("f", diff, BLUE)], "points": [],
                      "roots": [(r, "") for r in roots_f], "shade": []},
                     {"interval": "leer", "interval_text": "keine Zahl im erlaubten Bereich",
                      "method": "interval", "probe": probe, "roots": root_txt,
                      "D": _de(D, 4)})

    parts, plain = [], []
    for lo, hi, lo_closed, hi_closed in segments:
        if lo is None and hi is None:
            parts.append("x \\in \\mathbb{R}")
            plain.append("alle reellen Zahlen")
        elif lo is None:
            parts.append("x " + ("\\le " if hi_closed else "< ") + _bnd(hi))
            plain.append("x " + ("≤ " if hi_closed else "< ") + _bnd(hi))
        elif hi is None:
            parts.append("x " + ("\\ge " if lo_closed else "> ") + _bnd(lo))
            plain.append("x " + ("≥ " if lo_closed else "> ") + _bnd(lo))
        else:
            parts.append(_bnd(lo) + (" \\le " if lo_closed else " < ") + "x" +
                         (" \\le " if hi_closed else " < ") + _bnd(hi))
            plain.append(_bnd(lo) + (" ≤ " if lo_closed else " < ") + "x" +
                         (" ≤ " if hi_closed else " < ") + _bnd(hi))
    board.tex("interval", "loesung", "ergebnis", " \\lor ".join(parts))
    if domain:
        board.tex("domain", "loesung", "schritte",
                  _bnd(domain[0]) + " \\le x \\le " + _bnd(domain[1]) + "\\ \\text{(vorgegeben)}")
    board.say(sign_desc)
    shade = [(s[0], s[1]) for s in segments if s[0] is not None and s[1] is not None]
    return _pack(title, board, spec, F, G,
                 {"curves": [("f", diff, BLUE)], "points": [],
                  "roots": [(r, "") for r in roots_f], "shade": shade},
                 {"interval": " | ".join(parts),
                  "interval_text": " oder ".join(plain),
                  "method": "interval", "probe": probe,
                  "roots": root_txt, "D": _de(D, 4)})


def _which_is_above(diff, roots_f, root_txt):
    """Welche Kurve liegt zwischen den Schnittstellen oben?

    Genau der Fehler aus der Vorlage: "die erste Rakete ist hoeher" wurde
    behauptet, ohne f und g zu vergleichen (f(3)=15, g(3)=16). Hier wird die
    Differenz in der Mitte des Intervalls ausgewertet — mit exakten Bruechen.

    Rueckgabe: (Satz, "f"|"g") oder None.
    """
    if len(roots_f) != 2:
        return None
    lo, hi = sorted(roots_f)
    mid = Fraction(lo + hi) / 2
    val = _p_eval_num(diff, Fraction(mid).limit_denominator(10 ** 9))
    if val == 0:
        return None
    above, below = ("f", "g") if val > 0 else ("g", "f")
    return (("Zwischen x = " + root_txt[0] + " und x = " + root_txt[1] + " liegt " + above +
             " oberhalb von " + below + ", außerhalb ist es umgekehrt."), above)


def _solution_set(roots, a_sign, op, domain):
    """Loesungsmenge von p(x) op 0 (Grad 2), geschnitten mit dem Definitionsbereich."""
    strict = op in (">", "<")
    positive = op in (">", ">=")
    same = (a_sign > 0) == positive

    if len(roots) == 2:
        r1, r2 = roots
        if same:
            segs = [(None, r1, False, not strict), (r2, None, not strict, False)]
        else:
            segs = [(r1, r2, not strict, not strict)]
    elif len(roots) == 1:
        r1 = roots[0]
        segs = ([(None, r1, False, not strict), (r1, None, not strict, False)]
                if same else [])
    else:
        segs = [(None, None, False, False)] if same else []

    if domain:
        d0, d1 = float(domain[0]), float(domain[1])
        clipped = []
        for lo, hi, lc, hc in segs:
            if lo is None or lo < d0:
                lo2, lc2 = d0, True
            else:
                lo2, lc2 = lo, lc
            if hi is None or hi > d1:
                hi2, hc2 = d1, True
            else:
                hi2, hc2 = hi, hc
            if lo2 > hi2 + 1e-12:
                continue
            clipped.append((lo2, hi2, lc2, hc2))
        segs = clipped

    segs = [s for s in segs if s[0] is None or s[1] is None or s[1] - s[0] > 1e-12]
    return segs, _sign_text(roots, a_sign, op, segs)


def _inequality_probe(segments, roots_f, diff, op):
    """Einen Probelpunkt waehlen und exakt pruefen, ob er die Ungleichung erfuellt.

    Der Punkt dient nur der Frage an die Schuelerin; die Antwort wird am
    Polynom nachgerechnet, nicht aus der Intervallliste abgeleitet.
    """
    candidates: list[float] = []
    for lo, hi, _lc, _hc in segments:
        if lo is not None and hi is not None:
            candidates.append((lo + hi) / 2)
        elif lo is not None:
            candidates.append(lo + 1)
        elif hi is not None:
            candidates.append(hi - 1)
    if not candidates:
        if len(roots_f) == 2:
            candidates.append((roots_f[0] + roots_f[1]) / 2)
        elif roots_f:
            candidates.append(roots_f[0] + 1)
        else:
            candidates.append(0.0)
    x = Fraction(candidates[0]).limit_denominator(1000)
    val = _p_eval_num(diff, x)
    holds = {"<": val < 0, "<=": val <= 0, ">": val > 0, ">=": val >= 0}.get(op, False)
    return x, bool(holds)


def _sign_text(roots, a_sign, op, segs):
    if not segs:
        return "Keine Zahl im Definitionsbereich erfüllt die Ungleichung."
    parts = []
    for lo, hi, lc, hc in segs:
        if lo is None and hi is None:
            parts.append("alle reellen Zahlen")
        elif lo is None:
            parts.append("alle x " + ("≤ " if hc else "< ") + _bnd(hi))
        elif hi is None:
            parts.append("alle x " + ("≥ " if lc else "> ") + _bnd(lo))
        else:
            parts.append("alle x zwischen " + _bnd(lo) + " und " + _bnd(hi))
    if len(roots) == 2:
        lead = ("Die Parabel ist nach oben geöffnet" if a_sign > 0
                else "Die Parabel ist nach unten geöffnet")
        return (lead + ", sie schneidet die x-Achse bei " + _de(roots[0]) + " und "
                + _de(roots[1]) + ". Die Ungleichung gilt für " + " und ".join(parts) + ".")
    return "Die Ungleichung gilt für " + " und ".join(parts) + "."


def _parametric(spec, F, G, diff, sol):
    board = _Board()
    title = "Schnittpunkte (Parameter a)"
    board.text("task", "aufgabe", "eingabe", "<h2>" + title + "</h2>")
    board.tex("f", "aufgabe", "eingabe", "f(x) = " + _p_tex(F))
    board.tex("g", "aufgabe", "eingabe", "g(x) = " + _p_tex(G))
    board.tex("set", "loesung", "schritte", _p_tex(F) + " = " + _p_tex(G))
    board.tex("norm", "loesung", "schritte", _p_tex(diff) + " = 0")
    board.tex("disc", "loesung", "schritte",
              "D = " + _ap_tex(sol["disc"]) + " = \\left(" + _ap_tex(sol["sqrt"]) + "\\right)^{2}")
    u, v = sol["roots_p"]
    board.tex("factored", "loesung", "schritte",
              _factor_ap_tex(u) + _factor_ap_tex(v) + " = 0")
    board.tex("roots", "loesung", "schritte",
              "x_{1} = " + _ap_tex(u) + ",\\quad x_{2} = " + _ap_tex(v))

    facts = {"roots": [_ap_plain(u), _ap_plain(v)], "points": [], "method": "param"}
    for i, r in enumerate((u, v), start=1):
        y = _p_eval_param(F, r)
        board.tex("pt" + str(i), "loesung", "ergebnis",
                  "P_{" + str(i) + "}\\left(" + _ap_tex(r) + " \\mid " + _ap_tex(y) + "\\right)")
        facts["points"].append(_ap_plain(r) + " | " + _ap_plain(y))

    board.say("Die x-Werte hängen von a ab: " + _ap_plain(u) + " und " + _ap_plain(v) +
              ". Setzt man sie in f ein, kommen " + _ap_plain(_p_eval_param(F, u)) + " und " +
              _ap_plain(_p_eval_param(F, v)) + " heraus. Zur Kontrolle: in g eingesetzt "
              "kommt dasselbe heraus, weil wir f = g gelöst haben.")
    return _pack(title, board, spec, F, G, None, facts)


# ---------------------------------------------------------------------------
# SVG (gleicher Massstab auf beiden Achsen)
# ---------------------------------------------------------------------------

def _nice_step(span: float) -> float:
    raw = span / 8.0
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _num_coef(p, dx):
    c = _p_coef_x(p).get(dx, _ap_zero())
    return float(_ap_const(c)) if _ap_is_const(c) else 0.0


def svg_plot(curves, points=None, roots=None, shade=None,
             width=520, height=380, pad=46) -> str:
    """Schaubild mit beschrifteten Achsen.

    Die Fenster werden aus den markierten Punkten (Schnittpunkte, Nullstellen)
    bestimmt; x und y haben eigene Einheiten, damit die Lösung im Bild lesbar
    bleibt. Beide Achsen sind mit Zahlen beschriftet — anders als bei der
    Vorlage, die die y-Achse still staucht und unbeschriftet lässt.
    """
    points = points or []
    roots = roots or []
    shade = shade or []

    quad = []
    for label, poly, color in curves:
        quad.append((label, _num_coef(poly, 2), _num_coef(poly, 1), _num_coef(poly, 0), color))

    xs = [px for (px, _py, _lab) in points] + [rx for (rx, _lab) in roots]
    ys = [py for (_px, py, _lab) in points] + [0.0 for _ in roots]

    # Scheitel mit ins Bild: sonst liegt die Parabel ausserhalb des Fensters
    for _lab, a, b, c, _color in quad:
        if a != 0:
            xs.append(-b / (2 * a))
            ys.append(c - b * b / (4 * a))

    if not xs:
        # keine markierten Punkte: Scheitel der ersten Parabel als Anker
        for _lab, a, b, c, _color in quad:
            if a != 0:
                xs.append(-b / (2 * a))
                ys.append(c - b * b / (4 * a))
                break
    if not xs:
        xs, ys = [-1.0, 1.0], [-1.0, 1.0]

    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    xspan = max(x1 - x0, 2.0)
    yspan = max(y1 - y0, 1.0)

    x0 -= xspan * 0.40 + 0.8
    x1 += xspan * 0.40 + 0.8
    y0 -= yspan * 0.30 + 0.8
    y1 += yspan * 0.30 + 0.8

    if y0 > 0:
        y0 = -0.6
    if y1 < 0:
        y1 = 0.6

    sx = (width - 2 * pad) / (x1 - x0)
    sy = (height - 2 * pad) / (y1 - y0)

    def X(x):
        return pad + (x - x0) * sx

    def Y(y):
        return pad + (y1 - y) * sy

    def inside(y):
        return y0 - 1e-9 <= y <= y1 + 1e-9

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="' + str(width) + '" height="' +
           str(height) + '" viewBox="0 0 ' + str(width) + ' ' + str(height) +
           '" data-name="plot" role="img" aria-label="Schaubild">',
           '<rect width="' + str(width) + '" height="' + str(height) + '" fill="#ffffff"/>',
           '<clipPath id="plotclip"><rect x="' + str(pad) + '" y="' + str(pad) +
           '" width="' + str(width - 2 * pad) + '" height="' + str(height - 2 * pad) + '"/></clipPath>']

    step_x = _nice_step(x1 - x0)
    step_y = _nice_step(y1 - y0)

    # Gitter
    gx = math.ceil(x0 / step_x) * step_x
    while gx <= x1:
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="1"/>'
                   % (X(gx), pad, X(gx), height - pad, GRID))
        gx += step_x
    gy = math.ceil(y0 / step_y) * step_y
    while gy <= y1:
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1"/>'
                   % (pad, Y(gy), width - pad, Y(gy), GRID))
        gy += step_y

    ax_y = Y(0) if inside(0) else (height - pad if y0 > 0 else pad)
    ax_x = X(0) if x0 <= 0 <= x1 else pad

    # schattierte Loesungsbaender
    out.append('<g clip-path="url(#plotclip)">')
    for lo, hi in shade:
        out.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s" opacity="0.38"/>'
                   % (X(lo), pad, max(0.0, X(hi) - X(lo)), height - 2 * pad, AREA))
    out.append('</g>')

    out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1.5"/>'
               % (pad, ax_y, width - pad, ax_y, AXIS))
    out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="1.5"/>'
               % (ax_x, pad, ax_x, height - pad, AXIS))
    out.append('<text x="%d" y="%.1f" font-family="Arial, sans-serif" font-size="16" '
               'fill="%s" text-anchor="end">x</text>' % (width - pad - 4, ax_y - 8, INK))
    out.append('<text x="%.1f" y="%d" font-family="Arial, sans-serif" font-size="16" '
               'fill="%s">y</text>' % (ax_x + 8, pad + 14, INK))

    # Achsenbeschriftung
    t = math.ceil(x0 / step_x) * step_x
    while t <= x1:
        if abs(t) > 1e-9:
            out.append('<text x="%.1f" y="%.1f" font-family="Arial, sans-serif" font-size="12" '
                       'fill="#6B7280" text-anchor="middle">%s</text>'
                       % (X(t), ax_y + 16, _tick(t, step_x)))
        t += step_x

    t = math.ceil(y0 / step_y) * step_y
    while t <= y1:
        if abs(t) > 1e-9:
            tx = ax_x - 7 if ax_x > pad + 30 else pad + 4
            anchor = "end" if ax_x > pad + 30 else "start"
            out.append('<text x="%.1f" y="%.1f" font-family="Arial, sans-serif" font-size="12" '
                       'fill="#6B7280" text-anchor="%s">%s</text>'
                       % (tx, Y(t) + 4, anchor, _tick(t, step_y)))
        t += step_y

    # Kurven
    out.append('<g clip-path="url(#plotclip)">')
    for label, a, b, c, color in quad:
        n = 260
        pts = []
        for i in range(n + 1):
            x = x0 + (x1 - x0) * i / n
            y = a * x * x + b * x + c if a != 0 else b * x + c
            pts.append("%.1f %.1f" % (X(x), Y(y)))
        out.append('<polyline points="' + " ".join(pts) + '" fill="none" stroke="' + color +
                   '" stroke-width="3" stroke-linecap="round" data-descriptor="' + label + '"/>')
    out.append('</g>')

    # Nullstellen
    for (rx, lab) in roots:
        out.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s" stroke="%s" stroke-width="2"/>'
                   % (X(rx), Y(0), POINT_FILL, POINT_STROKE))
        out.append('<text x="%.1f" y="%.1f" font-family="Arial, sans-serif" font-size="13" '
                   'fill="%s" text-anchor="middle">%s</text>' % (X(rx), Y(0) + 20, INK, lab))

    # markierte Punkte
    for (px, py, lab) in points:
        out.append('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="%s" stroke-width="2"/>'
                   % (X(px), Y(py), POINT_FILL, POINT_STROKE))
        # Label above-right, or above-left near the right edge, so it never
        # sits on top of its own marker.
        anchor, dx = ("start", 13) if X(px) < width * 0.76 else ("end", -13)
        out.append('<text x="%.1f" y="%.1f" font-family="Arial, sans-serif" font-size="13" '
                   'fill="%s" text-anchor="%s" dominant-baseline="middle">%s</text>'
                   % (X(px) + dx, Y(py) - 13, INK, anchor, lab))

    out.append("</svg>")
    return "".join(out)


def with_plot(result: dict) -> dict:
    """Haengt ein massstabsgetreues Diagramm an, wenn Kurven vorliegen."""
    plot = result.get("plot")
    if not result.get("ok") or not plot or not plot.get("curves"):
        return result
    svg = svg_plot(plot["curves"], plot.get("points"), plot.get("roots"), plot.get("shade"))
    board = _Board()
    board.commands = list(result["commands"])
    board.n = len(board.commands)
    board.draw("plot", "grafik", "schaubild", svg, result["title"])
    board.say("Im Bild sind die markierten Punkte eingezeichnet: die berechneten "
              "Lösungen. Beide Achsen sind mit Zahlen beschriftet.")
    result = dict(result)
    result["commands"] = board.commands
    return result


def build_lesson(text: str) -> dict:
    """Oeffentlicher Einstieg: Text -> fertige Tafelbefehle inkl. Diagramm."""
    return with_plot(analyse(text))


# ---------------------------------------------------------------------------
# Gefuehrter Unterricht (ein Schritt, dann eine Frage — wie in der Vorlage)
# ---------------------------------------------------------------------------

_BEAT_OF = {
    "task": "task", "f": "task", "g": "task",
    "set": "set",
    "norm": "norm",
    "disc": "disc",
    "formula": "formula", "factored": "factor",
    "roots": "roots", "root": "roots",
    "vertex": "vertex",
    "pt1": "points", "pt2": "points", "pt": "points",
    "res": "res",
    "interval": "interval", "domain": "interval",
    "plot": "plot",
}


def _answer_variants(value) -> list[str]:
    """Schreibweisen, die als richtig gelten (Komma/Punkt, ganze Zahl)."""
    s = str(value).strip()
    out = {s, s.replace(",", "."), s.replace(".", ",")}
    try:
        f = float(s.replace(",", "."))
    except ValueError:
        return sorted(out)
    out.add(("%g" % f))
    out.add(("%g" % f).replace(".", ","))
    if abs(f - round(f)) < 1e-9:
        out.add(str(int(round(f))))
    return sorted(out)


def _q(question, kind, answer, hint, ok, no, choices=None):
    return {
        "type": "ask",
        "id": "q" + str(abs(hash(question)) % 100000),
        "question": question,
        "kind": kind,
        "choices": choices or [],
        "answer": str(answer),
        "accept": _answer_variants(answer),
        "hint": hint,
        "okText": ok,
        "noText": no,
    }


def _beat_say(key, result, ids) -> str:
    f = result.get("facts") or {}
    title = result.get("title", "")
    op = f.get("op", "=")
    roots = f.get("roots") or []

    if key == "task":
        return "Hier ist die Aufgabe: " + title + ". Ich schreibe die Angabe an die Tafel."
    if key == "set":
        if f.get("method") == "linear":
            return "Wir beginnen mit der Gleichung."
        if f.get("mode") == "intersect":
            return "Zuerst setzen wir die beiden Funktionen f(x) und g(x) gleich."
        if op != "=":
            return "Wir stellen die Ungleichung auf."
        return "Wir suchen die Stellen, an denen die Funktion den Wert 0 hat."
    if key == "norm":
        if f.get("mode") == "intersect" or op != "=":
            return "Jetzt bringen wir alle Terme auf die linke Seite und fassen zusammen: ax² + bx + c = 0."
        return "Wir formen die Gleichung in Normalform um."
    if key == "disc":
        d = f.get("D")
        if d in (None, "None", ""):
            return "Wir berechnen die Diskriminante D = b² − 4ac aus den Koeffizienten."
        return ("Wir berechnen die Diskriminante: D = " + str(d) +
                ". Ihr Vorzeichen sagt uns, wie viele Lösungen es gibt.")
    if key == "formula":
        return "Nun setzen wir die Koeffizienten in die Mitternachtsformel ein, um nach x aufzulösen."
    if key == "factor":
        if f.get("method") == "factor":
            return "Der Term lässt sich in Linearfaktoren zerlegen."
        if f.get("method") == "param":
            return "Der Term lässt sich in Linearfaktoren mit a zerlegen."
        return "Wir faktorisieren den Ausdruck."
    if key == "roots":
        if f.get("method") == "param":
            if len(roots) >= 2:
                return ("Die Lösungen hängen von a ab: x₁ = " + roots[0] +
                        " und x₂ = " + roots[1] + ".")
            return "Die Lösungen hängen von a ab."
        if f.get("method") == "linear":
            return "Damit steht x allein: x = " + (roots[0] if roots else "") + "."
        if len(roots) == 1:
            return "Es gibt genau eine Lösung: x = " + roots[0] + "."
        if len(roots) >= 2:
            return "Daraus erhalten wir die beiden x-Stellen: x₁ = " + roots[0] + " und x₂ = " + roots[1] + "."
        return "Dazu gibt es keine reelle Lösung."
    if key == "vertex":
        v = f.get("vertex")
        return "Der Scheitelpunkt liegt bei S(" + str(v).replace(" | ", " | ") + ")." if v else "Hier ist der Scheitelpunkt."
    if key == "points":
        pts = f.get("points") or []
        if f.get("method") == "param":
            return "Nun setzen wir die x-Werte ein, um die Schnittpunkte zu bestimmen: " + " und ".join(pts) + "."
        if f.get("mode") == "roots":
            if len(pts) >= 2:
                return ("Die Nullstellen liegen bei (" + pts[0] + ") und (" + pts[1] + ").")
            if pts:
                return "Die Nullstelle liegt bei (" + pts[0] + ")."
            return "Jetzt bestimmen wir die Nullstellen."
        if len(pts) >= 2:
            return ("Jetzt berechnen wir in der Nebenspalte die Schnittpunkte: P₁(" + pts[0] +
                    ") und P₂(" + pts[1] + ").")
        if pts:
            return "In f(x) eingesetzt ergibt sich der Schnittpunkt P(" + pts[0] + ")."
        return "Jetzt berechnen wir die Punkte."
    if key == "above":
        return f.get("above") or ""
    if key == "res":
        if title == "Identische Gleichung":
            return "Beide Seiten sind gleich. Jede Zahl ist Lösung."
        return "Es gibt keine reelle Lösung."
    if key == "interval":
        text = f.get("interval_text") or f.get("interval") or ""
        say = "Die Lösungsmenge ist " + str(text) + "."
        if f.get("domain"):
            say += (" Der vorgegebene Bereich ist " +
                    str(f["domain"]).replace("<=", "≤") + ".")
        return say
    if key == "plot":
        return ("Im Schaubild sind die berechneten Punkte und Kurven eingezeichnet.")
    return ""


def _beat_question(key, result):
    """Eine kurze Frage zu einem Schritt — die Antwort ist schon berechnet."""
    f = result.get("facts") or {}
    roots = f.get("roots") or []
    method = f.get("method")

    if key == "disc" and f.get("D") is not None:
        d = str(f["D"])
        return _q("Wie groß ist die Diskriminante D?", "number", d,
                  "D = b² − 4ac. Setze die Zahlen aus der Normalform ein.",
                  "Genau, D = " + d + ".", "Nicht ganz. Rechne b² − 4ac noch einmal nach; D = " + d + ".")

    if key == "res":
        return _q("Wie viele reelle Lösungen hat die Gleichung?", "choice", "0",
                  "Schau auf das Vorzeichen der Diskriminante.",
                  "Richtig: keine reelle Lösung.", "Doch, es gibt eine. D ist negativ, also keine reelle Lösung.",
                  ["0", "1", "2"])

    if key == "roots":
        if method == "param":
            u = roots[0] if roots else ""
            # a = 1 einsetzen: aus "3a" wird 3, aus "a" wird 1.
            value = _ap_eval_num(_ap_from_plain(u), 1) if u else None
            if value is not None:
                txt = _de(value, 4)
                return _q("Setze a = 1. Welchen Wert hat x₁ dann?", "number", txt,
                          "Ersetze in x₁ = " + u + " jedes a durch 1.",
                          "Genau, dann ist x₁ = " + txt + ".", "Fast. Mit a = 1 wird x₁ = " + txt + ".")
        elif roots:
            r = roots[0]
            if method == "linear":
                return _q("Welchen Wert hat x?", "number", r,
                          "Forme die Gleichung um, bis x allein steht, und setze zur Probe ein.",
                          "Richtig, x = " + r + ".", "Noch nicht. Die Lösung ist x = " + r + ".")
            return _q("Was ist die kleinere Lösung x₁?", "number", r,
                      "Setze deinen Wert in die Gleichung ein; es muss 0 herauskommen.",
                      "Richtig, x₁ = " + r + ".", "Noch nicht. Die kleinere Lösung ist x₁ = " + r + ".")
        elif method in ("none", "double"):
            return None

    if key == "points" and f.get("above_graph"):
        right = f["above_graph"]
        return _q("Welcher Graph liegt zwischen den Schnittpunkten oberhalb?", "choice", right,
                  "Vergleiche die y-Werte in der Mitte zwischen den beiden Schnittstellen.",
                  "Genau: " + right + " liegt dort oben.", "Andersherum: " + right + " liegt oben.",
                  ["f", "g"])

    if key == "interval" and isinstance(f.get("probe"), dict):
        probe = f["probe"]
        right = "ja" if probe.get("holds") else "nein"
        x = str(probe.get("x"))
        return _q("Erfüllt x = " + x + " die Ungleichung?", "choice", right,
                  "Setze " + x + " in die Ungleichung ein und rechne beide Seiten aus.",
                  "Richtig, x = " + x + " erfüllt die Ungleichung." if right == "ja"
                  else "Richtig, x = " + x + " erfüllt die Ungleichung nicht.",
                  "Doch: x = " + x + " erfüllt die Ungleichung." if right == "ja"
                  else "Doch: x = " + x + " erfüllt sie nicht.",
                  ["ja", "nein"])

    return None


def _ap_from_plain(text: str):
    """Einfaches a-Polynom aus ``facts`` (z. B. ``"3a"``, ``"a"``, ``"2 - a"``)."""
    s = text.replace(" ", "")
    terms = re.findall(r"([+-]?)(\d*)(a(?:\^(\d+))?)?", s)
    highest = 0
    parsed = []
    for sign, num, var, exp in terms:
        if not (num or var):
            continue
        coef = Fraction(int(num) if num else 1)
        if sign == "-":
            coef = -coef
        power = int(exp) if exp else (1 if var else 0)
        parsed.append((power, coef))
        highest = max(highest, power)
    out = [Fraction(0)] * (highest + 1)
    for power, coef in parsed:
        out[power] += coef
    return _ap_trim(out)


def build_plan(text: str) -> dict:
    """Text -> geordnete Unterrichtsschritte: erst erklaeren, dann fragen.

    Die Schritte sind bewusst klein: ein Tafelschritt, ein Satz, dann eine
    Frage. Der Client wartet nach jeder Frage auf die Schuelerin.
    """
    result = build_lesson(text)
    base = {"ok": result["ok"], "title": result["title"], "note": result["note"],
            "facts": result.get("facts") or {}, "problem": text}

    if not result["ok"]:
        base["steps"] = [{"type": "teach", "say": result["note"],
                          "commands": result["commands"], "wait": 0, "beat": "task"}]
        return base

    beats: list[tuple[str, list[dict]]] = []
    for cmd in result["commands"]:
        if cmd.get("commandName") == "speak":
            continue
        key = _BEAT_OF.get(cmd.get("elementId"))
        if not key:
            continue
        if not beats or beats[-1][0] != key:
            beats.append((key, []))
        beats[-1][1].append(cmd)

    # Der Satz "Welche Kurve liegt oben?" bekommt einen eigenen Schritt.
    above = (result.get("facts") or {}).get("above")
    if above:
        # nach dem Punkte-Schritt einschieben
        insert_at = len(beats)
        for i, (key, _cmds) in enumerate(beats):
            if key == "points":
                insert_at = i + 1
                break
        beats.insert(insert_at, ("above", []))

    steps = []
    for key, cmds in beats:
        say = _beat_say(key, result, {c.get("elementId") for c in cmds})
        if not say:
            continue
        steps.append({"type": "teach", "beat": key, "say": say, "commands": cmds,
                      "wait": 900 if key == "plot" else 650})
        question = _beat_question(key, result)
        if question:
            steps.append(question)

    # Abschluss: keine neuen Zahlen, nur der Verweis auf das Ergebnis.
    summary = _summary_text(result)
    steps.append({"type": "teach", "beat": "done",
                  "say": summary, "commands": [], "wait": 800})

    base["steps"] = steps
    return base


def _summary_text(result: dict) -> str:
    f = result.get("facts") or {}
    if f.get("interval_text"):
        return "Fertig. Die Lösungsmenge ist " + str(f["interval_text"]) + "."
    if f.get("method") == "linear" and f.get("roots"):
        return "Fertig. Die Lösung ist x = " + str(f["roots"][0]) + "."
    if f.get("points"):
        return "Fertig. Die Punkte sind " + " und ".join(f["points"]) + "."
    if f.get("roots"):
        return "Fertig. Die Lösungen sind x = " + ", ".join(f["roots"]) + "."
    return "Fertig."


def summary(result: dict) -> str:
    """Kurze Zusammenfassung in einem Satz (ohne Modell)."""
    if not result.get("ok"):
        return result.get("note") or "Dazu kann ich nichts Sicheres sagen."
    facts = result.get("facts") or {}
    if facts.get("points"):
        return "Lösung: " + "; ".join(facts["points"]) + "."
    if facts.get("roots"):
        return "Lösung: x = " + ", ".join(facts["roots"]) + "."
    return result.get("title", "")
