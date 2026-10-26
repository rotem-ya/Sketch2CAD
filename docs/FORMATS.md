# Sketch2CAD – internal formats and module APIs (developer reference)

All geometry is in **drawing units** (`units`: `"mm"` or `"m"`); a sheet is drawn at `1:scale`, so
one paper-mm = `scale` mm of model space (or `scale/1000` m). `scale` may be `"auto"`: the largest
standard scale that passes the same fit test as the render (smallest denominator of 1:1, 1:2, 1:5,
1:10, 1:15, 1:20, 1:25, 1:50, 1:75, 1:100, 1:125, 1:150, 1:200, 1:250, 1:400, 1:500, 1:750, 1:1000, …).
Each candidate is drawn and measured with `content_extents` — geometry, labels and dimension text —
against the space left after the notes block is fitted. The first scale that fits is used. On a
multi-view sheet the limit is the paper left after the other views. `"NTS"` is drawn at that same
fitted scale and labelled NTS.

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

Extra keys supported by the renderer:
- element type `circle` (`at`, `radius`) for small fittings;
- any element: `centerline`, `label_at`, `label_align`, `label_pinned`, `label_height` (paper mm), `leader`, `leader_to`; block size overrides `D`, `t`, `L`, `H`, `Dt`;
- `pipe`: `slope` (fall/run) and optional `slope_unit` `percent`, `inv` `[start, end]`, optional `inv_stations` `[s0, s1]` or `inv_at` `[[x,y],[x,y]]` for the run between those inverts (the full drawn length is not assumed), `dn`, `sn`, `cover`; a label `SLOPE 0.4%` is a percent; a text `INV` or `INV.` counts when `pipe` names the pipe, or when it lies within about 10 paper-mm of a centre-line (every such pipe, and only the ones that state a slope when any of them does);
- `nts` (true) or the text `NTS` / `N.T.S.` in `view`, `views`, the sheet scale field or a text element marks the sheet not-to-scale;
- `connector`: `offset`, `offset_from` (point or element id), `offset_axis` `"x"|"y"` (omit for a distance);
- `manhole`: `cover_item` (own BOM line), `cover_status`, `di` / `do`, `grate_size`; `connector`: `height`;
- spec root: `notes_at`, `sheet.origin`, `style` (text sizes in paper mm), `views` (see 1.4); dimension: `decimals`.

Element labels are placed deterministically. `label_at` is the first candidate, in drawing units. It is kept when the
text box (real paper height × scale, font-width estimate, both lines of an en/he label) is clear of other text,
dimension text, drawn linework and other leaders. Otherwise the label is nudged by at most about 25 mm on paper
and a leader is added or extended. A leader we add is rejected when it is longer than about 25 mm, or when it
crosses another text box, another leader, or a dimension line. Label boxes keep about 1.5 mm of clear paper
between them. When nothing within that distance is clear, the label stays at `label_at`, or at the default point
just above the element, and keeps the leader the spec asked for (`leader` or `leader_to`). An error names
that label. A label is never omitted, on one sheet or on a multi-view sheet. Nothing is placed outside the
drawing area: a candidate past that rectangle is rejected. Notes and the legend with no explicit position are
laid out after the labels, so their column moves aside instead of covering a label.
`label_pinned: true` keeps that point even when it collides. A `text` element is not moved and is not given a leader
(it is only an obstacle); `leader_to` on that element is the only leader it can have. Dimension text may only slide
along its own dimension line, or flip to the other side of that line. The same spec always produces the same sheet.

Paper text heights snap to the ISO 3098 set **2.5 / 3.5 / 5 / 7 mm** (a tie takes the larger size) only for
defaults. A `height` on an element, and any height the spec sets in `style`, are kept exactly; QA may still warn
about them. A default such as dimension text at 2.0 mm still snaps to 2.5. The title-block title rounds up
(3.29 stays 3.5, 4.1 becomes 5), then shrinks within that series or splits onto two lines so the English and the
Hebrew never overlap inside the field. ezdxf cannot create annotative text, so model-space text height is that
paper height times the view's scale denominator.
Entities are BYLAYER. Layers `0` and `DEFPOINTS` hold no geometry: below-grade work goes on `EXISTING_DASH`,
`NEW_DASH` or `PROPOSED_DASH` instead of a per-entity linetype.

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
On a multi-view sheet (1.4) `legend_at` and `notes_at` are paper millimetres from the frame's bottom-left,
because the legend and the notes are drawn in paper space.
On a single-view sheet the notes go below the drawing when a column beside it would leave the sheet,
and they are left undrawn (with an error warning) rather than placed past the frame.

