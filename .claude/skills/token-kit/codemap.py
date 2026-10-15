#!/usr/bin/env python3
"""Code map (token-kit): a short generated index of a repository, so a session finds code without reading whole files.

    python3 tools/codemap.py                 write docs/codemap/INDEX.md + one map per area (gitignored, regenerate freely)
    python3 tools/codemap.py find <regex>    every symbol whose name matches, with file and line range
    python3 tools/codemap.py outline <file>  one file's symbols (local ones too) with line ranges and its tests

Each symbol line reads `kind name(params)  L<start>-<end>  <first line of its doc>`: read only that range
(Read offset=start, limit=end-start+1). Each file line names the tests that import it.

Languages: Python (ast, exact), TypeScript / JavaScript (the TypeScript parser when the repository has `typescript` in a
node_modules, else a brace-matching fallback), Dart (brace matching). Standard library only.
Optional `.codemap.json` at the repository root: {"exclude": [glob, ...], "include": [glob, ...]}.
Source: https://github.com/rotem-ya/claude-guides/tree/main/token-kit (do not edit the copy in a project; update it there).
"""
from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION = "1.1"
ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or ".").resolve()
OUT = Path(os.environ.get("CODEMAP_OUT") or ROOT / "docs" / "codemap").resolve()
HERE = Path(__file__).resolve().parent

AREA_LIMIT = 24000  # characters per area map (about 6k tokens); a bigger directory is split by sub-directory, then in parts
LOCALS_FROM = 300  # files at least this long also list their non-exported functions...
LOCAL_MIN_LINES = 8  # ...when they are at least this long
TESTS_MAX = 4
SIG_MAX = 100
NOTE_MAX = 90

CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".dart"}
TS_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
SKIP_DIRS = {
    "node_modules", "dist", "build", "out", "static", "ui_dist", "vendor", "third_party", ".venv", "venv", "__pycache__",
    "migrations", "versions", "coverage", ".dart_tool", "generated", "public", "assets", "fixtures",
}
FLUTTER_PLATFORM_DIRS = {"android", "ios", "linux", "macos", "windows", "web"}  # generated runner code in a Flutter app
TEST_DIRS = {"tests", "test", "__tests__", "integration_test", "e2e"}


def cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def is_test(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (
        any(p in TEST_DIRS for p in parts[:-1])
        or name.startswith("test_")
        or re.search(r"(_test\.(py|dart)|\.(test|spec)\.[cm]?[jt]sx?)$", name) is not None
        or name == "conftest.py"
    )


def list_files() -> tuple[list[str], list[str]]:
    """(source files, test files), repository-relative, from git (so .gitignore holds)."""
    res = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True)
    cfg = {}
    if (ROOT / ".codemap.json").exists():
        cfg = json.loads((ROOT / ".codemap.json").read_text(encoding="utf-8"))
    inc, exc = cfg.get("include", []), cfg.get("exclude", [])
    src, tests = [], []
    skip = SKIP_DIRS | (FLUTTER_PLATFORM_DIRS if (ROOT / "pubspec.yaml").exists() else set())
    for rel in sorted(set(res.stdout.splitlines())):
        p = Path(rel)
        if p.suffix not in CODE_EXT or rel.endswith(".d.ts") or ".min." in p.name or not (ROOT / rel).is_file():
            continue
        if any(fnmatch.fnmatch(rel, g) for g in exc) or rel in ("tools/codemap.py", "tools/codemap_ts.cjs"):
            continue
        forced = any(fnmatch.fnmatch(rel, g) for g in inc)
        if is_test(rel):
            tests.append(rel)
        elif forced or not any(part in skip or part.startswith(".") for part in p.parts[:-1]):
            src.append(rel)
    return src, tests


# ---------- Python ----------
def py_doc_line(node) -> str:
    doc = ast.get_docstring(node, clean=True) if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else None
    if not doc:
        return ""
    return cut(next((l.strip() for l in doc.splitlines() if l.strip()), ""), NOTE_MAX)


def py_params(fn) -> str:
    a = fn.args
    names = [x.arg for x in a.posonlyargs + a.args if x.arg not in ("self", "cls")]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return "(" + cut(", ".join(names), SIG_MAX) + ")"


def py_start(node) -> int:
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])


