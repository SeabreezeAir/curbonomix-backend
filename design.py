from typing import Dict, Any, Optional, List
import math
import ezdxf

from models_db import lookup_rtu_model

MIN_H = 12.0
MAX_H = 24.0

def clamp(v, a, b): return max(a, min(b, v))

def pick_height(slope_factor: float, wind_speed_mph: float) -> float:
    sf = clamp(slope_factor or 0.5, 0.0, 1.0)
    wind = max(0.0, wind_speed_mph or 0.0)
    wind_term = clamp((wind - 90.0) / 60.0, 0.0, 1.0)
    h = MIN_H + 12.0 * (0.6*sf + 0.4*wind_term)
    return round(clamp(h, MIN_H, MAX_H), 2)

def rectangle_vertices(L: float, W: float, H: float):
    return [[0,0,0],[L,0,0],[L,W,0],[0,W,0],[0,0,H],[L,0,H],[L,W,H],[0,W,H]]

def compute_cog(L: float, W: float, H: float, supply=None, ret=None):
    cx, cy, cz = L/2.0, W/2.0, H/2.0
    def area(p): return (p.get("w",0.0) * p.get("l",0.0)) if p else 0.0
    panel_area = L*W
    shift_x = shift_y = 0.0
    for p in [supply, ret]:
        a = area(p)
        if a > 0 and panel_area > 0:
            weight_loss = 0.01 * (a / panel_area)
            px = (p["x"] + p["w"]/2.0) - cx
            py = (p["y"] + p["l"]/2.0) - cy
            shift_x -= weight_loss * px
            shift_y -= weight_loss * py
    return [round(cx+shift_x,3), round(cy+shift_y,3), round(cz,3)]

def wind_pressure_psf(v_mph: float) -> float:
    return round(0.00256 * (max(0.0, v_mph)**2), 3)

def validate_inputs(L: float, W: float, H: float, supply=None, ret=None) -> List[str]:
    errs = []
    if L <= 0 or W <= 0: errs.append("Length and width must be positive.")
    if H < MIN_H or H > MAX_H: errs.append(f"Height must be between {MIN_H} and {MAX_H} inches.")
    def check(p, name):
        if not p: return
        for k in ("x","y","w","l"):
            if p.get(k, None) is None: errs.append(f"{name} missing {k}")
        if p.get("w",0)<=0 or p.get("l",0)<=0: errs.append(f"{name} width/length must be positive.")
        if p.get("x",0)<0 or p.get("y",0)<0: errs.append(f"{name} x/y must be >= 0.")
        if p.get("x",0)+p.get("w",0) > L or p.get("y",0)+p.get("l",0) > W:
            errs.append(f"{name} cutout exceeds curb top frame bounds.")
    check(supply, "Supply")
    check(ret, "Return")
    return errs

def design_curb(req: Dict[str, Any]) -> Dict[str, Any]:
    mode = (req.get("mode") or "manual").lower()
    width = req.get("width"); length = req.get("length"); height = req.get("height")
    wind_speed_mph = req.get("wind_speed_mph") or 90.0
    slope_factor = req.get("slope_factor") or 0.5
    supply = req.get("supply"); ret = req.get("return") or req.get("return_plenum")

    if mode == "rtu":
        model = req.get("rtu_model") or ""
        dims = lookup_rtu_model(model) or {}
        length = dims.get("rtu_length") or length
        width  = dims.get("rtu_width") or width

    if not (width and length): raise ValueError("width and length are required (RTU or manual)")
    if not height: height = pick_height(float(slope_factor), float(wind_speed_mph))

    L=float(length); W=float(width); H=float(height)

    angle_deg = round(5 + 20*(0.6*slope_factor + 0.4*max(0.0, (wind_speed_mph-90)/60)), 2)
    angle_deg = min(35.0, max(5.0, angle_deg))
    verts = rectangle_vertices(L,W,H)
    cog = compute_cog(L,W,H, supply=supply, ret=ret)
    errs = validate_inputs(L,W,H,supply=supply, ret=ret)
    q = wind_pressure_psf(wind_speed_mph)

    out = {
        "inputs": {"mode": mode, "length": L, "width": W, "height": H,
                   "wind_speed_mph": wind_speed_mph, "slope_factor": slope_factor,
                   "supply": supply, "return": ret},
        "rules": {
            "rectangle_4_sides": True,
            "height_range_in": [12,24],
            "aero_angle_deg": angle_deg,
            "cog": {"x": cog[0], "y": cog[1], "z": cog[2]},
            "wind_pressure_psf": q,
            "anchorage_note": f"Guidance: design for ~{q} psf lateral; verify per local code/ASCE 7."
        },
        "geometry": {"vertices": verts, "faces": [[0,1,2,3],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]},
        "validation_errors": errs,
        "stability_index": round( (H / max(L,W)) * 0.5 + 0.5, 3),
        "notes": ["COG at geometric center with cutout adjustments.","Wind q = 0.00256*V^2 psf (guidance)."]
    }
    return out