### 1.4 Views – more than one scale on a sheet (experimental)

Multi-view sheets are experimental. The layout below is what the program draws, and a view that does not fit
is reported instead of being placed off the paper, but a dense real plan can still need a scale the engineer
sets by hand or a larger sheet.

`scale` on the spec root is the **primary** view (the root `elements` and `dimensions`). Optional `views`
adds further views on the same sheet. A spec with no `views` key is unchanged.

```json
"views": [
  {
    "id": "section-a",
    "title": {"en": "SECTION A-A", "he": "חתך א-א"},
    "scale": 50,
    "origin_on_sheet": [280, 40],
    "elements": [],
    "dimensions": []
  },
  {"id": "detail-1", "title": {"en": "DETAIL 1"}, "scale": "NTS", "fit_mm": 80, "elements": []}
]
```

| key | meaning |
|---|---|
| `id` | unique on the sheet |
| `title` | string or `{en, he}`, drawn above the view as one underlined line (`SECTION A-A / חתך א-א` when both languages are on), with `SCALE 1:50` or `SCALE NTS` centred underneath |
| `scale` | positive number, `"NTS"`, or `"auto"` |
| `fit_mm` | longer side on paper when `scale` is `NTS` (default 80). `"auto"` does not use it |
| `origin_on_sheet` | `[x, y]` paper mm from the frame's bottom-left, the bottom-left of the view window. The same key on the spec root fixes the primary view. Omitted: the primary view stays large; the other views share a row under it (titles on one baseline) or, when that does not fit, a column beside it. Auto-placed views stay at least 10 mm inside the frame and clear of the title block. A view that does not fit at its scale is not drawn off the sheet: it is fitted into the free rectangle that is left, so its entities stay inside a viewport. The warning quotes that rectangle. When a standard scale would actually fit that rectangle, the warning names it — 1:50, 1:100, 1:125, 1:200, 1:250, 1:500 or 1:1000. An NTS view is not given a 1:n suggestion |
| `elements`, `dimensions` | same objects as the root lists, in drawing units |

`scale: "auto"` keeps a readable floor: a detail is 1:50 or larger, a section (also an elevation or a
profile) is 1:100 or larger, and a plan is 1:500 or larger. The main view is scaled first, into the paper
left after those floors. A side view then grows finer if the sheet still packs. A callout or a label that
sits far from that view's geometry does not set its size, and `fit_mm` is only for `"NTS"`. Every viewport
uses a standard scale. An auto view that does not fit steps to the next coarser standard scale and the
viewport is shrunk to match; a fixed scale is kept exactly. When the floors and the notes do not all fit,
the sheet says so and names a larger sheet (A2/A1). The notes column is reserved before the views are
packed and is never given to a view. Notes try 2.0 mm, then English and Hebrew side by side, then a free
strip, and are reported as omitted only when none of those fit. A view that still has no viewport is
named once on the sheet (`DETAIL-1 NOT SHOWN – NEEDS A2 SHEET`) and QA reports `omitted_view`. The main
view's "would be 1:n" error is raised only when that view is placed coarser than its floor.