def py_symbols(text: str) -> dict:
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return {"syms": [], "header": f"(parse error: line {e.lineno})", "lines": text.count("\n") + 1}
    syms = []
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            syms.append(dict(kind="fn", name=st.name, sig=py_params(st), start=py_start(st), end=st.end_lineno,
                             note=py_doc_line(st), exported=not st.name.startswith("_")))
        elif isinstance(st, ast.ClassDef):
            members = []
            for m in st.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and (not m.name.startswith("_") or m.name == "__init__"):
                    members.append(dict(name=m.name, sig=py_params(m), start=py_start(m), end=m.end_lineno))
            bases = ", ".join(ast.unparse(b) for b in st.bases)
            syms.append(dict(kind="class", name=st.name, sig=f"({cut(bases, 60)})" if bases else "", start=py_start(st),
                             end=st.end_lineno, note=py_doc_line(st), exported=not st.name.startswith("_"), members=members))
        elif isinstance(st, (ast.Assign, ast.AnnAssign)):
            targets = st.targets if isinstance(st, ast.Assign) else [st.target]
            for t in targets:
                if isinstance(t, ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]+", t.id):
                    syms.append(dict(kind="const", name=t.id, sig="", start=st.lineno, end=st.end_lineno, note="", exported=True))
    return {"syms": syms, "header": py_doc_line(tree), "lines": text.count("\n") + 1}


# ---------- brace languages (fallback TS/JS, Dart) ----------
def block_end(lines: list[str], i: int) -> int:
    """1-based end line of the declaration that starts at 0-based line i: matching brace, else the first `;`."""
    depth, opened, in_str, block_comment = 0, False, None, False
    for j in range(i, min(len(lines), i + 5000)):
        s = lines[j]
        k = 0
        while k < len(s):
            c = s[k]
            if block_comment:
                if s.startswith("*/", k):
                    block_comment, k = False, k + 1
            elif in_str:
                if c == "\\":
                    k += 1
                elif c == in_str:
                    in_str = None
            elif s.startswith("//", k):
                break
            elif s.startswith("/*", k):
                block_comment = True
            elif c in "'\"`":
                in_str = c
            elif c in "{(":
                depth, opened = depth + 1, opened or c == "{"
            elif c in "})":
                depth -= 1
                if depth == 0 and opened:
                    return j + 1
            elif c == ";" and depth == 0 and not opened:
                return j + 1
            k += 1
        if in_str in ("'", '"'):
            in_str = None
    return i + 1


def comment_above(lines: list[str], i: int) -> str:
    j = i - 1
    while j >= 0 and re.match(r"\s*@\w", lines[j]):  # decorators / annotations
        j -= 1
    block = []
    while j >= 0 and re.match(r"\s*(///?|\*|/\*\*)", lines[j]):
        block.insert(0, lines[j])
        j -= 1
    for l in block:
        t = re.sub(r"^\s*(/\*\*?|\*/?|///?)\s?", "", l).strip().rstrip("*/").strip()
        if t and not t.startswith("@"):
            return cut(t, NOTE_MAX)
    return ""


TS_DECL = re.compile(
    r"^(export\s+)?(default\s+)?(declare\s+)?(abstract\s+)?(async\s+)?"
    r"(function\*?|class|interface|type|enum|const|let|var)\s+([A-Za-z_$][\w$]*)"
)
DART_DECL = re.compile(r"^(abstract\s+|sealed\s+|final\s+|base\s+)*(class|mixin|enum|extension|typedef)\s+([A-Za-z_]\w*)")
DART_FN = re.compile(r"^(?:[\w<>?,\[\] ]+\s+)?([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\(([^)]*)\)?\s*(async\*?|sync\*)?\s*(\{|=>)")
DART_MEMBER = re.compile(r"^  (?:static\s+|@override\s+)?(?:[\w<>?,\[\]][\w<>?,\[\] ]*\s+)?([A-Za-z_]\w*)\s*\(([^)]*)\)?\s*(async\*?)?\s*(\{|=>)")


def first_params(text: str) -> str:
    m = re.search(r"\(([^()]*)\)", text)
    if not m:
        return "()"
    names = [re.split(r"[:=?]", re.sub(r"[{}\[\]]", "", p).strip())[0].split()[-1:] for p in m.group(1).split(",") if p.strip()]
    return "(" + cut(", ".join(n[0] for n in names if n), SIG_MAX) + ")"


