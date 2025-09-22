import os, math, time, csv, json, smtplib
from email.message import EmailMessage
from typing import List, Dict, Any
from flask import Flask, request, jsonify
from flask_cors import CORS

import ezdxf
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ---- CORS ----
CORS_ALLOW_ORIGIN = os.environ.get("CORS_ALLOW_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": CORS_ALLOW_ORIGIN}})

# ---- Paths ----
ROOT = os.path.dirname(os.path.abspath(__file__))
STORAGE = os.path.join(ROOT, "storage")
RTU_CSV = os.path.join(STORAGE, "rtu_master.csv")
JOBS = os.path.join(STORAGE, "jobs")
os.makedirs(STORAGE, exist_ok=True)
os.makedirs(JOBS, exist_ok=True)

# ---- Helpers / core math ----
def clamp(v, lo, hi): return max(lo, min(hi, v))
def center_of(o: Dict[str, float]): return (o["x"] + o["W"]/2.0, o["y"] + o["L"]/2.0)
def rects_overlap(a: Dict[str,float], b: Dict[str,float]) -> bool:
    return not (a["x"] + a["W"] <= b["x"] or b["x"] + b["W"] <= a["x"] or a["y"] + a["L"] <= b["y"] or b["y"] + b["L"] <= a["y"])

def plan_path(a: Dict[str,float], b: Dict[str,float], corridor_width: float, height: float, rise: float):
    ax, ay = center_of(a); bx, by = center_of(b)
    mid = (bx, ay)  # L-shaped: horizontal then vertical
    return [
        {"from": [ax, ay], "to": list(mid), "rise": rise/2.0, "width": corridor_width, "height": height},
        {"from": list(mid), "to": [bx, by], "rise": rise/2.0, "width": corridor_width, "height": height},
    ]

def estimate_sp(cfm: float, area_in2: float, eq_len_ft: float, k_total: float, friction_rate: float = 0.08) -> float:
    area_ft2 = max(area_in2, 1.0) / 144.0
    v_fpm = cfm / area_ft2
    vp = (v_fpm / 4005.0) ** 2
    friction = (eq_len_ft/100.0) * friction_rate
    minor = k_total * vp
    return friction + minor

def cog_offset(baseW, baseL, seatX, seatY, seatW, seatL) -> float:
    base_cx, base_cy = baseW/2.0, baseL/2.0
    top_cx, top_cy = seatX + seatW/2.0, seatY + seatL/2.0
    dx, dy = (top_cx - base_cx)/2.0, (top_cy - base_cy)/2.0
    return math.hypot(dx, dy)

DEFAULT_PARAMS = {
    "envelopeMin": 16.0,
    "envelopeMax": 32.0,
    "spLimit": 2.0,
    "cfmSupply": 2000.0,
    "cfmReturn": 2000.0,
    "corridorWidth": 10.0,
    "defaultRise": 8.0,
    "cogMaxOffset": 6.0,
    "baffleThickness": 1.0,
}

