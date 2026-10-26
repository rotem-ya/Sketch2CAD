# -*- coding: utf-8 -*-
"""Sketch2CAD - local UI (Streamlit). Run:  streamlit run app/app.py"""
from __future__ import annotations

import html
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__" and "streamlit" not in sys.modules:
    # Started as a plain script (double-click on app.py, "python app.py"): that runs no web server, so the
    # browser shows "connection refused". Start the real app with the program's own Python instead.
    import subprocess
    _root = Path(__file__).resolve().parents[1]
    _script = str(Path(__file__).resolve())
    _cmd = ["-m", "streamlit", "run", _script, "--server.port", "8517", "--server.headless", "true"]
    if os.name == "nt":
        _py = _root / ".venv" / "Scripts" / "pythonw.exe"
        if not _py.exists():
            _py = _root / ".venv" / "Scripts" / "python.exe"
        _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(subprocess, "DETACHED_PROCESS", 8)
        subprocess.Popen([str(_py if _py.exists() else sys.executable), *_cmd], cwd=_root,
                         creationflags=_flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
        sys.exit(0)
    _venv = _root / ".venv" / "bin" / "python"
    sys.exit(subprocess.call([str(_venv if _venv.exists() else sys.executable), *_cmd], cwd=_root))

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sketch2cad import bom, codes, search, typical  # noqa: E402
from sketch2cad.catalog import CATEGORIES, Catalog, load_catalog, save_item  # noqa: E402
from sketch2cad.checks import run_checks  # noqa: E402
from sketch2cad.config import Settings, app_home  # noqa: E402
from sketch2cad.documents import (DOC_TYPES, STATUSES, Document, add_source, create_document,  # noqa: E402
                                  list_documents, new_revision)
from sketch2cad.i18n import notice as ui_notice, t  # noqa: E402
from sketch2cad.projects import Project, create_project, is_project_folder, relocate_project  # noqa: E402

ICON = Path(__file__).with_name("icon.png")
st.set_page_config(page_title="Sketch2CAD", page_icon=str(ICON) if ICON.exists() else None, layout="wide")

if os.name == "nt":
    from sketch2cad.native import disable_console_quick_edit  # noqa: E402
    disable_console_quick_edit()          # a click in the black window must not freeze the app

settings = Settings.load()
ss = st.session_state
ss.setdefault("page", "projects")
ss.setdefault("project_folder", None)
ss.setdefault("doc_folder", None)
L = settings.ui_language


def T(key: str) -> str:
    return t(key, L)


def _theme_css() -> None:
    """Local theme only: no fonts or images from the network."""
    st.markdown("""<style>
    .block-container { padding-top: 1.15rem; padding-bottom: 2.4rem; max-width: 1120px; }
    [data-testid="stSidebar"] { border-inline-end: 1px solid #DDD4C6; }
    h1, h2, h3 { font-weight: 650; letter-spacing: 0; }
    [data-testid="stCaptionContainer"] p { color: #3D4C5C; }
    div.stButton > button { border-radius: 8px; min-height: 2.35rem; }
    [data-testid="stImage"] img { border: 1px solid #DDD4C6; border-radius: 8px; background: #fff; }
    .s2c-kicker { font-size: 0.78rem; color: #5D6D7E; margin: 0 0 0.35rem; }
    .s2c-badge {
      display: inline-block; border-radius: 999px; padding: 0.16rem 0.7rem;
      font-weight: 650; margin: 0 0 0.15rem;
    }
    .s2c-idle { background: #E5E8EB; color: #1C2833; }
    .s2c-wait { background: #FDEBD0; color: #6E4B00; }
    .s2c-ready { background: #D6EAF8; color: #1A5276; }
    .s2c-done { background: #D5F5E3; color: #0E6655; }
    .s2c-fail { background: #FADBD8; color: #7B241C; }
    .s2c-q, .s2c-sev-error, .s2c-sev-warning, .s2c-sev-info {
      border-inline-start: 4px solid #B7950B; background: #FEF9E7;
      padding: 0.4rem 0.75rem; margin: 0.28rem 0; border-radius: 6px;
    }
    .s2c-sev-error { border-color: #C0392B; background: #FDEDEC; }
    .s2c-sev-warning { border-color: #D68910; background: #FEF5E7; }
    .s2c-sev-info { border-color: #2471A3; background: #EAF2F8; }
    </style>""", unsafe_allow_html=True)
    if L == "he":
        st.markdown("""<style>
        .stApp, .stMarkdown, [data-testid="stSidebar"], [data-testid="stForm"], .stTabs,
        [data-testid="stDialog"], label, p, h1, h2, h3, li
        { direction: rtl; text-align: right; }
        [data-testid="stDataFrame"], code { direction: ltr; text-align: left; }
        input, textarea { unicode-bidi: plaintext; text-align: start; }
        </style>""", unsafe_allow_html=True)


_theme_css()


def go(page: str, project: str | None = None, doc: str | None = None) -> None:
    ss.page = page
    if project is not None:
        ss.project_folder = project
    if doc is not None:
        ss.doc_folder = doc


def isolate(text: str) -> str:
    """Keep a code left-to-right inside plain RTL text where HTML is not rendered (buttons, st.success)."""
    return f"\u2066{text}\u2069"


def ltr(text: str) -> str:
    """Keep codes/paths left-to-right inside RTL text (otherwise "20117-W-X" shows as "W-X-20117")."""
    return f'<span dir="ltr" style="unicode-bidi:isolate">{text}</span>'


def label_of(table: dict, key: str) -> str:
    return table.get(key, {}).get(L, key)


def reindex(project: Project, doc: Document) -> None:
    search.index_document(project, doc)


def current_project() -> Project | None:
    return Project.load(ss.project_folder) if ss.project_folder and is_project_folder(ss.project_folder) else None


def load_spec(doc: Document) -> dict | None:
    f = doc.path / "spec.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def save_spec(doc: Document, spec: dict) -> None:
    (doc.path / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")


SPEC_HISTORY = "spec_history"


def backup_spec(doc: Document) -> None:
    """Keep the current spec.json before a revision replaces it (restored by "undo last revision")."""
    from datetime import datetime
    src = doc.path / "spec.json"
    if src.exists():
        folder = doc.path / SPEC_HISTORY
        folder.mkdir(exist_ok=True)
        shutil.copy2(src, folder / f"spec_{datetime.now():%Y%m%d_%H%M%S_%f}.json")


def spec_history(doc: Document) -> list[Path]:
    folder = doc.path / SPEC_HISTORY
    return sorted(folder.glob("spec_*.json")) if folder.exists() else []


def render_document(p: Project, d: Document, spec: dict, cat: Catalog, formats) -> list[str]:
    """Render the spec into the document folder and record the outputs; returns the render warnings."""
    from sketch2cad.drawing import render
    res = render(spec, d.path, f"{d.code}_Rev{d.rev}", project=p, document=d, catalog=cat,
                 formats=tuple(formats) or ("dxf",))
    d.outputs.update({("preview" if k == "png" else k): Path(v).name for k, v in res.items() if k != "warnings"})
    d.save()
    search.store_content(d, "spec.json", " ".join(
        (l.get("en", "") + " " + l.get("he", "")) for l in spec.get("legend", [])))
    reindex(p, d)
    return res.get("warnings", [])


def last_formats(d: Document) -> list[str]:
    """The formats rendered last time (PNG is always included: it is the preview)."""
    fmts = [k for k in ("dxf", "dwg", "pdf") if k in d.outputs]
    return fmts + ["png"] if fmts else ["dxf", "dwg", "pdf", "png"]


def lang_choice(label: str, default: str, key: str, options=("both", "en", "he")) -> str:
    return st.selectbox(label, list(options), index=list(options).index(default) if default in options else 0,
                        format_func=lambda x: T(f"lang_{x}"), key=key)


def index_source(doc: Document, path: Path) -> list[str]:
    """Extract text from a source file into the search index; returns warnings."""
    from sketch2cad.io.readers import read_any
    try:
        info = read_any(path, Path(tempfile.mkdtemp(prefix="s2c_")))
    except Exception as e:  # noqa: BLE001 - a broken file must not block the upload
        return [f"{path.name}: {e}"]
    search.store_content(doc, path.name, info.text)
    return list(info.warnings)


def _browse(key: str, title: str) -> None:
    from sketch2cad.native import pick_folder
    path = pick_folder(ss.get(key, ""), title)
    if path:
        ss[key] = path


def folder_input(label: str, key: str) -> str:
    """Text box for a folder path + a button that opens the Windows folder picker."""
    ss.setdefault(key, "")
    c1, c2 = st.columns([6, 1], vertical_alignment="bottom")
    c1.text_input(label, key=key)
    c2.button(f"📂 {T('browse')}", key=f"{key}_browse", on_click=_browse, args=(key, label),
              width="stretch")
    return ss[key]


def download(label: str, path: Path, key: str) -> None:
    if path and Path(path).exists():
        st.download_button(label, Path(path).read_bytes(), file_name=Path(path).name, key=key)


def show_converter_error(e: Exception) -> None:
    from sketch2cad.io.oda import ConverterNotFound
    st.warning(T("no_converter") if isinstance(e, ConverterNotFound) else ui_notice(str(e), L))


def check_updates(silent: bool = False) -> None:
    from sketch2cad import updater
    try:
        ss.update_info = updater.latest(timeout=4)
        ss.update_error = False
    except Exception:  # noqa: BLE001 - offline / GitHub unreachable
        ss.update_info, ss.update_error = None, not silent


def start_update() -> None:
    from sketch2cad import updater
    updater.start_update_and_restart(os.getpid())
    ss.updating = True


def update_box() -> None:
    """Sidebar: installed version, check for updates, update now (stops, updates and restarts the app)."""
    from sketch2cad import updater
    if os.name == "nt" and "update_info" not in ss:          # one quiet check per session
        check_updates(silent=True)
    cur = updater.installed_version()
    st.caption(f"{T('version')}: {ltr(cur[:7]) if cur else T('unknown')}", unsafe_allow_html=True)
    if ss.get("updating"):
        st.info(T("updating"))
        return
    info = ss.get("update_info")
    if info and updater.update_available(info):
        st.warning(f"{T('update_available')}  \n{info.date} - {info.message}")
        if os.name == "nt":
            st.button(f"⬇️ {T('update_now')}", on_click=start_update, width="stretch", type="primary")
        else:
            st.caption(T("update_manual"))
    elif info:
        st.caption(f"✓ {T('up_to_date')}")
    if ss.get("update_error"):
        st.caption(T("update_check_failed"))
    st.button(f"🔄 {T('check_updates')}", on_click=check_updates, width="stretch")


# ================================================================== sidebar
with st.sidebar:
    st.title("Sketch2CAD")
    lang = st.radio(T("language"), ["he", "en"], index=0 if L == "he" else 1,
                    format_func=lambda x: "עברית" if x == "he" else "English", horizontal=True)
    if lang != L:
        settings.ui_language = lang
        settings.save()
        st.rerun()
    st.divider()
    st.button(T("nav_projects"), on_click=go, args=("projects",), width="stretch")
    st.button(T("nav_search"), on_click=go, args=("search",), width="stretch")
    st.button(T("nav_import"), on_click=go, args=("import",), width="stretch")
    st.button(f"📧 {T('nav_mail')}", on_click=go, args=("mail",), width="stretch")
    st.button(T("nav_templates"), on_click=go, args=("templates",), width="stretch")
    st.button(T("nav_catalog"), on_click=go, args=("catalog",), width="stretch")
    st.button(T("nav_settings"), on_click=go, args=("settings",), width="stretch")
    if ss.project_folder and is_project_folder(ss.project_folder):
        cur = Project.load(ss.project_folder)
        st.divider()
        st.caption(T("current_project"))
        st.button(f"{cur.code} - {cur.display_name(L)}", on_click=go, args=("project",),
                  width="stretch")
    st.divider()
    update_box()


# ================================================================== pages
def page_projects() -> None:
    st.header(T("projects"))
    folders = list(settings.projects)
    if not folders:
        st.info(T("no_projects"))
    for folder in folders:
        c1, c2, c3 = st.columns([5, 1, 1])
        if not is_project_folder(folder):
            c1.warning(f"{T('missing_folder')}: {folder}")
            if c3.button(T("remove_from_list"), key=f"rm_{folder}"):
                settings.unregister_project(folder)
                st.rerun()
            continue
        p = Project.load(folder)
        c1.markdown(f"**{ltr(p.code)}** - {p.display_name(L)}  \n`{p.folder}`", unsafe_allow_html=True)
        c2.button(T("open"), key=f"open_{folder}", on_click=go, args=("project", p.folder))

    st.subheader(T("new_project"))
    for key, default in (("np_parent", settings.default_projects_root), ("np_pattern", codes.DEFAULT_PATTERN)):
        ss.setdefault(key, default)
    c1, c2, c3 = st.columns(3)
    code = c1.text_input(T("project_code"), key="np_code")
    name_he = c2.text_input(T("name_he"), key="np_name_he")
    name_en = c3.text_input(T("name_en"), key="np_name_en")
    c1, c2, c3, c4 = st.columns(4)
    client = c1.text_input(T("client"), key="np_client")
    contractor = c2.text_input(T("contractor"), key="np_contractor")
    location = c3.text_input(T("location"), key="np_location")
    contract_no = c4.text_input(T("contract_no"), key="np_contract_no")
    parent = folder_input(T("parent_folder"), "np_parent")
    c1, c2 = st.columns(2)
    pattern = c1.text_input(T("code_pattern"), key="np_pattern", help=T("code_pattern_help"))
    lang_mode = c2.selectbox(T("drawing_language"), ["both", "en", "he"], key="np_lang",
                             format_func=lambda x: T(f"lang_{x}"))
    if st.button(T("create"), key="np_create", type="primary"):
        if not code.strip():
            st.error(T("code_required"))
        elif not parent.strip():
            st.error(T("parent_required"))
        else:
            try:
                codes.seq_regex(pattern, prj=code.strip())          # validates the pattern first
                p = create_project(parent, code, name_he, name_en, client=client, contractor=contractor,
                                   location=location, contract_no=contract_no, doc_code_pattern=pattern,
                                   language_mode=lang_mode)
                settings.register_project(p.path)
                for k in [k for k in ss if str(k).startswith("np_")]:
                    del ss[k]
                go("project", p.folder)
                st.rerun()
            except Exception as e:  # noqa: BLE001 - show any validation error to the user
                st.error(f"{T('error')}: {e}")

    with st.expander(T("add_existing")):
        folder = folder_input(T("folder_path"), "existing_folder")
        if st.button(T("add")):
            if is_project_folder(folder):
                settings.register_project(folder)
                search.rebuild(settings)
                st.rerun()
            else:
                st.error(T("not_project_folder"))


def page_project() -> None:
    if not ss.project_folder or not is_project_folder(ss.project_folder):
        go("projects")
        st.rerun()
    p = Project.load(ss.project_folder)
    st.header(f"{p.code} - {p.display_name(L)}")
    st.caption(f"{T('folder')}: `{p.folder}`")
    tab_docs, tab_new, tab_print, tab_settings = st.tabs([T("documents"), T("new_document"), T("print_set"),
                                                          T("project_settings")])

    with tab_docs:
        c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
        q = c1.text_input(T("filter_text"))
        disc = c2.selectbox(T("discipline"), [""] + list(p.disciplines),
                            format_func=lambda k: T("all") if not k else f"{k} - {label_of(p.disciplines, k)}")
        typ = c3.selectbox(T("doc_type"), [""] + list(DOC_TYPES),
                           format_func=lambda k: T("all") if not k else label_of(DOC_TYPES, k))
        stat = c4.selectbox(T("status"), [""] + list(STATUSES),
                            format_func=lambda k: T("all") if not k else label_of(STATUSES, k))
        if q.strip():
            allowed = {h.folder for h in search.search(q, project_code=p.code)}
        else:
            allowed = None
        docs = [d for d in list_documents(p)
                if (allowed is None or d.folder in allowed)
                and (not disc or d.discipline == disc) and (not typ or d.type == typ)
                and (not stat or d.status == stat)]
        if not docs:
            st.info(T("no_documents"))
        for d in docs:
            c1, c2, c3, c4 = st.columns([2, 4, 1, 1])
            c1.markdown(f"**{ltr(d.code)}**  \n{ltr('Rev ' + d.rev)}", unsafe_allow_html=True)
            c2.markdown(f"{d.display_title(L)}  \n<small>{d.description}</small>", unsafe_allow_html=True)
            c3.markdown(label_of(STATUSES, d.status))
            c4.button(T("open"), key=f"doc_{d.folder}", on_click=go, args=("document", None, d.folder))

    with tab_new:
        c1, c2 = st.columns(2)
        disc = c1.selectbox(T("discipline"), list(p.disciplines), key="nd_disc",
                            format_func=lambda k: f"{k} - {label_of(p.disciplines, k)}")
        typ = c2.selectbox(T("doc_type"), list(DOC_TYPES), key="nd_type", format_func=lambda k: label_of(DOC_TYPES, k))
        subj = st.text_input(T("subject"), key="nd_subj")
        try:
            preview, _ = codes.next_code(p.doc_code_pattern, [d.code for d in list_documents(p)], prj=p.code,
                                         disc=disc, subj=subj, typ=typ)
            st.markdown(f"{T('code_preview')}: **{ltr(preview)}**", unsafe_allow_html=True)
        except ValueError as e:
            st.error(str(e))
        with st.form("new_doc"):
            c1, c2 = st.columns(2)
            title_he = c1.text_input(T("title_he"))
            title_en = c2.text_input(T("title_en"))
            desc = st.text_area(T("description"))
            tags = st.text_input(T("tags"))
            if st.form_submit_button(T("create")):
                try:
                    d = create_document(p, title_he=title_he, title_en=title_en, discipline=disc, subject=subj,
                                        type=typ, description=desc,
                                        tags=[x.strip() for x in tags.split(",") if x.strip()])
                    reindex(p, d)
                    go("document", None, d.folder)
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"{T('error')}: {e}")

    with tab_print:
        st.caption(T("print_set_help"))
        c1, c2 = st.columns([3, 1])
        stats = c1.multiselect(T("statuses"), list(STATUSES), default=list(STATUSES),
                               format_func=lambda k: label_of(STATUSES, k))
        lang = lang_choice(T("doc_language"), L, "ps_lang", ("en", "he"))
        if c2.button(T("build"), key="ps_build"):
            from sketch2cad.printset import build_print_set
            ss.print_set = str(build_print_set(p, p.path / f"{p.code}_PrintSet.pdf", lang=lang, statuses=stats))
        if ss.get("print_set") and Path(ss.print_set).parent == p.path:
            download(f"{T('download')} PDF", Path(ss.print_set), "ps_dl")

    with tab_settings:
        with st.form("proj_settings"):
            c1, c2, c3 = st.columns(3)
            name_he = c1.text_input(T("name_he"), value=p.name.get("he", ""))
            name_en = c2.text_input(T("name_en"), value=p.name.get("en", ""))
            client = c3.text_input(T("client"), value=p.client)
            c1, c2, c3 = st.columns(3)
            contractor = c1.text_input(T("contractor"), value=p.contractor)
            location = c2.text_input(T("location"), value=p.location)
            contract_no = c3.text_input(T("contract_no"), value=p.contract_no)
            c1, c2 = st.columns(2)
            pattern = c1.text_input(T("code_pattern"), value=p.doc_code_pattern, help=T("code_pattern_help"))
            lang_mode = c2.selectbox(T("drawing_language"), ["both", "en", "he"],
                                     index=["both", "en", "he"].index(p.language_mode),
                                     format_func=lambda x: T(f"lang_{x}"))
            disc_json = st.text_area(T("disciplines_json"), value=json.dumps(p.disciplines, ensure_ascii=False,
                                                                              indent=1), height=200)
            qa_json = st.text_area(T("qa_settings"),
                                   value=json.dumps(p.qa, ensure_ascii=False, indent=2) if p.qa else "",
                                   height=180, help=T("qa_settings_help"),
                                   placeholder='{"min_text_mm": 2.5}')
            if st.form_submit_button(T("save")):
                try:
                    codes.seq_regex(pattern, prj=p.code)
                    p.name, p.client, p.doc_code_pattern, p.language_mode = (
                        {"he": name_he, "en": name_en}, client, pattern, lang_mode)
                    p.contractor, p.location, p.contract_no = contractor, location, contract_no
                    p.disciplines = json.loads(disc_json)
                    p.qa = json.loads(qa_json) if qa_json.strip() else {}
                    if not isinstance(p.qa, dict):
                        raise ValueError("QA settings must be a JSON object")
                    p.save()
                    st.success(T("saved"))
                except Exception as e:  # noqa: BLE001
                    st.error(f"{T('error')}: {e}")

        st.subheader(T("relocate"))
        mode = st.radio(T("relocate"), ["move", "link"], format_func=lambda m: T("relocate_move" if m == "move" else
                                                                       "relocate_link"), label_visibility="collapsed")
        new_loc = folder_input(T("new_location"), "relocate_to")
        if st.button(T("apply")) and new_loc:
            try:
                old = p.folder
                moved = relocate_project(p, new_loc, move_files=(mode == "move"))
                settings.unregister_project(old)
                settings.register_project(moved.path)
                search.rebuild(settings)
                go("project", moved.folder)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"{T('error')}: {e}")


