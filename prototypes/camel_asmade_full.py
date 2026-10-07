# -*- coding: utf-8 -*-
"""
Hatzerim 20117 - As-Made drawing: connection to existing Base Water Supply "Camel" (6" / 160 mm).
Generates DXF (R2018) + PNG preview. DWG is produced by ODA File Converter.
Units: mm. Sheet: PLAN VIEW, DEVELOPED ELEVATION (1:10), DETAIL A (NTS), BOM, NOTES - A1.

Component data from the approved submittals (Drive: "הגשות/00מאושר"):
  - Gate valves  : AVK 06/61 DN150 PN16 (Mendelson AV0661D6), Sub 07 00 00-7.1 (code B)
                   L=210, H=448, Dt=212, D=285, PCD=240, 8xD23
  - Steel elbows : 6" LR 90 BW Sch40 A234 WPB, ASME B16.9, A=229, OD 168.3 x 7.11, cement lined
                   (Mendelson 1018005), Sub 57 00 00-11 (code B)
  - Transition   : Golan steel flange connector 160 x 6" (PEX5081-160 / SKU 50816060), ductile iron,
                   ASA150+BS drilling - dimensions NOT in submittal (TBC)
  - Pexgol pipe  : PE-Xa 160 class 10 (PA-1609.9BLK) OD160 x 9.9, Sub 57 00 00-8
  - Flanges      : 6" PN16 D=285, PCD=240, 8 holes, t=24 (Plasson 09903160 data)
Site geometry (heights / lengths) is estimated from photos and remains TBC.
"""
import math
import sys
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

OUT = Path(__file__).parent
REV = "C"

# ------------------------------------------------------------------ component data (from submittals)
OD = 168.3          # 6" steel pipe OD
WALL = 7.11         # 6" Sch40 / STD wall
R_P = OD / 2
FL_D = 285          # flange OD 6" PN16
FL_PCD = 240        # bolt circle
FL_T = 24           # flange thickness (Plasson 09903160)
VALVE_FTF = 210     # AVK 06/61 DN150 face-to-face L
VALVE_H = 448       # AVK 06/61 DN150 axis to stem top H
VALVE_DT = 212      # AVK 06/61 DN150 Dt (bonnet width)
R_ELB = 229         # 6" LR 90 elbow centre-to-end A (ASME B16.9)
PEX_OD = 160        # Pexgol 160 class 10
PEX_E = 9.9
EXIST_OD = 160      # existing 160 mm riser pipes of the camel
GOLAN_L = 250       # Golan connector body length - NOT IN SUBMITTAL (TBC)

# ------------------------------------------------------------------ site geometry (TBC on site)
Y_HEADER = 850      # camel header centreline above grade
Y_BRANCH = 500      # new branch centreline above grade
Y_LOWV = 260        # top face of lower valve flange (valve on existing leg)
Y_BURIED = -1284    # buried run centreline: 1.20 m min cover to crown (spec 57.06) + R 84
X_CENTER = 550      # camel centre riser
X_RIGHT = 1300      # camel right leg
X_DOWN = -900       # new branch vertical drop (developed elevation)
X_TRANS = -1450     # buried steel/Pexgol transition flange face (developed elevation)
X_END = -2450       # end of drawn Pexgol (elevation)

PLAN_Y0 = 2950      # plan view: camel header centreline
PLAN_DROP = 900     # plan: left leg -> vertical drop of new branch
PLAN_TRANS = 550    # plan: drop -> transition flange face
PLAN_END = 1450     # plan: drop -> end of drawn Pexgol

DET_X, DET_Y, DET_K = 3780, 150, 3.0    # Detail A origin (flange interface) and enlargement factor

TXT = 25            # 2.5 mm @ 1:10
TXT_L = 40
TXT_T = 60

doc = ezdxf.new("R2018", setup=True)
doc.units = ezdxf.units.MM
msp = doc.modelspace()

LAYERS = {
    "EXIST": 8, "NEW-STEEL": 1, "NEW-VALVE": 5, "PEXGOL": 6, "CENTER": 2,
    "DIM": 3, "TEXT": 7, "GROUND": 32, "CONCRETE": 9, "SAND": 42,
    "WRAP": 30, "FRAME": 7, "CALLOUT": 4, "TBC": 1, "HIDDEN": 8, "SECTION": 7,
}
for name, col in LAYERS.items():
    lay = doc.layers.add(name, color=col)
    if name == "CENTER":
        lay.dxf.linetype = "CENTER"
    if name in ("CONCRETE", "WRAP", "HIDDEN"):
        lay.dxf.linetype = "DASHED"
doc.header["$LTSCALE"] = 10


# ------------------------------------------------------------------ helpers
def line(p1, p2, layer, **kw):
    msp.add_line(p1, p2, dxfattribs={"layer": layer, **kw})


def pipe(p1, p2, r, layer, center=True):
    (x1, y1), (x2, y2) = p1, p2
    L = math.hypot(x2 - x1, y2 - y1)
    nx, ny = -(y2 - y1) / L * r, (x2 - x1) / L * r
    line((x1 + nx, y1 + ny), (x2 + nx, y2 + ny), layer)
    line((x1 - nx, y1 - ny), (x2 - nx, y2 - ny), layer)
    if center:
        line(p1, p2, "CENTER")


def bend(c, R, a0, a1, r, layer, center=True):
    """Arc bend, angles in degrees CCW from a0 to a1."""
    msp.add_arc(c, R + r, a0, a1, dxfattribs={"layer": layer})
    msp.add_arc(c, R - r, a0, a1, dxfattribs={"layer": layer})
    if center:
        msp.add_arc(c, R, a0, a1, dxfattribs={"layer": "CENTER"})