def design_adapter(old_rtu: Dict[str,Any], new_rtu: Dict[str,Any], p: Dict[str,Any]) -> Dict[str,Any]:
    # 4-sided exterior rectangle dimensions
    curbL = max(old_rtu["curb"]["L"], new_rtu["curb"]["L"]) + 6.0
    curbW = max(old_rtu["curb"]["W"], new_rtu["curb"]["W"]) + 6.0
    curbH = clamp(new_rtu["curb"].get("H", 20.0), p["envelopeMin"], p["envelopeMax"])

    seat = {
        "L": new_rtu["curb"]["L"],
        "W": new_rtu["curb"]["W"],
        "x": (curbW - new_rtu["curb"]["W"]) / 2.0,
        "y": (curbL - new_rtu["curb"]["L"]) / 2.0,
    }

    oldSupply = dict(old_rtu["supply"])
    oldReturn = dict(old_rtu["return"])
    newSupply = dict(new_rtu["supply"]); newSupply["x"] = seat["x"] + new_rtu["supply"]["x"]; newSupply["y"] = seat["y"] + new_rtu["supply"]["y"]
    newReturn = dict(new_rtu["return"]); newReturn["x"] = seat["x"] + new_rtu["return"]["x"]; newReturn["y"] = seat["y"] + new_rtu["return"]["y"]

    # Paths
    hSup = clamp(curbH*0.45, p["envelopeMin"]*0.4, p["envelopeMax"]*0.7)
    hRet = clamp(curbH*0.45, p["envelopeMin"]*0.4, p["envelopeMax"]*0.7)
    supplyPath = plan_path(oldSupply, newSupply, p["corridorWidth"], hSup, p["defaultRise"])
    returnPath = plan_path(oldReturn, newReturn, p["corridorWidth"], hRet, p["defaultRise"])

    # Separation check (buffered corridors)
    supBuf = {"x": min(supplyPath[0]["from"][0], supplyPath[1]["to"][0]) - p["corridorWidth"]/2.0,
              "y": min(supplyPath[0]["from"][1], supplyPath[1]["to"][1]) - p["corridorWidth"]/2.0,
              "W": abs(supplyPath[1]["to"][0] - supplyPath[0]["from"][0]) + p["corridorWidth"],
              "L": abs(supplyPath[1]["to"][1] - supplyPath[0]["from"][1]) + p["corridorWidth"]}
    retBuf = {"x": min(returnPath[0]["from"][0], returnPath[1]["to"][0]) - p["corridorWidth"]/2.0,
              "y": min(returnPath[0]["from"][1], returnPath[1]["to"][1]) - p["corridorWidth"]/2.0,
              "W": abs(returnPath[1]["to"][0] - returnPath[0]["from"][0]) + p["corridorWidth"],
              "L": abs(returnPath[1]["to"][1] - returnPath[0]["from"][1]) + p["corridorWidth"]}
    separationOK = not rects_overlap(supBuf, retBuf)

    # If cross occurs, add baffles internally (exterior stays 4-sided)
    baffles = []
    if not separationOK:
        xMid = max(min(supBuf["x"] + supBuf["W"], retBuf["x"] + retBuf["W"]) - p["corridorWidth"]/2.0, 0)
        yMid = max(min(supBuf["y"] + supBuf["L"], retBuf["y"] + retBuf["L"]) - p["corridorWidth"]/2.0, 0)
        centerX = min(max(xMid, 0), curbW)
        centerY = min(max(yMid, 0), curbL)
        baffles = [
            {"kind":"vertical","from":[centerX,0],"to":[centerX,curbL],"thickness":p["baffleThickness"]},
            {"kind":"horizontal","from":[0,centerY],"to":[curbW,centerY],"thickness":p["baffleThickness"]},
        ]

    # Envelope/profile checks
    envelopeOK = (curbH >= p["envelopeMin"] and curbH <= p["envelopeMax"])
    profileSidesOK = True  # exterior is fixed rectangle by construction

    # Static pressure estimate (HVAC VP method)
    supAreaIn2 = max(newSupply["W"]*newSupply["L"]/4.0, 1.0)
    retAreaIn2 = max(newReturn["W"]*newReturn["L"]/4.0, 1.0)
    supLenFt = (abs(supplyPath[0]["to"][0]-supplyPath[0]["from"][0]) + abs(supplyPath[1]["to"][1]-supplyPath[1]["from"][1]))/12.0
    retLenFt = (abs(returnPath[0]["to"][0]-returnPath[0]["from"][0]) + abs(returnPath[1]["to"][1]-returnPath[1]["from"][1]))/12.0
    kTotal = 0.25*2 + 0.15*2
    spSup = estimate_sp(p["cfmSupply"], supAreaIn2, supLenFt, kTotal)
    spRet = estimate_sp(p["cfmReturn"], retAreaIn2, retLenFt, kTotal)
    staticPressure = spSup + spRet
    staticPressureOK = staticPressure <= p["spLimit"]

    # COG
    cogOffsetIn = cog_offset(curbW, curbL, seat["x"], seat["y"], seat["W"], seat["L"])
    cogOK = cogOffsetIn <= p["cogMaxOffset"]

    report = {
        "separationOK": separationOK or len(baffles)>0,
        "distributionOK": True,
        "envelopeOK": envelopeOK,
        "profileSidesOK": profileSidesOK,
        "staticPressure": round(staticPressure, 3),
        "staticPressureOK": staticPressureOK,
        "cogOffsetIn": round(cogOffsetIn, 2),
        "cogOK": cogOK,
        "notes": [
            "Exterior profile fixed to 4-sided rectangle.",
            ("S/R corridors separate." if separationOK else "Cross detected — internal baffles added."),
            (f"Height within {p['envelopeMin']}–{p['envelopeMax']}″." if envelopeOK else "Adapter height violates envelope."),
            (f"Total SP {staticPressure:.2f} in.wg ≤ {p['spLimit']}." if staticPressureOK else f"Total SP {staticPressure:.2f} in.wg > {p['spLimit']} — enlarge area or shorten path."),
            (f"COG offset {cogOffsetIn:.1f}″ within {p['cogMaxOffset']}″." if cogOK else f"COG offset {cogOffsetIn:.1f}″ exceeds {p['cogMaxOffset']}″ — recenter seat or rebalance."),
        ]
    }

    return {
        "curb": {"L": curbL, "W": curbW, "H": curbH},
        "seat": seat,
        "supplyPath": supplyPath,
        "returnPath": returnPath,
        "baffles": baffles,
        "report": report
    }