def page_document() -> None:
    if not ss.doc_folder or not Path(ss.doc_folder, "document.json").exists():
        go("project")
        st.rerun()
    p = Project.load(ss.project_folder)
    d = Document.load(ss.doc_folder)
    st.button(T("back_to_project"), on_click=go, args=("project",))
    st.markdown(f"## {ltr(d.code + '  Rev ' + d.rev)}", unsafe_allow_html=True)
    st.subheader(d.display_title(L))
    cat = load_catalog(p)
    (tab_det, tab_src, tab_draw, tab_chk, tab_bom, tab_out, tab_pkg, tab_cmp, tab_rev) = st.tabs(
        [T("details"), T("sources"), T("drawing"), T("checks"), T("bom"), T("outputs"), T("package"), T("compare"),
         T("revisions")])

    with tab_det, st.form("doc_details"):
        c1, c2 = st.columns(2)
        title_he = c1.text_input(T("title_he"), value=d.title.get("he", ""))
        title_en = c2.text_input(T("title_en"), value=d.title.get("en", ""))
        desc = st.text_area(T("description"), value=d.description)
        c1, c2, c3 = st.columns(3)
        typ = c1.selectbox(T("doc_type"), list(DOC_TYPES), index=list(DOC_TYPES).index(d.type),
                           format_func=lambda k: label_of(DOC_TYPES, k))
        stat = c2.selectbox(T("status"), list(STATUSES), index=list(STATUSES).index(d.status),
                            format_func=lambda k: label_of(STATUSES, k))
        tags = c3.text_input(T("tags"), value=", ".join(d.tags))
        if st.form_submit_button(T("save")):
            d.title, d.description, d.type, d.status = {"he": title_he, "en": title_en}, desc, typ, stat
            d.tags = [x.strip() for x in tags.split(",") if x.strip()]
            d.save()
            reindex(p, d)
            st.success(T("saved"))

    with tab_src:
        st.caption(T("sources_help"))
        files = st.file_uploader(T("upload"), accept_multiple_files=True)
        if files and st.button(T("add")):
            warnings = []
            for f in files:
                rel = add_source(d, name=f.name, data=f.getvalue())
                warnings += index_source(d, d.path / rel)
            reindex(p, d)
            ss.src_warnings = warnings
            st.rerun()
        for w in ss.pop("src_warnings", []):
            st.warning(w)
        for rel in d.sources:
            fp = d.path / rel
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"`{rel}`")
            if fp.exists():
                c2.download_button(T("download"), fp.read_bytes(), file_name=fp.name, key=f"dl_{rel}")
                if fp.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    st.image(str(fp), width=320)

    with tab_draw:
        tab_drawing(p, d, cat)
    spec = load_spec(d)
    with tab_chk:
        if spec is None:
            st.info(T("no_spec"))
        else:
            findings = run_checks(spec, cat, p)
            if not findings:
                st.success(T("no_findings"))
            icons = {"error": "🔴", "warning": "🟠", "info": "🔵"}
            for f in findings:
                el = f" - {ltr(f.element)}" if f.element else ""
                st.markdown(f"{icons[f.severity]} {getattr(f, L)}{el}", unsafe_allow_html=True)
    with tab_bom:
        st.caption(T("bom_title"))
        if spec is None:
            st.info(T("no_spec"))
        else:
            rows = bom.build_bom(spec, cat)
            st.dataframe([{T("item"): str(r["no"]), "ID": r["item"], T("description"): r[L] or r["en"],
                           T("qty"): r["qty"], T("units"): r["unit"], T("submittal"): r["source"]} for r in rows],
                         width="stretch", hide_index=True)
            xlsx = bom.bom_to_xlsx(rows, d.path / f"{d.code}_BOM.xlsx", lang=L, title=f"{d.code} Rev {d.rev}")
            download(T("excel"), xlsx, "bom_xlsx")

    with tab_out:
        if not d.outputs:
            st.info(T("no_outputs"))
        for kind, rel in d.outputs.items():
            c1, c2 = st.columns([3, 1])
            with c1:
                download(f"{T('download')} {kind.upper()}", d.path / rel, f"out_{kind}")
            if os.name == "nt" and kind in ("dwg", "dxf") and (d.path / rel).exists():
                # the app runs on the user's own PC, so this opens the file in the program registered for it
                c2.button(T("open_in_cad"), key=f"open_{kind}", on_click=os.startfile, args=(str(d.path / rel),))
        if (d.path / d.outputs.get("preview", "-")).exists():
            st.image(str(d.path / d.outputs["preview"]), width="stretch")

    with tab_pkg:
        st.caption(T("package_help"))
        lang = lang_choice(T("doc_language"), L, "pkg_lang", ("en", "he"))
        if st.button(T("build"), key="pkg_build"):
            from sketch2cad.package import build_package
            out = build_package(p, d, cat, d.path / f"{d.code}_Rev{d.rev}_Package.pdf", lang=lang)
            d.outputs["package"] = out.name
            d.save()
        if d.outputs.get("package"):
            download(f"{T('download')} PDF", d.path / d.outputs["package"], "pkg_dl")

    with tab_cmp:
        tab_compare(p, d)

    with tab_rev:
        st.table([{T("rev"): r["rev"], T("date"): r["date"], T("note"): r.get("note", "")} for r in d.revisions])
        note = st.text_input(T("rev_note"))
        if st.button(T("new_revision")):
            new_revision(d, p, note)
            reindex(p, d)
            st.rerun()