def ts_fallback_symbols(text: str) -> dict:
    lines = text.split("\n")
    syms = []
    for i, l in enumerate(lines):
        m = TS_DECL.match(l)
        if not m:
            continue
        kind_word, name = m.group(6), m.group(7)
        exported = bool(m.group(1))
        end = block_end(lines, i)
        if kind_word.startswith("function"):
            syms.append(dict(kind="fn", name=name, sig=first_params(l), start=i + 1, end=end, note=comment_above(lines, i), exported=exported))
        elif kind_word in ("const", "let", "var"):
            is_fn = re.search(r"=\s*(async\s*)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*(:\s*[^=]+)?=>|=\s*(async\s+)?function", l)
            if is_fn:
                syms.append(dict(kind="fn", name=name, sig=first_params(l[l.index("="):]), start=i + 1, end=end, note=comment_above(lines, i), exported=exported))
            elif exported:
                syms.append(dict(kind="const", name=name, sig="", start=i + 1, end=end, note=comment_above(lines, i), exported=True))
        else:
            syms.append(dict(kind=kind_word, name=name, sig="", start=i + 1, end=end, note=comment_above(lines, i), exported=exported))
    head = comment_above(lines, next((i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith(("/", "*", "#!"))), 0))
    return {"syms": syms, "header": head, "lines": len(lines)}


def dart_symbols(text: str) -> dict:
    lines = text.split("\n")
    syms = []
    i = 0
    while i < len(lines):
        l = lines[i]
        m = DART_DECL.match(l)
        if m:
            end = block_end(lines, i)
            members = []
            if m.group(2) in ("class", "mixin", "enum", "extension"):
                for j in range(i + 1, end - 1):
                    mm = DART_MEMBER.match(lines[j])
                    if mm and mm.group(1) not in ("if", "for", "while", "switch", "return") and not mm.group(1).startswith("_"):
                        members.append(dict(name=mm.group(1), sig=first_params(lines[j]), start=j + 1, end=block_end(lines, j)))
            syms.append(dict(kind=m.group(2), name=m.group(3), sig="", start=i + 1, end=end, note=comment_above(lines, i),
                             exported=not m.group(3).startswith("_"), members=members))
            i = end
            continue
        f = DART_FN.match(l) if not l.startswith((" ", "\t", "import", "export", "part", "library", "}")) else None
        if f and f.group(1) not in ("if", "for", "while", "switch", "main") or (f and f.group(1) == "main"):
            end = block_end(lines, i)
            syms.append(dict(kind="fn", name=f.group(1), sig=first_params(l), start=i + 1, end=end, note=comment_above(lines, i),
                             exported=not f.group(1).startswith("_")))
            i = end
            continue
        i += 1
    head = comment_above(lines, next((i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith("/")), 0))
    return {"syms": syms, "header": head, "lines": len(lines)}


