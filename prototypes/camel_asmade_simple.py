# -*- coding: utf-8 -*-
"""
Hatzerim 20117 - connection to existing camel 6" - SIMPLE as-made drawing
(style per "BACKFLOW PREVENTER AND WATER METER DETAIL - PLAN": monochrome, double-line pipes,
chain dimensions, short leader labels, below-grade dashed). One developed elevation, A3 @ 1:15.
Component data = approved submittals (see make_drawing.py header). '~' = site value, verify.
"""
import math
import sys
from pathlib import Path

import ezdxf
from bidi.algorithm import get_display
from ezdxf.enums import TextEntityAlignment

OUT = Path(__file__).parent

# ------------------------------------------------------------------ data (submittals) / site (~)
OD = 168.3; R_P = OD / 2            # 6" Sch40
FL_D, FL_T, FL_PCD = 285, 24, 240   # 6" PN16 flange
VALVE_FTF, VALVE_H, VALVE_DT = 210, 448, 212   # AVK 06/61 DN150
R_ELB = 229                         # 6" LR 90 elbow A
PEX_R = 80                          # Pexgol 160
EXIST_R = 80                        # existing 160 risers
GOLAN_L = 250                       # Golan connector body (TBC)

Y_HEADER, Y_BRANCH, Y_LOWV = 850, 500, 260
Y_BURIED = -1280                    # Pexgol crown at -1.20 (min cover)
X_CENTER, X_RIGHT = 550, 1300
X_DOWN, X_TRANS, X_END = -900, -1450, -1950

TXT, TXT_D = 35, 30

doc = ezdxf.new("R2018", setup=True)
doc.units = ezdxf.units.MM
msp = doc.modelspace()
for name, lt in (("PIPE", "CONTINUOUS"), ("BELOW", "DASHED"), ("TEXT", "CONTINUOUS"),
                 ("DIM", "CONTINUOUS"), ("FRAME", "CONTINUOUS")):
    doc.layers.add(name, color=7, linetype=lt)
doc.header["$LTSCALE"] = 15
doc.styles.add("ARIAL", font="arial.ttf")

A, B = "PIPE", "BELOW"


# ------------------------------------------------------------------ helpers
def line(p1, p2, layer=A):
    msp.add_line(p1, p2, dxfattribs={"layer": layer})


def pipe(p1, p2, r, layer=A):
    (x1, y1), (x2, y2) = p1, p2
    L = math.hypot(x2 - x1, y2 - y1)
    nx, ny = -(y2 - y1) / L * r, (x2 - x1) / L * r
    line((x1 + nx, y1 + ny), (x2 + nx, y2 + ny), layer)
    line((x1 - nx, y1 - ny), (x2 - nx, y2 - ny), layer)


def bend(c, R, a0, a1, r, layer=A):
    for rr in (R + r, R - r):
        msp.add_arc(c, rr, a0, a1, dxfattribs={"layer": layer})


def cap(p, axis, r, layer=A):
    """Close a pipe end with a short line (weld / joint mark)."""
    x, y = p
    if axis == "v":
        line((x - r, y), (x + r, y), layer)
    else:
        line((x, y - r), (x, y + r), layer)


def brk(p, axis, r, layer=A):
    x, y = p
    if axis == "v":
        pts = [(x - r - 15, y), (x - 12, y), (x - 4, y + 22), (x + 4, y - 22), (x + 12, y), (x + r + 15, y)]
    else:
        pts = [(x, y - r - 15), (x, y - 12), (x + 22, y - 4), (x - 22, y + 4), (x, y + 12), (x, y + r + 15)]
    msp.add_lwpolyline(pts, dxfattribs={"layer": layer})