def tab_drawing(p: Project, d: Document, cat: Catalog) -> None:
    """Spec from a typical detail or a sketch sent to the agent, then render. JSON stays collapsed."""
    spec = load_spec(d)
    agent_status_card(p, d)
    rendered = ss.pop("render_note", None)
    if rendered:
        st.success(rendered[0])
        for warning in rendered[1]:
            st.warning(ui_notice(warning, L))
    for kind, msg in ss.pop("rev_msgs", []):
        getattr(st, kind)(ui_notice(msg, L) if kind == "warning" else msg)
    sent = ss.pop("agent_notice", None)
    if sent:
        getattr(st, sent[0])(sent[1])
    shown = d.path / d.outputs.get("preview", "-")
    if shown.is_file():
        st.image(str(shown), width="stretch")
    else:
        shown = None
    mode = st.radio(T("spec_source"), ["typical", "sketch"], horizontal=True, key="spec_mode",
                    format_func=lambda m: T(f"src_{m}"))
    lang_mode = d.language_mode or p.language_mode
    if mode == "typical":
        typs = {x.name: x for x in typical.list_typicals()}
        name = st.selectbox(T("typical"), list(typs), format_func=lambda n: typs[n].title[L])
        item_ids = [i["id"] for i in cat.items()]
        with st.form(f"typ_{name}"):
            values, cols = {}, st.columns(3)
            for i, (k, meta) in enumerate(typs[name].params.items()):
                c, dflt = cols[i % 3], meta["default"]
                if k == "language":
                    with c:
                        values[k] = lang_choice(meta[L], lang_mode, f"tp_{name}_{k}")
                elif k.endswith("_item"):
                    values[k] = c.selectbox(meta[L], item_ids, key=f"tp_{name}_{k}",
                                            index=item_ids.index(dflt) if dflt in item_ids else 0)
                elif isinstance(dflt, (int, float)):
                    values[k] = c.number_input(meta[L], value=dflt, key=f"tp_{name}_{k}")
                else:
                    values[k] = c.text_input(meta[L], value=str(dflt), key=f"tp_{name}_{k}")
            if st.form_submit_button(T("generate")):
                spec = typical.generate(name, values, cat)
                save_spec(d, spec)
                st.success(T("spec_saved"))
    elif mode == "sketch":
        usable = [s for s in d.sources if Path(s).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif",
                                                                     ".pdf", ".tif", ".tiff", ".bmp")]
        if usable:
            st.caption(T("sketch_sources").format(names=", ".join(usable)))
        else:
            st.info(T("sketch_no_sources"))
        st.caption(T("sketch_use_panel"))

    st.divider()
    if spec is None:
        st.info(T("no_spec"))
    else:
        _sheet_form(p, d, spec, cat)
    with st.expander(T("spec_advanced"), expanded=False):
        text = st.text_area(T("spec_json"), value=json.dumps(spec, ensure_ascii=False, indent=1) if spec else "",
                            height=400)
        if st.button(T("save"), key="spec_save"):
            try:
                spec = json.loads(text)
                save_spec(d, spec)
                st.success(T("saved"))
            except json.JSONDecodeError as e:
                st.error(f"{T('invalid_json')}: {e}")
    agent_workbench(p, d, cat, shown)
    _show_agent_dialog(d)


