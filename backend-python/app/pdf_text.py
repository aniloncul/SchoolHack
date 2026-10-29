"""PDF-Text lesen — nur mit der Standardbibliothek.

Warum handgeschrieben?  Das Projekt hat bewusst keine PDF-Abhaengigkeit
(``requirements.txt``: FastAPI, Jinja, multipart, itsdangerous).  Fuer die
Hausaufgaben-Seite reicht ein kleiner Leser, der genau das kann, was hier
gebraucht wird:

    PDF -> Objekte -> Seitenbaum -> Inhaltsstroeme -> Text

Unterstuetzt werden
    * unkomprimierte und ``FlateDecode``-Inhaltsstroeme,
    * Objektstroeme (``/Type /ObjStm``), wie sie moderne PDFs schreiben,
    * Textoperatoren ``Tj``, ``TJ``, ``'``, ``"``, ``Td``, ``TD``, ``Tm``, ``T*``,
    * ``/ToUnicode``-CMaps (auch Bereiche), sonst cp1252.

Nicht unterstuetzt werden gescannte Seiten (reine Bilder), verschluesselte
PDFs und LZW-kodierte Stroeme.  In diesen Faellen gibt es eine klare
Fehlermeldung statt geratener Zahlen — dieselbe Regel wie im Rechenkern:
lieber nichts sagen als etwas Falsches.

Oeffentliche API
    extract(data)        -> {"pages": [...], "text": str, "chars": int, ...}
    split_tasks(text)    -> [{"label", "text", "expr", "ok", "title", "note"}]
    PdfError
"""
from __future__ import annotations

import base64
import re
import zlib

MAX_PAGES = 12
MAX_CHARS = 24000


class PdfError(ValueError):
    """Das PDF liess sich nicht lesen oder enthaelt keinen Text."""


# ---------------------------------------------------------------------------
# Objekte einsammeln
# ---------------------------------------------------------------------------