# ---- CSV model endpoints ----
def load_rtu_csv():
    items = []
    if os.path.exists(RTU_CSV):
        with open(RTU_CSV, newline='') as f:
            for row in csv.DictReader(f):
                items.append(row)
    return items

@app.get("/api/rtu/models")
def rtu_models():
    items = load_rtu_csv()
    man = request.args.get("manufacturer")
    if man:
        items = [r for r in items if r["manufacturer"].lower() == man.lower()]
    models = sorted({(r["manufacturer"], r["model"]) for r in items})
    out = [{"manufacturer": m, "model": mo} for (m, mo) in models]
    return jsonify(out)

@app.get("/api/rtu/rtu")
def rtu_get():
    man = request.args.get("manufacturer")
    model = request.args.get("model")
    items = load_rtu_csv()
    for r in items:
        if (not man or r["manufacturer"].lower()==man.lower()) and (not model or r["model"].lower()==model.lower()):
            rtu = {
                "manufacturer": r["manufacturer"],
                "series": r.get("series",""),
                "model": r["model"],
                "curb": {"L": float(r["curb_L"]), "W": float(r["curb_W"]), "H": float(r["curb_H"])},
                "supply": {"L": float(r["supply_L"]), "W": float(r["supply_W"]), "x": float(r["supply_x"]), "y": float(r["supply_y"])},
                "return": {"L": float(r["return_L"]), "W": float(r["return_W"]), "x": float(r["return_x"]), "y": float(r["return_y"])},
            }
            return jsonify(rtu)
    return jsonify({"error":"not found"}), 404

# ---- Health ----
@app.get("/api/health")
def health():
    return jsonify({"message":"Curbonomix API is running", "ts": int(time.time())})

# ---- Preview / Confirm ----
@app.post("/api/adapter/preview")
def adapter_preview():
    data = request.get_json(force=True)
    old_rtu = data.get("old_rtu")
    new_rtu = data.get("new_rtu")
    params = data.get("params", DEFAULT_PARAMS)
    design = design_adapter(old_rtu, new_rtu, params)
    return jsonify({"ok": True, "design": design})

@app.post("/api/adapter/confirm")
def adapter_confirm():
    data = request.get_json(force=True)
    adapter = data.get("adapter", {})
    job_key = f"JOB-{int(time.time())}"
    job_dir = os.path.join(JOBS, job_key)
    os.makedirs(job_dir, exist_ok=True)

    # Save payload
    with open(os.path.join(job_dir, "payload.json"), "w") as f:
        json.dump(data, f, indent=2)

    dxf_path = generate_dxf(job_dir, adapter)
    pdf_path = generate_pdf(job_dir, adapter)

    # Optional email
    emailed = False
    to_address = data.get("email_to")
    if to_address:
        try:
            email_with_attachment(to_address, pdf_path, subject=f"Curbonomix Submittal {job_key}")
            emailed = True
        except Exception as e:
            print("Email failed:", e)

    return jsonify({"ok": True, "job_key": job_key, "dxf_path": dxf_path, "pdf_path": pdf_path, "emailed": emailed})

# ---- DXF & PDF ----
MAX_PANEL = 84.0  # inches (7 ft)

def split_length(length: float, max_len: float) -> List[float]:
    if length <= max_len:
        return [length]
    parts, remaining = [], length
    while remaining > max_len:
        parts.append(max_len); remaining -= max_len
    if remaining > 0.5: parts.append(remaining)
    else: parts[-1] += remaining
    return parts