def weld(p, axis, r, layer):
    """Weld line across pipe with tick marks (axis 'v' or 'h')."""
    x, y = p
    if axis == "v":
        line((x - r - 15, y), (x + r + 15, y), layer)
        for dx in range(int(-r), int(r) + 1, 25):
            line((x + dx, y), (x + dx + 12, y - 12), layer)
    else:
        line((x, y - r - 15), (x, y + r + 15), layer)
        for dy in range(int(-r), int(r) + 1, 25):
            line((x, y + dy), (x + 12, y + dy + 12), layer)


def break_mark(p, axis, r, layer):
    x, y = p
    if axis == "v":
        pts = [(x - r - 20, y), (x - 15, y), (x - 5, y + 25), (x + 5, y - 25), (x + 15, y), (x + r + 20, y)]
    else:
        pts = [(x, y - r - 20), (x, y - 15), (x + 25, y - 5), (x - 25, y + 5), (x, y + 15), (x, y + r + 20)]
    msp.add_lwpolyline(pts, dxfattribs={"layer": layer})


def text(s, p, h=TXT, layer="TEXT", align=TextEntityAlignment.LEFT, rot=0):
    t = msp.add_text(s, height=h, dxfattribs={"layer": layer, "rotation": rot, "style": "OpenSans"})
    t.set_placement(p, align=align)
    return t


def mtext(s, p, width, h=TXT, layer="TEXT"):
    m = msp.add_mtext(s, dxfattribs={"layer": layer, "char_height": h, "width": width, "style": "OpenSans"})
    m.set_location(p, attachment_point=1)
    return m


def callout(n, target, at, tbc=False):
    """Numbered balloon with leader from balloon edge to target point."""
    rr = 38
    dx, dy = target[0] - at[0], target[1] - at[1]
    L = math.hypot(dx, dy)
    start = (at[0] + dx / L * rr, at[1] + dy / L * rr)
    line(start, target, "CALLOUT")
    msp.add_circle(target, 6, dxfattribs={"layer": "CALLOUT"})
    msp.add_circle(at, rr, dxfattribs={"layer": "TBC" if tbc else "CALLOUT"})
    text(str(n), at, h=TXT + 5, layer="CALLOUT", align=TextEntityAlignment.MIDDLE_CENTER)


def detail_mark(c, letter, r=230):
    """Dashed circle with detail letter (reference to Detail A)."""
    msp.add_circle(c, r, dxfattribs={"layer": "HIDDEN", "color": 4})
    tag = (c[0] + r * 0.75, c[1] + r * 0.75)
    msp.add_circle(tag, 40, dxfattribs={"layer": "CALLOUT"})
    text(letter, tag, h=TXT_L, layer="CALLOUT", align=TextEntityAlignment.MIDDLE_CENTER)


DIM_OVR = {"dimtxt": TXT, "dimasz": 20, "dimexe": 15, "dimexo": 15,
           "dimgap": 8, "dimdec": 0, "dimtad": 1, "dimlfac": 1}


def dim_h(p1, p2, y, txt="<>"):
    d = msp.add_linear_dim(base=(p1[0], y), p1=p1, p2=p2, angle=0, text=txt, dimstyle="EZDXF",
                           override=DIM_OVR, dxfattribs={"layer": "DIM"})
    d.render()


def dim_v(p1, p2, x, txt="<>"):
    d = msp.add_linear_dim(base=(x, p1[1]), p1=p1, p2=p2, angle=90, text=txt, dimstyle="EZDXF",
                           override=DIM_OVR, dxfattribs={"layer": "DIM"})
    d.render()


def arrow(p_from, p_to, label_lines, layer="TEXT"):
    line(p_from, p_to, layer)
    dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    b = (p_to[0] - ux * 50, p_to[1] - uy * 50)
    msp.add_solid([p_to, (b[0] - uy * 18, b[1] + ux * 18), (b[0] + uy * 18, b[1] - ux * 18)],
                  dxfattribs={"layer": layer})
    for i, s in enumerate(label_lines):
        text(s, (p_to[0] - 40, p_to[1] + 40 - i * 70), align=TextEntityAlignment.RIGHT)


# ------------------------------------------------------------------ blocks
def make_flange_block():
    b = doc.blocks.new("FLANGE_6IN")
    b.add_lwpolyline([(-FL_T / 2, -FL_D / 2), (FL_T / 2, -FL_D / 2), (FL_T / 2, FL_D / 2),
                      (-FL_T / 2, FL_D / 2)], close=True)
    for yy in (-FL_PCD / 2, FL_PCD / 2):                 # bolt centre marks
        b.add_line((-FL_T / 2 - 22, yy), (FL_T / 2 + 22, yy))


def _valve_ends_and_body(b):
    h = VALVE_FTF / 2
    for sx in (-1, 1):                                   # end flanges
        x0 = sx * h
        x1 = x0 - sx * FL_T
        b.add_lwpolyline([(x0, -FL_D / 2), (x1, -FL_D / 2), (x1, FL_D / 2), (x0, FL_D / 2)], close=True)
    xi = h - FL_T                                        # body (bow-tie)
    b.add_lwpolyline([(-xi, -100), (xi, 100), (xi, -100), (-xi, 100)], close=True)


def make_gate_valve_block():
    """AVK 06/61 DN150 gate valve, elevation: axis along X, stem towards +Y (top at VALVE_H)."""
    b = doc.blocks.new("GATE_VALVE_DN150")
    _valve_ends_and_body(b)
    hb = VALVE_DT / 2
    b.add_lwpolyline([(-hb, 100), (hb, 100), (70, 360), (-70, 360)], close=True)   # bonnet
    b.add_lwpolyline([(-45, 360), (45, 360), (45, 395), (-45, 395)], close=True)   # gland
    b.add_line((0, 395), (0, VALVE_H - 38))                                        # stem
    b.add_lwpolyline([(-10, VALVE_H - 38), (10, VALVE_H - 38), (10, VALVE_H), (-10, VALVE_H)],
                     close=True)                                                   # square cap 19.3