AGENT_OPTIONS = ("agent_opt_scale", "agent_opt_units", "agent_opt_section", "agent_opt_detail", "agent_opt_inv")
_ATTACH_TYPES = ["pdf", "dwg", "dxf", "png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"]


def _sheet_form(p: Project, d: Document, spec: dict, cat: Catalog) -> None:
    """Title block and render. The agent panel below is separate."""
    from sketch2cad.templates import list_templates
    lang_mode = d.language_mode or p.language_mode
    st.subheader(T("sheet"))
    templates = {x["id"]: x for x in list_templates()}
    current = d.titleblock or spec.get("sheet", {}).get("template") or p.default_titleblock
    ids = list(templates)
    with st.form("render_form"):
        c1, c2 = st.columns(2)
        tb = c1.selectbox(T("titleblock"), ids, index=ids.index(current) if current in ids else 0,
                          format_func=lambda i: (templates[i].get("name") or {}).get(L, i))
        formats = c2.multiselect(T("formats"), ["dxf", "dwg", "pdf", "png"], default=["dxf", "dwg", "pdf", "png"])
        c1, c2 = st.columns(2)
        ov_en = c1.text_input(T("title_override_en"), value=d.title_overrides.get("title_en", ""))
        ov_he = c2.text_input(T("title_override_he"), value=d.title_overrides.get("title_he", ""))
        go_render = st.form_submit_button(T("render"))
    if go_render:
        d.titleblock = tb
        d.title_overrides = {k: v for k, v in (("title_en", ov_en), ("title_he", ov_he)) if v}
        spec.setdefault("sheet", {})["template"] = tb
        spec.setdefault("language", lang_mode)
        save_spec(d, spec)
        try:
            warnings = render_document(p, d, spec, cat, formats)
        except Exception as e:  # noqa: BLE001 - show render errors (bad spec) to the user
            st.error(f"{T('error')}: {e}")
            return
        ss.render_note = (T("rendered"), warnings)
        st.rerun()


def _show_time(value: str) -> str:
    return (value or "").replace("T", " ")[:19]


def _history_label(path: Path) -> str:
    parts = path.stem.removeprefix("spec_").split("_")
    if len(parts) >= 2 and len(parts[0]) == 8 and len(parts[1]) == 6:
        day, clock = parts[0], parts[1]
        return f"{day[:4]}-{day[4:6]}-{day[6:8]} {clock[:2]}:{clock[2:4]}:{clock[4:6]}"
    return path.name


def _text_of(item) -> str:
    if isinstance(item, dict):
        return str(item.get(L) or item.get("en") or item.get("he") or "")
    return str(item)


