# Decisions

## IDEAS-12 — IFC export (IfcOpenShell, LGPL-3.0)

A Sketch2CAD drawing can leave as IFC4 without Revit. The same library that already reads IFC
(`ifcopenshell`, LGPL-3.0, imported, not vendored) writes the file. Sketch2CAD does not copy
IfcOpenShell's source, so its own licence is unchanged.

What is mapped, from the root `elements` and `dimensions` only (a view is another picture of the
sheet, not a second building):

| spec | IFC |
|---|---|
| `wall` | `IfcWall`, body extruded on the centre-line, one wall per straight segment |
| `opening` | `IfcOpeningElement` (`OPENING`) voiding its host via `IfcRelVoidsElement` |
| `opening` `kind` `door` / `window` | `IfcDoor` / `IfcWindow` filling the opening via `IfcRelFillsElement` |
| `floor` | `IfcSlab` (`FLOOR` by default; also `ROOF`, `LANDING`, `BASESLAB`). `z` is the top |
| `dimensions` chain | one `IfcAnnotation` per segment, `ObjectType` `DIMENSION`, with the measured length |

IFC4 has no dimension class. `ObjectType DIMENSION` plus a plan curve is the interchange an IFC4
reader can keep. IFC4X3's `PredefinedType` was not used: the reader already in this repository, and
the open tools this file is meant for, speak IFC4.

Units: the project length unit is the spec's unit (millimetres or metres). Geometry is passed to
IfcOpenShell in metres and stored in that unit. Area and volume units are square metres and cubic
metres, and the matching quantities (`GrossSideArea`, `NetVolume`, …) are in those units. A prefix
on `SQUARE_METRE` does not mean square millimetres, so areas are not written in the length unit.
Standard quantity sets (`Qto_WallBaseQuantities`, `Qto_SlabBaseQuantities`,
`Qto_OpeningElementBaseQuantities`, `Qto_DoorBaseQuantities`, `Qto_WindowBaseQuantities`) are there
so a quantities tool can read length, area and volume without guessing the solid. Each product also
carries a `Sketch2CAD` property set (`ElementId`, `Status`, `SpecType`) so a later edit can be
traced back to the spec. Pipes, valves and the other drawing symbols stay on the DXF; they are not
dropped and they are not given a fake IFC body.

`validate_ifc` reopens the file with IfcOpenShell and reports the schema, the length unit and the
entity counts. `render(..., formats` including `"ifc")` writes the file next to the DXF. If
IfcOpenShell is missing, the sheet is still produced and the warning says IFC was skipped.

ייצוא IFC4 בלי Revit: קיר, פתח (וממנו דלת או חלון), רצפה ומידת שרשרת. יחידת האורך כמו בשרטוט;
שטחים ונפחים במטרים, בסט כמויות תקני.

## IDEAS-13 — Pascal Editor (scan to BIM)

Pascal Editor (https://github.com/pascalorg/editor, MIT) is the open editor for the as-built side
of the same chain: a scan as a reference, then editable walls, doors, windows, slabs and ceilings,
then IFC. Sketch2CAD does not vendor Pascal and does not call it. `sketch2cad.io.pascal_bim`
writes a scene in the node shapes Pascal publishes (`site`, `building`, `level`, `wall`, `door`,
`window`, `slab`, `ceiling`, `scan`) and the IFC from IDEAS-12, including an `IfcCovering` ceiling.

Plan metres are Pascal's `[x, z]` (their Y is up). A door or window is a child of its wall; its
`position` is the centre in wall-local coordinates, which is the placement Pascal's schema
describes. Storeys become levels whose height is the gap up to the next storey. The stack starts
at zero, so a basement keeps its datum as `baseElevation`, and each level above adds only the extra
offset. Import reads that stack (order, height, baseElevation), not the elevation cached at export.
Node ids are `{prefix}_{slug}` and a second id that slugs the same gains a suffix, so `wall_1` and
`wall-1` stay two walls.

A scan path is not a `scan.url` unless it matches Pascal's asset allowlist (`https`, `asset://`,
`artifact://` plus a 64-digit hash, `blob:`, `data:image/`, an app-relative path, or
`http://localhost`). A Windows path stays in `metadata.sketch2cad.sourcePath`. `file:` and
`javascript:` are refused. `connection.json` lists the public CLI (`npx @pascal-app/cli editor
--no-open` and `pascal mcp connect`). It does not contain an API key, a bearer token or a
password; the local connector reads `~/.pascal/run` itself. The Linux CLI is not what Pascal
verifies, so this prototype does not launch it.

אב-טיפוס בלבד: סריקת as-built נשמרת כהפניה, וקירות, פתחים, רצפות ותקרות נכתבים כסצנה של Pascal
ולצד זה כ-IFC. אין מפתח ואין קריאה לרשת.

## IDEAS-14 — Clash and defect check (IfcClash, BCF 2.1)

The check is a separate call, `sketch2cad.io.clash.check_model`. `render` does not run it, and
`install.bat` does not install it. IfcClash (LGPL-3.0, https://docs.ifcopenshell.org/ifcclash.html)
is imported from `requirements-clash.txt`. Its source is not in this repository.

IfcClash can write BCF itself, and that path imports the PyPI package `bcf-client`, which is
GPL-3.0. This module does not import it. It keeps the records IfcClash returns (the two
IfcGuids, the class and name, the two points, the distance in metres) and writes a BCF 2.1 zip
in the buildingSMART shape: `bcf.version`, one folder per topic, `markup.bcf`, `viewpoint.bcfv`.
Paths inside the zip use `/`. The camera looks at the midpoint of the two clash points and is in
metres, which is the BCF unit, including when the IFC file is in millimetres.

Default sets are intersections, tolerance 2 mm, worst distance kept (`check_all`). Walls against
floors, walls against ceilings, and walls against walls. A pair that shares one Sketch2CAD
`ElementId` is two segments of one wall and is not a topic. A second file can be the opposing
group (`other=`): walls in the first file against walls, slabs and coverings in the second. Doors
and windows are not in a default set, because a filling sits in its opening.

A defect does not need IfcClash. It is one of: no geometric body, no base quantity set, a length
or area or volume that is not positive, or an `IfcOpeningElement` that no `IfcRelVoidsElement`
points at. Topic types are `Clash` and `Defect`, status `Open`. A missing IfcClash install still
writes the defects and says the clash check was skipped.

ifc-lite (MPL-2.0) was evaluated and is not a dependency. The note is in `docs/CLASH.md`, including
how Mida can read this BCF later. Mida is not called from here.

בדיקה נפרדת, לא חלק מההפקה: IfcClash מוצא חדירות, והתוצאה נכתבת כ-BCF בלי חבילת ה-GPL.
ליקוי הוא גוף חסר, כמות חסרה, כמות שאינה חיובית, או פתח בלי מארח. ifc-lite לא שולב.
