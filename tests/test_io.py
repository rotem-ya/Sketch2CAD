import os
from pathlib import Path

import ezdxf
import matplotlib
import pymupdf
import pytest

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from sketch2cad import compare as cmp  # noqa: E402
from sketch2cad.config import Settings  # noqa: E402
from sketch2cad.io import oda, pdf2dxf, readers  # noqa: E402

ODA_IN_CONTAINER = ("/tmp/claude-0/-home-user/63f41b80-ff49-5075-b696-9e9d0ee8cf6c/scratchpad/"
                    "dl/squashfs-root/AppRun")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    if not os.environ.get("ODA_CONVERTER") and Path(ODA_IN_CONTAINER).exists():
        monkeypatch.setenv("ODA_CONVERTER", ODA_IN_CONTAINER)


def needs_oda():
    if not oda.find_converter(Settings()):
        pytest.skip("ODA File Converter not available")


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def dxf_file(tmp_path):
    doc = ezdxf.new(setup=True)
    doc.styles.add("ARIAL", font="arial.ttf")
    doc.layers.add("PIPES")
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "PIPES"})
    msp.add_line((0, 0), (0, 500))
    msp.add_circle((500, 250), 100)
    msp.add_text("PIPE %%c160", height=40, dxfattribs={"style": "ARIAL"}).set_placement((50, 50))
    msp.add_mtext("\\pxqr;מגוף קיים", dxfattribs={"char_height": 40, "style": "ARIAL", "insert": (50, 400)})
    path = tmp_path / "plan.dxf"
    doc.saveas(path)
    return path


def _pdf_figure(path, *, lines=True, text=True, extra_line=False):
    """A5-landscape PDF; data coordinates are PDF points with y up."""
    w, h = 595.0, 420.0
    fig = Figure(figsize=(w / 72, h / 72))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.set_axis_off()
    if lines:
        ax.plot([100, 200], [100, 100], color="red", lw=1)       # 100 pt = 35.28 mm
        for i in range(25):
            ax.plot([300, 500], [150 + 8 * i, 150 + 8 * i], color="black", lw=0.5)
    if extra_line:
        ax.plot([50, 550], [380, 60], color="black", lw=2)
    if text:
        ax.text(100, 300, "PIPE Ø160", fontsize=12)
        ax.text(100, 330, "מגוף קיים", fontsize=12)
    fig.savefig(path)
    return path


@pytest.fixture
def vector_pdf(tmp_path):
    return _pdf_figure(tmp_path / "vector.pdf")


@pytest.fixture
def png_file(tmp_path):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200), False)
    pix.clear_with(255)
    pix.set_rect(pymupdf.IRect(20, 90, 280, 96), (0, 0, 0))
    pix.set_rect(pymupdf.IRect(140, 20, 146, 180), (0, 0, 0))
    path = tmp_path / "scan.png"
    pix.save(path)
    return path


@pytest.fixture
def raster_pdf(tmp_path, png_file):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=420)
    page.insert_image(page.rect, filename=str(png_file))
    path = tmp_path / "scan.pdf"
    doc.save(path)
    return path


@pytest.fixture
def ifc_file(tmp_path):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    import ifcopenshell.api.aggregate
    import ifcopenshell.api.context
    import ifcopenshell.api.geometry
    import ifcopenshell.api.project
    import ifcopenshell.api.root
    import ifcopenshell.api.spatial
    import ifcopenshell.api.unit

    api = ifcopenshell.api
    f = api.project.create_file(version="IFC4")
    project = api.root.create_entity(f, ifc_class="IfcProject", name="Demo Project")
    api.unit.assign_unit(f)
    model = api.context.add_context(f, context_type="Model")
    body = api.context.add_context(f, context_type="Model", context_identifier="Body",
                                   target_view="MODEL_VIEW", parent=model)
    site = api.root.create_entity(f, ifc_class="IfcSite", name="Site")
    building = api.root.create_entity(f, ifc_class="IfcBuilding", name="Pump House")
    storey = api.root.create_entity(f, ifc_class="IfcBuildingStorey", name="Ground Floor")
    api.aggregate.assign_object(f, relating_object=project, products=[site])
    api.aggregate.assign_object(f, relating_object=site, products=[building])
    api.aggregate.assign_object(f, relating_object=building, products=[storey])
    wall = api.root.create_entity(f, ifc_class="IfcWall", name="W1")
    api.geometry.edit_object_placement(f, product=wall)
    rep = api.geometry.add_wall_representation(f, context=body, length=5, height=3, thickness=0.2)
    api.geometry.assign_representation(f, product=wall, representation=rep)
    slab = api.root.create_entity(f, ifc_class="IfcSlab", name="S1")
    api.geometry.edit_object_placement(f, product=slab)
    rep = api.geometry.add_slab_representation(f, context=body, depth=0.2,
                                               polyline=[(0, 0), (5, 0), (5, 4), (0, 4)])
    api.geometry.assign_representation(f, product=slab, representation=rep)
    api.spatial.assign_container(f, relating_structure=storey, products=[wall, slab])
    path = tmp_path / "model.ifc"
    f.write(str(path))
    return path


