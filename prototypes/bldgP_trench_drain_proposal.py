# -*- coding: utf-8 -*-
"""
Hatzerim 20117 - Bldg P drainage: PROPOSAL - trench drains connected directly to the existing
drainage manholes (instead of %%c200 into the %%c400 BT pipe per E5/CG501) + grated covers on manholes.
Plan view, A3 @ 1:400, units = METERS. Geometry scaled from CG104/CG105 (1:400) - approximate.
Existing (per plans): %%c400 BT lines N & S of the paved area, concrete manholes %%c125:
  NW INV 225.20, SW INV 225.20 (CG105), SE INV 224.94 (CG104); north line outlets to headwall INV 224.84.
Trench drains A8/CG501 (L-shaped, along the %%c400 lines) - per CG104 keynote 5 / CG105 keynote 3.
"""
import math
import sys
from pathlib import Path

import ezdxf
from bidi.algorithm import get_display
from ezdxf.enums import TextEntityAlignment

OUT = Path(__file__).parent
NAME = "20117-D-BLDG-P_TrenchDrain_Proposal"

# ------------------------------------------------------------------ geometry (m, origin = MH-NW)
Y_N, Y_S = 0.0, -40.0               # %%c400 lines (north / south)
MH = {"NW": (0.0, Y_N, "225.20"), "SW": (0.0, Y_S, "225.20"), "SE": (64.6, Y_S, "224.94")}
X_TW, X_TE = 1.5, 63.7              # trench drain west / east legs
Y_TN, Y_TS = -1.0, -39.0            # trench drain north / south runs
RET_N, RET_S = 9.7, 13.9            # return-leg lengths (north / south)
X_HW = 92.0                         # headwall (north line outlet to swale)
BERM = dict(x0=-21.0, x1=73.3, yn=6.0, ys=-45.9, gap=(-9.9, 1.6))
BLDG = (10.0, -8.1, 55.0, -31.5)
TD_W = 0.32                         # trench drain outer width (60+200+60 mm)
MH_RI, MH_RO = 0.625, 0.775         # %%c125 concrete manhole inner / outer radius
PIPE_R = 0.24                       # %%c400 BT drawn radius

TXT, TXT_S, TXT_D = 1.0, 0.8, 0.8   # 2.5 / 2.0 mm @ 1:400

doc = ezdxf.new("R2018", setup=True)
doc.units = ezdxf.units.M
msp = doc.modelspace()
doc.styles.add("ARIAL", font="arial.ttf")
for name, col, lt in (("EXIST", 7, "CONTINUOUS"), ("PIPE", 7, "CONTINUOUS"), ("BERM", 7, "CONTINUOUS"),
                      ("PROPOSED", 1, "CONTINUOUS"), ("TEXT", 7, "CONTINUOUS"), ("DIM", 7, "CONTINUOUS"),
                      ("FRAME", 7, "CONTINUOUS"), ("HIDDEN", 8, "DASHED")):
    doc.layers.add(name, color=col, linetype=lt)
doc.header["$LTSCALE"] = 0.4
E, P = "EXIST", "PROPOSED"


def line(p1, p2, layer=E, **kw):
    msp.add_line(p1, p2, dxfattribs={"layer": layer, **kw})


def pline(pts, layer=E, close=False, **kw):
    msp.add_lwpolyline(pts, close=close, dxfattribs={"layer": layer, **kw})


def text(s, p, h=TXT, layer="TEXT", align=TextEntityAlignment.LEFT, rot=0):
    t = msp.add_text(s, height=h, dxfattribs={"layer": layer, "style": "ARIAL", "rotation": rot})
    t.set_placement(p, align=align)


def offset_path(pts, d):
    """Offset an orthogonal open polyline by d (left side positive)."""
    out = []
    n = len(pts)
    for i in range(n):
        if i == 0:
            dx, dy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
            L = math.hypot(dx, dy)
            out.append((pts[0][0] - dy / L * d, pts[0][1] + dx / L * d))
        elif i == n - 1:
            dx, dy = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
            L = math.hypot(dx, dy)
            out.append((pts[i][0] - dy / L * d, pts[i][1] + dx / L * d))
        else:
            ax, ay = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
            bx, by = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
            la, lb = math.hypot(ax, ay), math.hypot(bx, by)
            na = (-ay / la, ax / la)
            nb = (-by / lb, bx / lb)
            out.append((pts[i][0] + (na[0] + nb[0]) * d, pts[i][1] + (na[1] + nb[1]) * d))
    return out


