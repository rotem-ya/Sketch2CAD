# Sketch2CAD – internal formats and module APIs (developer reference)

All geometry is in **drawing units** (`units`: `"mm"` or `"m"`); a sheet is drawn at `1:scale`, so
one paper-mm = `scale` mm of model space (or `scale/1000` m).

## 1. Drawing spec – `spec.json` (one per document)

```json
{
  "version": 1,
  "units": "mm",
  "scale": 15,
  "paper": "A3",
  "language": "both",                     // "en" | "he" | "both"
  "view": "elevation",                    // free text, shown in the title
  "sheet": {"template": "a3_simple", "fields": {"title_en": "...", "title_he": "...", "line3": "..."}},
  "elements": [ ... ],                    // see 1.1
  "dimensions": [ ... ],                  // see 1.2
  "legend": [ ... ],                      // see 1.3
  "notes": {"en": ["..."], "he": ["..."]},
  "params": {"cover_min": 1200, "grade_y": 0, "max_discharge_distance": 20000}
}
```

### 1.1 Elements – common keys
`id` (unique string), `type`, `status`: `"existing" | "new" | "proposed"` (proposed is drawn red),
`below_grade`: bool (drawn dashed), `item`: catalog id (optional – gives dimensions, BOM line, source),
`label`: `{"en": "...", "he": "..."}` (optional free text near the element).

| type | geometry keys | notes |
|---|---|---|
| `pipe` | `points` [[x,y],…] polyline, `od` | double line; length goes to the BOM |
| `bend` | `center`, `radius`, `start_angle`, `end_angle` (deg, CCW), `od` | |
| `flange` | `at`, `rotation` (0 = pipe axis along X) | size from `item` (`flange.D`, thickness `flange.t`) |
| `gate_valve` | `at`, `rotation`, `view`: `"elevation"\|"plan"` | `dims.L`, `dims.H`, `dims.Dt`, `flange.D` from item |
| `connector` | `at`, `rotation`, `length` | e.g. Golan PEX-steel flange connector |
| `manhole` | `at`, `cover`: `"closed"\|"grate"` | `dims.Di` (inner), `dims.Do` (outer) from item |
| `trench_drain` | `points`, `width` | grate ticks along the run |
| `rect` | `corners` [[x1,y1],[x2,y2]], `style`: `"solid"\|"dashed"`, `hatch`: `null\|"sand"\|"concrete"` | building, thrust block, sand |
| `polyline` | `points`, `style`: `"solid"\|"dashed"\|"berm"\|"ground"` | berm = ticks; ground = grade hatch ticks |
| `text` | `at`, `text` {en,he}, `height` (paper mm), `align` | |
| `flow_arrow` | `at`, `angle`, `length` | |
| `north_arrow` | `at`, `size` | |
| `weld` | `at`, `axis`: `"v"\|"h"`, `od` | weld mark across a pipe |
| `break` | `at`, `axis`, `od` | pipe break mark |

### 1.2 Dimensions
```json
{"type": "chain", "points": [[x,y], ...], "axis": "h" | "v", "base": 1120, "texts": ["", "~<>", ...]}
```
`base` = y (axis h) or x (axis v) of the dimension line; `texts[i]` overrides segment i (`<>` = measured value, `""` = default).

### 1.3 Legend (numbered balloons + bilingual table)
```json
{"no": 6, "targets": [[x,y], ...], "balloons": [[x,y], ...], "en": "GATE VALVE ...", "he": "מגוף ...",
 "item": "avk_0661_dn150"}
```
Each balloon i gets a leader to targets[i]. The legend table lists No | DESCRIPTION | תיאור.
Table placement: `legend_at` [x, y] in the spec root (top-left of the table), optional.

## 2. Catalog – `catalog/*.yaml` (repo) + optional `<project>/catalog.yaml`