# ------------------------------------------------------------------ readers
def test_read_dxf(dxf_file, tmp_path):
    info = readers.read_any(dxf_file, tmp_path / "out")
    assert info.kind == "dxf" and info.dxf_path == dxf_file
    assert "PIPE" in info.text and "160" in info.text
    assert "מגוף קיים" in info.text                              # logical order, no MTEXT codes
    assert "PIPES" in info.text
    assert info.preview_png.exists()
    assert "מגוף" in ezdxf.readfile(dxf_file).modelspace().query("MTEXT")[0].text   # source untouched


def test_read_pdf(vector_pdf, tmp_path):
    info = readers.read_any(vector_pdf, tmp_path / "out")
    assert info.kind == "pdf" and info.pages == 1
    assert "PIPE Ø160" in info.text and "מגוף" in info.text
    assert info.preview_png.exists()
    pix = pymupdf.Pixmap(str(info.preview_png))
    assert max(pix.width, pix.height) <= readers.MAX_PX


def test_read_image(png_file, tmp_path):
    info = readers.read_any(png_file, tmp_path / "out")
    assert info.kind == "image" and info.pages == 1 and info.preview_png.exists()


def test_read_ifc(ifc_file, tmp_path):
    info = readers.read_any(ifc_file, tmp_path / "out")
    assert info.kind == "ifc"
    assert "Demo Project" in info.text and "Ground Floor" in info.text and "IfcWall: 1" in info.text
    assert info.dxf_path.exists() and info.preview_png.exists()


def test_read_rvt_and_other(tmp_path):
    (tmp_path / "model.rvt").write_bytes(b"\0" * 10)
    info = readers.read_any(tmp_path / "model.rvt", tmp_path / "out")
    assert info.kind == "rvt" and "IFC" in info.warnings[0]
    (tmp_path / "notes.xyz").write_text("x")
    assert readers.read_any(tmp_path / "notes.xyz", tmp_path / "out").kind == "other"


def test_read_dwg_without_converter(dxf_file, tmp_path, monkeypatch):
    monkeypatch.setattr(oda, "find_converter", lambda settings=None: None)
    dwg = tmp_path / "plan.dwg"
    dwg.write_bytes(dxf_file.read_bytes())
    info = readers.read_any(dwg, tmp_path / "out")
    assert info.kind == "dwg" and info.preview_png is None and "ODA" in info.warnings[0]


# ------------------------------------------------------------------ pdf -> dxf
def test_pdf_to_dxf_vector(vector_pdf, tmp_path):
    out = pdf2dxf.pdf_to_dxf(vector_pdf, tmp_path / "v.dxf")
    msp = ezdxf.readfile(out).modelspace()
    assert len(msp.query("LINE LWPOLYLINE")) >= 26
    red = [e for e in msp.query("LINE") if e.dxf.layer == "PDF_FF0000"]
    assert len(red) == 1
    assert red[0].dxf.start.distance(red[0].dxf.end) == pytest.approx(35.28, abs=0.01)
    texts = [e.dxf.text for e in msp.query("TEXT")]
    assert "PIPE %%c160" in texts and "מגוף קיים" in texts
    assert not msp.query("IMAGE")


def test_pdf_to_dxf_scale_and_units(vector_pdf, tmp_path):
    msp = ezdxf.readfile(pdf2dxf.pdf_to_dxf(vector_pdf, tmp_path / "s.dxf", scale=50, units="m")).modelspace()
    red = next(e for e in msp.query("LINE") if e.dxf.layer == "PDF_FF0000")
    assert red.dxf.start.distance(red.dxf.end) == pytest.approx(35.278 * 50 / 1000, rel=1e-3)


def test_pdf_to_dxf_raster_fallback(raster_pdf, tmp_path):
    out = pdf2dxf.pdf_to_dxf(raster_pdf, tmp_path / "r.dxf", scale=10)
    msp = ezdxf.readfile(out).modelspace()
    image = msp.query("IMAGE")[0]
    assert image.dxf.u_pixel.magnitude * image.dxf.image_size.x == pytest.approx(595 * 25.4 / 72 * 10, rel=1e-3)
    assert (tmp_path / "r_underlay.png").exists()
    assert msp.query('TEXT[layer=="PDF_NOTE"]')


def test_calibrate():
    assert pdf2dxf.calibrate((0, 0), (3, 4), 500) == pytest.approx(100)
    with pytest.raises(ValueError):
        pdf2dxf.calibrate((1, 1), (1, 1), 10)