# ---------- TypeScript through the real parser ----------
def find_typescript() -> str | None:
    if not shutil.which("node"):
        return None
    cands = []
    for pkg in subprocess.run(["git", "ls-files", "*package.json"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines():
        cands.append(ROOT / Path(pkg).parent / "node_modules" / "typescript")
    cands.append(ROOT / "node_modules" / "typescript")
    for c in cands:
        if (c / "package.json").exists():
            return str(c)
    return None


def ts_symbols_all(files: list[str]) -> dict:
    tsdir = find_typescript()
    if tsdir and files:
        res = subprocess.run(["node", str(HERE / "codemap_ts.cjs"), tsdir], input=json.dumps([str(ROOT / f) for f in files]),
                             capture_output=True, text=True)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            return {f: data[str(ROOT / f)] for f in files}
        print(f"codemap: TypeScript parser failed, using the fallback ({cut(res.stderr.strip(), 120)})", file=sys.stderr)
    return {f: ts_fallback_symbols((ROOT / f).read_text(encoding="utf-8", errors="replace")) for f in files}


def symbols(files: list[str]) -> dict:
    out = ts_symbols_all([f for f in files if Path(f).suffix in TS_EXT])
    for f in files:
        suf = Path(f).suffix
        if suf == ".py":
            out[f] = py_symbols((ROOT / f).read_text(encoding="utf-8", errors="replace"))
        elif suf == ".dart":
            out[f] = dart_symbols((ROOT / f).read_text(encoding="utf-8", errors="replace"))
    return out


# ---------- tests that import each file ----------
def test_index(src: list[str], tests: list[str]) -> dict[str, set[str]]:
    idx: dict[str, set[str]] = {}
    srcset = set(src)
    by_module: dict[str, str] = {}  # dotted / package path suffixes → file
    for f in src:
        stem = f.rsplit(".", 1)[0]
        if stem.endswith("/__init__"):
            stem = stem[: -len("/__init__")]
        parts = stem.split("/")
        for k in range(1, len(parts) + 1):
            by_module.setdefault(".".join(parts[-k:]), f)
            by_module.setdefault("/".join(parts[-k:]), f)
    for t in tests:
        text = (ROOT / t).read_text(encoding="utf-8", errors="replace")
        hits = set()
        if t.endswith(".py"):
            for m in re.finditer(r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w, ()\n]+?)$|import\s+([\w.]+))", text, re.M):
                mod = m.group(1) or m.group(3)
                for cand in [mod] + [f"{mod}.{n.strip()}" for n in (m.group(2) or "").replace("(", "").replace(")", "").split(",") if n.strip()]:
                    if cand.count(".") >= 1 and cand in by_module:
                        hits.add(by_module[cand])
        else:
            for m in re.finditer(r"(?:from|import)\s*\(?\s*['\"]([^'\"]+)['\"]", text):
                spec = m.group(1)
                if spec.startswith("."):
                    base = os.path.normpath(os.path.join(os.path.dirname(t), spec)).replace(os.sep, "/")
                    for c in (base, *(base + e for e in (".ts", ".tsx", ".js", ".jsx", ".dart")), base + "/index.ts", base + "/index.tsx"):
                        if c in srcset:
                            hits.add(c)
                            break
                elif spec.startswith("package:"):
                    tail = spec.split("/", 1)[-1]
                    hit = by_module.get(tail.rsplit(".", 1)[0])
                    if hit:
                        hits.add(hit)
                elif spec.startswith("@/"):
                    hit = by_module.get(spec[2:])
                    if hit:
                        hits.add(hit)
        for h in hits:
            idx.setdefault(h, set()).add(Path(t).name)
    return idx


# ---------- rendering ----------
def sym_line(s: dict) -> str:
    loc = f"L{s['start']}" if s["start"] == s["end"] else f"L{s['start']}-{s['end']}"
    local = "" if s["exported"] else " (local)"
    out = f"- {s['kind']} {s['name']}{s['sig']}{local}  {loc}" + (f"  {s['note']}" if s.get("note") else "")
    for m in s.get("members") or []:
        out += f"\n  - {m['name']}{m['sig']}  L{m['start']}-{m['end']}"
    return out


def tests_text(f: str, tests: set[str] | None, all_: bool = False) -> str:
    if not tests:
        return ""
    stem = Path(f).stem.lower()
    ordered = sorted(tests, key=lambda t: (stem not in t.lower(), len(t), t))
    if all_:
        return " · tests: " + ", ".join(sorted(tests))
    more = f" +{len(ordered) - TESTS_MAX} more" if len(ordered) > TESTS_MAX else ""
    return " · tests: " + ", ".join(ordered[:TESTS_MAX]) + more


def file_block(f: str, info: dict, tests, with_locals: bool = False) -> str:
    big = info["lines"] >= LOCALS_FROM
    shown = [s for s in info["syms"] if s["exported"] or with_locals or (big and s["kind"] == "fn" and s["end"] - s["start"] + 1 >= LOCAL_MIN_LINES)]
    parts = [f"### {f} · {info['lines']} lines{tests_text(f, tests, with_locals)}"]
    if info.get("header"):
        parts.append(info["header"])
    parts += [sym_line(s) for s in shown]
    return "\n".join(parts)