def label(lines, target, at, width=None):
    """Reference-style label: text block + underline + leader to the element."""
    s = "\\P".join(lines)
    m = msp.add_mtext(s, dxfattribs={"layer": "TEXT", "char_height": TXT, "style": "ARIAL"})
    if width:
        m.dxf.width = width
    m.set_location(at, attachment_point=7)          # bottom-left at 'at'
    w = width or max(len(t) for t in lines) * TXT * 0.62
    ux = at[0] + w if target[0] > at[0] + w / 2 else at[0]
    line((at[0], at[1] - 12), (at[0] + w, at[1] - 12), "TEXT")
    line((ux, at[1] - 12), target, "TEXT")


DIM_OVR = {"dimtxt": TXT_D, "dimasz": 25, "dimexe": 20, "dimexo": 20, "dimgap": 10,
           "dimdec": 0, "dimtad": 1, "dimlfac": 1, "dimtsz": 0, "dimtxsty": "ARIAL"}


def dim_h(p1, p2, y, txt="<>"):
    msp.add_linear_dim(base=(p1[0], y), p1=p1, p2=p2, angle=0, text=txt, dimstyle="EZDXF",
                       override=DIM_OVR, dxfattribs={"layer": "DIM"}).render()


def dim_v(p1, p2, x, txt="<>"):
    msp.add_linear_dim(base=(x, p1[1]), p1=p1, p2=p2, angle=90, text=txt, dimstyle="EZDXF",
                       override=DIM_OVR, dxfattribs={"layer": "DIM"}).render()


# blocks (entities on layer 0 -> inherit insert layer / linetype)
fb = doc.blocks.new("FLG")
fb.add_lwpolyline([(-FL_T / 2, -FL_D / 2), (FL_T / 2, -FL_D / 2), (FL_T / 2, FL_D / 2), (-FL_T / 2, FL_D / 2)],
                  close=True)
vb = doc.blocks.new("GV")
h = VALVE_FTF / 2
for sx in (-1, 1):
    x0, x1 = sx * h, sx * (h - FL_T)
    vb.add_lwpolyline([(x0, -FL_D / 2), (x1, -FL_D / 2), (x1, FL_D / 2), (x0, FL_D / 2)], close=True)
xi = h - FL_T
vb.add_lwpolyline([(-xi, -100), (xi, 100), (xi, -100), (-xi, 100)], close=True)
vb.add_lwpolyline([(-VALVE_DT / 2, 100), (VALVE_DT / 2, 100), (70, 360), (-70, 360)], close=True)
vb.add_lwpolyline([(-45, 360), (45, 360), (45, 395), (-45, 395)], close=True)
vb.add_line((0, 395), (0, VALVE_H - 38))
vb.add_lwpolyline([(-10, VALVE_H - 38), (10, VALVE_H - 38), (10, VALVE_H), (-10, VALVE_H)], close=True)


def flg(p, rot, layer=A):
    msp.add_blockref("FLG", p, dxfattribs={"layer": layer, "rotation": rot})


def gv(p, rot, layer=A):
    msp.add_blockref("GV", p, dxfattribs={"layer": layer, "rotation": rot})


# ================================================================== existing camel
bend((R_ELB, Y_HEADER - R_ELB), R_ELB, 90, 180, R_P)
pipe((R_ELB, Y_HEADER), (X_CENTER - R_P, Y_HEADER), R_P)
x_hv = X_CENTER + R_P + 100
pipe((X_CENTER + R_P, Y_HEADER), (x_hv, Y_HEADER), R_P)
flg((x_hv + FL_T / 2, Y_HEADER), 0)
gv((x_hv + FL_T + VALVE_FTF / 2, Y_HEADER), 0)
xv2 = x_hv + FL_T + VALVE_FTF
flg((xv2 + FL_T / 2, Y_HEADER), 0)
pipe((xv2 + FL_T, Y_HEADER), (X_RIGHT - R_ELB, Y_HEADER), R_P)
bend((X_RIGHT - R_ELB, Y_HEADER - R_ELB), R_ELB, 0, 90, R_P)
pipe((X_RIGHT, Y_HEADER - R_ELB), (X_RIGHT, 450), R_P)
flg((X_RIGHT, 450 - FL_T / 2), 90)
flg((X_RIGHT, 450 - 1.5 * FL_T), 90)
pipe((X_RIGHT, 450 - 2 * FL_T), (X_RIGHT, 0), EXIST_R)
pipe((X_RIGHT, 0), (X_RIGHT, -600), EXIST_R, B)
brk((X_RIGHT, -600), "v", EXIST_R, B)
msp.add_lwpolyline([(X_CENTER - R_P, Y_HEADER + R_P), (X_CENTER - R_P, 680), (X_CENTER + R_P, 680),
                    (X_CENTER + R_P, Y_HEADER + R_P)], dxfattribs={"layer": A})