def trench(pts):
    """Trench drain: outer outline + grate ticks along the run."""
    a, b = offset_path(pts, TD_W / 2), offset_path(pts, -TD_W / 2)
    pline(a, E)
    pline(b, E)
    line(a[0], b[0])
    line(a[-1], b[-1])
    for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        for k in range(1, int(L / 0.8)):
            cx, cy = x1 + ux * k * 0.8, y1 + uy * k * 0.8
            line((cx - uy * TD_W / 2, cy + ux * TD_W / 2), (cx + uy * TD_W / 2, cy - ux * TD_W / 2))


def pipe400(p1, p2):
    (x1, y1), (x2, y2) = p1, p2
    L = math.hypot(x2 - x1, y2 - y1)
    nx, ny = -(y2 - y1) / L * PIPE_R, (x2 - x1) / L * PIPE_R
    line((x1 + nx, y1 + ny), (x2 + nx, y2 + ny), "PIPE")
    line((x1 - nx, y1 - ny), (x2 - nx, y2 - ny), "PIPE")


def grate(c, half, layer=P):
    """Square grated cover symbol."""
    x, y = c
    pline([(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)], layer, True)
    n = 5
    for i in range(1, n):
        xx = x - half + 2 * half * i / n
        line((xx, y - half), (xx, y + half), layer)


def manhole(c):
    msp.add_circle(c, MH_RO, dxfattribs={"layer": E})
    msp.add_circle(c, MH_RI, dxfattribs={"layer": E})
    grate(c, 0.42)


def flow_arrow(p, ang, layer=E, L=2.2):
    x, y = p
    ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    tip = (x + ux * L / 2, y + uy * L / 2)
    line((x - ux * L / 2, y - uy * L / 2), tip, layer)
    b = (tip[0] - ux * 0.6, tip[1] - uy * 0.6)
    msp.add_solid([tip, (b[0] - uy * 0.25, b[1] + ux * 0.25), (b[0] + uy * 0.25, b[1] - ux * 0.25)],
                  dxfattribs={"layer": layer})


DIM_OVR = {"dimtxt": TXT_D, "dimasz": 0.5, "dimexe": 0.4, "dimexo": 0.4, "dimgap": 0.2,
           "dimdec": 1, "dimtad": 1, "dimlfac": 1, "dimtxsty": "ARIAL"}


def dim_h(p1, p2, y, txt="<>"):
    msp.add_linear_dim(base=(p1[0], y), p1=p1, p2=p2, angle=0, text=txt, dimstyle="EZDXF",
                       override=DIM_OVR, dxfattribs={"layer": "DIM"}).render()


def dim_v(p1, p2, x, txt="<>"):
    msp.add_linear_dim(base=(x, p1[1]), p1=p1, p2=p2, angle=90, text=txt, dimstyle="EZDXF",
                       override=DIM_OVR, dxfattribs={"layer": "DIM"}).render()


# ================================================================== berm / paved area / building
g0, g1 = BERM["gap"]
B = "BERM"
berm_paths = [
    [(g1, BERM["yn"]), (BERM["x1"], BERM["yn"]), (BERM["x1"], BERM["ys"]), (g1, BERM["ys"])],
    [(g0, BERM["ys"]), (BERM["x0"], BERM["ys"]), (BERM["x0"], BERM["yn"]), (g0, BERM["yn"])],
]
for pts in berm_paths:
    pline(pts, B, lineweight=50)
    for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):           # berm ticks (outward)
        L = math.hypot(x2 - x1, y2 - y1)
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        for k in range(1, int(L / 1.6)):
            cx, cy = x1 + ux * k * 1.6, y1 + uy * k * 1.6
            line((cx, cy), (cx + uy * 0.9, cy - ux * 0.9), B)