def agent_status_card(p: Project, d: Document) -> None:
    """What was sent, whether a reply is waiting, and what was last applied."""
    from sketch2cad.claude_runner import status_for_document, tick
    from sketch2cad.workflow import workflow_view
    try:
        tick(projects=[p.path])
    except Exception:  # noqa: BLE001 - the status card still renders if a run cannot advance
        pass
    view = workflow_view(p.path, d.path, d.code)
    try:
        run = status_for_document(p.path, d.code)
    except Exception:  # noqa: BLE001
        run = None
    badge = {"none": "s2c-idle", "awaiting": "s2c-wait", "reply": "s2c-ready", "applied": "s2c-done"}[view["state"]]
    with st.container(border=True):
        st.markdown(
            f'<p class="s2c-kicker">{html.escape(T("agent_panel"))}</p>'
            f'<span class="s2c-badge {badge}">{html.escape(T("agent_status_" + view["state"]))}</span>',
            unsafe_allow_html=True)
        sent, inbox, applied = st.columns(3)
        sent.markdown(f"**{T('agent_last_sent')}**")
        last = view["last_sent"]
        if last:
            sent.caption(_show_time(last["at"]))
            text = (last.get("request") or "").strip() or T("agent_no_request")
            sent.markdown(html.escape(text).replace("\n", "  \n"))
        else:
            sent.caption(T("agent_no_package"))
        inbox.markdown(f"**{T('agent_inbox')}**")
        if view["reply_waiting"]:
            inbox.markdown(
                f'<span class="s2c-badge s2c-ready">{html.escape(T("agent_inbox_waiting"))}</span>',
                unsafe_allow_html=True)
            if view.get("reply_name"):
                inbox.caption(isolate(view["reply_name"]))
        else:
            inbox.caption(T("agent_inbox_empty"))
        applied.markdown(f"**{T('agent_last_applied')}**")
        done = view["last_applied"]
        if done:
            applied.caption(_show_time(done.get("at") or ""))
            backup = done.get("backup") or ""
            if backup:
                applied.caption(isolate(Path(backup).name))
        else:
            applied.caption(T("agent_never_applied"))
        if run and run.get("status") in ("queued", "working", "ready", "failed"):
            kind = {"queued": "s2c-idle", "working": "s2c-wait", "ready": "s2c-ready",
                    "failed": "s2c-fail"}[run["status"]]
            st.markdown(
                f'<span class="s2c-badge {kind}">{html.escape(T("claude_status_" + run["status"]))}</span>',
                unsafe_allow_html=True)
            if run["status"] == "failed" and run.get("log_tail"):
                st.caption(run["log_tail"])


def _composer(prefix: str):
    """Free text, optional instructions, and extra source files. Nothing is sent yet."""
    from sketch2cad.workflow import compose_request
    text = st.text_area(T("revise_request"), key=f"{prefix}_text", height=110,
                        placeholder=T("revise_placeholder"))
    st.caption(T("agent_compose_note"))
    picked = []
    cols = st.columns(3)
    for i, key in enumerate(AGENT_OPTIONS):
        if cols[i % 3].checkbox(T(key), key=f"{prefix}_{key}"):
            picked.append(T(key))
    uploads = st.file_uploader(T("agent_extra"), type=_ATTACH_TYPES, accept_multiple_files=True,
                               key=f"{prefix}_files")
    return compose_request(text, picked), list(uploads or [])


def _staged_uploads(uploads) -> list[Path]:
    if not uploads:
        return []
    folder = Path(tempfile.mkdtemp(prefix="s2c_attach_"))
    paths = []
    for item in uploads:
        name = Path(getattr(item, "name", "source")).name or "source"
        dest = folder / name
        stem, suffix, n = dest.stem, dest.suffix, 1
        while dest.exists():
            dest = dest.with_name(f"{stem}_{n}{suffix}")
            n += 1
        dest.write_bytes(item.getvalue())
        paths.append(dest)
    return paths


def _agent_send_body() -> None:
    """Request form when the panel was empty. Sends only; nothing is applied."""
    from sketch2cad.bridge import BridgeError, export_to_outbox
    request, uploads = _composer("dlg")
    if st.button(T("agent_request_send"), type="primary", key="agent_send_go"):
        paths = _staged_uploads(uploads)
        if not request.strip() and not paths:
            st.warning(T("revise_empty"))
            return
        try:
            path = export_to_outbox(ss.agent_doc, request=request, extra_sources=paths or None)
        except BridgeError as exc:
            st.error(ui_notice(str(exc), L))
            return
        ss.agent_dialog = None
        notice = T("agent_sent").format(path=path)
        if paths:
            notice = f"{notice} ({T('agent_extra_added').format(n=len(paths))})"
        ss.agent_notice = ("success", notice)
        ss.rev_open = True
        st.rerun()


def _trial_pair(report: dict) -> None:
    d = Document.load(ss.agent_doc)
    current = d.path / d.outputs.get("preview", "")
    trial = Path(report.get("trial_dir") or "")
    name = (report.get("outputs") or {}).get("png")
    trial_png = trial / name if name else None
    left, right = st.columns(2)
    left.markdown(f"**{T('agent_before')}**")
    if current.is_file():
        left.image(str(current), width="stretch")
    else:
        left.caption(T("agent_no_preview"))
    right.markdown(f"**{T('agent_after')}**")
    if trial_png is not None and trial_png.is_file():
        right.image(str(trial_png), width="stretch")
    else:
        right.caption(T("agent_no_preview"))


def _reply_lists(report: dict) -> None:
    for key in ("changes", "assumptions"):
        st.markdown(f"**{T(key)}**")
        items = report.get(key) or []
        if not items:
            st.caption(T("agent_none_listed"))
            continue
        for item in items:
            st.markdown(f"- {_text_of(item)}")
    st.markdown(f"**{T('questions')}**")
    st.caption(T("agent_questions_need"))
    questions = report.get("questions") or []
    if not questions:
        st.caption(T("agent_no_questions"))
    for item in questions:
        st.markdown(f'<div class="s2c-q">{html.escape(_text_of(item))}</div>', unsafe_allow_html=True)


def _checks_grouped(findings: list) -> None:
    st.markdown(f"**{T('checks')}**")
    if not findings:
        st.caption(T("no_findings"))
        return
    grouped: dict[str, list] = {"error": [], "warning": [], "info": []}
    for finding in findings:
        grouped.setdefault(finding.get("severity") or "info", []).append(finding)
    for severity in ("error", "warning", "info"):
        rows = grouped.get(severity) or []
        if not rows:
            continue
        st.markdown(f"**{T(f'agent_sev_{severity}')}**")
        for finding in rows:
            text = html.escape(_text_of(finding))
            extra = " ".join(part for part in (finding.get("code") or "", finding.get("element") or "") if part)
            if extra:
                text += f' <span dir="ltr">({html.escape(extra)})</span>'
            st.markdown(f'<div class="s2c-sev-{severity}">{text}</div>', unsafe_allow_html=True)