flg((X_CENTER, 680 - FL_T / 2), 90)
flg((X_CENTER, 680 - 1.5 * FL_T), 90)
msp.add_lwpolyline([(X_CENTER - 110, 632), (X_CENTER + 110, 632), (X_CENTER + 110, 400), (X_CENTER - 110, 400)],
                   close=True, dxfattribs={"layer": A})
flg((X_CENTER, 400 - FL_T / 2), 90)
pipe((X_CENTER, 400 - FL_T), (X_CENTER, 0), EXIST_R)
pipe((X_CENTER, 0), (X_CENTER, -600), EXIST_R, B)
brk((X_CENTER, -600), "v", EXIST_R, B)
pipe((X_CENTER, Y_HEADER + R_P), (X_CENTER, 1250), 30)                 # air valve riser
pipe((X_CENTER - 30, 1150), (X_CENTER - 170, 1150), 12)                # sampling tap
msp.add_circle((X_CENTER - 185, 1150), 22, dxfattribs={"layer": A})
msp.add_lwpolyline([(X_CENTER - 45, 1250), (X_CENTER + 45, 1250), (X_CENTER + 45, 1380), (X_CENTER - 45, 1380)],
                   close=True, dxfattribs={"layer": A})
msp.add_lwpolyline([(X_CENTER - 30, 1380), (X_CENTER + 30, 1380), (X_CENTER + 30, 1425), (X_CENTER - 30, 1425)],
                   close=True, dxfattribs={"layer": A})
Y_WELD = Y_HEADER - R_ELB - 20
pipe((0, Y_HEADER - R_ELB), (0, Y_WELD), R_P)

# ================================================================== new works
cap((0, Y_WELD), "v", R_P)                                              # weld
line((R_P, Y_WELD), (R_P, Y_LOWV + FL_T))                               # spool wall opposite branch
line((-R_P, Y_WELD), (-R_P, Y_BRANCH + R_P))                           # spool wall, branch side
line((-R_P, Y_BRANCH - R_P), (-R_P, Y_LOWV + FL_T))
X_BF = -(R_P + 170)
pipe((-R_P, Y_BRANCH), (X_BF + FL_T, Y_BRANCH), R_P)
flg((X_BF + FL_T / 2, Y_BRANCH), 0)
flg((0, Y_LOWV + FL_T / 2), 90)
xv = X_BF - VALVE_FTF / 2
gv((xv, Y_BRANCH), 0)                                                   # branch valve
gv((0, Y_LOWV - VALVE_FTF / 2), 90)                                     # valve on leg (stem horiz.)
y_low = Y_LOWV - VALVE_FTF
flg((0, y_low - FL_T / 2), 90)
pipe((0, y_low - FL_T), (0, 0), EXIST_R)
pipe((0, 0), (0, -600), EXIST_R, B)
brk((0, -600), "v", EXIST_R, B)
x_af = X_BF - VALVE_FTF
flg((x_af - FL_T / 2, Y_BRANCH), 0)
x0 = x_af - FL_T
pipe((x0, Y_BRANCH), (X_DOWN + R_ELB, Y_BRANCH), R_P)
cap((X_DOWN + R_ELB, Y_BRANCH), "h", R_P)
bend((X_DOWN + R_ELB, Y_BRANCH - R_ELB), R_ELB, 90, 180, R_P)
cap((X_DOWN, Y_BRANCH - R_ELB), "v", R_P)
pipe((X_DOWN, Y_BRANCH - R_ELB), (X_DOWN, 0), R_P)
pipe((X_DOWN, 0), (X_DOWN, Y_BURIED + R_ELB), R_P, B)
cap((X_DOWN, Y_BURIED + R_ELB), "v", R_P, B)
bend((X_DOWN - R_ELB, Y_BURIED + R_ELB), R_ELB, 270, 360, R_P, B)
cap((X_DOWN - R_ELB, Y_BURIED), "h", R_P, B)
pipe((X_DOWN - R_ELB, Y_BURIED), (X_TRANS + FL_T, Y_BURIED), R_P, B)
flg((X_TRANS + FL_T / 2, Y_BURIED), 0, B)                               # steel flange
flg((X_TRANS - FL_T / 2, Y_BURIED), 0, B)                               # Golan flange
xg1, xg2 = X_TRANS - FL_T, X_TRANS - FL_T - GOLAN_L
msp.add_lwpolyline([(xg1, Y_BURIED - 115), (xg2, Y_BURIED - 115), (xg2, Y_BURIED + 115), (xg1, Y_BURIED + 115)],
                   close=True, dxfattribs={"layer": B})
