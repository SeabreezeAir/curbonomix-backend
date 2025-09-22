import json, os, re
_DB_PATH = os.path.join(os.path.dirname(__file__), "rtu_db.json")
def _load():
    try:
        with open(_DB_PATH, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}
_DB = _load()
def lookup_rtu_model(model: str):
    if not model: return None
    key = model.strip().upper()
    if key in _DB:
        L, W = _DB[key]; return {"rtu_length": float(L), "rtu_width": float(W)}
    if key.startswith("48FC"): return {"rtu_length": 42.0, "rtu_width": 30.0}
    norm = re.sub(r"[^A-Z0-9]", "", key)
    for k,(L,W) in _DB.items():
        if re.sub(r"[^A-Z0-9]", "", k) == norm:
            return {"rtu_length": float(L), "rtu_width": float(W)}
    return None
def search_models(q: str, limit=25):
    if not q: return []
    qn = q.strip().upper(); out = []
    for k,(L,W) in _DB.items():
        if qn in k.upper():
            out.append({"model": k, "rtu_length": float(L), "rtu_width": float(W)})
            if len(out) >= limit: break
    return out