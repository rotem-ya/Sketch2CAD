import json

import pytest

from sketch2cad import codes, search
from sketch2cad.config import Settings
from sketch2cad.documents import Document, add_source, create_document, list_documents, new_revision
from sketch2cad.projects import Project, create_project, relocate_project


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))


# ------------------------------------------------------------------ codes
def test_render_and_next_code():
    pat = "{PRJ}-{DISC}-{SUBJ}-{SEQ:02}"
    assert codes.render(pat, prj="20117", disc="W", subj="camel", seq=1) == "20117-W-CAMEL-01"
    code, seq = codes.next_code(pat, ["20117-W-CAMEL-01", "20117-W-CAMEL-03", "20117-D-X-07"],
                                prj="20117", disc="W", subj="camel")
    assert (code, seq) == ("20117-W-CAMEL-04", 4)


def test_empty_token_leaves_no_double_dash():
    assert codes.render("{PRJ}-{DISC}-{SUBJ}-{SEQ:03}", prj="20117", disc="D", subj="", seq=5) == "20117-D-005"


def test_next_rev_skips_i_and_o():
    assert codes.next_rev(None) == "A"
    assert codes.next_rev("H") == "J"
    assert codes.next_rev("N") == "P"
    assert codes.next_rev("2") == "3"


# ------------------------------------------------------------------ projects & documents
def test_project_folder_structure(tmp_path):
    p = create_project(tmp_path, "20117", "חצרים", "Hatzerim")
    assert p.path.name == "20117_Hatzerim"
    for sub in ("00_Sources", "10_Drawings", "20_Imports", "90_Archive"):
        assert (p.path / sub).is_dir()
    data = json.loads((p.path / "project.json").read_text(encoding="utf-8"))
    assert data["name"]["he"] == "חצרים" and "folder" not in data
    assert Project.load(p.path).code == "20117"
    with pytest.raises(FileExistsError):
        create_project(tmp_path, "20117", "", "Hatzerim")


def test_documents_codes_sources_revisions(tmp_path):
    p = create_project(tmp_path, "20117", "", "Hatzerim")
    d1 = create_document(p, title_he="חיבור לגמל", title_en="Camel connection", discipline="W", subject="CAMEL",
                         type="as-made", tags=["RFI-0021"])
    d2 = create_document(p, title_en="Camel detail", discipline="W", subject="CAMEL")
    assert (d1.code, d2.code) == ("20117-W-CAMEL-01", "20117-W-CAMEL-02")
    assert [d.code for d in list_documents(p)] == ["20117-W-CAMEL-01", "20117-W-CAMEL-02"]

    src = tmp_path / "sketch.jpg"
    src.write_bytes(b"jpg")
    assert add_source(d1, src) == "sources/sketch.jpg"
    assert add_source(d1, src) == "sources/sketch_1.jpg"          # never overwrites
    assert add_source(d1, name="upload.pdf", data=b"%PDF") == "sources/upload.pdf"

    (d1.path / "out.dwg").write_bytes(b"dwg")
    d1.outputs = {"dwg": "out.dwg"}
    assert new_revision(d1, p, "QA comments") == "B"
    assert (p.path / "90_Archive" / "20117-W-CAMEL-01_RevA" / "out.dwg").exists()
    reloaded = Document.load(d1.path)
    assert reloaded.rev == "B" and reloaded.revisions[-1]["note"] == "QA comments"
    assert len(reloaded.sources) == 3


def test_relocate_project(tmp_path):
    p = create_project(tmp_path / "a", "100", "", "Demo")
    create_document(p, title_en="x", discipline="G")
    moved = relocate_project(p, tmp_path / "b")
    assert moved.path == (tmp_path / "b" / "100_Demo").resolve()
    assert len(list_documents(moved)) == 1
    relinked = relocate_project(moved, moved.path, move_files=False)
    assert relinked.code == "100"


# ------------------------------------------------------------------ search
def test_search_hebrew_english_prefix_and_filters(tmp_path):
    s = Settings.load()
    p = create_project(tmp_path, "20117", "חצרים", "Hatzerim")
    s.register_project(p.path)
    create_document(p, title_he="חיבור לגמל קיים", title_en="Connection to existing camel", discipline="W",
                    subject="CAMEL", description="RFI-0021 as made", tags=["6in"])
    create_document(p, title_he="תעלות ניקוז מבנה P", title_en="Trench drains bldg P", discipline="D",
                    subject="BLDGP", type="proposal")
    assert search.rebuild(s) == 2
    assert [h.code for h in search.search("cam")] == ["20117-W-CAMEL-01"]
    assert [h.code for h in search.search("ניקוז")] == ["20117-D-BLDGP-01"]
    assert [h.code for h in search.search("RFI")] == ["20117-W-CAMEL-01"]
    assert len(search.search("", project_code="20117")) == 2
    assert [h.code for h in search.search("", type="proposal")] == ["20117-D-BLDGP-01"]
    assert search.search("nothing-here") == []
    assert [h.code for h in search.search("גמל")] == ["20117-W-CAMEL-01"]       # "לגמל" in the title


def test_hebrew_variants():
    v = search.hebrew_variants("חיבור לגמל והשוחה").split()
    assert {"גמל", "השוחה", "שוחה"} <= set(v) and "חיבור" not in v
    assert search.hebrew_variants("camel 6in") == ""