pipe((xg2, Y_BURIED), (X_END, Y_BURIED), PEX_R, B)
brk((X_END, Y_BURIED), "h", PEX_R, B)
msp.add_lwpolyline([(X_DOWN - 500, Y_BURIED - 300), (X_DOWN + 200, Y_BURIED - 300),
                    (X_DOWN + 200, Y_BURIED + 300), (X_DOWN - 500, Y_BURIED + 300)], close=True,
                   dxfattribs={"layer": B})                                # thrust block
line((X_END - 200, 0), (X_RIGHT + 200, 0), A)                             # finished grade
for gx in range(int(X_END - 180), int(X_RIGHT + 200), 150):
    line((gx, 0), (gx - 45, -45), A)

# ================================================================== dimensions (chain, reference style)
yd = Y_BRANCH + 620
dim_h((X_DOWN, Y_BRANCH), (X_DOWN + R_ELB, Y_BRANCH), yd)
dim_h((X_DOWN + R_ELB, Y_BRANCH), (x0, Y_BRANCH), yd, "~<>")
dim_h((x0, Y_BRANCH), (x_af, Y_BRANCH), yd)
dim_h((x_af, Y_BRANCH), (X_BF, Y_BRANCH), yd)
dim_h((X_BF, Y_BRANCH), (0, Y_BRANCH), yd, "~<>")
dim_v((-1250, 0), (-1250, Y_BRANCH), -1250, "~<>")
dim_v((X_DOWN - 120, Y_BRANCH - R_ELB), (X_DOWN - 120, Y_BRANCH), -1150)
dim_v((X_RIGHT + 250, 0), (X_RIGHT + 250, Y_HEADER), X_RIGHT + 250, "~<>")
dim_v((220, Y_LOWV - VALVE_FTF), (220, Y_LOWV), 220)
dim_v((-1850, 0), (-1850, Y_BURIED + PEX_R), -1850, "<> MIN")
yb = Y_BURIED - 450
dim_h((X_TRANS - FL_T - GOLAN_L, Y_BURIED), (X_TRANS, Y_BURIED), yb, "~<>")
dim_h((X_TRANS, Y_BURIED), (X_DOWN - R_ELB, Y_BURIED), yb, "~<>")
dim_h((X_DOWN - R_ELB, Y_BURIED), (X_DOWN, Y_BURIED), yb)
dim_h((X_DOWN, Y_BURIED), (0, Y_BURIED), yb, "~<>")

# ================================================================== balloons + legend (EN / HE)
def balloon(n, target, at):
    rr = 45
    dx, dy = target[0] - at[0], target[1] - at[1]
    L = math.hypot(dx, dy)
    line((at[0] + dx / L * rr, at[1] + dy / L * rr), target, "TEXT")
    msp.add_circle(target, 7, dxfattribs={"layer": "TEXT"})
    msp.add_circle(at, rr, dxfattribs={"layer": "TEXT"})
    msp.add_text(str(n), height=TXT, dxfattribs={"layer": "TEXT", "style": "ARIAL"}).set_placement(
        at, align=TextEntityAlignment.MIDDLE_CENTER)