for xg in (g0, g1):                                             # access roads through the openings
    line((xg, BERM["yn"]), (xg, BERM["yn"] + 8))
    line((xg, BERM["ys"]), (xg, BERM["ys"] - 8))
text("ACCESS", ((g0 + g1) / 2, BERM["yn"] + 4), h=TXT_S, align=TextEntityAlignment.MIDDLE_CENTER)
text("ACCESS", ((g0 + g1) / 2, BERM["ys"] - 4), h=TXT_S, align=TextEntityAlignment.MIDDLE_CENTER)
bx0, by0, bx1, by1 = BLDG
pline([(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)], E, True, lineweight=35)
text("BUILDING P", ((bx0 + bx1) / 2, (by0 + by1) / 2 + 1.0), h=1.6, align=TextEntityAlignment.MIDDLE_CENTER)
text("FFE 227.00", ((bx0 + bx1) / 2, (by0 + by1) / 2 - 1.5), h=TXT_S, align=TextEntityAlignment.MIDDLE_CENTER)

# ================================================================== existing %%c400 lines + manholes + headwall
mnw, msw, mse = (MH["NW"][:2]), (MH["SW"][:2]), (MH["SE"][:2])
pipe400((MH_RO, Y_N), (X_HW, Y_N))
pipe400((MH_RO, Y_S), (mse[0] - MH_RO, Y_S))
pipe400((mse[0], Y_S - MH_RO), (mse[0], Y_S - 14))
pline([(mse[0] - 0.8, Y_S - 14), (mse[0] - 0.2, Y_S - 14), (mse[0], Y_S - 13.6), (mse[0], Y_S - 14.4),
       (mse[0] + 0.2, Y_S - 14), (mse[0] + 0.8, Y_S - 14)], "PIPE")
pline([(X_HW, Y_N - 1.2), (X_HW, Y_N + 1.2), (X_HW + 0.5, Y_N + 1.2), (X_HW + 0.5, Y_N - 1.2)], E, True)
for c in (mnw, msw, mse):
    manhole(c)
flow_arrow((40, Y_N + 1.1), 0, "PIPE")
flow_arrow((40, Y_S - 1.1), 0, "PIPE")
flow_arrow((mse[0] + 1.1, Y_S - 8), -90, "PIPE")
text("%%c400 BT @ 0.4% (EXISTING)", (22, Y_N + 0.6), h=TXT_S)
text("%%c400 BT @ 0.4% (EXISTING)", (22, Y_S - 1.9), h=TXT_S)
text("HEADWALL - OUTLET TO SWALE, INV 224.84", (X_HW - 0.5, Y_N + 2.0), h=TXT_S,
     align=TextEntityAlignment.RIGHT)
text("MH-NW  INV 225.20", (-1.2, Y_N + 1.4), h=TXT_S, align=TextEntityAlignment.RIGHT)
text("MH-SW  INV 225.20", (-1.2, Y_S - 2.2), h=TXT_S, align=TextEntityAlignment.RIGHT)
text("MH-SE  INV 224.94", (mse[0] + 1.3, Y_S - 2.2), h=TXT_S)

# ================================================================== trench drains (A8/CG501)
north_td = [(X_TW, Y_TN - RET_N), (X_TW, Y_TN), (X_TE, Y_TN), (X_TE, Y_TN - RET_N - 0.3)]
south_td = [(X_TW, Y_TS + RET_S), (X_TW, Y_TS), (X_TE, Y_TS), (X_TE, Y_TS + RET_S)]
trench(north_td)
trench(south_td)
for x in (14, 30, 46):                                          # flow: north run -> west (to MH-NW)
    flow_arrow((x, Y_TN - 1.1), 180)
flow_arrow((X_TW + 1.1, Y_TN - 5), 90)
flow_arrow((X_TE - 1.1, Y_TN - 5), 90)
for x in (12, 24):                                              # south run splits W / E
    flow_arrow((x, Y_TS + 1.1), 180)