The sheet `scale` is the primary view. When another view uses a different scale, including `"auto"` or `"NTS"`,
the title block reads `AS SHOWN / כמצוין` instead of `SCALE 1:n`. A bare primary title such as `plan` is drawn
`PLAN / תוכנית`. Each view is real model-space geometry (text height = paper height × that view's scale).
Labels are placed before the view window is measured, so the viewport includes the label boxes and the leaders
(plus a 2 mm pad) instead of clipping them. A viewport is not drawn over the title block. The sheet is one
paper-space layout plotted 1:1: a `VIEWPORT` per view at that scale, plus the title block, view titles
(underlined, `SCALE 1:50` or `SCALE NTS` on the line below), legend and notes in paper space. Single-view sheets stay in model space,
which is what existing specs already produce.

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
Optional: `drawing_area`, `legend_area` (paper mm rectangles), per field `w` (wrap/shrink width), `he_y`, `align`.
User templates in `<app_home>/templates/*.yaml` override repo templates with the same id.
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
| `sketch2cad.checks` | `run_checks(spec, catalog, project=None) -> list[Finding]` | includes drawing QA |
| `sketch2cad.qa` | `review(spec, catalog, project)`, `write_overlay(spec, path, catalog, project)` | paper-space QA and `qa_overlay.png`; defaults in `QA_DEFAULTS`, overridden by `project.qa` |
| `sketch2cad.bom` | `build_bom(spec, catalog) -> list[row]`, `bom_to_xlsx(rows, path)` | |
| `sketch2cad.package` | `build_package(project, document, catalog, out_pdf, *, lang, spec)` | path |
| `sketch2cad.printset` | `build_print_set(project, out_pdf, *, lang, codes, statuses)` | path |
| `sketch2cad.typical` | `list_typicals()`, `generate(name, params, catalog=None) -> spec` | riser_connection, trench_to_manhole, valve_in_line |
| `sketch2cad.spec` | `normalize_spec(spec, *, units, language)`, `parse_reply(text)` | spec plus warnings / JSON object |
| `sketch2cad.search` | `store_content(doc, name, text)` | extracted file text kept in `content.txt` for the index |
| `sketch2cad.bridge` | `export_to_outbox(folder, *, drawing, request)`, `latest_inbox_reply(project_dir)`, `import_reply(reply, drawing, *, apply)` | package path / reply path / report dict |

## 5. Agent exchange – `<project>/agent_exchange/`

The drawing program does not call a model. It leaves a work package for an outside agent and reads the agent's reply back.

```text
<project>/
    agent_exchange/
        outbox/<YYYYMMDD_HHMMSS_ffffff>_<document code>/    # one work package (see docs/BRIDGE.md)
        inbox/*.json                                        # agent replies; the newest file is imported
```

`export --to-outbox` creates both folders and writes the package into `outbox`. The agent puts one JSON envelope (`spec`, `changes`, `assumptions`, `questions`) in `inbox`. `import --from-inbox` uses the newest `*.json` there. Every package `manifest.json` repeats the relative folders:

```json
"exchange": {"outbox": "agent_exchange/outbox", "inbox": "agent_exchange/inbox"}
```

Paths are relative to the project folder. A trial import does not change `spec.json`. `--apply` replaces it only after the trial render succeeded, and keeps the previous file in `spec_history/`. A DWG is written next to the trial DXF when a converter is installed; without one, the report says `dwg.produced: false` and the import still succeeds.

### Text rules (verified in AutoCAD)
- DXF/DWG: Hebrew stored in **logical order**; Hebrew table cells as MTEXT `\pxqr;` right-aligned.
- PDF/PNG preview (ezdxf drawing addon, which draws the glyph outlines itself, no shaping): convert Hebrew to
  visual order with `python-bidi` **after** replacing `%%c` → `Ø`. Do NOT do this for text drawn with matplotlib's own
  `ax.text` – matplotlib ≥ 3.11 shapes RTL itself (libraqm) and would reverse it again.
- PDF built with PyMuPDF HTML (package / print set): logical order; see `sketch2cad/pdfdoc.py` for RLM / LRE rules.
- Text style `ARIAL` (`arial.ttf`) for everything. Layout widths, including a missing style and ezdxf's default face, and QA ink widths (Latin and Hebrew) are measured with the bundled Liberation Sans 2.1.5 (the same file on every system), not with the installed system font. Diameter sign: `%%c`.
- Avoid parentheses around mixed Hebrew/Latin in Hebrew strings (use " - ").
