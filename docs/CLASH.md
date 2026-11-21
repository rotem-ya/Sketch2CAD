# Clash and defect check (optional)

`sketch2cad.io.clash.check_model` reads an IFC file, finds geometric clashes and model defects,
and writes one BCF 2.1 file. `render` does not call it. `install.bat` does not install the clash
package. A drawing is still produced when this check is absent.

```bash
pip install -r requirements-clash.txt
python -c "from sketch2cad.io.clash import check_model; check_model('model.ifc', 'model.bcf')"
```

The sample `examples/clash/crossing-walls.ifc` is the same shape the tests use. `w1` starts 100 mm
below the slab top, so it cuts `f1`. `w2` crosses `w1` and sits on the slab. `wL` is one wall in
two segments meeting at a corner, away from that crossing.

## What is checked

Clashes use IfcClash (LGPL-3.0, https://docs.ifcopenshell.org/ifcclash.html, `import ifcclash`).
The package is not copied into this repository. The default sets are intersections with a
tolerance of 2 mm, and IfcClash keeps the worst distance for each pair:

| set | group A | group B |
|---|---|---|
| Walls and floors | `IfcWall` | `IfcSlab` |
| Walls and ceilings | `IfcWall` | `IfcCovering` |
| Walls | `IfcWall` | the same walls |

A set is skipped when that class is not in the file. Doors and windows are not in a default set:
a filling sits in its opening, and reporting that as a clash hides the pairs that change a
quantity. `other=` adds one more set, walls in the first file against walls, slabs and coverings
in the second file.

Two products in the same file that carry the same Sketch2CAD `ElementId` are segments of one
wall. That pair is not a topic. The same id in a second file is still a clash: the id is local
to the drawing. A product is not reported against itself.

A defect does not use IfcClash. It needs the IfcOpenShell already used for IFC export.

| code | meaning |
|---|---|
| `no_body` | a wall, slab, covering, door, window, column or beam has no geometric items |
| `missing_quantity` | that element has no base quantity set |
| `non_positive_quantity` | a length, area or volume on it is zero or negative |
| `opening_without_host` | an `IfcOpeningElement` is not the opening of an `IfcRelVoidsElement` |

`clashes=False` writes defects only. If IfcClash is not installed, the defects are still written
and the warning says the clash check was skipped.

## BCF 2.1

IfcClash can write BCF through the PyPI package `bcf-client`. That package is GPL-3.0. This
module does not import it. It writes the zip from the records IfcClash already returned.

The zip contains `bcf.version` (`VersionId` 2.1) and one folder per topic. The folder name is
the topic GUID. Inside it: `markup.bcf` and `viewpoint.bcfv`. Paths use `/`, and the folder is a
directory entry. There is no snapshot.

| markup | value |
|---|---|
| `TopicType` | `Clash` or `Defect` |
| `TopicStatus` | `Open` |
| `Title` | the two classes and names, or the defect code and the element |
| `Description` | the kind, the distance in metres for a clash, and the IfcGuids |
| `Priority` | `High` when a clash is 50 mm or more, or the defect is a missing body, a dead quantity, or an opening without a host. `Medium` otherwise |
| `Header/File/Filename` | the IFC file name, not a disk path |
| viewpoint `Component/@IfcGuid` | each element in the topic |
| viewpoint `Lines/Line` | the two IfcClash points, in metres. A defect has no line |
| camera | metres, looking at the clash midpoint, or at the element's world placement for a defect (the storey elevation is included) |

`read_bcf` reads that shape back. An empty check is a BCF with no topic folders.

## ifc-lite

ifc-lite (https://github.com/LTplus-AG/ifc-lite, MPL-2.0) was considered as a browser viewer for
the model and these clashes. It does not fit this program, and it is not a dependency.

- The viewer is a WebGPU application (Rust to WASM, plus a TypeScript UI). Sketch2CAD is a
  local Streamlit process. Showing the model would be a second application, not a call in the
  check.
- Its clash panel (`@ifc-lite/clash`) runs its own detector and can export its own BCF. It is
  not a reader of the BCF 2.1 file this check writes, so the topics and the highlight would not
  be the same result.
- The public viewer at ifclite.com would send the model off the machine. A project file stays
  here. A local `ifc-lite view model.ifc` would keep the file local and can show the IFC, and
  it still would not open this BCF.
- MPL-2.0 allows use as a package. A file this repository changed would stay under MPL. That is
  acceptable as a dependency, and it is not a reason to vendor the project. Nothing in that
  package displays these topics today.

A later viewer fits only as a separate local page, on a machine whose browser has WebGPU, opening
the IFC and selecting the IfcGuids already stored in `viewpoint.bcfv`. Until ifc-lite reads a
BCF 2.1 file from outside, that page is a model viewer beside the check.

## How Mida can consume the BCF later

Mida (the quantity-surveying application) does not import IFC or BCF today. Its claims are
disruption claims over the site diary, and its quality records are lab certificates. This BCF
must not be written into either of those.

A later import in Mida can read the zip only. It does not need the IFC geometry.

| BCF | later Mida note |
|---|---|
| topic GUID | the id of the note, so a second export updates the same note |
| `Title` and `Description` | the text the person reads |
| `TopicType` `Clash` | two products overlap. A takeoff of both would count the shared volume twice. The distance in the description is metres, as IfcClash reported it |
| `TopicType` `Defect` | the element is not measurable yet (`no_body`, `missing_quantity`, `non_positive_quantity`, `opening_without_host`) |
| `TopicStatus` `Open` | a draft. The bill does not change until a person accepts the note |
| `Component/@IfcGuid` | the IFC `GlobalId`. On a file this program exported, the `Sketch2CAD` property set `ElementId` is the drawing element |
| `Header/File/Filename` | which model the note belongs to |
| `CreationAuthor` | `Sketch2CAD` |

The note sits beside the takeoff. Accepting it is a person. This repository does not call Mida
and does not store a key.

בדיקה רשות: חדירות דרך IfcClash, וליקויים שאפשר למדוד, בקובץ BCF אחד. ifc-lite לא מתאים כרגע
לצפייה בתוצאות האלה. מידה תוכל אחר כך לקרוא את ה-BCF כהערות לכמויות, בלי לגעת בתביעת הפרעות
ובלי תעודות מעבדה, ורק אחרי אישור.
