from typing import Dict, Any
import math
import ezdxf

from design import clamp, pick_height, validate_inputs, compute_cog

def _center(p):
    return (p["x"] + p["w"]/2.0, p["y"] + p["l"]/2.0)

def _would_cross(bottom_supply, bottom_return, top_supply, top_return):
    def ccw(A,B,C): return (C[1]-A[1])*(B[0]-A[0]) > (B[1]-A[1])*(C[0]-A[0])
    def inter(A,B,C,D): return (ccw(A,C,D) != ccw(B,C,D)) and (ccw(A,B,C) != ccw(A,B,D))
    bs = _center(bottom_supply); br = _center(bottom_return); ts = _center(top_supply); tr = _center(top_return)
    return inter(bs, ts, br, tr)

def design_adapter(req: Dict[str, Any]) -> Dict[str, Any]:
    bottom = req.get("existing") or {}
    top    = req.get("new") or {}
    H      = float(req.get("height") or pick_height(req.get("slope_factor") or 0.5, req.get("wind_speed_mph") or 90.0))
    H = max(12.0, min(24.0, H))

    bL = float(bottom.get("length") or 0); bW = float(bottom.get("width") or 0)
    tL = float(top.get("length") or 0);    tW = float(top.get("width") or 0)

    bs = bottom.get("supply"); br = bottom.get("return")
    ts = top.get("supply");    tr = top.get("return")

    errs = []
    errs += validate_inputs(bL, bW, H, supply=bs, ret=br)
    errs += validate_inputs(tL, tW, H, supply=ts, ret=tr)
    if errs: return {"ok": False, "errors": errs}

    rotate_top = False
    if _would_cross(bs, br, ts, tr):
        ts = {**ts, "x": tL - ts["x"] - ts["w"], "y": tW - ts["y"] - ts["l"]}
        tr = {**tr, "x": tL - tr["x"] - tr["w"], "y": tW - tr["y"] - tr["l"]}
        rotate_top = True

    pad = 2.0
    L = max(bL, tL) + 2*pad
    W = max(bW, tW) + 2*pad

    def center_offsets(LL, WW, l, w): return ((LL - l)/2.0, (WW - w)/2.0)
    b_off = center_offsets(L, W, bL, bW)
    t_off = center_offsets(L, W, tL, tW)

    def move(p, off): return {**p, "x": p["x"] + off[0], "y": p["y"] + off[1]}
    bsA = move(bs, b_off); brA = move(br, b_off)
    tsA = move(ts, t_off); trA = move(tr, t_off)

    def midpoint(P,Q): return ((P[0]+Q[0])/2.0, (P[1]+Q[1])/2.0)
    ms = midpoint(_center(bsA), _center(tsA))
    mr = midpoint(_center(brA), _center(trA))
    divider = {"x1": ms[0], "y1": ms[1], "x2": mr[0], "y2": mr[1]}

    cog = [round(L/2.0,3), round(W/2.0,3), round(H/2.0,3)]

    out = {
        "ok": True,
        "adapter": {
            "length": round(L,3), "width": round(W,3), "height": round(H,3),
            "bottom_rect": {"x": b_off[0], "y": b_off[1], "l": bL, "w": bW},
            "top_rect": {"x": t_off[0], "y": t_off[1], "l": tL, "w": tW, "rotated_180": rotate_top},
            "bottom_supply": bsA, "bottom_return": brA,
            "top_supply": tsA, "top_return": trA,
            "divider": divider,
            "notes": [
                "Outer shell is 4-sided rectangle.",
                "Supply and return paths kept separate; top auto-rotated if needed to avoid crossing.",
                "If your specific geometry requires dog-legging, we recommend manual review."
            ],
            "cog": {"x": cog[0], "y": cog[1], "z": cog[2]}
        }
    }
    return out

def make_adapter_dxf(design: Dict[str, Any]) -> bytes:
    a = design["adapter"]
    L = a["length"]; W = a["width"]; H = a["height"]
    b = a["bottom_rect"]; t = a["top_rect"]
    bs = a["bottom_supply"]; br = a["bottom_return"]
    ts = a["top_supply"];    tr = a["top_return"]
    div = a["divider"]

    doc = ezdxf.new(dxfversion="R2018")
    for layer in ["PANEL","BEND","CUTOUT","TEXT","DIVIDER"]:
        if layer not in doc.layers: doc.layers.add(layer)
    msp = doc.modelspace()

    def rect(x,y,w,l,layer="PANEL"):
        msp.add_lwpolyline([(x,y),(x+w,y),(x+w,y+l),(x,y+l),(x,y)], dxfattribs={"layer": layer, "closed": True})
    def label(x,y,text,h=0.25):
        msp.add_text(text, dxfattribs={"layer":"TEXT","height":h}).set_pos((x,y))

    flange = 1.0; gap = 2.0; x0 = 0.0; y0 = 0.0
    panels = [("FRONT", L, H),("RIGHT", W, H),("BACK", L, H),("LEFT", W, H)]
    cx = x0; cy = y0; row_h = 0.0
    for name,pw,ph in panels:
        dw = pw + 2*flange; dh = ph + 2*flange
        rect(cx,cy,dw,dh,"PANEL")
        msp.add_line((cx+flange, cy), (cx+flange, cy+dh), dxfattribs={"layer":"BEND"})
        msp.add_line((cx+dw-flange, cy), (cx+dw-flange, cy+dh), dxfattribs={"layer":"BEND"})
        label(cx+0.2,cy+dh+0.3,f"{name} PANEL {pw:.2f}x{ph:.2f}")
        cx += dw + gap; row_h = max(row_h, dh)

    plate_y = cy + row_h + gap*2
    rect(x0, plate_y, L, W, "PANEL"); label(x0, plate_y-0.6, "BOTTOM PLATE (Existing curb)")
    rect(x0+L+gap, plate_y, L, W, "PANEL"); label(x0+L+gap, plate_y-0.6, "TOP PLATE (New RTU)")

    def draw_cutout(base_x, base_y, p, lbl):
        rect(base_x + p["x"], base_y + p["y"], p["w"], p["l"], "CUTOUT")
        label(base_x + p["x"], base_y + p["y"]-0.3, lbl, 0.2)

    draw_cutout(x0, plate_y, bs, "BOTTOM SUPPLY")
    draw_cutout(x0, plate_y, br, "BOTTOM RETURN")
    draw_cutout(x0+L+gap, plate_y, ts, "TOP SUPPLY")
    draw_cutout(x0+L+gap, plate_y, tr, "TOP RETURN")

    label(x0, plate_y + W + 1.0, "INTERNAL DIVIDER STRIP")
    div_len = math.dist((div["x1"],div["y1"]), (div["x2"],div["y2"]))
    rect(x0, plate_y + W + 1.2, div_len, H, "PANEL")

    label(x0, plate_y + W + 3.0, f"Adapter LxW/H: {L:.2f}x{W:.2f}/{H:.2f} in")
    label(x0, plate_y + W + 2.6, "Keep S and R paths isolated; verify onsite orientation.")

    import io
    bio = io.BytesIO(); doc.write(bio); return bio.getvalue()