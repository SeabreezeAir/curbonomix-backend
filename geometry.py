from typing import Dict, Any

def compute_geometry(payload: Dict[str, Any]) -> Dict[str, Any]:
    L = float(payload["curb_length"])
    W = float(payload["curb_width"])
    H = float(payload["height"])
    rL = float(payload["rtu_length"])
    rW = float(payload["rtu_width"])
    rtu_origin_x = (L - rL)/2.0
    rtu_origin_y = (W - rW)/2.0
    panels = [
        {"name": "Front", "length": L, "height": H},
        {"name": "Back",  "length": L, "height": H},
        {"name": "Left",  "length": W, "height": H},
        {"name": "Right", "length": W, "height": H},
    ]
    brake_limit = float(payload.get("brake_limit", 84))
    long_panels_over = [p for p in panels if p["length"] > brake_limit]
    return {
        "curb_outline": {"x": 0, "y": 0, "length": L, "width": W},
        "rtu_seat": {"x": rtu_origin_x, "y": rtu_origin_y, "length": rL, "width": rW},
        "drops": {
            "supply": {"x": float(payload["supply_x"]), "y": float(payload["supply_y"])},
            "return": {"x": float(payload["return_x"]), "y": float(payload["return_y"])},
        },
        "panels": panels,
        "warnings": [
            f"Panel '{p['name']}' length {p['length']:.1f} in exceeds brake limit {brake_limit:.1f} in."
            for p in long_panels_over
        ]
    }