def make_plan_valve_block():
    """AVK 06/61 DN150 gate valve seen from above (stem out of page)."""
    b = doc.blocks.new("GATE_VALVE_DN150_PLAN")
    _valve_ends_and_body(b)
    b.add_circle((0, 0), VALVE_DT / 2)
    b.add_lwpolyline([(-10, -10), (10, -10), (10, 10), (-10, 10)], close=True)


def make_air_valve_block():
    b = doc.blocks.new("AIR_VALVE")
    b.add_lwpolyline([(-45, 0), (45, 0), (45, 130), (-45, 130)], close=True)
    b.add_lwpolyline([(-30, 130), (30, 130), (30, 175), (-30, 175)], close=True)
    b.add_line((0, 175), (0, 200))


make_flange_block()
make_gate_valve_block()
make_plan_valve_block()
make_air_valve_block()


def flange(p, rot, layer):
    msp.add_blockref("FLANGE_6IN", p, dxfattribs={"layer": layer, "rotation": rot})


def gate_valve(p, rot, layer, plan=False):
    name = "GATE_VALVE_DN150_PLAN" if plan else "GATE_VALVE_DN150"
    msp.add_blockref(name, p, dxfattribs={"layer": layer, "rotation": rot})


def golan_connector_h(x_face, y, layer, direction=-1):
    """Golan steel flange connector (flange + clamp body) on a horizontal line; returns pipe start x."""
    d = direction
    flange((x_face + d * FL_T / 2, y), 0, layer)
    x1 = x_face + d * FL_T
    x2 = x1 + d * GOLAN_L
    msp.add_lwpolyline([(x1, y - 115), (x2, y - 115), (x2, y + 115), (x1, y + 115)], close=True,
                       dxfattribs={"layer": layer})
    for xc in (x1 + d * GOLAN_L * 0.3, x1 + d * GOLAN_L * 0.75):   # clamp bolt lugs
        msp.add_lwpolyline([(xc - 18, y + 115), (xc + 18, y + 115), (xc + 18, y + 140), (xc - 18, y + 140)],
                           close=True, dxfattribs={"layer": layer})
    return x2


# ================================================================== DEVELOPED ELEVATION
E = "EXIST"
N = "NEW-STEEL"
V = "NEW-VALVE"
P = "PEXGOL"
# --- existing camel
bend((R_ELB, Y_HEADER - R_ELB), R_ELB, 90, 180, R_P, E)
pipe((R_ELB, Y_HEADER), (X_CENTER - R_P, Y_HEADER), R_P, E)
x_hv = X_CENTER + R_P + 100                                          # existing valve flange face
pipe((X_CENTER + R_P, Y_HEADER), (x_hv, Y_HEADER), R_P, E)
flange((x_hv + FL_T / 2, Y_HEADER), 0, E)
gate_valve((x_hv + FL_T + VALVE_FTF / 2, Y_HEADER), 0, E)
xv2 = x_hv + FL_T + VALVE_FTF
flange((xv2 + FL_T / 2, Y_HEADER), 0, E)
pipe((xv2 + FL_T, Y_HEADER), (X_RIGHT - R_ELB, Y_HEADER), R_P, E)
bend((X_RIGHT - R_ELB, Y_HEADER - R_ELB), R_ELB, 0, 90, R_P, E)
pipe((X_RIGHT, Y_HEADER - R_ELB), (X_RIGHT, 450), R_P, E)
flange((X_RIGHT, 450 - FL_T / 2), 90, E)
flange((X_RIGHT, 450 - 1.5 * FL_T), 90, E)
pipe((X_RIGHT, 450 - 2 * FL_T), (X_RIGHT, -650), EXIST_OD / 2, E)
break_mark((X_RIGHT, -650), "v", EXIST_OD / 2, E)
msp.add_lwpolyline([(X_CENTER - R_P, Y_HEADER + R_P), (X_CENTER - R_P, 680),
                    (X_CENTER + R_P, 680), (X_CENTER + R_P, Y_HEADER + R_P)], dxfattribs={"layer": E})
flange((X_CENTER, 680 - FL_T / 2), 90, E)
flange((X_CENTER, 680 - 1.5 * FL_T), 90, E)
msp.add_lwpolyline([(X_CENTER - 110, 680 - 2 * FL_T), (X_CENTER + 110, 680 - 2 * FL_T), (X_CENTER + 110, 400),
                    (X_CENTER - 110, 400)], close=True, dxfattribs={"layer": E})
flange((X_CENTER, 400 - FL_T / 2), 90, E)
pipe((X_CENTER, 400 - FL_T), (X_CENTER, -650), EXIST_OD / 2, E)
break_mark((X_CENTER, -650), "v", EXIST_OD / 2, E)
pipe((X_CENTER, Y_HEADER + R_P), (X_CENTER, 1250), 30, E)
pipe((X_CENTER - 30, 1150), (X_CENTER - 170, 1150), 12, E, center=False)
msp.add_circle((X_CENTER - 185, 1150), 22, dxfattribs={"layer": E})
msp.add_blockref("AIR_VALVE", (X_CENTER, 1250), dxfattribs={"layer": E})
Y_WELD = Y_HEADER - R_ELB - 20
pipe((0, Y_HEADER - R_ELB), (0, Y_WELD), R_P, E)