def _agent_receive_body() -> None:
    """Trial preview and checks. The spec changes only when the user presses Apply."""
    from sketch2cad.bridge import import_reply
    from sketch2cad.workflow import record_applied, record_restore
    report = ss.agent_report
    st.caption(T("agent_trial"))
    if not report.get("ok"):
        for error in report.get("errors") or []:
            st.error(ui_notice(error, L))
        return
    _trial_pair(report)
    st.markdown(T("agent_elements").format(n=report.get("element_count", 0)))
    dwg = report.get("dwg") or {}
    if dwg.get("produced"):
        st.success(T("agent_dwg_ok").format(name=dwg.get("file", "trial.dwg")))
    else:
        st.warning(ui_notice(dwg.get("reason", ""), L))
    _reply_lists(report)
    _checks_grouped(list(report.get("checks") or []))
    for warning in list(report.get("warnings") or []) + list(report.get("render_warnings") or []):
        if str(warning).startswith("DWG skipped"):
            continue
        st.warning(ui_notice(warning, L))
    overlay_name = report.get("qa_overlay")
    if overlay_name:
        overlay_path = Path(report.get("trial_dir") or "") / overlay_name
        if overlay_path.is_file():
            st.image(str(overlay_path), caption=T("qa_overlay"))
    errors = [f for f in (report.get("checks") or []) if f.get("severity") == "error"]
    if errors:
        st.error(T("agent_blocked"))
        override = st.checkbox(T("agent_override"), key="agent_qa_override")
    else:
        override = True
    if st.button(T("agent_apply"), type="primary", key="agent_apply_go", disabled=not override):
        applied = import_reply(ss.agent_reply, ss.agent_doc, apply=True)
        if not (applied.get("ok") and applied.get("applied")):
            for error in applied.get("errors") or [T("agent_failed")]:
                st.error(ui_notice(error, L))
            return
        p = current_project()
        d = Document.load(ss.agent_doc)
        try:
            warnings = render_document(p, d, load_spec(d), load_catalog(p), last_formats(d))
        except Exception as exc:  # noqa: BLE001 - a spec that does not render is rolled back
            restore_last_spec(d)
            record_restore(d.path, "render")
            st.error(f"{T('revise_failed')}: {exc}")
            return
        record_applied(d.path, reply=ss.agent_reply, backup=applied.get("backup"))
        ss.agent_dialog = None
        ss.agent_notice = ("success", T("agent_applied"))
        ss.rev_msgs = [("warning", ui_notice(w, L)) for w in warnings]
        ss.rev_open = True
        st.rerun()


def _dismiss_agent_dialog() -> None:
    ss.agent_dialog = None


def _open_agent_dialog(kind: str) -> None:
    title = T("agent_send") if kind == "send" else T("agent_recv")
    body = _agent_send_body if kind == "send" else _agent_receive_body
    st.dialog(title, width="large", on_dismiss=_dismiss_agent_dialog)(body)()


def _show_agent_dialog(d: Document) -> None:
    if ss.get("agent_dialog") in ("send", "receive") and ss.get("agent_doc") == str(d.path):
        _open_agent_dialog(ss.agent_dialog)


def _remember_markup(d: Document, preview: Path, strokes) -> None:
    """Save the current pen marks so the next package includes the newest marked sheet."""
    if not strokes:
        return
    from datetime import datetime

    from sketch2cad.markup import compose
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    compose(preview, strokes, d.path / "markups" / f"markup_{stamp}.png")


def _send_work_package(d: Document, request: str, uploads=None) -> None:
    """Write a work package. An empty request opens the form; nothing is applied here."""
    from sketch2cad.bridge import BridgeError, export_to_outbox
    paths = _staged_uploads(uploads)
    if request.strip() or paths:
        try:
            path = export_to_outbox(d.path, request=request, extra_sources=paths or None)
        except BridgeError as exc:
            st.error(ui_notice(str(exc), L))
            return
        try:
            from sketch2cad.claude_runner import watch_package
            watch_package(path)
        except Exception:  # noqa: BLE001 - the package is already written; receive still works
            pass
        notice = T("agent_sent").format(path=path)
        if paths:
            notice = f"{notice} ({T('agent_extra_added').format(n=len(paths))})"
        ss.agent_notice = ("success", notice)
        st.rerun()
        return
    ss.agent_doc = str(d.path)
    ss.agent_dialog = "send"
    st.rerun()


def _open_inbox(p: Project, d: Document) -> None:
    """Read the newest inbox reply and show its trial. The spec changes only on Apply."""
    from sketch2cad.bridge import BridgeError, import_reply, latest_inbox_reply
    try:
        reply = latest_inbox_reply(p.path)
        report = import_reply(reply, d.path, apply=False)
    except BridgeError as exc:
        st.error(ui_notice(str(exc), L))
        return
    ss.agent_doc = str(d.path)
    ss.agent_reply = str(reply)
    ss.agent_report = report
    ss.agent_dialog = "receive"
    st.rerun()


def _rollback_to(p: Project, d: Document, cat: Catalog, backup: Path) -> None:
    """Put one saved spec back and render it. The spec that was on screen is kept in the history."""
    from sketch2cad.workflow import record_restore
    current = d.path / "spec.json"
    snapshot = current.read_bytes() if current.is_file() else None
    if snapshot is not None:
        backup_spec(d)
    shutil.copy2(backup, current)
    try:
        warnings = render_document(p, d, load_spec(d), cat, last_formats(d))
    except Exception as exc:  # noqa: BLE001 - leave the drawing as it was
        if snapshot is not None:
            current.write_bytes(snapshot)
            history = spec_history(d)
            if history:
                history[-1].unlink(missing_ok=True)
        st.error(f"{T('error')}: {exc}")
        return
    record_restore(d.path, backup.name)
    ss.rev_msgs = [("success", T("agent_rolled_back").format(name=_history_label(backup)))]
    ss.rev_msgs += [("warning", w) for w in warnings]
    st.rerun()


def agent_workbench(p: Project, d: Document, cat: Catalog, preview: Path | None) -> None:
    """Markup, the request form, and one-click restore of a saved spec."""
    from sketch2cad.markup import markup_canvas
    from sketch2cad.workflow import record_restore
    strokes = None
    if preview is not None:
        with st.expander(f"✏️ {T('revise')}", expanded=bool(ss.get("rev_open"))):
            st.caption(T("revise_help"))
            labels = {k: T(f"mk_{k}") for k in ("pen", "line", "undo", "clear", "red", "blue", "green", "count")}
            strokes = markup_canvas(preview, key=f"mk_{Path(d.folder).name}_{preview.stat().st_mtime_ns}",
                                    labels=labels, rtl=L == "he")
            if strokes:
                st.caption(T("mk_will_send").format(n=len(strokes)))
    st.subheader(T("agent_compose_title"))
    st.caption(T("agent_help"))
    request, uploads = _composer(f"rev_{Path(d.folder).name}")
    history = spec_history(d)
    send_col, recv_col, undo_col = st.columns(3)
    send = send_col.button(T("agent_send"), key="agent_send", type="primary")
    recv = recv_col.button(T("agent_recv"), key="agent_recv")
    undo = undo_col.button(f"↶ {T('revise_undo')}", key="rev_undo", disabled=not history)
    st.subheader(T("agent_history"))
    backups = list(reversed(history))
    chosen = None
    if not backups:
        st.caption(T("agent_history_empty"))
    for path in backups:
        label_col, button_col = st.columns([5, 1])
        label_col.markdown(ltr(_history_label(path)), unsafe_allow_html=True)
        if button_col.button(T("agent_rollback"), key=f"rb_{path.name}"):
            chosen = path
    if undo:
        ss.rev_open = True
        name = spec_history(d)[-1].name
        restore_last_spec(d)
        try:
            warnings = render_document(p, d, load_spec(d), cat, last_formats(d))
        except Exception as exc:  # noqa: BLE001
            st.error(f"{T('error')}: {exc}")
            return
        record_restore(d.path, name)
        ss.rev_msgs = [("success", T("revise_undone"))] + [("warning", w) for w in warnings]
        st.rerun()
    if chosen is not None:
        ss.rev_open = True
        _rollback_to(p, d, cat, chosen)
    if send:
        ss.rev_open = True
        if preview is not None:
            _remember_markup(d, preview, strokes)
        _send_work_package(d, request, uploads)
    if recv:
        ss.rev_open = True
        if preview is not None:
            _remember_markup(d, preview, strokes)
        _open_inbox(p, d)


def restore_last_spec(d: Document) -> None:
    """Put the newest backed-up spec back as spec.json (and remove it from the history)."""
    history = spec_history(d)
    if history:
        shutil.move(str(history[-1]), str(d.path / "spec.json"))