# ------------------------------------------------------------------ ifc -> dxf
def test_ifc_to_dxf(ifc_file, tmp_path):
    from sketch2cad.io.ifc2dxf import ifc_to_dxf

    msp = ezdxf.readfile(ifc_to_dxf(ifc_file, tmp_path / "plan.dxf")).modelspace()
    walls = [e for e in msp.query("LINE") if e.dxf.layer.endswith("IfcWall")]
    assert len(walls) == 4                                       # rectangle, collinear pieces merged
    xs = sorted(round(c) for e in walls for c in (e.dxf.start.x, e.dxf.end.x))
    assert xs[0] == 0 and xs[-1] == 5000                          # mm
    slabs = [e for e in msp.query("LINE") if e.dxf.layer.endswith("IfcSlab")]
    assert slabs and all(e.dxf.linetype == "DASHED" for e in slabs)


# ------------------------------------------------------------------ compare
def test_compare_pdf(tmp_path):
    a = _pdf_figure(tmp_path / "a.pdf", text=False)
    b = _pdf_figure(tmp_path / "b.pdf", text=False, extra_line=True)
    out, ratio = cmp.compare(a, a, tmp_path / "same.png")
    assert out.exists() and ratio == 0
    out, ratio = cmp.compare(a, b, tmp_path / "diff.png")
    assert ratio > 0.05
    pix = pymupdf.Pixmap(str(out))
    assert any(pix.pixel(x, y) == (0, 170, 0) for x in range(0, pix.width, 3) for y in range(0, pix.height, 3))


def test_compare_dxf(dxf_file, tmp_path):
    doc = ezdxf.readfile(dxf_file)
    doc.modelspace().add_line((0, 500), (1000, 500), dxfattribs={"layer": "PIPES"})
    for circle in doc.modelspace().query("CIRCLE"):
        doc.modelspace().delete_entity(circle)
    b = tmp_path / "b.dxf"
    doc.saveas(b)
    assert cmp.compare(dxf_file, dxf_file, tmp_path / "same.png")[1] == 0
    assert cmp.compare(dxf_file, b, tmp_path / "diff.png")[1] > 0
    summary = cmp.compare_dxf_entities(dxf_file, b)
    assert summary["added"] == 1 and summary["removed"] == 1
    assert summary["by_type"]["LINE"] == {"added": 1, "removed": 0}
    assert summary["by_type"]["CIRCLE"] == {"added": 0, "removed": 1}
    assert summary["by_layer"]["PIPES"]["added"] == 1


def test_compare_pdf_with_png(vector_pdf, png_file, tmp_path):
    out, ratio = cmp.compare(vector_pdf, png_file, tmp_path / "mixed.png")
    assert out.exists() and 0 < ratio <= 1


# ------------------------------------------------------------------ ODA
def test_find_converter_none(monkeypatch):
    monkeypatch.delenv("ODA_CONVERTER", raising=False)
    monkeypatch.setattr(oda.shutil, "which", lambda name: None)
    monkeypatch.setattr(oda.os, "name", "posix")
    assert oda.find_converter(Settings()) is None


def test_find_converter_from_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("ODA_CONVERTER", raising=False)
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_bytes(b"")
    assert oda.find_converter(Settings(oda_converter_path=str(exe))) == str(exe)


def test_convert_without_converter(dxf_file, monkeypatch):
    monkeypatch.setattr(oda, "find_converter", lambda settings=None: None)
    with pytest.raises(oda.ConverterNotFound):
        oda.convert(dxf_file, "dwg")


def test_oda_round_trip(dxf_file, tmp_path):
    needs_oda()
    dwg = oda.convert(dxf_file, "dwg", tmp_path / "dwg")
    assert dwg.suffix == ".dwg" and dwg.stat().st_size > 0
    back = oda.convert(dwg, "dxf", tmp_path / "back")
    msp = ezdxf.readfile(back).modelspace()
    assert len(msp.query("LINE")) == 2 and len(msp.query("CIRCLE")) == 1
    info = readers.read_any(dwg, tmp_path / "read")
    assert info.kind == "dwg" and "מגוף" in info.text and info.preview_png.exists()


@pytest.mark.skipif(os.name == "nt", reason="fake console is a POSIX shell script")
def test_autocad_core_console_fallback(tmp_path, monkeypatch):
    """No ODA: convert() drives accoreconsole with /i <file> /s <script>; SAVEAS target comes from the script."""
    fake = tmp_path / "accoreconsole"
    fake.write_text('#!/bin/sh\nout=$(sed -n \'s/^"\\(.*\\)"$/\\1/p\' "$4")\ncp "$2" "$out"\n')
    fake.chmod(0o755)
    monkeypatch.setattr(oda, "find_converter", lambda settings=None: None)
    monkeypatch.setenv("AUTOCAD_CORECONSOLE", str(fake))
    src = tmp_path / "חיבור.dxf"
    src.write_text("0\nEOF\n")
    out = oda.convert(src, "dwg", tmp_path / "out")
    assert out == tmp_path / "out" / "חיבור.dwg" and out.read_text() == "0\nEOF\n"
    script = oda.autocad_script("dxf", Path("C:/t/out.dxf"))
    assert script.splitlines() == ["FILEDIA 0", "_.SAVEAS", "DXF", "", '"C:/t/out.dxf"']