# --- new works
weld((0, Y_WELD), "v", R_P, N)                                       # 2 welded spool with branch
pipe((0, Y_WELD), (0, Y_BRANCH + R_P), R_P, N)
pipe((0, Y_BRANCH - R_P), (0, Y_LOWV + FL_T), R_P, N)
X_BF = -(R_P + 170)                                                  # branch flange face
pipe((-R_P, Y_BRANCH), (X_BF + FL_T, Y_BRANCH), R_P, N)
weld((-R_P - 5, Y_BRANCH), "h", R_P, N)
flange((X_BF + FL_T / 2, Y_BRANCH), 0, N)                            # 3 branch flange
flange((0, Y_LOWV + FL_T / 2), 90, N)                                # 3 spool bottom flange
xv = X_BF - VALVE_FTF / 2
gate_valve((xv, Y_BRANCH), 0, V)                                     # 4 (stem vertical)
gate_valve((0, Y_LOWV - VALVE_FTF / 2), 90, V)                       # 5 (stem horizontal)
y_low = Y_LOWV - VALVE_FTF
flange((0, y_low - FL_T / 2), 90, E)
pipe((0, y_low - FL_T), (0, -650), EXIST_OD / 2, E)
break_mark((0, -650), "v", EXIST_OD / 2, E)
x_af = X_BF - VALVE_FTF                                              # 6 steel pipe + LR elbows
flange((x_af - FL_T / 2, Y_BRANCH), 0, N)
x0 = x_af - FL_T
pipe((x0, Y_BRANCH), (X_DOWN + R_ELB, Y_BRANCH), R_P, N)
weld((X_DOWN + R_ELB, Y_BRANCH), "h", R_P, N)
bend((X_DOWN + R_ELB, Y_BRANCH - R_ELB), R_ELB, 90, 180, R_P, N)
pipe((X_DOWN, Y_BRANCH - R_ELB), (X_DOWN, Y_BURIED + R_ELB), R_P, N)
weld((X_DOWN, Y_BRANCH - R_ELB), "v", R_P, N)
weld((X_DOWN, Y_BURIED + R_ELB), "v", R_P, N)
bend((X_DOWN - R_ELB, Y_BURIED + R_ELB), R_ELB, 270, 360, R_P, N)
weld((X_DOWN - R_ELB, Y_BURIED), "h", R_P, N)
pipe((X_DOWN - R_ELB, Y_BURIED), (X_TRANS + FL_T, Y_BURIED), R_P, N)
W = R_P + 12                                                         # tape wrap envelope
line((x0, Y_BRANCH + W), (X_DOWN + R_ELB, Y_BRANCH + W), "WRAP")
line((x0, Y_BRANCH - W), (X_DOWN + R_ELB, Y_BRANCH - W), "WRAP")
msp.add_arc((X_DOWN + R_ELB, Y_BRANCH - R_ELB), R_ELB + W, 90, 180, dxfattribs={"layer": "WRAP"})
msp.add_arc((X_DOWN + R_ELB, Y_BRANCH - R_ELB), R_ELB - W, 90, 180, dxfattribs={"layer": "WRAP"})
line((X_DOWN - W, Y_BRANCH - R_ELB), (X_DOWN - W, Y_BURIED + R_ELB), "WRAP")
line((X_DOWN + W, Y_BRANCH - R_ELB), (X_DOWN + W, Y_BURIED + R_ELB), "WRAP")
msp.add_arc((X_DOWN - R_ELB, Y_BURIED + R_ELB), R_ELB + W, 270, 360, dxfattribs={"layer": "WRAP"})
msp.add_arc((X_DOWN - R_ELB, Y_BURIED + R_ELB), R_ELB - W, 270, 360, dxfattribs={"layer": "WRAP"})
line((X_DOWN - R_ELB, Y_BURIED + W), (X_TRANS + FL_T, Y_BURIED + W), "WRAP")
line((X_DOWN - R_ELB, Y_BURIED - W), (X_TRANS + FL_T, Y_BURIED - W), "WRAP")
# 7: steel flange + Golan steel flange connector (Detail A)
flange((X_TRANS + FL_T / 2, Y_BURIED), 0, N)
x_pex = golan_connector_h(X_TRANS, Y_BURIED, P)
detail_mark((X_TRANS - 60, Y_BURIED), "A", r=300)
# 8: Pexgol 160 to new network
pipe((x_pex, Y_BURIED), (X_END, Y_BURIED), PEX_OD / 2, P)
break_mark((X_END, Y_BURIED), "h", PEX_OD / 2, P)
arrow((X_END - 60, Y_BURIED), (X_END - 260, Y_BURIED), ["TO NEW NETWORK", "(HATZERIM 20117)"])
# 9: thrust block (dashed = TBC), 10: sand
tb = [(X_DOWN - 500, Y_BURIED - 300), (X_DOWN + 200, Y_BURIED - 300),
      (X_DOWN + 200, Y_BURIED + 300), (X_DOWN - 500, Y_BURIED + 300)]
msp.add_lwpolyline(tb, close=True, dxfattribs={"layer": "CONCRETE"})
text("THRUST BLOCK - TBC", (X_DOWN - 490, Y_BURIED - 350), h=20, layer="TBC")
sand = [(X_END + 100, Y_BURIED - R_P - 200), (X_DOWN - 500, Y_BURIED - R_P - 200),
        (X_DOWN - 500, Y_BURIED + R_P + 200), (X_END + 100, Y_BURIED + R_P + 200)]
h = msp.add_hatch(dxfattribs={"layer": "SAND"})
h.set_pattern_fill("AR-SAND", scale=0.6)
h.paths.add_polyline_path(sand, is_closed=True)
msp.add_lwpolyline(sand, close=True, dxfattribs={"layer": "SAND"})

line((X_END - 350, 0), (X_RIGHT + 300, 0), "GROUND", lineweight=50)  # grade
for gx in range(int(X_END - 330), int(X_RIGHT + 300), 120):
    line((gx, 0), (gx - 40, -40), "GROUND")
text("FINISHED GRADE  +0.00", (X_END - 330, 25), layer="GROUND")

dim_v((-400, 0), (-400, Y_BRANCH), -1150, "~<> TBC")
dim_v((X_RIGHT + 150, 0), (X_RIGHT + 150, Y_HEADER), X_RIGHT + 250, "~<> TBC")
dim_v((X_END - 150, 0), (X_END - 150, Y_BURIED + R_P), X_END - 150, "COVER <> MIN")
dim_h((x_af, Y_BRANCH + 170), (X_BF, Y_BRANCH + 170), Y_BRANCH + 560, "L=<>")
dim_h((X_DOWN, Y_BURIED - 500), (0, Y_BURIED - 500), Y_BURIED - 500, "~<> TBC")
dim_h((X_DOWN - R_ELB, Y_BRANCH - R_ELB), (X_DOWN, Y_BRANCH - R_ELB), Y_BRANCH - 330, "A=<>")

