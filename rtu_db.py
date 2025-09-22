import os, csv
from typing import Optional, Dict, Any
BASE_DIR = os.path.join(os.path.dirname(__file__), "storage")
CSV_PATH = os.path.join(BASE_DIR, "rtu_master.csv")

def get_rtu(model_code: str) -> Optional[Dict[str,Any]]:
    if not os.path.exists(CSV_PATH):
        return None
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for row in rd:
            if row.get("Model Code"," ").strip().lower() == model_code.strip().lower():
                return row
    return None

def list_models(limit: int = 1000):
    out = []
    if not os.path.exists(CSV_PATH):
        return out
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for i, row in enumerate(rd):
            out.append({
                "model": row.get("Model Code",""),
                "manufacturer": row.get("Manufacturer",""),
                "series": row.get("Series",""),
                "tons": row.get("Nominal Tons",""),
                "heat": row.get("Heat Type",""),
            })
            if i+1 >= limit:
                break
    return out
