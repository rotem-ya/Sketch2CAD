# -*- coding: utf-8 -*-
"""Sketch2CAD - local UI (Streamlit). Run:  streamlit run app/app.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sketch2cad import codes, search  # noqa: E402
from sketch2cad.config import Settings  # noqa: E402
from sketch2cad.documents import (DOC_TYPES, STATUSES, Document, add_source, create_document,  # noqa: E402
                                  list_documents, new_revision)
from sketch2cad.i18n import t  # noqa: E402
from sketch2cad.projects import Project, create_project, is_project_folder, relocate_project  # noqa: E402

st.set_page_config(page_title="Sketch2CAD", layout="wide")

settings = Settings.load()
ss = st.session_state
ss.setdefault("page", "projects")
ss.setdefault("project_folder", None)
ss.setdefault("doc_folder", None)
L = settings.ui_language


def T(key: str) -> str:
    return t(key, L)


if L == "he":
    st.markdown("""<style>
    .stApp, .stMarkdown, [data-testid="stSidebar"], [data-testid="stForm"], .stTabs, label, p, h1, h2, h3
    { direction: rtl; text-align: right; }
    [data-testid="stDataFrame"], code, input, textarea { direction: ltr; }
    </style>""", unsafe_allow_html=True)


def go(page: str, project: str | None = None, doc: str | None = None) -> None:
    ss.page = page
    if project is not None:
        ss.project_folder = project
    if doc is not None:
        ss.doc_folder = doc


def ltr(text: str) -> str:
    """Keep codes/paths left-to-right inside RTL text (otherwise "20117-W-X" shows as "W-X-20117")."""
    return f'<span dir="ltr" style="unicode-bidi:isolate">{text}</span>'


def label_of(table: dict, key: str) -> str:
    return table.get(key, {}).get(L, key)


def reindex(project: Project, doc: Document) -> None:
    search.index_document(project, doc)


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
    st.button(T("nav_projects"), on_click=go, args=("projects",), use_container_width=True)
    st.button(T("nav_search"), on_click=go, args=("search",), use_container_width=True)
    st.button(T("nav_settings"), on_click=go, args=("settings",), use_container_width=True)
    if ss.project_folder and is_project_folder(ss.project_folder):
        cur = Project.load(ss.project_folder)
        st.divider()
        st.caption(T("current_project"))
        st.button(f"{cur.code} - {cur.display_name(L)}", on_click=go, args=("project",),
                  use_container_width=True)


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
    with st.form("new_project"):
        c1, c2, c3 = st.columns(3)
        code = c1.text_input(T("project_code"))
        name_he = c2.text_input(T("name_he"))
        name_en = c3.text_input(T("name_en"))
        c1, c2 = st.columns(2)
        client = c1.text_input(T("client"))
        parent = c2.text_input(T("parent_folder"), value=settings.default_projects_root)
        c1, c2 = st.columns(2)
        pattern = c1.text_input(T("code_pattern"), value=codes.DEFAULT_PATTERN, help=T("code_pattern_help"))
        lang_mode = c2.selectbox(T("drawing_language"), ["both", "en", "he"],
                                 format_func=lambda x: T(f"lang_{x}"))
        if st.form_submit_button(T("create")):
            try:
                p = create_project(parent or ".", code, name_he, name_en, client=client,
                                   doc_code_pattern=pattern, language_mode=lang_mode)
                codes.seq_regex(pattern, prj=p.code)          # validates the pattern
                settings.register_project(p.path)
                go("project", p.folder)
                st.rerun()
            except Exception as e:  # noqa: BLE001 - show any validation error to the user
                st.error(f"{T('error')}: {e}")

    with st.expander(T("add_existing")):
        folder = st.text_input(T("folder_path"), key="existing_folder")
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
    tab_docs, tab_new, tab_settings = st.tabs([T("documents"), T("new_document"), T("project_settings")])

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

    with tab_settings:
        with st.form("proj_settings"):
            c1, c2, c3 = st.columns(3)
            name_he = c1.text_input(T("name_he"), value=p.name.get("he", ""))
            name_en = c2.text_input(T("name_en"), value=p.name.get("en", ""))
            client = c3.text_input(T("client"), value=p.client)
            c1, c2 = st.columns(2)
            pattern = c1.text_input(T("code_pattern"), value=p.doc_code_pattern, help=T("code_pattern_help"))
            lang_mode = c2.selectbox(T("drawing_language"), ["both", "en", "he"],
                                     index=["both", "en", "he"].index(p.language_mode),
                                     format_func=lambda x: T(f"lang_{x}"))
            disc_json = st.text_area(T("disciplines_json"), value=json.dumps(p.disciplines, ensure_ascii=False,
                                                                              indent=1), height=200)
            if st.form_submit_button(T("save")):
                try:
                    codes.seq_regex(pattern, prj=p.code)
                    p.name, p.client, p.doc_code_pattern, p.language_mode = (
                        {"he": name_he, "en": name_en}, client, pattern, lang_mode)
                    p.disciplines = json.loads(disc_json)
                    p.save()
                    st.success(T("saved"))
                except Exception as e:  # noqa: BLE001
                    st.error(f"{T('error')}: {e}")

        st.subheader(T("relocate"))
        mode = st.radio(T("relocate"), ["move", "link"], format_func=lambda m: T("relocate_move" if m == "move" else
                                                                       "relocate_link"), label_visibility="collapsed")
        new_loc = st.text_input(T("new_location"))
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
    tab_det, tab_src, tab_out, tab_rev = st.tabs([T("details"), T("sources"), T("outputs"), T("revisions")])

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
        files = st.file_uploader(T("upload"), accept_multiple_files=True)
        if files and st.button(T("add")):
            for f in files:
                add_source(d, name=f.name, data=f.getvalue())
            st.success(T("uploaded"))
            st.rerun()
        for rel in d.sources:
            fp = d.path / rel
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"`{rel}`")
            if fp.exists():
                c2.download_button(T("download"), fp.read_bytes(), file_name=fp.name, key=f"dl_{rel}")
                if fp.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    st.image(str(fp), width=320)

    with tab_out:
        if not d.outputs:
            st.info(T("no_outputs"))
        for kind, rel in d.outputs.items():
            fp = d.path / rel
            if fp.exists():
                st.download_button(f"{T('download')} {kind.upper()}", fp.read_bytes(), file_name=fp.name,
                                   key=f"out_{kind}")

    with tab_rev:
        st.table([{T("rev"): r["rev"], T("date"): r["date"], T("note"): r.get("note", "")} for r in d.revisions])
        note = st.text_input(T("rev_note"))
        if st.button(T("new_revision")):
            new_revision(d, p, note)
            reindex(p, d)
            st.rerun()


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
    st.header(T("settings"))
    with st.form("settings"):
        root = st.text_input(T("default_root"), value=settings.default_projects_root)
        oda = st.text_input(T("oda_path"), value=settings.oda_converter_path)
        key = st.text_input(T("api_key"), value=settings.anthropic_api_key, type="password")
        if st.form_submit_button(T("save")):
            settings.default_projects_root, settings.oda_converter_path, settings.anthropic_api_key = root, oda, key
            settings.save()
            st.success(T("saved"))
    if st.button(T("rebuild_index")):
        st.success(f"{search.rebuild(settings)} {T('indexed')}")


{"projects": page_projects, "project": page_project, "document": page_document, "search": page_search,
 "settings": page_settings}[ss.page]()