_OBJ_START = re.compile(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b")
_STREAM_KW = re.compile(rb"\bstream\r?\n")
_DIRECT_LEN = re.compile(rb"/Length\s+(\d+)(?!\s+\d+\s+R)")


def _read_objects(data: bytes) -> dict[int, dict]:
    """Alle Objekte der Datei: Nummer -> {dict, stream}.

    Es wird bewusst linear gescannt statt die xref-Tabelle zu lesen: die ist
    bei vielen PDFs fehlerhaft, und fuer Text brauchen wir nur die Objekte.
    """
    objs: dict[int, dict] = {}
    pos = 0
    while True:
        m = _OBJ_START.search(data, pos)
        if not m:
            break
        num = int(m.group(1))
        start = m.end()
        end = data.find(b"endobj", start)
        if end == -1:
            end = len(data)
        sm = _STREAM_KW.search(data, start, min(end + 6, len(data)))
        if sm and sm.start() < end:
            head = data[start:sm.start()]
            length = _DIRECT_LEN.search(head)
            body_start = sm.end()
            if length:
                n = int(length.group(1))
                body = data[body_start:body_start + n]
                after = body_start + n
            else:
                stop = data.find(b"endstream", body_start)
                if stop == -1:
                    stop = end
                body = data[body_start:stop]
                after = stop
            objs[num] = {"dict": head, "stream": body}
            nxt = data.find(b"endobj", after)
            pos = len(data) if nxt == -1 else nxt + 6
        else:
            objs[num] = {"dict": data[start:end], "stream": None}
            pos = end + 6
    if not objs:
        raise PdfError("Das ist kein lesbares PDF.")
    return objs


def _decode_stream(head: bytes, raw: bytes) -> bytes:
    """Filterkette anwenden (nur die haeufigen, verlustfreien Filter)."""
    filters = [f.decode("latin-1") for f in
               re.findall(rb"/(FlateDecode|Fl|ASCIIHexDecode|ASCII85Decode|RunLengthDecode)", head)]
    out = raw or b""
    for name in filters:
        if name in ("FlateDecode", "Fl"):
            if not out:
                return b""
            try:
                out = zlib.decompress(out)
            except zlib.error:
                # Manche Erzeuger schreiben einen kaputten Adler-Wert.
                try:
                    out = zlib.decompressobj().decompress(out)
                except zlib.error:
                    return b""
        elif name == "ASCIIHexDecode":
            hexpart = re.sub(rb"[^0-9A-Fa-f]", b"", out.split(b">")[0])
            if len(hexpart) % 2:
                hexpart += b"0"
            try:
                out = bytes.fromhex(hexpart.decode("ascii"))
            except ValueError:
                return b""
        elif name == "ASCII85Decode":
            try:
                body = out.strip()
                if body.startswith(b"<~"):
                    body = body[2:]
                body = body.split(b"~>")[0]
                out = base64.a85decode(body, adobe=False)
            except ValueError:
                return b""
        elif name == "RunLengthDecode":
            out = _run_length(out)
    return out


def _run_length(raw: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(raw):
        n = raw[i]
        i += 1
        if n == 128:
            break
        if n < 128:
            out += raw[i:i + n + 1]
            i += n + 1
        else:
            out += raw[i:i + 1] * (257 - n)
            i += 1
    return bytes(out)


def _expand_object_streams(objs: dict[int, dict]) -> None:
    """Objekte aus ``/ObjStm``-Stroemen nachziehen (moderne PDFs)."""
    extra: dict[int, dict] = {}
    for obj in list(objs.values()):
        head = obj["dict"]
        if b"/ObjStm" not in head or not obj["stream"]:
            continue
        data = _decode_stream(head, obj["stream"])
        if not data:
            continue
        n_match = re.search(rb"/N\s+(\d+)", head)
        first_match = re.search(rb"/First\s+(\d+)", head)
        if not n_match or not first_match:
            continue
        n, first = int(n_match.group(1)), int(first_match.group(1))
        header = data[:first].split()
        if len(header) < 2 * n:
            continue
        pairs = [(int(header[2 * i]), int(header[2 * i + 1])) for i in range(n)]
        for i, (num, off) in enumerate(pairs):
            start = first + off
            end = first + pairs[i + 1][1] if i + 1 < n else len(data)
            if num not in objs:
                extra[num] = {"dict": data[start:end], "stream": None}
    objs.update(extra)


# ---------------------------------------------------------------------------
# Verweise und Seitenbaum
# ---------------------------------------------------------------------------

_REF = re.compile(rb"(\d+)\s+\d+\s+R")
_SINGLE_REF = re.compile(rb"/%s\s+(\d+)\s+\d+\s+R")


def _ref(head: bytes, key: bytes):
    m = re.search(rb"/" + key + rb"\s+(\d+)\s+\d+\s+R", head)
    return int(m.group(1)) if m else None


def _ref_list(head: bytes, key: bytes) -> list[int]:
    m = re.search(rb"/" + key + rb"\s*\[(.*?)\]", head, re.S)
    if not m:
        one = _ref(head, key)
        return [one] if one is not None else []
    return [int(x) for x in _REF.findall(m.group(1))]


def _find_catalog(objs: dict[int, dict]) -> int | None:
    for num, obj in objs.items():
        if re.search(rb"/Type\s*/Catalog", obj["dict"]):
            return num
    return None


def _pages(objs: dict[int, dict]) -> list[int]:
    """Seiten in Dokumentreihenfolge."""
    root = _find_catalog(objs)
    out: list[int] = []
    seen: set[int] = set()

    def walk(num: int) -> None:
        if num in seen or num not in objs:
            return
        seen.add(num)
        head = objs[num]["dict"]
        if re.search(rb"/Type\s*/Pages", head):
            for kid in _ref_list(head, b"Kids"):
                walk(kid)
        elif re.search(rb"/Type\s*/Page\b", head):
            out.append(num)

    if root is not None:
        top = _ref(objs[root]["dict"], b"Pages")
        if top is not None:
            walk(top)
    if not out:
        # Kein brauchbarer Seitenbaum: alle Seiten in Dateireihenfolge.
        out = [n for n, o in sorted(objs.items())
               if re.search(rb"/Type\s*/Page\b", o["dict"])]
    return out


def _inherited(objs: dict[int, dict], page: int, key: bytes, depth: int = 0):
    if page not in objs or depth > 24:
        return None
    head = objs[page]["dict"]
    ref = _ref(head, key)
    if ref is not None:
        return ref
    if re.search(rb"/" + key + rb"\s*<<", head):
        return page          # inline, z. B. /Resources << ... >>
    parent = _ref(head, b"Parent")
    return _inherited(objs, parent, key, depth + 1) if parent is not None else None


# ---------------------------------------------------------------------------
# Schriften und ToUnicode
# ---------------------------------------------------------------------------

_HEX = re.compile(rb"<([0-9A-Fa-f\s]*)>")


class _CMap:
    def __init__(self):
        self.map: dict[int, str] = {}
        self.lens: set[int] = set()

    def decode(self, raw: bytes) -> str:
        if not self.map:
            return _cp1252(raw)
        for n in sorted(self.lens, reverse=True):
            out = []
            ok = True
            for i in range(0, len(raw) - n + 1, n):
                code = int.from_bytes(raw[i:i + n], "big")
                ch = self.map.get(code)
                if ch is None:
                    ok = False
                    break
                out.append(ch)
            if ok and out:
                return "".join(out)
        return _cp1252(raw)


def _cp1252(raw: bytes) -> str:
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace")


def _hex_bytes(token: bytes) -> bytes:
    digits = re.sub(rb"[^0-9A-Fa-f]", b"", token)
    if len(digits) % 2:
        digits += b"0"
    try:
        return bytes.fromhex(digits.decode("ascii"))
    except ValueError:
        return b""


def _utf16(raw: bytes) -> str:
    if len(raw) % 2:
        raw = b"\x00" + raw
    try:
        return raw.decode("utf-16-be")
    except UnicodeDecodeError:
        return _cp1252(raw)


def _parse_cmap(data: bytes) -> _CMap:
    """``beginbfchar`` / ``beginbfrange`` lesen."""
    cmap = _CMap()
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        pairs = _HEX.findall(block)
        for i in range(0, len(pairs) - 1, 2):
            src, dst = _hex_bytes(pairs[i]), _hex_bytes(pairs[i + 1])
            if src:
                cmap.map[int.from_bytes(src, "big")] = _utf16(dst)
                cmap.lens.add(len(src))
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        # <lo> <hi> <dst>  oder  <lo> <hi> [ <dst> ... ]
        pattern = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])")
        for m in pattern.finditer(block):
            lo_b, hi_b = _hex_bytes(m.group(1)), _hex_bytes(m.group(2))
            if not lo_b or not hi_b or len(lo_b) != len(hi_b):
                continue
            lo, hi = int.from_bytes(lo_b, "big"), int.from_bytes(hi_b, "big")
            if hi - lo > 65535:
                continue
            tail = m.group(3)
            if tail.startswith(b"["):
                dests = [_hex_bytes(h) for h in _HEX.findall(tail)]
                for k, dest in enumerate(dests):
                    if lo + k > hi:
                        break
                    cmap.map[lo + k] = _utf16(dest)
                    cmap.lens.add(len(lo_b))
            else:
                dest = _hex_bytes(tail[1:-1])
                if not dest:
                    continue
                base = int.from_bytes(dest, "big")
                for k in range(hi - lo + 1):
                    cmap.map[lo + k] = _utf16((base + k).to_bytes(len(dest), "big"))
                cmap.lens.add(len(lo_b))
    return cmap