callout(1, (X_RIGHT - 80, Y_HEADER + 100), (X_RIGHT + 150, 1250))
callout(2, (R_P, Y_WELD - 60), (450, 250), tbc=True)
callout(3, (X_BF + FL_T / 2, Y_BRANCH + 120), (-60, 1080))
callout(4, (xv, Y_BRANCH + 330), (-560, 1150))
callout(5, (-230, Y_LOWV - VALVE_FTF / 2), (-520, -250))
callout(6, (X_DOWN - 90, Y_BRANCH + 120), (-1250, 950))
callout(7, (X_TRANS - 100, Y_BURIED + 140), (-1750, -600), tbc=True)
callout(8, (-2150, Y_BURIED + PEX_OD / 2), (-2150, -750))
callout(9, (X_DOWN - 500, Y_BURIED + 100), (-500, -700), tbc=True)
callout(10, (-2300, Y_BURIED - R_P - 150), (-2450, -1850))
callout(11, (X_BF, Y_BRANCH - FL_PCD / 2), (-750, 150), tbc=True)

text("DEVELOPED ELEVATION - CONNECTION TO EXISTING CAMEL 6\" (AS-MADE)", (-2500, 1600), h=TXT_T)
text("SCALE 1:10  -  NEW BRANCH ROTATED INTO PLANE OF CAMEL, SEE PLAN FOR ACTUAL ORIENTATION  -  "
     "SITE HEIGHTS/LENGTHS APPROXIMATE (TBC)", (-2500, 1500), h=TXT)
text("LEGEND:  GREY = EXISTING   RED = NEW STEEL   BLUE = NEW VALVES   MAGENTA = PEXGOL / GOLAN CONNECTOR   "
     "DASHED ORANGE = TAPE WRAP   RED BALLOON = TO BE CONFIRMED", (-2500, 1420), h=20)

# ================================================================== PLAN VIEW
Y0 = PLAN_Y0
YD = Y0 - PLAN_DROP
pipe((0, Y0), (x_hv, Y0), R_P, E)
flange((x_hv + FL_T / 2, Y0), 0, E)
gate_valve((x_hv + FL_T + VALVE_FTF / 2, Y0), 0, E, plan=True)
flange((xv2 + FL_T / 2, Y0), 0, E)
pipe((xv2 + FL_T, Y0), (X_RIGHT, Y0), R_P, E)
for xx in (0, X_RIGHT):                                              # legs turning down
    msp.add_circle((xx, Y0), R_P, dxfattribs={"layer": E})
msp.add_circle((X_CENTER, Y0), 45, dxfattribs={"layer": E})          # air valve from above
line((X_CENTER - 45, Y0), (X_CENTER - 170, Y0), E)
msp.add_circle((X_CENTER - 185, Y0), 22, dxfattribs={"layer": E})
yb_f = Y0 + X_BF                                                     # branch flange face (plan)
pipe((0, Y0 - R_P), (0, yb_f + FL_T), R_P, N)
weld((0, Y0 - R_P - 5), "v", R_P, N)
flange((0, yb_f + FL_T / 2), 90, N)
gate_valve((0, yb_f - VALVE_FTF / 2), 90, V, plan=True)
y_af = yb_f - VALVE_FTF
flange((0, y_af - FL_T / 2), 90, N)
pipe((0, y_af - FL_T), (0, YD), R_P, N)
line((-W, y_af - FL_T), (-W, YD), "WRAP")
line((W, y_af - FL_T), (W, YD), "WRAP")
msp.add_circle((0, YD), R_P, dxfattribs={"layer": N})                 # pipe turns down
line((-R_P * 0.7, YD - R_P * 0.7), (R_P * 0.7, YD + R_P * 0.7), N)
line((-R_P * 0.7, YD + R_P * 0.7), (R_P * 0.7, YD - R_P * 0.7), N)
line((-R_P, Y0 + 40), (-260, Y0 + 40), "HIDDEN")                      # lower valve stem (hidden)
x_tr = -PLAN_TRANS
pipe((-R_P, YD), (x_tr + FL_T, YD), R_P, N)
line((-R_P, YD + W), (x_tr + FL_T, YD + W), "WRAP")
line((-R_P, YD - W), (x_tr + FL_T, YD - W), "WRAP")
flange((x_tr + FL_T / 2, YD), 0, N)
x_pp = golan_connector_h(x_tr, YD, P)
detail_mark((x_tr - 60, YD), "A", r=300)
pipe((x_pp, YD), (-PLAN_END, YD), PEX_OD / 2, P)
break_mark((-PLAN_END, YD), "h", PEX_OD / 2, P)
arrow((-PLAN_END - 60, YD), (-PLAN_END - 260, YD), ["TO NEW NETWORK", "(DIRECTION TBC)"])
msp.add_lwpolyline([(-500, YD - 300), (200, YD - 300), (200, YD + 300), (-500, YD + 300)], close=True,
                   dxfattribs={"layer": "CONCRETE"})