for x in (40, 52):
    flow_arrow((x, Y_TS + 1.1), 0)
flow_arrow((X_TW + 1.1, Y_TS + 7), -90)
flow_arrow((X_TE - 1.1, Y_TS + 7), -90)

# ================================================================== PROPOSED %%c200 connections trench -> manhole
def conn(p_td, c_mh):
    dx, dy = c_mh[0] - p_td[0], c_mh[1] - p_td[1]
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    a = (p_td[0] + ux * 0.25, p_td[1] + uy * 0.25)
    b = (c_mh[0] - ux * MH_RO, c_mh[1] - uy * MH_RO)
    r = 0.12
    line((a[0] - uy * r, a[1] + ux * r), (b[0] - uy * r, b[1] + ux * r), P, lineweight=35)
    line((a[0] + uy * r, a[1] - ux * r), (b[0] + uy * r, b[1] - ux * r), P, lineweight=35)
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


c1 = conn((X_TW, Y_TN), mnw)
c2 = conn((X_TW, Y_TS), msw)
c3 = conn((X_TE, Y_TS), mse)

# ================================================================== dimensions (approx.)
dim_h((X_TW, Y_TN), (X_TE, Y_TN), 3.2, "<> (APPROX.)")
dim_h((0, Y_S), (mse[0], Y_S), Y_S - 4.0, "<> (APPROX.)")
dim_v((X_TE, Y_TN), (X_TE, Y_TN - RET_N - 0.3), X_TE + 3.0)
dim_v((X_TE, Y_TS), (X_TE, Y_TS + RET_S), X_TE + 3.0)
dim_v((-3.5, Y_N), (-3.5, Y_S), -6.0)

# north arrow
na = (125, 6)
pline([(na[0], na[1] + 4), (na[0] - 1.4, na[1] - 1.5), (na[0], na[1] - 0.6), (na[0] + 1.4, na[1] - 1.5)], E, True)
msp.add_solid([(na[0], na[1] + 4), (na[0], na[1] - 0.6), (na[0] + 1.4, na[1] - 1.5)], dxfattribs={"layer": E})
text("N", (na[0], na[1] + 5.2), h=1.6, align=TextEntityAlignment.MIDDLE_CENTER)

# ================================================================== DETAIL 1 - typical connection (enlarged)
K = 5.0
DX, DY = 107.0, -26.0                                           # detail origin (trench outlet end)


def dd(x, y):
    return DX + K * x, DY + K * y


def dlabel(s, at, target, layer="TEXT"):
    text(s, at, h=TXT_S, layer=layer)
    x_end = at[0] + len(s.replace("%%c", "c")) * TXT_S * 0.80
    line((x_end + 0.8, at[1] + 0.4), target, "TEXT")
    msp.add_circle(target, 0.15, dxfattribs={"layer": "TEXT"})


ex = -2.6                                                        # trench drain shown 2.6 m
for yy in (TD_W / 2, -TD_W / 2):
    line(dd(ex, yy), dd(0, yy), E)
line(dd(0, TD_W / 2), dd(0, -TD_W / 2), E, lineweight=35)
for k in range(1, 13):
    x = ex + k * 0.2
    line(dd(x, TD_W / 2 - 0.04), dd(x, -TD_W / 2 + 0.04), E)
msp.add_circle(dd(-0.12, 0), K * 0.1, dxfattribs={"layer": P})  # outlet
L_C = 1.6
for yy in (0.11, -0.11):
    line(dd(0, yy), dd(L_C, yy), P, lineweight=35)