def _fonts(objs: dict[int, dict], resources: int | None) -> dict[str, _CMap]:
    """Schriftname (``F1``) -> CMap, soweit eine ToUnicode-Tabelle da ist."""
    fonts: dict[str, _CMap] = {}
    if resources is None or resources not in objs:
        return fonts
    head = objs[resources]["dict"]
    fm = re.search(rb"/Font\s*<<(.*?)>>", head, re.S)
    if fm:
        block = fm.group(1)
    else:
        fref = _ref(head, b"Font")
        block = objs[fref]["dict"] if fref in objs else b""
    for name, num in re.findall(rb"/([A-Za-z0-9#_.+-]+)\s+(\d+)\s+\d+\s+R", block):
        font = objs.get(int(num))
        if not font:
            continue
        tu = _ref(font["dict"], b"ToUnicode")
        if tu and tu in objs:
            raw = _decode_stream(objs[tu]["dict"], objs[tu]["stream"] or b"")
            if raw:
                fonts[name.decode("latin-1")] = _parse_cmap(raw)
    return fonts


# ---------------------------------------------------------------------------
# Inhaltsstrom -> Text
# ---------------------------------------------------------------------------

_DELIMS = b"()<>[]{}/%"
_SPACE = b" \t\r\n\f\x00"


def _tokens(data: bytes):
    """Kleiner Tokenizer fuer PDF-Inhaltsstroeme."""
    i, n = 0, len(data)
    while i < n:
        c = data[i:i + 1]
        if c in _SPACE:
            i += 1
            continue
        if c == b"%":
            nl = data.find(b"\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if c == b"(":
            depth, j, out = 1, i + 1, bytearray()
            while j < n and depth:
                ch = data[j:j + 1]
                if ch == b"\\":
                    j += 1
                    if j >= n:
                        break
                    esc = data[j:j + 1]
                    table = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                             b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}
                    if esc in table:
                        out += table[esc]
                        j += 1
                    elif esc in b"01234567":
                        k, oct_digits = 0, b""
                        while k < 3 and data[j:j + 1] in b"01234567":
                            oct_digits += data[j:j + 1]
                            j += 1
                            k += 1
                        out.append(int(oct_digits or b"0", 8) & 0xFF)
                    elif esc in (b"\n", b"\r"):
                        j += 1
                    else:
                        out += esc
                        j += 1
                    continue
                if ch == b"(":
                    depth += 1
                    out += ch
                    j += 1
                    continue
                if ch == b")":
                    depth -= 1
                    if depth:
                        out += ch
                    j += 1
                    continue
                out += ch
                j += 1
            i = j
            yield ("str", bytes(out))
            continue
        if c == b"<" and data[i + 1:i + 2] != b"<":
            j = data.find(b">", i + 1)
            if j == -1:
                j = n
            yield ("str", _hex_bytes(data[i + 1:j]))
            i = j + 1
            continue
        if c == b"[":
            yield ("[", None)
            i += 1
            continue
        if c == b"]":
            yield ("]", None)
            i += 1
            continue
        if c == b"/":
            j = i + 1
            while j < n and data[j:j + 1] not in _SPACE and data[j:j + 1] not in _DELIMS:
                j += 1
            yield ("name", data[i + 1:j].decode("latin-1"))
            i = j
            continue
        if c in b"+-.0123456789":
            j = i
            while j < n and data[j:j + 1] in b"+-.0123456789":
                j += 1
            try:
                yield ("num", float(data[i:j]))
            except ValueError:
                yield ("num", 0.0)
            i = j
            continue
        # Operator oder unbekanntes Zeichen
        j = i
        while j < n and data[j:j + 1] not in _SPACE and data[j:j + 1] not in _DELIMS:
            j += 1
        if j == i:
            j += 1
        yield ("op", data[i:j].decode("latin-1"))
        i = j


def _content_text(data: bytes, fonts: dict[str, _CMap]) -> str:
    out: list[str] = []
    stack: list[tuple[str, object]] = []
    cmap: _CMap | None = None
    size = 12.0
    leading = 14.0
    line_x = line_y = 0.0          # Textzeilen-Matrix
    x = y = 0.0                    # aktuelle Position
    prev_y = None                  # y der zuletzt geschriebenen Zeile
    end_x = 0.0                    # x hinter dem zuletzt geschriebenen Text
    prev_start = 0.0               # x, an dem der letzte Text begann
    prev_len = 0                   # Zeichen im letzten Textstueck

    def emit(s: str) -> None:
        nonlocal x, end_x, prev_y, prev_start, prev_len
        if not s:
            return
        if out:
            prev = out[-1]
            if prev_y is not None and abs(y - prev_y) > size * 0.6:
                if not prev.endswith("\n"):
                    out.append("\n")
            elif (not s.isspace() and not prev.endswith((" ", "\n"))
                  and x - end_x > size * 0.25):
                out.append(" ")
        out.append(s)
        # Breite grob schaetzen; sie steuert nur Leerzeichen und Zeilenumbrueche.
        width = sum(0.5 if ch != " " else 0.28 for ch in s) * size
        prev_start = x
        prev_len = len(s)
        x += width
        end_x = x
        prev_y = y

    for kind, value in _tokens(data):
        if kind == "[":
            stack.append(("[", None))
            continue
        if kind == "]":
            # Array schliessen: die Elemente bleiben fuer TJ im Stack.
            continue
        if kind == "num":
            stack.append(("num", value))
            continue
        if kind == "str":
            stack.append(("str", value))
            continue
        if kind == "name":
            stack.append(("name", value))
            continue
        if kind != "op":
            continue

        op = value
        if op == "BT":
            line_x = line_y = x = y = 0.0
            stack = []
            continue
        if op == "Tf":
            names = [v for k, v in stack if k == "name"]
            nums = [v for k, v in stack if k == "num"]
            if names:
                cmap = fonts.get(names[-1])
            if nums:
                size = nums[-1] or size
            stack = []
            continue
        if op == "TL":
            nums = [v for k, v in stack if k == "num"]
            if nums:
                leading = nums[-1]
            stack = []
            continue
        if op in ("Td", "TD"):
            nums = [v for k, v in stack if k == "num"]
            if len(nums) >= 2:
                tx, ty = nums[-2], nums[-1]
                # Chrome setzt jedes Zeichen mit einem eigenen Td. Dann ist tx
                # die tatsaechliche Breite des vorigen Zeichens — genauer als
                # die Schaetzung und noetig, damit keine Leerzeichen mitten
                # im Wort entstehen.
                if (op == "Td" and abs(ty) < 1e-6 and 0 < tx < size * 6
                        and out and prev_len):
                    end_x = prev_start + tx
                line_x += tx
                line_y += ty
                x, y = line_x, line_y
                if op == "TD":
                    leading = -nums[-1]
            stack = []
            continue
        if op == "Tm":
            nums = [v for k, v in stack if k == "num"]
            if len(nums) >= 6:
                line_x, line_y = nums[-2], nums[-1]
                x, y = line_x, line_y
            stack = []
            continue
        if op == "T*":
            line_y -= leading
            x, y = line_x, line_y
            stack = []
            continue
        if op in ("Tj", "'", '"'):
            strings = [v for k, v in stack if k == "str"]
            if op in ("'", '"'):
                line_y -= leading
                x, y = line_x, line_y
            if strings:
                raw = strings[-1]
                text = cmap.decode(raw) if cmap else _cp1252(raw)
                emit(text)
            stack = []
            continue
        if op == "TJ":
            for k, v in stack:
                if k == "str":
                    text = cmap.decode(v) if cmap else _cp1252(v)
                    emit(text)
                elif k == "num":
                    x -= v / 1000.0 * size
                    if v < -160 and out and not out[-1].endswith((" ", "\n")):
                        out.append(" ")
            stack = []
            continue
        # alle anderen Operatoren ignorieren, ihren Stack aber leeren
        stack = []

    text = "".join(out)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Oeffentliche API
# ---------------------------------------------------------------------------

def extract(data: bytes, max_pages: int = MAX_PAGES) -> dict:
    """Text aus einem PDF lesen.

    Rueckgabe::

        {"pages": [{"no": 1, "text": "..."}], "text": "...",
         "chars": 812, "page_count": 1, "shown_pages": 1,
         "scanned": False, "truncated": False}
    """
    if not data or not data.lstrip().startswith(b"%PDF"):
        raise PdfError("Die Datei ist kein PDF.")
    if len(data) > 12 * 1024 * 1024:
        raise PdfError("Das PDF ist zu groß (max. 12 MB).")

    objs = _read_objects(data)
    _expand_object_streams(objs)
    page_ids = _pages(objs)

    pages: list[dict] = []
    for index, page in enumerate(page_ids[:max_pages], start=1):
        head = objs[page]["dict"]
        res = _inherited(objs, page, b"Resources")
        fonts = _fonts(objs, res)
        chunks: list[bytes] = []
        for ref in _ref_list(head, b"Contents"):
            obj = objs.get(ref)
            if not obj:
                continue
            raw = _decode_stream(obj["dict"], obj["stream"] or b"")
            if raw:
                chunks.append(raw)
        if not chunks:
            continue
        text = _content_text(b"\n".join(chunks), fonts).strip()
        if text:
            pages.append({"no": index, "text": text})

    if not pages:
        raise PdfError(
            "In diesem PDF steht kein auswählbarer Text. "
            "Wahrscheinlich ist es ein Scan (Foto). Bitte tippe die Aufgabe ab "
            "oder lade ein PDF mit echtem Text hoch.")

    full = "\n\n".join(p["text"] for p in pages)
    truncated = len(full) > MAX_CHARS
    if truncated:
        full = full[:MAX_CHARS]

    return {
        "pages": pages,
        "text": full,
        "chars": len(full),
        "page_count": len(page_ids),
        "shown_pages": len(pages[:max_pages]),
        "scanned": False,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Aufgaben erkennen
# ---------------------------------------------------------------------------

# Nummerierung am Zeilenanfang: "1.", "2)", "Aufgabe 3", "(4)".
_TASK_LINE = re.compile(r"^\s*(?:(?:Aufgabe|Aufg\.?)\s*)?(\d{1,2})\s*(?:[.):\-]\s*|(?=\s))",
                        re.I)
_TASK_PAREN = re.compile(r"^\s*\((\d{1,2})\)\s*")
_MATH_CHARS = set("0123456789xaX+-*/^().,;<>=\u00b2\u00b3\u2074\u2212\u2013\u2014"
                  "\u00b7\u00d7\u00f7\u2264\u2265\u00a0")
_STOP_WORDS = re.compile(
    r"\b(?:und|oder|sowie|berechne|bestimme|ermittle|gegeben|geg\.|fuer|für|mit|"
    r"auf|aufgabe|von|die|der|das|den|dem|welche|welcher|wo|liegt|liegen|schneiden|"
    r"schnittpunkte|schnittpunkt|nullstellen|ungleichung|gleichung|funktionen|"
    r"funktion|graphen|parabeln|koordinaten|zeichne|skizziere|begruende|"
    r"begründe|löse|loese|lies|notiere|trage|im|in|ist|sind|hat|haben|sich|aus|"
    r"x\s*\u2208)\b", re.I)

_KNOWN_MATH_WORDS = (
    "Aufgabe", "Berechne", "Bestimme", "Ermittle", "Gegeben", "Schnittpunkte",
    "Nullstellen", "Ungleichung", "Gleichung", "Funktion", "Funktionen",
    "Parabel", "Gerade", "Definitionsbereich", "Lösungsmenge", "Lösung",
    "Schaubild", "Graph", "Koordinaten", "Skizziere", "Begründe",
)


def _plain_math(text: str) -> str:
    """Unicode-Mathe in die Schreibweise des Rechenkerns bringen."""
    t = (text.replace("\u00b2", "^2").replace("\u00b3", "^3").replace("\u2074", "^4")
         .replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
         .replace("\u00b7", "*").replace("\u00d7", "*").replace("\u00f7", "/")
         .replace("\u2264", "<=").replace("\u2265", ">=")
         .replace("\u00a0", " "))
    # Zeilen bleiben Zeilen: nur innerhalb einer Zeile zusammenfassen.
    t = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in t.split("\n"))
    # deutsches Dezimalkomma bleibt zunaechst erhalten; der Rechenkern kennt es.
    return t.strip(" .;:")


def _math_run(text: str, start: int) -> str:
    """Ab ``start`` zusammenhaengende Mathe-Zeichen einsammeln."""
    out = []
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            break
        if ch in _MATH_CHARS or ch.isspace():
            out.append(ch)
            i += 1
        else:
            break
    run = "".join(out)
    run = _STOP_WORDS.split(run, maxsplit=1)[0]
    run = _plain_math(run)
    # Ein einzelnes "a" am Ende ist meist ein Wortrest ("... + 3 a").
    run = re.sub(r"\s+[aA]$", "", run)
    return run.strip(" .,;")


def _function_defs(text: str) -> list[tuple[str, str]]:
    defs = []
    for m in re.finditer(r"([fgh])\s*\(\s*x\s*\)\s*=", text, re.I):
        rhs = _math_run(text, m.end())
        if rhs:
            defs.append((m.group(1).lower(), rhs))
    # doppelte Namen (f auf zwei Seiten) nur einmal behalten
    seen = {}
    for name, rhs in defs:
        seen.setdefault(name, rhs)
    return list(seen.items())


def _candidate_expr(text: str) -> str:
    """Aus dem Aufgabentext einen Ausdruck fuer den Rechenkern bauen."""
    clean = _plain_math(text)
    defs = _function_defs(clean)
    domain_m = re.search(r"(-?\d+(?:\.\d+)?)\s*<=\s*x\s*<=\s*(-?\d+(?:\.\d+)?)", text)
    domain_str = f", {domain_m.group(0).replace(' ', '')}" if domain_m else ""

    if len(defs) >= 2:
        (n1, r1), (n2, r2) = defs[0], defs[1]
        lower_t = text.lower()
        if "g>f" in lower_t.replace(" ", "") or "g(x)>f(x)" in lower_t.replace(" ", "") or "b>a" in lower_t.replace(" ", "") or "b şehri daha sıcak" in lower_t or "b sehir" in lower_t:
            return f"{r2} > {r1}{domain_str}"
        if "f>g" in lower_t.replace(" ", "") or "f(x)>g(x)" in lower_t.replace(" ", "") or "a>b" in lower_t.replace(" ", "") or "a şehri daha sıcak" in lower_t:
            return f"{r1} > {r2}{domain_str}"
        return f"{n1}(x)={r1} ; {n2}(x)={r2}{domain_str}"

    if len(defs) == 1:
        name, rhs = defs[0]
        # "f(x)=... = ..." -> beide Seiten stehen lassen
        return f"{name}(x)={rhs}{domain_str}"

    # Keine Funktionsschreibweise: den laengsten Mathe-Lauf mit x und Relation
    # nehmen. Ein Lauf ab dem fuehrenden Minus ist laenger als ab der Ziffer,
    # deshalb bleibt das Vorzeichen erhalten.
    best = ""
    for m in re.finditer(r"[-0-9xX(]", clean):
        run = _math_run(clean, m.start())
        if "x" in run.lower() and re.search(r"[=<>]", run) and len(run) > len(best):
            best = run
    if best:
        return best
    # Letzter Versuch: ein Ausdruck ohne Relation (wird als Nullstellen gelesen).
    for m in re.finditer(r"[-0-9xX(]", clean):
        run = _math_run(clean, m.start())
        if "x" in run.lower() and len(run) > len(best):
            best = run
    return best


def _task_label(line: str) -> str:
    m = _TASK_LINE.match(line) or _TASK_PAREN.match(line)
    if not m:
        return ""
    return m.group(1)


def split_tasks(text: str, limit: int = 12) -> list[dict]:
    """Nummerierte Aufgaben aus dem PDF-Text schneiden und pruefen.

    Jede Aufgabe bekommt einen ``expr`` (falls erkennbar) und das Ergebnis
    von ``math_agent.analyse`` — die Seite zeigt damit sofort, welche Aufgabe
    wirklich rechenbar ist und welche nicht.
    """
    from . import math_agent   # lokal, damit dieses Modul allein testbar bleibt

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]

    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        label = _task_label(stripped)
        if label:
            starts.append((i, label))

    groups: list[tuple[str, list[str]]] = []
    if starts:
        for k, (index, label) in enumerate(starts):
            end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
            body = [ln for ln in lines[index:end] if ln.strip()]
            groups.append((label, body))
    else:
        # Keine Nummerierung: Absaetze als Aufgaben behandeln.
        block: list[str] = []
        for line in lines:
            if line.strip():
                block.append(line)
            elif block:
                groups.append((str(len(groups) + 1), block))
                block = []
        if block:
            groups.append((str(len(groups) + 1), block))

    tasks = []
    for label, body in groups[:limit]:
        joined = "\n".join(body).strip()
        if len(joined) < 4:
            continue
        expr = _candidate_expr(joined)
        result = math_agent.analyse(expr) if expr else {"ok": False,
                                                        "note": "Keine Rechnung erkannt.",
                                                        "title": ""}
        tasks.append({
            "label": label,
            "text": joined[:900],
            "expr": expr,
            "ok": bool(result.get("ok")),
            "title": result.get("title", ""),
            "note": result.get("note", "") if not result.get("ok") else "",
        })

    if not tasks and text.strip():
        expr = _candidate_expr(text)
        result = math_agent.analyse(expr) if expr else {"ok": False,
                                                        "note": "Keine Rechnung erkannt.",
                                                        "title": ""}
        tasks.append({
            "label": "1",
            "text": text.strip()[:900],
            "expr": expr,
            "ok": bool(result.get("ok")),
            "title": result.get("title", ""),
            "note": result.get("note", "") if not result.get("ok") else "",
        })
    return tasks