def tab_compare(p: Project, d: Document) -> None:
    """Overlay two versions: current outputs, archived revisions and sources."""
    from sketch2cad.compare import compare
    st.caption(T("compare_help"))
    exts = (".pdf", ".dxf", ".dwg", ".png")
    files = [d.path / r for r in d.outputs.values() if (d.path / r).suffix.lower() in exts]
    files += sorted(f for f in (p.path / "90_Archive").glob(f"{d.code}_Rev*/*") if f.suffix.lower() in exts)
    files += [d.path / s for s in d.sources if Path(s).suffix.lower() in exts]
    files = [f for f in dict.fromkeys(files) if f.exists()]
    if len(files) < 2:
        st.info(T("need_two"))
        return

    def name(f: Path) -> str:
        return f"{f.parent.name}/{f.name}"

    c1, c2 = st.columns(2)
    a = c1.selectbox(T("compare_a"), files, index=min(1, len(files) - 1), format_func=name)
    b = c2.selectbox(T("compare_b"), files, index=0, format_func=name)
    if st.button(T("compare"), key="cmp_go"):
        try:
            out, ratio = compare(a, b, d.path / "compare.png")
            st.metric(T("changed"), f"{ratio:.1%}")
            st.image(str(out), width="stretch")
        except Exception as e:  # noqa: BLE001
            show_converter_error(e)


def page_import() -> None:
    """Read any plan format, preview it, convert (PDF/IFC → DXF/DWG, DXF ↔ DWG) and file it in a project."""
    from sketch2cad.io import oda, readers
    st.header(T("nav_import"))
    up = st.file_uploader(T("import_file"), key="imp_file")
    if not up:
        return
    work = app_home() / "imports" / Path(up.name).stem
    work.mkdir(parents=True, exist_ok=True)
    src = work / up.name
    if not src.exists() or src.stat().st_size != up.size:
        src.write_bytes(up.getvalue())
    info = readers.read_any(src, work)
    c1, c2 = st.columns(2)
    c1.metric(T("file_kind"), info.kind.upper())
    c2.metric(T("pages"), info.pages)
    for w in info.warnings:
        st.warning(w)
    if info.kind == "rvt":
        st.info(T("rvt_help"))
    if info.kind == "image":
        st.info(T("image_help"))
    if info.kind == "mail":
        st.info(T("mail_import_hint"))
    if info.preview_png and Path(info.preview_png).exists():
        st.image(str(info.preview_png), width="stretch")
    if info.text:
        with st.expander(T("extracted_text")):
            st.text(info.text[:20000])

    key = f"imp_dxf_{src}"                       # DXF produced from this upload (kept across reruns)
    if info.kind == "pdf":
        from sketch2cad.io.pdf2dxf import pdf_to_dxf
        c1, c2, c3 = st.columns(3)
        page = c1.number_input(T("pdf_page"), 1, max(info.pages, 1), 1) - 1
        scale = c2.number_input(T("pdf_scale"), 1.0, 10000.0, 100.0)
        units = c3.selectbox(T("units"), ["mm", "m"])
        if st.button(T("convert")):
            ss[key] = str(pdf_to_dxf(src, work / f"{src.stem}_p{page + 1}.dxf", page=page, scale=scale, units=units))
    elif info.kind == "ifc":
        from sketch2cad.io.ifc2dxf import ifc_to_dxf
        cut = st.number_input(T("cut_height"), 0.0, 10.0, 1.2, 0.1)
        if st.button(T("convert")):
            ss[key] = str(ifc_to_dxf(src, work / f"{src.stem}_plan.dxf", cut_height=cut))
    elif info.kind == "dwg" and info.dxf_path:
        ss[key] = str(info.dxf_path)
    elif info.kind == "dxf":
        ss[key] = str(src)

    results: list[Path] = []                     # converted files, besides the uploaded original
    if ss.get(key):
        dxf = Path(ss[key])
        dwg = src if info.kind == "dwg" else dxf.with_suffix(".dwg")
        if not dwg.exists() and st.button(T("to_dwg")):
            try:
                oda.convert(dxf, "dwg", work)
            except Exception as e:  # noqa: BLE001
                show_converter_error(e)
        results = [f for f in (dxf, dwg) if f.exists() and f != src]
    for f in results:
        download(f"{T('download')} {f.suffix[1:].upper()} - {f.name}", f, f"imp_dl_{f}")

    st.divider()
    p = current_project()
    if p is None:
        st.info(T("no_current_project"))
        return
    with st.form("imp_save"):
        st.markdown(f"**{T('save_to_project')}** - {ltr(p.code)}", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        title_en = c1.text_input(T("title_en"), value=src.stem)
        title_he = c2.text_input(T("title_he"))
        c1, c2 = st.columns(2)
        disc = c1.selectbox(T("discipline"), list(p.disciplines), format_func=lambda k: f"{k} - {label_of(p.disciplines, k)}")
        subj = c2.text_input(T("subject"), value="IMP")
        if st.form_submit_button(T("save")):
            d = create_document(p, title_he=title_he, title_en=title_en, discipline=disc, subject=subj,
                                type="import", description=up.name)
            add_source(d, src)
            for f in results:
                shutil.copy2(f, d.path / f.name)
                d.outputs[f.suffix[1:].lower()] = f.name
            if info.preview_png and Path(info.preview_png).exists():
                shutil.copy2(info.preview_png, d.path / "preview.png")
                d.outputs["preview"] = "preview.png"
            d.save()
            search.store_content(d, src.name, info.text)
            reindex(p, d)
            go("document", None, d.folder)
            st.rerun()


def page_mail() -> None:
    """A reviewer's e-mail (.msg / .eml): read it, find the document it is about, and file it."""
    from sketch2cad import mail as mailmod
    st.header(f"📧 {T('nav_mail')}")
    st.caption(T("mail_help"))
    up = st.file_uploader(T("mail_file"), type=["msg", "eml"], key="mail_file")
    for kind, msg in ss.pop("mail_msgs", []):
        getattr(st, kind)(msg)
    done = ss.get("mail_done")
    if done and Path(done["doc"], "document.json").exists():
        if st.button(f"📄 {T('open_document')}: {isolate(done['code'])}", key="mail_open"):
            ss.pop("mail_done", None)
            go("document", done["project"], done["doc"])
            st.rerun()
    if not up:
        return
    key = f"mail_{up.name}_{up.size}"
    if key not in ss:                                    # a new mail: read it once, forget the previous result
        ss.pop("mail_done", None)
        work = app_home() / "imports" / "mail"
        work.mkdir(parents=True, exist_ok=True)
        src = work / up.name
        src.write_bytes(up.getvalue())
        try:
            ss[key] = mailmod.read_mail(src)
        except Exception as e:  # noqa: BLE001 - a file that is not a readable mail is reported
            st.error(f"{T('error')}: {e}")
            return
    m = ss[key]
    c1, c2, c3 = st.columns([2, 1, 1])
    c1.markdown(f"**{T('mail_from')}:** {m.sender} {ltr(m.sender_email)}", unsafe_allow_html=True)
    c2.markdown(f"**{T('date')}:** {ltr(m.date)}", unsafe_allow_html=True)
    c3.markdown(f"**{T('mail_attachments')}:** {len(m.files)}")
    st.markdown(f"**{T('mail_subject')}:** {m.subject}")
    for w in m.warnings:
        st.warning(w)
    points = mailmod.review_points(m.latest)
    if points:
        st.markdown(f"**{T('mail_points')}**")
        for i, pt in enumerate(points, 1):          # dir=auto: English comments read left-to-right
            st.markdown(f'<div dir="auto">{i}. {html.escape(pt)}</div>', unsafe_allow_html=True)
    with st.expander(T("mail_text")):
        st.markdown(f'<div dir="auto" style="white-space: pre-wrap">{html.escape(m.latest[:20000])}</div>',
                    unsafe_allow_html=True)

    projects = [Project.load(f) for f in settings.projects if is_project_folder(f)]
    matches = mailmod.match_document(m, projects, prefer=ss.project_folder)
    if not matches:
        st.info(T("mail_no_documents"))
        return
    labels = {i: f"{mt.project.code} | {mt.document.code} | {mt.document.display_title(L)}"
              for i, mt in enumerate(matches)}
    idx = st.selectbox(T("mail_target"), list(labels), format_func=lambda i: labels[i], key=f"{key}_target")
    if matches[0].reasons:
        why = "; ".join(r.get(L) or r.get("en", "") for r in matches[0].reasons)
        st.caption(f"{T('mail_why')} ({ltr(matches[0].document.code)}): {why}", unsafe_allow_html=True)
    else:
        st.caption(T("mail_no_match"))
    p, d = matches[idx].project, matches[idx].document
    st.caption(T("mail_then_agent"))
    if not st.button(f"📎 {T('mail_file_only')}", key="mail_file_only"):
        return
    filed = mailmod.file_mail(d, m, up.getvalue(), up.name)
    warnings = []
    for rel in filed.attachments:
        warnings += index_source(d, d.path / rel)
    reindex(p, d)
    msgs = [("success", T("mail_filed").format(doc=isolate(d.code), n=len(filed.attachments)))]
    msgs += [("warning", w) for w in warnings]
    ss.mail_msgs = msgs
    ss.mail_done = {"project": p.folder, "doc": d.folder, "code": d.code}
    st.rerun()


def page_templates() -> None:
    """Browse title-block templates, preview, and save edited copies as user templates."""
    from sketch2cad.drawing import render
    from sketch2cad.templates import list_templates, load_template
    st.header(T("nav_templates"))
    templates = {x["id"]: x for x in list_templates()}
    tid = st.selectbox(T("titleblock"), list(templates),
                       format_func=lambda i: f"{i} - {(templates[i].get('name') or {}).get(L, '')}")
    tpl = load_template(tid)
    c1, c2 = st.columns([1, 1])
    with c1:
        text = st.text_area(T("template_yaml"), value=yaml.safe_dump(tpl, allow_unicode=True, sort_keys=False),
                            height=480, key=f"tpl_{tid}")
        new_id = st.text_input(T("new_id"), value=f"{tid}_my" if not tid.endswith("_my") else tid)
        if st.button(T("save_as_user")):
            try:
                data = yaml.safe_load(text)
                data["id"] = new_id
                folder = app_home() / "templates"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{new_id}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                                       encoding="utf-8")
                st.success(T("saved"))
            except Exception as e:  # noqa: BLE001
                st.error(f"{T('error')}: {e}")
    with c2:
        st.markdown(f"**{T('preview')}**")
        tmp = Path(tempfile.mkdtemp(prefix="s2c_tpl_"))
        spec = {"version": 1, "units": "mm", "scale": 1, "language": "both",
                "sheet": {"template": tid, "fields": {"title_en": "SAMPLE TITLE", "title_he": "כותרת לדוגמה"}},
                "elements": [], "dimensions": [], "legend": [], "notes": {"en": [], "he": []}, "params": {}}
        try:
            res = render(spec, tmp, "preview", formats=("png",))
            st.image(str(res["png"]), width="stretch")
        except Exception as e:  # noqa: BLE001
            st.warning(f"{T('error')}: {e}")