mc = (L_C + MH_RO, 0)
msp.add_circle(dd(*mc), K * MH_RO, dxfattribs={"layer": E})
msp.add_circle(dd(*mc), K * MH_RI, dxfattribs={"layer": E})
gx, gy = dd(*mc)
grate((gx, gy), K * 0.42)
flow_arrow(dd(-2.0, 0.42), 0, E, L=4)
text("DETAIL 1 - TYPICAL CONNECTION (PLAN)", (DX - K * 2.7, DY + 10.0), h=1.1)
text("ENLARGED APPROX. 1:80", (DX - K * 2.7, DY + 8.5), h=TXT_S)
dlabel("GRATED COVER E600 (NEW)", (DX + K * 0.2, DY + K * 1.25), dd(mc[0] + 0.25, 0.42), P)
dlabel("TRENCH DRAIN A8/CG501", (DX - K * 2.7, DY - K * 1.05), dd(-1.3, -TD_W / 2))
dlabel("%%c200 PVC SN8, L~1.5 m, SLOPE >= 1% TO MH", (DX - K * 2.7, DY - K * 1.45), dd(0.8, -0.11), P)
dlabel("CORE DRILL + RUBBER SEAL INTO EXISTING MH %%c125", (DX - K * 2.7, DY - K * 1.85), dd(L_C, -0.12), P)

# ================================================================== balloons + legend (EN / HE)
def balloon(n, target, at):
    rr = 1.1
    dx, dy = target[0] - at[0], target[1] - at[1]
    L = math.hypot(dx, dy)
    line((at[0] + dx / L * rr, at[1] + dy / L * rr), target, "TEXT")
    msp.add_circle(target, 0.18, dxfattribs={"layer": "TEXT"})
    msp.add_circle(at, rr, dxfattribs={"layer": "TEXT"})
    text(str(n), at, h=TXT, align=TextEntityAlignment.MIDDLE_CENTER)


LEGEND = [
    (1, (60, Y_N - PIPE_R), (60, 8.5),
     "EXISTING %%c400 BT DRAINAGE PIPE (INSTALLED)", "צינור ניקוז %%c400 בטון קיים - מותקן"),
    (2, (mse[0] + 0.6, Y_S - 0.5), (70, -50),
     "EXISTING CONCRETE DRAINAGE MANHOLE %%c125 (INSTALLED)", "שוחת ניקוז קיימת מבטון %%c125 - מותקנת"),
    (3, (msw[0] + 0.3, Y_S - 0.3), (-16, -52),
     "PROPOSED: GRATED COVER E600 INSTEAD OF CLOSED LID", "מוצע: מכסה רשת E600 בשוחה במקום מכסה אטום"),
    (4, (30, Y_TS - TD_W / 2), (30, -50),
     "TRENCH DRAIN (PRECAST CHANNEL, A8/CG501)", "תעלת ניקוז טרומית - פרט A8/CG501"),
    (5, c1, (-10, -6),
     "PROPOSED: %%c200 FROM TRENCH OUTLET DIRECTLY INTO MANHOLE (INSTEAD OF INTO %%c400, E5/CG501)",
     "מוצע: חיבור %%c200 מקצה התעלה ישירות לשוחה, במקום לצינור %%c400 לפי פרט E5"),
    (6, (X_HW, Y_N + 1.2), (X_HW + 3, 6),
     "EXISTING CONCRETE HEADWALL - OUTLET TO SWALE", "ראש קיר בטון קיים - יציאה לתעלה"),
    (7, (BERM["x1"], -20), (80, -20),
     "TOE OF BERM", "רגל סוללה"),
    (8, (bx1, by1 + 3), (50, -24),
     "BUILDING P (FFE 227.00)", "מבנה P - מפלס רצפה 227.00"),
]
for n, tgt, at, _, _ in LEGEND:
    balloon(n, tgt, at)
balloon(5, c2, (-10, -36))
balloon(5, c3, (69, -33))

TX, TY = -30.0, -60.0
CW = (5.0, 68.0, 50.0)
RH, TH = 2.4, 0.85
xs = [TX, TX + CW[0], TX + CW[0] + CW[1], TX + sum(CW)]


def hcell(t, x, y):
    m = msp.add_mtext("\\pxqr;" + t, dxfattribs={"layer": "TEXT", "char_height": TH, "style": "ARIAL",
                                                "width": CW[2] - 1.0})
    m.set_location((x, y), attachment_point=6)