text("BURIED (COVER 1.20)", (-PLAN_END + 100, YD - 130), h=20, layer="HIDDEN")
dim_h((0, Y0 + 150), (X_CENTER, Y0 + 150), Y0 + 220, "~<> TBC")
dim_h((X_CENTER, Y0 + 150), (X_RIGHT, Y0 + 150), Y0 + 220, "~<> TBC")
dim_v((0, Y0), (0, YD), 330, "~<> TBC")
dim_h((0, YD), (x_tr, YD), YD + 380, "~<> TBC")
callout(1, (X_RIGHT - 150, Y0 - R_P), (X_RIGHT + 150, 2700))
callout(4, (VALVE_DT / 2, yb_f - VALVE_FTF / 2), (550, 2600))
callout(5, (-260, Y0 + 40), (-520, 3120))
callout(6, (R_P, YD + 320), (550, 2250))
callout(7, (x_tr - 100, YD - 115), (-850, 1760), tbc=True)
callout(8, (-1200, YD + PEX_OD / 2), (-1200, 2400))
callout(9, (200, YD - 150), (550, 1800), tbc=True)
text("PLAN VIEW (AS-MADE)", (-2500, 3100), h=TXT_T)
text("SCALE 1:10  -  ORIENTATION FROM SITE PHOTOS, NORTH ARROW TBC FROM SURVEY", (-2500, 3000), h=TXT)

# ================================================================== DETAIL A
K = DET_K


def dp(x, y):
    return DET_X + K * x, DET_Y + K * y


def dpoly(pts, layer, close=True):
    msp.add_lwpolyline([dp(*p) for p in pts], close=close, dxfattribs={"layer": layer})


def dhatch(pts, layer, pattern="ANSI31", scale=3.0, angle=0):
    hh = msp.add_hatch(dxfattribs={"layer": layer})
    hh.set_pattern_fill(pattern, scale=scale, angle=angle)
    hh.paths.add_polyline_path([dp(*p) for p in pts], is_closed=True)
    dpoly(pts, layer)


def dtag(letter, target, tag):
    """Leader from detail point 'target' to a lettered tag at detail point 'tag' (both real mm)."""
    tp, tg = dp(*target), dp(*tag)
    rr = 32
    dx, dy = tp[0] - tg[0], tp[1] - tg[1]
    L = math.hypot(dx, dy)
    line((tg[0] + dx / L * rr, tg[1] + dy / L * rr), tp, "CALLOUT")
    msp.add_circle(tp, 5, dxfattribs={"layer": "CALLOUT"})
    msp.add_circle(tg, rr, dxfattribs={"layer": "CALLOUT"})
    text(letter, tg, h=TXT + 3, layer="CALLOUT", align=TextEntityAlignment.MIDDLE_CENTER)


ST_RO, ST_RI = OD / 2, OD / 2 - WALL
PX_RO, PX_RI = PEX_OD / 2, PEX_OD / 2 - PEX_E
FR = FL_D / 2
GB = -FL_T - 3                                 # Golan flange back face (gasket 3 mm)
GE = GB - GOLAN_L                              # Golan body end
for s in (1, -1):
    # steel weld-neck flange (plate 24, hub 52) + steel pipe
    dhatch([(0, s * ST_RI), (0, s * FR), (FL_T, s * FR), (FL_T, s * 96), (FL_T + 52, s * ST_RO),
            (FL_T + 52, s * ST_RI)], N)
    dhatch([(FL_T + 52, s * ST_RI), (FL_T + 52, s * ST_RO), (380, s * ST_RO), (380, s * ST_RI)], N)
    dpoly([(FL_T + 52, s * ST_RO), (FL_T + 56, s * (ST_RO + 5)), (FL_T + 48, s * (ST_RO + 5))], N)
    # EPDM gasket
    dhatch([(-3, s * ST_RI), (0, s * ST_RI), (0, s * 110), (-3, s * 110)], "TBC", pattern="SOLID")
    # Golan connector: flange plate + clamp body (ductile iron)
    dhatch([(-3, s * PX_RI), (-3, s * FR), (GB, s * FR), (GB, s * 115), (GE, s * 115), (GE, s * PX_RO),
            (GB - 20, s * PX_RO), (GB - 20, s * PX_RI)], P, pattern="ANSI37", scale=2.0)
    for xc in (GB - GOLAN_L * 0.3, GB - GOLAN_L * 0.75):
        dpoly([(xc - 18, s * 115), (xc + 18, s * 115), (xc + 18, s * 140), (xc - 18, s * 140)], P)
    # Pexgol pipe inside connector
    dhatch([(GB - 20, s * PX_RI), (GB - 20, s * PX_RO), (-560, s * PX_RO), (-560, s * PX_RI)], P, angle=90)
    # internal insert (dashed, TBC)
    dpoly([(GB - 20, s * (PX_RI - 8)), (GE + 20, s * (PX_RI - 8))], "HIDDEN", close=False)
    # bolt M20 + nuts at PCD 240
    yb_ = s * FL_PCD / 2
    dpoly([(GB - 40, yb_ - 10), (FL_T + 40, yb_ - 10), (FL_T + 40, yb_ + 10), (GB - 40, yb_ + 10)], "NEW-VALVE")
    dpoly([(GB - 3 - 18, yb_ - 16), (GB - 3, yb_ - 16), (GB - 3, yb_ + 16), (GB - 3 - 18, yb_ + 16)], "NEW-VALVE")
    dpoly([(FL_T + 3, yb_ - 16), (FL_T + 21, yb_ - 16), (FL_T + 21, yb_ + 16), (FL_T + 3, yb_ + 16)], "NEW-VALVE")
    # tape envelope
    dpoly([(-470, s * (PX_RO + 8)), (GE - 30, s * (PX_RO + 8)), (GE - 10, s * 158), (FL_T + 60, s * 158),
           (FL_T + 110, s * (ST_RO + 10)), (330, s * (ST_RO + 10))], "WRAP", close=False)
line(dp(-580, 0), dp(400, 0), "CENTER")
for xx, rr in ((380, ST_RO), (-560, PX_RO)):
    x_m, _ = dp(xx, 0)
    for s in (1, -1):
        y_a, y_b = DET_Y + s * K * (rr - 12), DET_Y + s * K * (rr + 15)
        msp.add_lwpolyline([(x_m, y_a), (x_m + 10, (y_a + y_b) / 2), (x_m - 10, (y_a + y_b) / 2), (x_m, y_b)],
                           dxfattribs={"layer": "SECTION"})