def place_rect(msp, x, y, w, h, layer, label=None):
    msp.add_lwpolyline([(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)], dxfattribs={"layer":layer, "closed":True})
    if label:
        msp.add_text(label, dxfattribs={"height": 1.0, "layer": "LABELS"}).set_pos((x+0.5, y+h+1.0))

def generate_dxf(job_dir: str, adapter: Dict[str,Any]) -> str:
    curb = adapter.get("curb") or {}
    baffles = adapter.get("baffles") or []

    curbL = float(curb.get("L", 60.0))
    curbW = float(curb.get("W", 40.0))
    curbH = float(curb.get("H", 20.0))

    doc = ezdxf.new(setup=True)
    msp = doc.modelspace()
    for layer in ["WALLS","BAFFLES","LABELS"]:
        if layer not in doc.layers:
            doc.layers.add(name=layer)

    panels = [
        ("WALL_NORTH", curbW, curbH),
        ("WALL_SOUTH", curbW, curbH),
        ("WALL_EAST",  curbL, curbH),
        ("WALL_WEST",  curbL, curbH),
    ]

    x_cursor, y_cursor, row_height, spacing = 0.0, 0.0, 0.0, 2.0
    for name, length, height in panels:
        for idx, seg in enumerate(split_length(length, MAX_PANEL), start=1):
            label = f"{name}-{idx}  {seg:.1f} x {height:.1f} in"
            place_rect(msp, x_cursor, y_cursor, seg, height, "WALLS", label)
            x_cursor += seg + spacing; row_height = max(row_height, height)
            if x_cursor > 240:
                x_cursor = 0.0; y_cursor += row_height + spacing; row_height = 0.0

    # baffles as flat plates
    for i, b in enumerate(baffles, start=1):
        fx, fy = b["from"]; tx, ty = b["to"]
        length = math.hypot(tx - fx, ty - fy)
        for j, seg in enumerate(split_length(length, MAX_PANEL), start=1):
            label = f"BAFFLE-{i}-{j}  {seg:.1f} x {curbH:.1f} in"
            place_rect(msp, x_cursor, y_cursor, seg, curbH, "BAFFLES", label)
            x_cursor += seg + spacing; row_height = max(row_height, curbH)
            if x_cursor > 240:
                x_cursor = 0.0; y_cursor += row_height + spacing; row_height = 0.0

    out_path = os.path.join(job_dir, "adapter_panels.dxf")
    doc.saveas(out_path)
    return out_path

def generate_pdf(job_dir: str, adapter: Dict[str,Any]) -> str:
    report = adapter.get("report", {})
    curb = adapter.get("curb", {})
    seat = adapter.get("seat", {})
    baffles = adapter.get("baffles", [])

    out_path = os.path.join(job_dir, "submittal.pdf")
    c = canvas.Canvas(out_path, pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, h-40, "Curbonomix — Adapter Submittal")
    c.setFont("Helvetica", 10)
    y = h-70
    def line(txt):
        nonlocal y; c.drawString(40, y, txt); y -= 14

    line(f"Curb (RECT): {curb.get('L','?')} x {curb.get('W','?')} x {curb.get('H','?')} in")
    line(f"Seat:        {seat.get('L','?')} x {seat.get('W','?')} @ ({int(seat.get('x',0))}, {int(seat.get('y',0))})")
    line(f"SP: {report.get('staticPressure','?')} in.wg  (limit <= {DEFAULT_PARAMS['spLimit']})")
    line(f"COG Offset: {report.get('cogOffsetIn','?')} in  (<= {DEFAULT_PARAMS['cogMaxOffset']})")
    line(f"Envelope 16–32 in: {'PASS' if report.get('envelopeOK') else 'FAIL'}")
    line(f"S/R Separation: {'PASS' if report.get('separationOK') else 'FAIL'}")
    line(f"Exterior 4-sides: {'PASS' if report.get('profileSidesOK') else 'FAIL'}")

    y -= 8; c.setFont("Helvetica-Bold", 11); line("Notes:"); c.setFont("Helvetica", 10)
    for n in report.get("notes", []): line(f"  • {n}")

    y -= 8; c.setFont("Helvetica-Bold", 11); line("Baffles:"); c.setFont("Helvetica", 10)
    if not baffles: line("  (None)")
    else:
        for i,b in enumerate(baffles, start=1):
            fx, fy = b['from']; tx, ty = b['to']
            length = round(math.hypot(tx-fx, ty-fy),1)
            line(f"  - {b['kind']} length {length} in x height {curb.get('H')} in, thickness {b['thickness']} in")

    c.showPage(); c.save()
    return out_path