text("LEGEND / מקרא", (TX, TY + 1.0), h=1.2)
rows = [("No.", "DESCRIPTION", "תיאור")] + [(str(n), en, he) for n, _, _, en, he in LEGEND]
for i, (c0, c1_, c2_) in enumerate(rows):
    y = TY - i * RH
    line((TX, y), (xs[-1], y), "TEXT")
    ym = y - RH / 2
    text(c0, ((xs[0] + xs[1]) / 2, ym), h=TH, align=TextEntityAlignment.MIDDLE_CENTER)
    text(c1_, (xs[1] + 0.6, ym), h=TH, align=TextEntityAlignment.MIDDLE_LEFT)
    hcell(c2_, xs[3] - 0.6, ym)
yb = TY - len(rows) * RH
line((TX, yb), (xs[-1], yb), "TEXT")
line((TX, TY - RH - 0.2), (xs[-1], TY - RH - 0.2), "TEXT")
for x in xs:
    line((x, TY), (x, yb), "TEXT")

NOTES = ("NOTES:\\P"
         "1. PROPOSAL: TRENCH DRAINS DISCHARGE DIRECTLY INTO THE EXISTING DRAINAGE MANHOLES (MH-NW, MH-SW, MH-SE). "
         "THE %%c200 DISCHARGE INTO THE %%c400 BT PIPE PER E5/CG501 IS CANCELLED.\\P"
         "2. MANHOLE COVERS: GRATED COVER CLASS E600 (EN 124) INSTEAD OF CLOSED LID.\\P"
         "3. CONNECTION INTO MANHOLE WALL BY CORE DRILLING + RUBBER SEAL. MANHOLE BENCHING PER GOVT REMARK "
         "57 00 00-7.2 (b).\\P"
         "4. %%c200 INVERTS AND TRENCH DRAIN SLOPES PER COORDINATED PROFILE (GOVT REMARK c.i) - TO BE ISSUED.\\P"
         "5. RED = PROPOSED.  DIMENSIONS IN METERS, SCALED FROM CG104/CG105 - APPROXIMATE.")
msp.add_mtext(NOTES, dxfattribs={"layer": "TEXT", "char_height": 0.85, "style": "ARIAL", "width": 36.0}
              ).set_location((xs[-1] + 3.0, TY + 0.5), attachment_point=1)

# ================================================================== frame + title
FX0, FY0, FX1, FY1 = -36.0, -102.8, 132.0, 16.0
pline([(FX0, FY0), (FX1, FY0), (FX1, FY1), (FX0, FY1)], "FRAME", True, const_width=0.25)
msp.add_mtext("BUILDING P - TRENCH DRAIN CONNECTION TO EXISTING MANHOLES - PROPOSAL (PLAN VIEW)\\P"
              "HATZERIM 20117  |  REF: CG104, CG105, CG501 (A8, C8, E5), TRANSMITTAL 57 00 00-7.2  |  "
              "SCALE 1:400 (A3)",
              dxfattribs={"layer": "TEXT", "char_height": 1.2, "style": "ARIAL", "width": 160.0}
              ).set_location((FX0 + 2.0, FY0 + 5.5), attachment_point=7)

dxf_path = OUT / f"{NAME}.dxf"
doc.saveas(dxf_path)
print("saved", dxf_path)

if "--png" in sys.argv or "--pdf" in sys.argv:
    # preview/PDF only: matplotlib has no bidi -> Hebrew in visual order (DXF/DWG keep logical order)
    for e in msp.query("TEXT MTEXT"):
        t = e.dxf.text.replace("%%c", "\u00d8")
        e.dxf.text = t
        if any("֐" <= ch <= "׿" for ch in t):
            e.dxf.text = ("\\pxqr;" + get_display(t.replace("\\pxqr;", ""))) if "\\pxqr;" in t else get_display(t)
    from ezdxf.addons.drawing import matplotlib as mpl_draw
    if "--png" in sys.argv:
        mpl_draw.qsave(msp, str(OUT / f"{NAME}.png"), bg="#FFFFFF", dpi=200, size_inches=(16.54, 11.69))
    if "--pdf" in sys.argv:
        mpl_draw.qsave(msp, str(OUT / f"{NAME}.pdf"), bg="#FFFFFF", size_inches=(16.54, 11.69))
    print("preview saved")