dtag("a", (300, ST_RO), (330, 190))
dtag("b", (12, 135), (150, 190))
dtag("c", (FL_T + 30, FL_PCD / 2), (60, 190))
dtag("d", (-1.5, 100), (-40, 190))
dtag("e", (GB - 100, 115), (-170, 190))
dtag("f", (-450, PX_RO), (-450, 190))
dtag("g", (-100, -158), (-100, -195))
dtag("h", (GE + 60, -(PX_RI - 8)), (-330, -195))
text("PCD 240", dp(-470, 100), h=22, layer="DIM")
text("Pexgol OD160 x 9.9", dp(-540, -PX_RO - 35), h=22, layer="DIM")
text("6\" OD168.3 x 7.11", dp(150, -ST_RO - 35), h=22, layer="DIM")
text("D285", dp(5, -FR - 30), h=22, layer="DIM")

DLX, DLY = 2080, DET_Y - K * 225
DET_LIST = [
    ("a", "STEEL PIPE 6\" SCH40 OD168.3x7.11; LR 90 ELBOWS 6\" BW A234 WPB, ASME B16.9 (A=229), CEMENT LINED - SUB 57 00 00-11", "TEXT"),
    ("b", "STEEL WELD-NECK FLANGE 6\" PN16: D285, PCD240, 8xD23, t24 (TYPE TBC)", "TEXT"),
    ("c", "8 x M20 BOLTS + NUTS SS316 A4-80, WASHERS BOTH SIDES, ANTI-SEIZE SI 5452 (RFI-0021 D) - TORQUE TBC", "TBC"),
    ("d", "FLAT GASKET 6\" PN16 EPDM, 3 mm, SI 5452 - NOT DEFINED IN SUBMITTALS (TBC)", "TBC"),
    ("e", "GOLAN STEEL FLANGE CONNECTOR 160x6\" (PEX5081-160 / SKU 50816060), DUCTILE IRON, ASA150+BS - DIMS TBC", "TBC"),
    ("f", "PEXGOL PE-Xa 160 CLASS 10 (SDR16.2) OD160x9.9, PA-1609.9BLK, EN ISO 15875 / SI 16893 - SUB 57 00 00-8", "TEXT"),
    ("g", "CORROSION PROTECTION PER SI 1427: PRIMER + 3 LAYERS VINYL TAPE (P-501) OVER FLANGES, BOLTS & CONNECTOR", "TEXT"),
    ("h", "INTERNAL INSERT / GRIP SYSTEM PER GOLAN (TBC)", "TBC"),
]
for i, (lt, s, lay) in enumerate(DET_LIST):
    text(f"{lt})  {s}", (DLX, DLY - i * 58), h=22, layer=lay)
text("DETAIL A - TRANSITION STEEL 6\" / PEXGOL 160 (GOLAN STEEL FLANGE CONNECTOR)",
     (DLX, DET_Y + K * 205 + 70), h=TXT_L)
text("NOT TO SCALE (ENLARGED APPROX. 1:3.5)  -  LONGITUDINAL SECTION", (DLX, DET_Y + K * 205), h=TXT)
mtext("NOTES: 1) FLANGE DRILLING - GOLAN CONNECTOR ASA150+BS vs AVK/STEEL PN16 (D285 PCD240 8xD23): MENDELSON "
      "STATES 6\" ASA & DIN DRILLINGS MATCH - VERIFY ON SITE.  2) TIGHTEN CROSSWISE IN TWO STAGES (75% / 100%) "
      "TO GOLAN TORQUE; RE-CHECK AFTER PRESSURE TEST.  3) PRESSURE TEST 12 atm / 4 h, MAX DROP 0.5 atm (SPEC 57.06); "
      "APPLY TAPE (g) AFTER TEST.  4) REPAIR INTERNAL CEMENT LINING AT ALL WELDS (GOVT REMARK, SUB 57 00 00-11).",
      (DLX, DLY - len(DET_LIST) * 58 - 30), width=2950, h=21)

# ================================================================== BOM table
BOM = [
    ("1", "EXISTING CAMEL MANIFOLD 6\" STEEL WITH EXISTING GATE VALVE, AIR VALVE & SAMPLING TAP; EXISTING 160 mm RISERS", "EXISTING", "-"),
    ("2", "WELDED STEEL SPOOL 6\" SCH40 WITH 6\" BRANCH, INSERTED IN EXISTING LEFT LEG (CUT HEIGHT PER RFI-0021 A.2)", "NEW", "CUT LEVEL TBC"),
    ("3", "STEEL FLANGES 6\" PN16 EN 1092 / ISO 7005: D285, PCD240, 8xD23, t24", "NEW", "-"),
    ("4", "GATE VALVE AVK 06/61 DN150 PN16 (AV0661D6), DI GGG-50, EPOXY 250um, DIN 3352-4 / SI 61, L=210 H=448 - SUB 07 00 00-7.1", "NEW", "APPROVED (B)"),
    ("5", "GATE VALVE AVK 06/61 DN150 PN16 (AV0661D6) ON EXISTING LEG, STEM HORIZONTAL (RFI-0021 B) - SUB 07 00 00-7.1", "NEW", "APPROVED (B)"),
    ("6", "STEEL PIPE 6\" SCH40 + 2x LR 90 ELBOWS 6\" BW A234 WPB (A=229), CEMENT LINED (1018005); 3 LAYERS VINYL TAPE", "NEW", "APPROVED (B)"),
    ("7", "GOLAN STEEL FLANGE CONNECTOR 160x6\" (PEX5081-160 / 50816060), DUCTILE IRON - SEE DETAIL A", "NEW", "DIMS TBC"),
    ("8", "PEXGOL PE-Xa 160 CLASS 10, OD160x9.9 (PA-1609.9BLK), 10 bar @20C - SUB 57 00 00-8", "NEW", "APPROVED"),
    ("9", "CONCRETE THRUST BLOCK AT BURIED ELBOW / TRANSITION (P-501, SI 1928)", "REQUIRED", "VERIFY EXECUTED"),
    ("10", "SAND 200 mm ALL ROUND, COVER MIN 1.20 m (SPEC 57.06)", "NEW", "-"),
    ("11", "BOLTS M20 SS316 A4-80 + WASHERS (RFI-0021 D, SPEC 07.04.02); EPDM GASKETS SI 5452", "NEW", "TORQUE/GASKET TBC"),
]
TX, TY = 1900, 3050
cols = [0, 150, 2300, 2650, 3150]
rowh = 110
line((TX, TY), (TX + cols[-1], TY), "FRAME")
text("BILL OF MATERIALS", (TX, TY + 40), h=TXT_L)
for i, s in enumerate(("ITEM", "DESCRIPTION", "STATUS", "REMARK")):
    text(s, (TX + cols[i] + 20, TY - 70), h=TXT)