ELB45 = (X_DOWN + R_ELB - (R_ELB + R_P) * 0.7071, Y_BRANCH - R_ELB + (R_ELB + R_P) * 0.7071)
LEGEND = [  # (no, target, balloon, English, Hebrew)
    (1, (X_RIGHT + R_P, Y_HEADER - R_ELB + 40), (1750, 1000),
     'EXISTING CAMEL 6" - STEEL MANIFOLD', 'גמל קיים 6 צול - סעפת פלדה'),
    (2, (xv2 - VALVE_FTF / 2 + 10, Y_HEADER + VALVE_H), (1150, 1600),
     'EXISTING GATE VALVE', 'מגוף קיים'),
    (3, (X_CENTER + 45, 1330), (820, 1700),
     'EXISTING AIR VALVE & SAMPLING TAP', 'שסתום אוויר וברז דיגום קיימים'),
    (4, (X_RIGHT + EXIST_R, -450), (1750, -450),
     'EXISTING 160 PIPE BELOW GRADE (TYP.)', 'צינור 160 קיים מתחת לקרקע (טיפוסי)'),
    (5, (R_P, 340), (300, 330),
     'NEW 6" STEEL SPOOL WITH 6" BRANCH, WELDED INTO EXISTING LEG',
     'קטע פלדה חדש 6 צול עם יציאה 6 צול, מרותך לרגל הקיימת'),
    (6, (xv + 10, Y_BRANCH + VALVE_H), (-550, 1350),
     'GATE VALVE AVK 06/61 DN150 PN16, L=210 - ON BRANCH, STEM VERTICAL',
     'מגוף טריז AVK 06/61 קוטר 150 דרג 16 - על הענף, ציר אנכי'),
    (7, (-200, Y_LOWV - VALVE_FTF / 2), (-600, -300),
     'GATE VALVE AVK 06/61 DN150 PN16 - ON EXISTING LEG, STEM HORIZONTAL',
     'מגוף טריז AVK 06/61 קוטר 150 דרג 16 - על הרגל הקיימת, ציר אופקי'),
    (8, (X_RIGHT + FL_D / 2, 450 - FL_T), (1750, 430),
     'FLANGED JOINT 6" PN16 (D285 PCD240), EPDM GASKET, 8xM20 SS316 BOLTS (TYP.)',
     'חיבור אוגנים 6 צול דרג 16, אטם EPDM, 8 ברגי M20 נירוסטה 316 (טיפוסי)'),
    (9, ELB45, (-1500, 950),
     '6" STEEL PIPE SCH40 + LR 90 ELBOW A234 WPB, 3 LAYERS VINYL TAPE (TYP.)',
     'צינור פלדה 6 צול סקדיול 40 + קשת 90 רדיוס ארוך, עטוף 3 שכבות סרט ויניל'),
    (10, (X_TRANS - FL_T - 120, Y_BURIED + 115), (-1550, -750),
     'GOLAN STEEL FLANGE CONNECTOR 160x6" (PEX5081-160)', 'מחבר אוגן פלדה לפקסגול 160 מ"מ / 6 צול, גולן'),
    (11, (X_END + 60, Y_BURIED - PEX_R), (-2100, -1750),
     'PEXGOL PE-Xa 160 CLASS 10 - BELOW GRADE, TO SITE', 'צינור פקסגול 160 דרג 10 - מתחת לקרקע, לאתר'),
    (12, (X_DOWN + 200, Y_BURIED - 250), (-450, -1500),
     'CONCRETE THRUST BLOCK', 'גוש עיגון מבטון'),
]
for n, tgt, at, _, _ in LEGEND:
    balloon(n, tgt, at)