def areas(src: list[str], blocks: dict[str, str]) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []

    def size(fs):
        return sum(len(blocks[f]) + 2 for f in fs)

    def push_parts(name, own):
        parts, n = [[]], 0
        for f in sorted(own):
            b = len(blocks[f]) + 2
            if n + b > AREA_LIMIT and parts[-1]:
                parts.append([])
                n = 0
            parts[-1].append(f)
            n += b
        for i, p in enumerate(parts):
            out.append((f"{name}~{i + 1}" if len(parts) > 1 else name, p))

    def split(d: str, fs: list[str]):
        name = d or "(root)"
        if size(fs) <= AREA_LIMIT:
            out.append((name, fs))
            return
        own = [f for f in fs if os.path.dirname(f) == d]
        if own:
            push_parts(name, own)
        subs: dict[str, list[str]] = {}
        for f in fs:
            fd = os.path.dirname(f)
            if fd == d:
                continue
            rest = fd[len(d) + 1:] if d else fd
            sub = (d + "/" if d else "") + rest.split("/")[0]
            subs.setdefault(sub, []).append(f)
        for sub in sorted(subs):
            split(sub, subs[sub])

    split("", src)
    return out


def map_name(area: str) -> str:
    return area.replace("/", ".").replace("(root)", "root") + ".md"


def write() -> None:
    src, tests = list_files()
    info = symbols(src)
    tix = test_index(src, tests)
    blocks = {f: file_block(f, info[f], tix.get(f)) for f in src}
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    rows, total = [], 0
    for name, fs in areas(src, blocks):
        lines = sum(info[f]["lines"] for f in fs)
        body = "\n\n".join(blocks[f] for f in sorted(fs))
        head = f"# {name}: {len(fs)} files, {lines} lines\nGenerated by token-kit codemap {VERSION}; do not edit. `L<start>-<end>`: Read(offset=start, limit=end-start+1).\n\n"
        (OUT / map_name(name)).write_text(head + body + "\n", encoding="utf-8")
        total += len(head) + len(body)
        init = next((f for f in fs if Path(f).name in ("__init__.py", "index.ts", "index.tsx", "main.py", "main.dart") and info[f].get("header")), None)
        top = sorted(((s["end"] - s["start"], s["name"]) for f in fs for s in info[f]["syms"] if s["exported"] and s["kind"] != "re-export"), reverse=True)
        about = info[init]["header"] if init else ", ".join(n for _, n in top[:6])
        rows.append(f"| {name} | {len(fs)} | {lines} | {cut(about, 110)} | {map_name(name)} |")
    index = "\n".join([
        "# Code map: index",
        f"Generated by token-kit codemap {VERSION} (`python3 tools/codemap.py`; gitignored, the SessionStart hook regenerates it). Do not edit.",
        "Find code: this table → the area map (every function, class, type with its line range) → read only that range.",
        "Or: `python3 tools/codemap.py find <regex>` / `outline <file>`. A file's `tests:` are the tests that import it.",
        "",
        "| area | files | lines | about | map |",
        "|---|---|---|---|---|",
        *rows,
        "",
    ])
    (OUT / "INDEX.md").write_text(index, encoding="utf-8")
    print(f"codemap: {len(src)} files ({len(tests)} tests), {len(rows)} areas, {round(total / 1024)} KB of maps, index {round(len(index) / 1024)} KB → {os.path.relpath(OUT, ROOT)}")


def find(rx: str) -> None:
    r = re.compile(rx, re.I)
    src, _ = list_files()
    info = symbols(src)
    hits = []
    for f in src:
        for s in info[f]["syms"]:
            if r.search(s["name"]):
                hits.append(f"{f}:{s['start']}-{s['end']}  {s['kind']} {s['name']}{s['sig']}{'' if s['exported'] else ' (local)'}")
            for m in s.get("members") or []:
                if r.search(m["name"]):
                    hits.append(f"{f}:{m['start']}-{m['end']}  method {s['name']}.{m['name']}{m['sig']}")
    for h in hits[:60]:
        print(h)
    if len(hits) > 60:
        print(f"… {len(hits) - 60} more; narrow the pattern")
    if not hits:
        print("no symbol matches")


def outline(path: str) -> None:
    rel = os.path.relpath(Path(path).resolve(), ROOT).replace(os.sep, "/")
    src, tests = list_files()
    if rel not in src:
        print(f"not a mapped source file: {rel}", file=sys.stderr)
        sys.exit(1)
    info = symbols([rel])
    print(file_block(rel, info[rel], test_index(src, tests).get(rel), with_locals=True))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        write()
    elif a[0] == "find" and len(a) == 2:
        find(a[1])
    elif a[0] == "outline" and len(a) == 2:
        outline(a[1])
    else:
        print(__doc__.split("\n\n")[1])
        sys.exit(2)
