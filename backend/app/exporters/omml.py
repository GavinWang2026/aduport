"""LaTeX ↔ OMML helpers for DOCX MathBlock round-trip.

Supports a practical TeX subset: nested frac/sqrt, sub/sup/subsup, n-ary
(sum/int/prod), delimiters, Greek/symbols, and concatenated expressions.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from docx.oxml import OxmlElement

from app.core import xml_safe

_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_MN = "{%s}" % _M

_GREEK = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Alpha": "Α",
    "Beta": "Β",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    "infty": "∞",
    "pm": "±",
    "mp": "∓",
    "times": "×",
    "cdot": "·",
    "div": "÷",
    "leq": "≤",
    "geq": "≥",
    "neq": "≠",
    "approx": "≈",
    "equiv": "≡",
    "sim": "∼",
    "subset": "⊂",
    "supset": "⊃",
    "subseteq": "⊆",
    "supseteq": "⊇",
    "in": "∈",
    "notin": "∉",
    "forall": "∀",
    "exists": "∃",
    "partial": "∂",
    "nabla": "∇",
    "ell": "ℓ",
    "hbar": "ℏ",
    "circ": "∘",
    "bullet": "•",
    "ldots": "…",
    "cdots": "⋯",
}

_NARY = {
    "sum": ("∑", "∑"),
    "prod": ("∏", "∏"),
    "int": ("∫", "∫"),
    "oint": ("∮", "∮"),
    "bigcup": ("⋃", "⋃"),
    "bigcap": ("⋂", "⋂"),
}


def _m(tag: str):
    try:
        return OxmlElement(f"m:{tag}")
    except ValueError:
        return ET.Element(f"{_MN}{tag}")


def _run(text: str):
    r = _m("r")
    t = _m("t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    r.append(t)
    return r


def _arg(tag: str, *children):
    el = _m(tag)
    for ch in children:
        el.append(ch)
    return el


def _append_nodes(parent, nodes: list) -> None:
    for n in nodes:
        parent.append(n)


def _fraction(num_nodes: list, den_nodes: list):
    f = _m("f")
    f.append(_arg("num", *num_nodes))
    f.append(_arg("den", *den_nodes))
    return f


def _ssup(base: list, sup: list):
    s = _m("sSup")
    s.append(_arg("e", *base))
    s.append(_arg("sup", *sup))
    return s


def _ssub(base: list, sub: list):
    s = _m("sSub")
    s.append(_arg("e", *base))
    s.append(_arg("sub", *sub))
    return s


def _ssubsup(base: list, sub: list, sup: list):
    s = _m("sSubSup")
    s.append(_arg("e", *base))
    s.append(_arg("sub", *sub))
    s.append(_arg("sup", *sup))
    return s


def _rad(inner: list, deg: list | None = None):
    r = _m("rad")
    if deg:
        r.append(_arg("deg", *deg))
    else:
        r.append(_m("deg"))
    r.append(_arg("e", *inner))
    return r


def _matrix(rows: list[list[str]], *, left: str = "", right: str = ""):
    """Build OMML m:m (optionally wrapped in delimiter)."""
    ncols = max((len(r) for r in rows), default=1)
    m = _m("m")
    m_pr = _m("mPr")
    mcs = _m("mcs")
    mc = _m("mc")
    mc_pr = _m("mcPr")
    count = _m("count")
    count.set("val", str(ncols))
    mc_jc = _m("mcJc")
    mc_jc.set("val", "center")
    mc_pr.append(count)
    mc_pr.append(mc_jc)
    mc.append(mc_pr)
    mcs.append(mc)
    m_pr.append(mcs)
    m.append(m_pr)
    for row in rows:
        mr = _m("mr")
        for c in range(ncols):
            cell = row[c] if c < len(row) else ""
            mr.append(_arg("e", *_parse_expr(cell)))
        m.append(mr)
    if left or right:
        return _delim(left or "", right or "", [m])
    return m


def _parse_matrix_body(body: str) -> list[list[str]]:
    rows: list[list[str]] = []
    # strip trailing \\ 
    chunk = (body or "").strip()
    if chunk.endswith("\\\\"):
        chunk = chunk[:-2]
    for raw_row in re.split(r"\\\\", chunk):
        row = raw_row.strip()
        if not row and not rows:
            continue
        cells = [c.strip() for c in row.split("&")]
        if cells == [""] and not rows:
            continue
        rows.append(cells)
    return rows or [[""]]


_MATRIX_DELIMS = {
    "matrix": ("", ""),
    "pmatrix": ("(", ")"),
    "bmatrix": ("[", "]"),
    "Bmatrix": ("{", "}"),
    "vmatrix": ("|", "|"),
    "Vmatrix": ("∥", "∥"),
    "cases": ("{", ""),
    "array": ("", ""),
}


def _take_begin_env(s: str, i: int) -> tuple[str, str, int]:
    """After \\begin, read {env} ... \\end{env}. Returns (env, body, new_i)."""
    env, i = _take_brace(s, i)
    env = (env or "").strip()
    end = f"\\end{{{env}}}"
    j = s.find(end, i)
    if j < 0:
        return env, s[i:], len(s)
    body = s[i:j]
    return env, body, j + len(end)


def _nary(chr_: str, sub: list | None, sup: list | None, body: list):
    n = _m("nary")
    props = _m("naryPr")
    ch = _m("chr")
    ch.set("{http://schemas.openxmlformats.org/officeDocument/2006/math}val", chr_)
    # python-docx / ET attribute without ns fallback:
    ch.set("val", chr_)
    props.append(ch)
    n.append(props)
    n.append(_arg("sub", *(sub or [_run("")])))
    n.append(_arg("sup", *(sup or [_run("")])))
    n.append(_arg("e", *body))
    return n


def _delim(left: str, right: str, inner: list):
    d = _m("d")
    props = _m("dPr")
    beg = _m("begChr")
    beg.set("val", left)
    end = _m("endChr")
    end.set("val", right)
    props.append(beg)
    props.append(end)
    d.append(props)
    d.append(_arg("e", *inner))
    return d


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _take_brace(s: str, i: int) -> tuple[str, int]:
    i = _skip_ws(s, i)
    if i >= len(s) or s[i] != "{":
        return "", i
    depth = 0
    j = i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        elif s[j] == "\\" and j + 1 < len(s):
            j += 2
            continue
        j += 1
    return s[i + 1 :], len(s)


def _take_bracket(s: str, i: int) -> tuple[str, int]:
    i = _skip_ws(s, i)
    if i >= len(s) or s[i] != "[":
        return "", i
    j = s.find("]", i + 1)
    if j < 0:
        return "", i
    return s[i + 1 : j], j + 1


def _cmd_name(s: str, i: int) -> tuple[str, int]:
    """Read \\command starting at backslash."""
    assert s[i] == "\\"
    i += 1
    if i >= len(s):
        return "", i
    if not s[i].isalpha():
        return s[i], i + 1
    j = i
    while j < len(s) and s[j].isalpha():
        j += 1
    return s[i:j], j


def _parse_expr(tex: str) -> list:
    """Parse a TeX expression into a list of OMML nodes (concatenation)."""
    s = tex or ""
    i = 0
    out: list = []
    buf: list[str] = []

    def flush_buf() -> None:
        nonlocal buf
        if buf:
            out.append(_run("".join(buf)))
            buf = []

    while i < len(s):
        ch = s[i]
        if ch.isspace():
            buf.append(" ")
            i += 1
            continue
        if ch == "{":
            flush_buf()
            inner, i = _take_brace(s, i)
            out.extend(_parse_expr(inner))
            continue
        if ch == "\\":
            flush_buf()
            name, i = _cmd_name(s, i)
            if name in ("frac",):
                a, i = _take_brace(s, i)
                b, i = _take_brace(s, i)
                out.append(_fraction(_parse_expr(a), _parse_expr(b)))
                continue
            if name == "sqrt":
                deg, i2 = _take_bracket(s, i)
                if deg or (i2 > i and i < len(s) and s[i] == "["):
                    i = i2
                    body, i = _take_brace(s, i)
                    out.append(
                        _rad(_parse_expr(body), _parse_expr(deg) if deg else None)
                    )
                else:
                    body, i = _take_brace(s, i)
                    out.append(_rad(_parse_expr(body)))
                continue
            if name in _NARY:
                chr_, _ = _NARY[name]
                sub = sup = None
                i = _skip_ws(s, i)
                if i < len(s) and s[i] == "_":
                    i += 1
                    if i < len(s) and s[i] == "{":
                        sub_s, i = _take_brace(s, i)
                    else:
                        sub_s, i = (s[i], i + 1) if i < len(s) else ("", i)
                    sub = _parse_expr(sub_s)
                i = _skip_ws(s, i)
                if i < len(s) and s[i] == "^":
                    i += 1
                    if i < len(s) and s[i] == "{":
                        sup_s, i = _take_brace(s, i)
                    else:
                        sup_s, i = (s[i], i + 1) if i < len(s) else ("", i)
                    # also allow ^ before _ (swap)
                    if sub is None:
                        # might be sum^{n}_{i} — handle second
                        pass
                    sup = _parse_expr(sup_s)
                # second optional after first
                i = _skip_ws(s, i)
                if sub is None and i < len(s) and s[i] == "_":
                    i += 1
                    if i < len(s) and s[i] == "{":
                        sub_s, i = _take_brace(s, i)
                    else:
                        sub_s, i = (s[i], i + 1) if i < len(s) else ("", i)
                    sub = _parse_expr(sub_s)
                i = _skip_ws(s, i)
                if i < len(s) and s[i] == "{":
                    body_s, i = _take_brace(s, i)
                    body = _parse_expr(body_s)
                else:
                    body = [_run("")]
                out.append(_nary(chr_, sub, sup, body))
                continue
            if name in ("left", "right"):
                # \left( ... \right)
                if name == "left":
                    i = _skip_ws(s, i)
                    left = s[i] if i < len(s) else "("
                    if left == "\\":
                        ln, i = _cmd_name(s, i)
                        left = {"|": "|", "Vert": "∥", "langle": "⟨"}.get(ln, ln or "(")
                    else:
                        i += 1
                        if left == ".":
                            left = ""
                    # find matching \right
                    depth = 1
                    j = i
                    while j < len(s):
                        if s[j] == "\\" and s.startswith("\\left", j):
                            depth += 1
                            j += 5
                            continue
                        if s[j] == "\\" and s.startswith("\\right", j):
                            depth -= 1
                            if depth == 0:
                                inner = s[i:j]
                                j += 6  # \right
                                j = _skip_ws(s, j)
                                right = s[j] if j < len(s) else ")"
                                if right == "\\":
                                    rn, j = _cmd_name(s, j)
                                    right = {"|": "|", "Vert": "∥", "rangle": "⟩"}.get(
                                        rn, rn or ")"
                                    )
                                else:
                                    j += 1
                                    if right == ".":
                                        right = ""
                                out.append(
                                    _delim(left or "(", right or ")", _parse_expr(inner))
                                )
                                i = j
                                break
                            j += 6
                            continue
                        j += 1
                    else:
                        out.append(_run(left or "("))
                    continue
                # lone \right — ignore
                i = _skip_ws(s, i)
                if i < len(s):
                    if s[i] == "\\":
                        _, i = _cmd_name(s, i)
                    else:
                        i += 1
                continue
            if name in ("text", "mathrm", "operatorname", "textbf", "textit"):
                body, i = _take_brace(s, i)
                out.append(_run(body))
                continue
            if name == "begin":
                env, body, i = _take_begin_env(s, i)
                # optional {cols} for array
                i2 = _skip_ws(s, i)
                if env == "array" and i2 < len(s) and s.startswith("{", i2):
                    # body already consumed env content; array colspec is inside begin... 
                    pass
                # Re-parse: \begin{array}{cc} a&b\\c&d \end{array}
                # Our _take_begin_env got everything between begin{array} and end;
                # leading {cc} may be in body.
                body_work = body
                if env == "array":
                    body_work = body_work.lstrip()
                    if body_work.startswith("{"):
                        _, rest_i = _take_brace(body_work, 0)
                        body_work = body_work[rest_i:]
                if env in _MATRIX_DELIMS or env == "array":
                    left, right = _MATRIX_DELIMS.get(env, ("", ""))
                    rows = _parse_matrix_body(body_work)
                    out.append(_matrix(rows, left=left, right=right))
                    continue
                # unknown env → flatten as text
                out.extend(_parse_expr(body_work))
                continue
            if name == "end":
                # stray \end{...}
                _, i = _take_brace(s, i)
                continue
            if name in (",", ";", "!", " ", "quad", "qquad"):
                out.append(_run(" "))
                continue
            if name in _GREEK:
                out.append(_run(_GREEK[name]))
                continue
            # unknown command → keep as text
            out.append(_run("\\" + name))
            continue

        # superscript / subscript on previous atom
        if ch in "^_":
            # Literal underscore/caret unless TeX script form: x^{2} / x_i / x_{i}
            nxt = s[i + 1] if i + 1 < len(s) else ""
            braced = nxt == "{"
            single = bool(nxt) and (nxt.isalnum() or nxt in ("+", "-", "*"))
            if not braced and not single:
                buf.append(ch)
                i += 1
                continue
            flush_buf()
            if not out:
                buf.append(ch)
                i += 1
                continue
            kind = ch
            i += 1
            if braced:
                arg, i = _take_brace(s, i)
            else:
                arg, i = nxt, i + 1
            script = _parse_expr(arg)
            last = out.pop()
            # Peel last character from a multi-char run (TeX: mc^2 → m + c^{2})
            base_nodes: list
            try:
                tag = last.tag.split("}")[-1] if hasattr(last, "tag") else ""
            except Exception:
                tag = ""
            if tag == "r":
                text = ""
                for c in list(last):
                    ct = c.tag.split("}")[-1] if hasattr(c, "tag") else ""
                    if ct == "t":
                        text += c.text or ""
                if len(text) > 1:
                    out.append(_run(text[:-1]))
                    base_nodes = [_run(text[-1])]
                else:
                    base_nodes = [last]
            else:
                base_nodes = [last]
            # peek for the other script (x_{i}^{2})
            i2 = _skip_ws(s, i)
            other = None
            other_nodes = None
            if i2 < len(s) and s[i2] in "^_" and s[i2] != kind:
                other = s[i2]
                i = i2 + 1
                if i < len(s) and s[i] == "{":
                    arg2, i = _take_brace(s, i)
                elif i < len(s) and (s[i].isalnum() or s[i] in ("+", "-", "*")):
                    arg2, i = s[i], i + 1
                else:
                    # other marker was literal — put back
                    i = i2
                    other = None
                    arg2 = ""
                if other:
                    other_nodes = _parse_expr(arg2)
            if other and other_nodes is not None:
                if kind == "_":
                    out.append(_ssubsup(base_nodes, script, other_nodes))
                else:
                    out.append(_ssubsup(base_nodes, other_nodes, script))
            elif kind == "^":
                out.append(_ssup(base_nodes, script))
            else:
                out.append(_ssub(base_nodes, script))
            continue

        # plain slash fraction a/b when both sides simple? keep as char for generality
        buf.append(ch)
        i += 1

    flush_buf()
    return out or [_run("")]


def _inline_nodes(tex: str) -> list:
    s = tex or ""
    stripped = s.strip()
    # Multi-segment identifiers (SENT_MATH_Hotel) are literal — not TeX scripts.
    # Single-letter segments (x_i, a_b) still go through the script parser.
    if stripped and "\\" not in stripped and "{" not in stripped and "^" not in stripped:
        if "_" in stripped and re.fullmatch(r"[A-Za-z0-9_]+", stripped):
            parts = stripped.split("_")
            if len(parts) >= 2 and all(len(p) > 1 for p in parts):
                return [_run(stripped)]
    return _parse_expr(tex)


def build_omath(tex: str, *, display: bool = True):
    """Return an ``m:oMath`` or ``m:oMathPara`` element for ``tex``."""
    omath = _m("oMath")
    for node in _inline_nodes(tex):
        omath.append(node)
    if display:
        para = _m("oMathPara")
        para.append(omath)
        return para
    return omath


def append_math_to_paragraph(paragraph, tex: str, *, display: bool = True) -> None:
    """Attach OMML under a python-docx paragraph."""
    el = build_omath(tex, display=display)
    paragraph._p.append(el)


def omml_element_to_tex(el: ET.Element) -> str:
    """Best-effort OMML → TeX."""
    tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
    if tag == "t":
        return el.text or ""
    if tag == "r":
        return "".join(omml_element_to_tex(c) for c in list(el))
    if tag == "f":
        num = den = ""
        for c in list(el):
            ct = c.tag.split("}")[-1]
            if ct == "num":
                num = omml_element_to_tex(c)
            elif ct == "den":
                den = omml_element_to_tex(c)
        return f"\\frac{{{num}}}{{{den}}}"
    if tag == "sSup":
        base = sup = ""
        for c in list(el):
            ct = c.tag.split("}")[-1]
            if ct == "e":
                base = omml_element_to_tex(c)
            elif ct == "sup":
                sup = omml_element_to_tex(c)
        return f"{base}^{{{sup}}}"
    if tag == "sSub":
        base = sub = ""
        for c in list(el):
            ct = c.tag.split("}")[-1]
            if ct == "e":
                base = omml_element_to_tex(c)
            elif ct == "sub":
                sub = omml_element_to_tex(c)
        return f"{base}_{{{sub}}}"
    if tag == "sSubSup":
        base = sub = sup = ""
        for c in list(el):
            ct = c.tag.split("}")[-1]
            if ct == "e":
                base = omml_element_to_tex(c)
            elif ct == "sub":
                sub = omml_element_to_tex(c)
            elif ct == "sup":
                sup = omml_element_to_tex(c)
        return f"{base}_{{{sub}}}^{{{sup}}}"
    if tag == "rad":
        deg = ""
        body = ""
        for c in list(el):
            ct = c.tag.split("}")[-1]
            if ct == "deg":
                deg = omml_element_to_tex(c)
            elif ct == "e":
                body = omml_element_to_tex(c)
        if deg:
            return f"\\sqrt[{deg}]{{{body}}}"
        return f"\\sqrt{{{body}}}"
    if tag == "nary":
        chr_ = "∑"
        sub = sup = body = ""
        for c in list(el):
            ct = c.tag.split("}")[-1]
            if ct == "naryPr":
                for p in list(c):
                    if p.tag.split("}")[-1] == "chr":
                        chr_ = p.get("val") or p.get(
                            "{http://schemas.openxmlformats.org/officeDocument/2006/math}val"
                        ) or chr_
            elif ct == "sub":
                sub = omml_element_to_tex(c)
            elif ct == "sup":
                sup = omml_element_to_tex(c)
            elif ct == "e":
                body = omml_element_to_tex(c)
        cmd = {"∑": "sum", "∏": "prod", "∫": "int", "∮": "oint"}.get(chr_, "sum")
        bits = f"\\{cmd}"
        if sub:
            bits += f"_{{{sub}}}"
        if sup:
            bits += f"^{{{sup}}}"
        bits += f"{{{body}}}"
        return bits
    if tag == "d":
        left, right, body = "(", ")", ""
        for c in list(el):
            ct = c.tag.split("}")[-1]
            if ct == "dPr":
                for p in list(c):
                    pt = p.tag.split("}")[-1]
                    if pt == "begChr":
                        v = p.get("val")
                        if v is not None:
                            left = v
                    elif pt == "endChr":
                        v = p.get("val")
                        if v is not None:
                            right = v
            elif ct == "e":
                body = omml_element_to_tex(c)
        # matrix inside delimiter → pmatrix/bmatrix/cases
        inner_el = None
        for c in list(el):
            if (c.tag.split("}")[-1] if "}" in c.tag else c.tag) == "e":
                kids = list(c)
                if len(kids) == 1 and (kids[0].tag.split("}")[-1] == "m"):
                    inner_el = kids[0]
                    break
        if inner_el is not None:
            mat = omml_element_to_tex(inner_el)
            if left == "{" and not right:
                return mat.replace("\\begin{matrix}", "\\begin{cases}", 1).replace(
                    "\\end{matrix}", "\\end{cases}", 1
                )
            env = {
                ("(", ")"): "pmatrix",
                ("[", "]"): "bmatrix",
                ("{", "}"): "Bmatrix",
                ("|", "|"): "vmatrix",
            }.get((left, right), "matrix")
            return mat.replace("\\begin{matrix}", f"\\begin{{{env}}}", 1).replace(
                "\\end{matrix}", f"\\end{{{env}}}", 1
            )
        return f"\\left{left}{body}\\right{right}"
    if tag == "m":
        rows_tex: list[str] = []
        for c in list(el):
            ct = c.tag.split("}")[-1]
            if ct != "mr":
                continue
            cells = []
            for e in list(c):
                if e.tag.split("}")[-1] == "e":
                    cells.append(omml_element_to_tex(e))
            rows_tex.append(" & ".join(cells))
        body = " \\\\ ".join(rows_tex)
        return f"\\begin{{matrix}}{body}\\end{{matrix}}"
    if tag in ("oMath", "oMathPara", "e", "num", "den", "sup", "sub", "deg", "naryPr", "mPr", "mr"):
        return "".join(omml_element_to_tex(c) for c in list(el))
    return "".join(omml_element_to_tex(c) for c in list(el))


def extract_math_blocks_from_document_xml(xml_bytes: bytes) -> list[tuple[str, bool]]:
    """Return list of (tex, display) from document.xml OMML."""
    try:
        root = xml_safe.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    except Exception:
        return []
    out: list[tuple[str, bool]] = []
    seen = set()
    for para in root.iter(f"{_MN}oMathPara"):
        tex = omml_element_to_tex(para).strip()
        if tex and id(para) not in seen:
            out.append((tex, True))
            seen.add(id(para))
            for child in para.iter(f"{_MN}oMath"):
                seen.add(id(child))
    for math in root.iter(f"{_MN}oMath"):
        if id(math) in seen:
            continue
        tex = omml_element_to_tex(math).strip()
        if tex:
            out.append((tex, False))
    return out[:200]