def _rect(msp, x, y, w, h, layer="PANEL"):
    msp.add_lwpolyline([(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)], dxfattribs={"layer": layer, "closed": True})

def _label(msp, x, y, text, layer="TEXT", height=0.2):
    msp.add_text(text, dxfattribs={"layer": layer, "height": height}).set_pos((x, y))

def make_dxf_bytes(design: Dict[str, Any]) -> bytes:
    L = float(design["inputs"]["length"]); W = float(design["inputs"]["width"]); H = float(design["inputs"]["height"])
    supply = design["inputs"].get("supply") or {}; ret = design["inputs"].get("return") or {}

    doc = ezdxf.new(dxfversion="R2018")
    for layer in ["PANEL","BEND","CUTOUT","TEXT"]:
        if layer not in doc.layers: doc.layers.add(layer)
    msp = doc.modelspace()

    flange = 1.0; gap = 2.0; x0 = 0.0; y0 = 0.0
    panels = [("FRONT", L, H),("RIGHT", W, H),("BACK", L, H),("LEFT", W, H)]
    cursor_x = x0; cursor_y = y0; max_row_h = 0.0; row_width_limit = 120.0

    for name, pw, ph in panels:
        dw = pw + 2*flange; dh = ph + 2*flange
        if cursor_x + dw > row_width_limit: cursor_x = x0; cursor_y += max_row_h + gap; max_row_h = 0.0
        _rect(msp, cursor_x, cursor_y, dw, dh, layer="PANEL")
        msp.add_line((cursor_x+flange, cursor_y), (cursor_x+flange, cursor_y+dh), dxfattribs={"layer":"BEND"})
        msp.add_line((cursor_x+dw-flange, cursor_y), (cursor_x+dw-flange, cursor_y+dh), dxfattribs={"layer":"BEND"})
        _label(msp, cursor_x+0.2, cursor_y+dh+0.3, f"{name} PANEL {pw:.2f}x{ph:.2f}", height=0.25)
        cursor_x += dw + gap; max_row_h = max(max_row_h, dh)

    frame_x = x0; frame_y = cursor_y + max_row_h + gap*2
    _rect(msp, frame_x, frame_y, L, W, layer="PANEL")
    _label(msp, frame_x+0.2, frame_y+W+0.3, "TOP FRAME (OPENINGS)", height=0.25)

    if all(k in supply for k in ("x","y","w","l")):
        _rect(msp, frame_x + float(supply["x"]), frame_y + float(supply["y"]), float(supply["w"]), float(supply["l"]), layer="CUTOUT")
        _label(msp, frame_x + float(supply["x"]), frame_y + float(supply["y"])-0.3, "SUPPLY CUTOUT", height=0.2)
    if all(k in ret for k in ("x","y","w","l")):
        _rect(msp, frame_x + float(ret["x"]), frame_y + float(ret["y"]), float(ret["w"]), float(ret["l"]), layer="CUTOUT")
        _label(msp, frame_x + float(ret["x"]), frame_y + float(ret["y"])-0.3, "RETURN CUTOUT", height=0.2)

    _label(msp, frame_x, frame_y - 0.8, f"COG: x={design['rules']['cog']['x']:.2f}, y={design['rules']['cog']['y']:.2f}, z={design['rules']['cog']['z']:.2f}", height=0.2)
    _label(msp, frame_x, frame_y - 1.2, f"Aero angle: {design['rules']['aero_angle_deg']:.1f} deg", height=0.2)
    _label(msp, frame_x, frame_y - 1.6, f"Wind q ~{design['rules']['wind_pressure_psf']:.2f} psf", height=0.2)

    import io
    bio = io.BytesIO(); doc.write(bio); return bio.getvalue()