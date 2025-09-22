from typing import List, Tuple, Dict, Any
from datetime import datetime

def _dxf_header() -> str:
    return """0
SECTION
2
HEADER
9
$INSUNITS
70
1
9
$LIMMIN
10
0.0
20
0.0
9
$LIMMAX
10
5000.0
20
5000.0
0
ENDSEC
0
SECTION
2
TABLES
0
ENDSEC
0
SECTION
2
BLOCKS
0
ENDSEC
0
SECTION
2
ENTITIES
"""

def _dxf_footer() -> str:
    return """0
ENDSEC
0
EOF
"""

def _lwpolyline(points: List[Tuple[float,float]], closed=True, layer="0") -> str:
    n = len(points)
    s = f"0\nLWPOLYLINE\n8\n{layer}\n90\n{n}\n70\n{1 if closed else 0}\n"
    for (x, y) in points:
        s += f"10\n{x}\n20\n{y}\n"
    return s

def _text(x: float, y: float, text: str, height: float=2.5, layer="TEXT") -> str:
    return f"""0
TEXT
8
{layer}
10
{x}
20
{y}
40
{height}
1
{text}
"""

def rect_entity(x: float, y: float, length: float, width: float, layer="0") -> str:
    pts = [(x,y), (x+length,y), (x+length,y+width), (x,y+width)]
    return _lwpolyline(pts, closed=True, layer=layer)

def build_curb_dxf(geom: Dict[str, Any], meta: Dict[str, str]) -> str:
    doc = _dxf_header()
    doc += _text(5, 4900, f"Curbonomix DXF - {meta.get('adapter_name','')}", 5)
    co = geom["curb_outline"]
    doc += rect_entity(co["x"], co["y"], co["length"], co["width"], layer="CURB")
    rs = geom["rtu_seat"]
    doc += rect_entity(rs["x"], rs["y"], rs["length"], rs["width"], layer="RTU_SEAT")
    doc += _text(rs["x"], rs["y"]-5, "RTU Seat", 2)
    for name, d in geom["drops"].items():
        x, y = d["x"], d["y"]
        doc += _lwpolyline([(x-2,y),(x+2,y)], closed=False, layer="DROPS")
        doc += _lwpolyline([(x,y-2),(x,y+2)], closed=False, layer="DROPS")
        doc += _text(x+3, y+3, name.upper(), 2)
    cursor_x, cursor_y = 0.0, -50.0
    gap = 5.0
    for p in geom["panels"]:
        doc += rect_entity(cursor_x, cursor_y, p["length"], p["height"], layer="PANEL")
        doc += _text(cursor_x + 2, cursor_y + p["height"] + 3, f"{p['name']} {p['length']}x{p['height']} in", 2)
        cursor_x += p["length"] + gap
    y_warn = -10.0
    for w in geom.get("warnings", []):
        doc += _text(0, y_warn, f"WARNING: {w}", 2, layer="TEXT")
        y_warn += -5.0
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    doc += _text(5, 4850, f"Job: {meta.get('job','N/A')}  Gauge: {meta.get('steel_gauge','')}  H: {meta.get('height','')} in  ({stamp})", 2)
    doc += _dxf_footer()
    return doc