```yaml
- id: avk_0661_dn150
  category: gate_valve
  name: {en: "Gate valve AVK 06/61 DN150 PN16", he: "מגוף טריז AVK 06/61 קוטר 150 דרג 16"}
  manufacturer: AVK
  supplier: Mendelson
  part_no: AV0661D6
  dn: 150
  pn: 16
  unit: pcs                      # pcs | m
  material: "Ductile iron GGG-50, epoxy 250um"
  flange: {std: PN16, D: 285, PCD: 240, holes: 8, hole_d: 23, t: 24}
  dims: {L: 210, H: 448, Dt: 212}
  standards: ["DIN 3352-4", "SI 61", "SI 5452"]
  source: {submittal: "07 00 00-7.1", status: "approved B", file: "0661_standard_Israel.pdf",
           drive_id: "1D3kthyuegFACwBuO83L1YqW8xYo6yYE_", fields: [L, H, Dt, D, PCD]}
```
API (`sketch2cad.catalog`): `load_catalog(project=None) -> Catalog`; `Catalog.get(id) -> dict`;
`Catalog.items(category=None)`; `Catalog.missing_source(item) -> list[str]`.

## 3. Title-block templates – `templates/*.yaml`

```yaml
id: a3_simple
name: {en: "A3 simple", he: "A3 פשוט"}
paper: {w: 420, h: 297}                # paper mm
margin: 10
frame_width: 0.7
blocks:                                 # rectangles of the title block, paper mm from bottom-left of frame
  - {x: 0, y: 0, w: 400, h: 22, lines: [y: 11]}
fields:                                 # text fields (paper mm, inside the frame)
  - {key: title, x: 4, y: 14, h: 3.5, en: "{title_en}", he: "{title_he}"}
  - {key: project, x: 4, y: 5, h: 2.5, en: "{project_name_en} | {code} Rev {rev} | SCALE 1:{scale}"}
```
Values available for `{…}`: code, rev, title_en, title_he, project_code, project_name_en,
project_name_he, client, date, scale, drawn, checked + anything in `sheet.fields`.

## 4. Module APIs

| module | function | returns |
|---|---|---|
| `sketch2cad.drawing` | `render(spec, out_dir, basename, *, project=None, document=None, catalog=None, formats=("dxf","pdf","dwg","png"))` | dict format → path |
| `sketch2cad.templates` | `list_templates()`, `load_template(id)`, `draw_titleblock(msp, template, values, scale, units)` | |
| `sketch2cad.io.oda` | `find_converter(settings=None)`, `convert(src, fmt, out_dir=None)` (fmt `"dwg"\|"dxf"`) | path |
| `sketch2cad.io.readers` | `read_any(path, out_dir) -> FileInfo(kind, pages, text, preview_png, dxf_path)` | |
| `sketch2cad.io.pdf2dxf` | `pdf_to_dxf(pdf, out, page=0, scale=1, units="mm", raster_fallback=True)` | path |
| `sketch2cad.io.ifc2dxf` | `ifc_to_dxf(ifc, out, cut_height=1.2, mode="plan")` | path |
| `sketch2cad.compare` | `compare(a, b, out_png)` (PDF/DXF/PNG) | path + change ratio |
| `sketch2cad.checks` | `run_checks(spec, catalog) -> list[Finding]` | |
| `sketch2cad.bom` | `build_bom(spec, catalog) -> list[row]`, `bom_to_xlsx(rows, path)` | |
| `sketch2cad.package` | `build_package(project, document, catalog, out_pdf, *, lang, spec)` | path |
| `sketch2cad.printset` | `build_print_set(project, out_pdf, *, lang, codes, statuses)` | path |
| `sketch2cad.typical` | `list_typicals()`, `generate(name, params, catalog=None) -> spec` | riser_connection, trench_to_manhole, valve_in_line |
| `sketch2cad.ai.sketch` | `draft_spec_from_images(paths, notes, api_key, *, catalog, units, language) -> Draft` | `Draft.spec`, `.assumptions`, `.questions`, `.warnings` |
| `sketch2cad.search` | `store_content(doc, name, text)` | extracted file text kept in `content.txt` for the index |

### Text rules (verified in AutoCAD)
- DXF/DWG: Hebrew stored in **logical order**; Hebrew table cells as MTEXT `\pxqr;` right-aligned.
- PDF/PNG (matplotlib): convert Hebrew to visual order with `python-bidi` **after** replacing `%%c` → `Ø`.
- Text style `ARIAL` (`arial.ttf`) for everything. Diameter sign: `%%c`.
- Avoid parentheses around mixed Hebrew/Latin in Hebrew strings (use " - ").