line((TX, TY - rowh), (TX + cols[-1], TY - rowh), "FRAME")
for r, row in enumerate(BOM):
    y = TY - rowh * (r + 1)
    for i, s in enumerate(row):
        if i == 1:
            mtext(s, (TX + cols[1] + 20, y - 15), width=cols[2] - cols[1] - 40, h=21)
        else:
            text(s, (TX + cols[i] + 20, y - 65), h=21, layer="TBC" if "TBC" in s or "VERIFY" in s else "TEXT")
    line((TX, y - rowh), (TX + cols[-1], y - rowh), "FRAME")
yb = TY - rowh * (len(BOM) + 1)
for c in cols:
    line((TX + c, TY), (TX + c, yb), "FRAME")

# ================================================================== general notes
NOTES = (
    "GENERAL NOTES:\\P"
    "1. RECORDS THE CONNECTION AS EXECUTED (SITE PHOTOS, FIELD SKETCH, APPROVED SUBMITTALS). SITE HEIGHTS/LENGTHS "
    "MARKED 'TBC' TO BE MEASURED ON SITE.\\P"
    "2. PN16 THROUGHOUT; PEXGOL CLASS 10 = 10 bar @ 20C. ALL WETTED MATERIALS CERTIFIED TO SI 5452.\\P"
    "3. FLANGES 6\" PN16 (D285 / PCD240 / 8xD23). GATE VALVES INSTALLED STEM VERTICAL / HORIZONTAL (RFI-0021 B).\\P"
    "4. ALL FLANGE BOLTS & NUTS SS316 (RFI-0021 D, SPECIAL SPEC 07.04.02), WASHER UNDER HEAD AND NUT.\\P"
    "5. BURIED / SEMI-BURIED STEEL CORROSION-PROTECTED PER SI 1427 - PRIMER + 3 LAYERS VINYL TAPE (P-501), "
    "INCL. FLANGED JOINT (DETAIL A).\\P"
    "6. PEXGOL PER GOLAN MANUAL; SAND 200 mm ALL ROUND; MIN COVER 1.20 m; TEST 12 atm / 4 h (SPEC 57.06).\\P"
    "7. THRUST BLOCK AND VALVE SUPPORT PER P-501 - EXECUTION TO BE CONFIRMED.\\P"
    "8. CANTILEVER WEIGHT ON EXISTING RISER (RFI-0021 C): SUPPORT SOLUTION TBC.\\P"
    "9. ELEVATION DEVELOPED ALONG PIPE ROUTE; ACTUAL ORIENTATION PER PLAN. COMPLY WITH HNTR 57046."
)
mtext(NOTES, (TX, yb - 100), width=cols[-1], h=22)

# ================================================================== frame + title block (A1 @ 1:10)
FX0, FY0, FX1, FY1 = -3300, -2700, 5110, 3240
msp.add_lwpolyline([(FX0, FY0), (FX1, FY0), (FX1, FY1), (FX0, FY1)], close=True,
                   dxfattribs={"layer": "FRAME", "const_width": 8})
TB_X, TB_Y = 3010, FY0
msp.add_lwpolyline([(TB_X, TB_Y), (FX1, TB_Y), (FX1, TB_Y + 700), (TB_X, TB_Y + 700)], close=True,
                   dxfattribs={"layer": "FRAME"})
for yy in (TB_Y + 560, TB_Y + 400, TB_Y + 240, TB_Y + 120):
    line((TB_X, yy), (FX1, yy), "FRAME")
text("HATZERIM PROJECT 20117 - USACE / CDM SMITH", (TB_X + 30, TB_Y + 610), h=TXT_L)
text("CONNECTION TO EXISTING BASE WATER SUPPLY (CAMEL) 6\"", (TB_X + 30, TB_Y + 470), h=TXT_L)
text("RFI-0021 - AS-MADE PLAN, ELEVATION & DETAIL A", (TB_X + 30, TB_Y + 300), h=TXT_L)
text(f"REV: {REV} (DRAFT FOR REVIEW)   SCALE 1:10, DETAIL A NTS (A1)   UNITS: mm", (TB_X + 30, TB_Y + 165), h=TXT)
text("DRAWN: ____   CHECKED: ____   DATE: 06/10/2026   DWG No.: 20117-W-CAMEL-01", (TB_X + 30, TB_Y + 45), h=TXT)
text("DRAFT - NOT FOR CONSTRUCTION", (-2500, -2550), h=TXT_T, layer="TBC")

dxf_path = OUT / f"20117-W-CAMEL-01_AsMade_Rev{REV}.dxf"
doc.saveas(dxf_path)
print("saved", dxf_path)

if "--png" in sys.argv:
    from ezdxf.addons.drawing import matplotlib as mpl_draw
    mpl_draw.qsave(msp, str(OUT / "preview.png"), bg="#FFFFFF", dpi=220, size_inches=(23.4, 16.5))
    print("preview saved")
