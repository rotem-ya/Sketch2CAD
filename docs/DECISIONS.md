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

Not started on this branch.