def page_catalog() -> None:
    """Catalog items with their sources; edit/add items into the project or user catalog."""
    st.header(T("nav_catalog"))
    p = current_project()
    cat = load_catalog(p)
    c1, c2 = st.columns([1, 3])
    category = c1.selectbox(T("category"), [""] + list(CATEGORIES), format_func=lambda k: k or T("all"))
    q = c2.text_input(T("filter_text"), key="cat_q").lower()
    items = [i for i in cat.items(category or None)
             if not q or q in json.dumps(i, ensure_ascii=False).lower()]
    st.dataframe([{"ID": i["id"], T("category"): i.get("category", ""), T("item"): Catalog.label(i, L),
                   T("submittal"): (i.get("source") or {}).get("submittal", ""),
                   T("status"): (i.get("source") or {}).get("status", ""),
                   T("without_source"): ", ".join(Catalog.missing_source(i))} for i in items],
                 width="stretch", hide_index=True)
    ids = [i["id"] for i in items]
    choice = st.selectbox(T("item"), [""] + ids, format_func=lambda k: k or T("new_item"))
    base = cat.get(choice) or {"id": "new_item", "category": "other", "name": {"en": "", "he": ""}, "unit": "pcs",
                               "dims": {}, "source": {"submittal": "", "status": "", "file": "", "fields": []}}
    base = {k: v for k, v in base.items() if not k.startswith("_")}
    text = st.text_area(T("item_yaml"), value=yaml.safe_dump(base, allow_unicode=True, sort_keys=False),
                        height=360, key=f"item_{choice}")
    targets = (["project"] if p else []) + ["user"]
    target = st.radio(T("save_to"), targets, horizontal=True,
                      format_func=lambda x: T("project_catalog") if x == "project" else T("user_catalog"))
    if st.button(T("save"), key="item_save"):
        try:
            item = yaml.safe_load(text)
            save_item(item, p if target == "project" else None)
            st.success(T("saved"))
        except Exception as e:  # noqa: BLE001
            st.error(f"{T('error')}: {e}")


def page_search() -> None:
    st.header(T("search_all"))
    projects = {Project.load(f).code: f for f in settings.projects if is_project_folder(f)}
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    q = c1.text_input(T("search_text"))
    prj = c2.selectbox(T("project"), [""] + list(projects), format_func=lambda k: k or T("all"))
    typ = c3.selectbox(T("doc_type"), [""] + list(DOC_TYPES),
                       format_func=lambda k: T("all") if not k else label_of(DOC_TYPES, k))
    stat = c4.selectbox(T("status"), [""] + list(STATUSES),
                        format_func=lambda k: T("all") if not k else label_of(STATUSES, k))
    hits = search.search(q, project_code=prj or None, type=typ or None, status=stat or None)
    st.caption(f"{len(hits)} {T('results')}")
    for h in hits:
        c1, c2, c3 = st.columns([2, 5, 1])
        c1.markdown(f"**{ltr(h.code)}**  \n{ltr(h.project_code)}", unsafe_allow_html=True)
        title = (h.title_he if L == "he" else h.title_en) or h.title_en or h.title_he
        c2.markdown(f"{title}  \n<small>{h.description}</small>", unsafe_allow_html=True)
        c3.button(T("open"), key=f"hit_{h.folder}", on_click=go, args=("document", h.project_folder, h.folder))


def page_settings() -> None:
    from sketch2cad import config
    st.header(T("settings"))
    ss.setdefault("set_data_home", str(app_home()))
    data_home = folder_input(T("data_home"), "set_data_home")
    st.caption(T("data_home_help"))
    ss.setdefault("set_root", settings.default_projects_root)
    root = folder_input(T("default_root"), "set_root")
    oda = st.text_input(T("oda_path"), value=settings.oda_converter_path)
    st.subheader(T("claude_runner"))
    st.caption(T("claude_runner_help"))
    claude_auto = st.checkbox(T("claude_auto"), value=bool(settings.claude_auto_run))
    claude_exe = st.text_input(T("claude_exe"), value=settings.claude_exe)
    from sketch2cad.claude_runner import detect_claude_exe
    detected = detect_claude_exe()
    if detected and not settings.claude_exe.strip():
        st.caption(T("claude_exe_detected").format(path=detected))
    elif not settings.claude_exe.strip():
        st.caption(T("claude_exe_missing"))
    accounts_text = st.text_area(T("claude_accounts"), value="\n".join(settings.claude_accounts), height=96)
    timeout = st.number_input(T("claude_timeout"), min_value=30, max_value=7200,
                              value=int(settings.claude_timeout_s or 600), step=30)
    if st.button(T("save"), key="settings_save", type="primary"):
        if oda.strip() and not (oda.strip().lower().endswith(".exe") and Path(oda.strip()).is_file()):
            st.error(T("oda_not_exe"))
            return
        settings.default_projects_root, settings.oda_converter_path = root, oda
        settings.claude_auto_run = bool(claude_auto)
        settings.claude_exe = claude_exe.strip()
        settings.claude_accounts = [line.strip() for line in accounts_text.splitlines() if line.strip()]
        settings.claude_timeout_s = int(timeout)
        settings.save()
        if data_home.strip() and Path(data_home).expanduser().resolve() != app_home().resolve():
            try:
                config.set_data_home(data_home)
                ss.settings_msg = T("data_home_changed")
                st.rerun()                       # reload settings from the new folder
            except OSError as e:
                st.error(f"{T('error')}: {e}")
        st.success(T("saved"))
    if ss.pop("settings_msg", None):
        st.success(f"{T('data_home_changed')}: {app_home()}")
    if st.button(T("rebuild_index")):
        st.success(f"{search.rebuild(settings)} {T('indexed')}")


{"projects": page_projects, "project": page_project, "document": page_document, "search": page_search,
 "import": page_import, "mail": page_mail, "templates": page_templates, "catalog": page_catalog,
 "settings": page_settings}[ss.page]()