# ---- Email (optional) ----
def email_with_attachment(to_addr: str, pdf_path: str, subject="Curbonomix Submittal"):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    from_addr = os.environ.get("SMTP_FROM", smtp_user)
    if not (smtp_host and smtp_user and smtp_pass and from_addr):
        raise RuntimeError("SMTP env vars not set (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM).")
    msg = EmailMessage()
    msg["Subject"] = subject; msg["From"] = from_addr; msg["To"] = to_addr
    msg.set_content("Attached: Curbonomix Submittal PDF.")
    with open(pdf_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=os.path.basename(pdf_path))
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls(); server.login(smtp_user, smtp_pass); server.send_message(msg)

# ---- AI (heuristics; swap for LLM if desired) ----
@app.post("/api/ai/suggest")
def ai_suggest():
    payload = request.get_json(force=True)
    rpt = (payload or {}).get("adapter", {}).get("report", {})
    suggestions, sp = [], rpt.get("staticPressure", 0.0)
    if not rpt.get("staticPressureOK", True):
        suggestions.append(f"Increase corridor width by 10–20% and smooth transitions (lower K). Current SP = {sp:.2f} in.wg.")
    if not rpt.get("separationOK", True):
        suggestions.append("Add internal baffles or offset seat by 1–2 inches to maintain S/R separation.")
    if not rpt.get("cogOK", True):
        suggestions.append("Recenter seat (adjust x,y by ~1–2 inches) to reduce COG offset.")
    if not rpt.get("envelopeOK", True):
        suggestions.append("Clamp height to 16–32 in and reduce rise along segments.")
    if not suggestions:
        suggestions.append("Design within limits; consider rounding internal corners to gain SP margin.")
    return jsonify({"ai":"heuristic-stub","suggestions":suggestions})

@app.post("/api/ai/monitor")
def ai_monitor():
    hb = request.get_json(force=True)
    rpt = (hb or {}).get("report", {})
    ok = (rpt.get("separationOK") and rpt.get("staticPressureOK") and rpt.get("cogOK") and rpt.get("envelopeOK"))
    return jsonify({"ok": ok, "echo": hb})

@app.post("/api/ai/autotune")
def ai_autotune():
    data = request.get_json(force=True)
    old_rtu = data.get("old_rtu"); new_rtu = data.get("new_rtu")
    params = data.get("params", DEFAULT_PARAMS.copy())
    target_sp = max(0.0, params["spLimit"] - 0.20)  # aim for margin
    best = None

    for cw in [params["corridorWidth"] + d for d in [0, 2, 4, 6]]:
        cw = min(16.0, cw)
        tmp = dict(params); tmp["corridorWidth"] = cw
        for offx in [0, -2, 2]:
            for offy in [0, -2, 2]:
                design = design_adapter(old_rtu, new_rtu, tmp)
                # apply seat offset for scoring (visual adjustment is up to client)
                design["seat"]["x"] += offx; design["seat"]["y"] += offy
                cog = cog_offset(design["curb"]["W"], design["curb"]["L"], design["seat"]["x"], design["seat"]["y"], design["seat"]["W"], design["seat"]["L"])
                design["report"]["cogOffsetIn"] = round(cog,2); design["report"]["cogOK"] = cog <= tmp["cogMaxOffset"]
                ok = (design["report"]["separationOK"] and design["report"]["envelopeOK"] and design["report"]["cogOK"] and design["report"]["staticPressure"] <= target_sp)
                score = ((target_sp - design["report"]["staticPressure"]) * 10.0) - abs(design["seat"]["x"])*0.1 - abs(design["seat"]["y"])*0.1 - (0 if design["report"]["separationOK"] else 1000)
                item = {"params": tmp.copy(), "seat_offset": (offx, offy), "design": design, "score": score, "ok": ok}
                if (best is None) or (item["score"] > best["score"]):
                    best = item
    return jsonify({"ok": True, "target_sp": target_sp, "result": best})

if __name__ == "__main__":
    # For local dev only. On Render we use gunicorn (see render.yaml / Procfile).
    app.run(host="0.0.0.0", port=8000, debug=True)