msp.add_text("FINISHED GRADE", height=TXT, dxfattribs={"layer": "TEXT", "style": "ARIAL"}).set_placement(
    (X_END - 180, 25))

# legend table: No. | DESCRIPTION | תיאור
TX, TY = 120, -800
CW = (120, 1450, 1250)                       # column widths
RH, TH = 92, 24
xs = [TX, TX + CW[0], TX + CW[0] + CW[1], TX + sum(CW)]


def heb(t):
    return t                                 # logical order - AutoCAD renders RTL itself


def hcell(t, x, y):
    """Hebrew cell: MTEXT anchored middle-right, paragraph right-aligned."""
    m = msp.add_mtext("\\pxqr;" + t, dxfattribs={"layer": "TEXT", "char_height": TH, "style": "ARIAL",
                                                "width": CW[2] - 36})
    m.set_location((x, y), attachment_point=6)


def tcell(t, x, y, align):
    msp.add_text(t, height=TH, dxfattribs={"layer": "TEXT", "style": "ARIAL"}).set_placement((x, y), align=align)


msp.add_text("LEGEND / " + heb("מקרא"), height=40, dxfattribs={"layer": "TEXT", "style": "ARIAL"}).set_placement(
    (TX, TY + 35))
rows = [("No.", "DESCRIPTION", heb("תיאור"))] + [(str(n), en, heb(he)) for n, _, _, en, he in LEGEND]
for i, (c0, c1, c2) in enumerate(rows):
    y = TY - i * RH
    line((TX, y), (xs[-1], y), "TEXT")
    ym = y - RH / 2
    tcell(c0, (xs[0] + xs[1]) / 2, ym, TextEntityAlignment.MIDDLE_CENTER)
    tcell(c1, xs[1] + 18, ym, TextEntityAlignment.MIDDLE_LEFT)
    hcell(c2, xs[3] - 18, ym)
yb_t = TY - len(rows) * RH
line((TX, yb_t), (xs[-1], yb_t), "TEXT")
for x in xs:
    line((x, TY), (x, yb_t), "TEXT")
line((TX, TY - RH - 6), (xs[-1], TY - RH - 6), "TEXT")   # double line under header

# ================================================================== frame + title line
FX0, FY0, FX1, FY1 = -3300, -2350, 3000, 2105
msp.add_lwpolyline([(FX0, FY0), (FX1, FY0), (FX1, FY1), (FX0, FY1)], close=True,
                   dxfattribs={"layer": "FRAME", "const_width": 10})
msp.add_mtext("CONNECTION TO EXISTING CAMEL 6\" - AS MADE  |  HATZERIM 20117 - RFI-0021  |  "
              "DEVELOPED ELEVATION, SCALE 1:15 (A3)\\P"
              "DIMENSIONS IN mm, '~' = SITE VALUE TO BE VERIFIED.  SAND 200 mm ALL ROUND, MIN COVER 1.20 m.  "
              "BURIED STEEL PROTECTED PER SI 1427.",
              dxfattribs={"layer": "TEXT", "char_height": 30, "width": 3300, "style": "ARIAL"}
              ).set_location((FX0 + 60, FY0 + 170), attachment_point=7)

path = OUT / "20117-W-CAMEL-02_AsMade_Simple.dxf"
doc.saveas(path)
print("saved", path)
if "--png" in sys.argv:
    # preview only: matplotlib has no bidi, so show Hebrew in visual order (DXF/DWG keep logical order)
    def _vis(t):
        if "\\pxqr;" in t:
            return "\\pxqr;" + get_display(t.replace("\\pxqr;", ""))
        return get_display(t)
    for e in msp.query("TEXT MTEXT"):
        if any("\u0590" <= ch <= "\u05ff" for ch in e.dxf.text):
            e.dxf.text = _vis(e.dxf.text)
    from ezdxf.addons.drawing import matplotlib as mpl_draw
    mpl_draw.qsave(msp, str(OUT / "preview_simple.png"), bg="#FFFFFF", dpi=200, size_inches=(16.5, 11.7))
    print("preview saved")
