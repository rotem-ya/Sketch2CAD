# -*- coding: utf-8 -*-
"""Sketch2CAD - local UI (Streamlit). Run:  streamlit run app/app.py"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sketch2cad import bom, codes, search, typical  # noqa: E402
from sketch2cad.catalog import CATEGORIES, Catalog, load_catalog, save_item  # noqa: E402
from sketch2cad.checks import run_checks  # noqa: E402
from sketch2cad.config import Settings, app_home  # noqa: E402
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


def current_project() -> Project | None:
    return Project.load(ss.project_folder) if ss.project_folder and is_project_folder(ss.project_folder) else None


def load_spec(doc: Document) -> dict | None:
    f = doc.path / "spec.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def save_spec(doc: Document, spec: dict) -> None:
    (doc.path / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")


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


def download(label: str, path: Path, key: str) -> None:
    if path and Path(path).exists():
        st.download_button(label, Path(path).read_bytes(), file_name=Path(path).name, key=key)


def show_converter_error(e: Exception) -> None:
    from sketch2cad.io.oda import ConverterNotFound
    st.warning(T("no_converter") if isinstance(e, ConverterNotFound) else f"{T('error')}: {e}")


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
    st.button(T("nav_import"), on_click=go, args=("import",), use_container_width=True)
    st.button(T("nav_templates"), on_click=go, args=("templates",), use_container_width=True)
    st.button(T("nav_catalog"), on_click=go, args=("catalog",), use_container_width=True)
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
            findings = run_checks(spec, cat)
            if not findings:
                st.success(T("no_findings"))
            icons = {"error": "🔴", "warning": "🟠", "info": "🔵"}
            for f in findings:
                el = f" - {ltr(f.element)}" if f.element else ""
                st.markdown(f"{icons[f.severity]} {getattr(f, L)}{el}", unsafe_allow_html=True)
    with tab_bom:
        if spec is None:
            st.info(T("no_spec"))
        else:
            rows = bom.build_bom(spec, cat)
            st.dataframe([{T("item"): r["no"], "ID": r["item"], T("description"): r[L] or r["en"],
                           T("qty"): r["qty"], T("units"): r["unit"], T("submittal"): r["source"]} for r in rows],
                         use_container_width=True, hide_index=True)
            xlsx = bom.bom_to_xlsx(rows, d.path / f"{d.code}_BOM.xlsx", lang=L, title=f"{d.code} Rev {d.rev}")
            download(T("excel"), xlsx, "bom_xlsx")

    with tab_out:
        if not d.outputs:
            st.info(T("no_outputs"))
        for kind, rel in d.outputs.items():
            download(f"{T('download')} {kind.upper()}", d.path / rel, f"out_{kind}")
        if (d.path / d.outputs.get("preview", "-")).exists():
            st.image(str(d.path / d.outputs["preview"]), use_container_width=True)

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
    """Spec from a typical detail / AI sketch reading / JSON, then render with a title-block template."""
    from sketch2cad.templates import list_templates
    spec = load_spec(d)
    mode = st.radio(T("spec_source"), ["typical", "ai", "json"], horizontal=True, index=2 if spec else 0,
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
    elif mode == "ai":
        usable = [s for s in d.sources if Path(s).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif",
                                                                     ".pdf", ".tif", ".tiff", ".bmp")]
        if not usable:
            st.info(T("ai_no_sources"))
        chosen = st.multiselect(T("ai_inputs"), usable, default=usable)
        notes = st.text_area(T("ai_notes"), value=d.description)
        has_key = bool(settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"))
        if not has_key:
            st.warning(T("ai_no_key"))
        if st.button(T("ai_draft"), disabled=not (chosen and has_key)):
            from sketch2cad.ai.sketch import draft_spec_from_images
            try:
                with st.spinner(T("ai_working")):
                    draft = draft_spec_from_images([d.path / s for s in chosen], notes, settings.anthropic_api_key,
                                                   catalog=cat, language=lang_mode)
                save_spec(d, draft.spec)
                (d.path / "ai_review.json").write_text(json.dumps(
                    {"assumptions": draft.assumptions, "questions": draft.questions, "warnings": draft.warnings},
                    ensure_ascii=False, indent=1), encoding="utf-8")
                spec = draft.spec
                st.success(T("spec_saved"))
            except Exception as e:  # noqa: BLE001 - API / parsing errors are shown to the user
                st.error(f"{T('error')}: {e}")
        review = d.path / "ai_review.json"
        if review.exists():
            data = json.loads(review.read_text(encoding="utf-8"))
            st.subheader(T("ai_review"))
            for key in ("assumptions", "questions"):
                if data.get(key):
                    st.markdown(f"**{T(key)}**")
                    for a in data[key]:
                        st.markdown(f"- {a.get(L) or a.get('en', '') if isinstance(a, dict) else a}")
            for w in data.get("warnings", []):
                st.warning(w)
    else:
        text = st.text_area(T("spec_json"), value=json.dumps(spec, ensure_ascii=False, indent=1) if spec else "",
                            height=400)
        if st.button(T("save"), key="spec_save"):
            try:
                spec = json.loads(text)
                save_spec(d, spec)
                st.success(T("saved"))
            except json.JSONDecodeError as e:
                st.error(f"{T('invalid_json')}: {e}")

    st.divider()
    if spec is None:
        st.info(T("no_spec"))
        return
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
        from sketch2cad.drawing import render
        d.titleblock = tb
        d.title_overrides = {k: v for k, v in (("title_en", ov_en), ("title_he", ov_he)) if v}
        spec.setdefault("sheet", {})["template"] = tb
        spec.setdefault("language", lang_mode)
        save_spec(d, spec)
        try:
            res = render(spec, d.path, f"{d.code}_Rev{d.rev}", project=p, document=d, catalog=cat,
                         formats=tuple(formats) or ("dxf",))
        except Exception as e:  # noqa: BLE001 - show render errors (bad spec) to the user
            st.error(f"{T('error')}: {e}")
            return
        d.outputs.update({("preview" if k == "png" else k): Path(v).name for k, v in res.items() if k != "warnings"})
        d.save()
        search.store_content(d, "spec.json", " ".join(
            (l.get("en", "") + " " + l.get("he", "")) for l in spec.get("legend", [])))
        reindex(p, d)
        st.success(T("rendered"))
        for w in res.get("warnings", []):
            st.warning(w)
    if (d.path / d.outputs.get("preview", "-")).exists():
        st.image(str(d.path / d.outputs["preview"]), use_container_width=True)


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
            st.image(str(out), use_container_width=True)
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
    if info.preview_png and Path(info.preview_png).exists():
        st.image(str(info.preview_png), use_container_width=True)
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
            st.image(str(res["png"]), use_container_width=True)
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
                 use_container_width=True, hide_index=True)
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
 "import": page_import, "templates": page_templates, "catalog": page_catalog, "settings": page_settings}[ss.page]()
