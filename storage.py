import os, json, sqlite3, hashlib
from contextlib import closing
from typing import Optional, Dict, Any

BASE_DIR = os.path.join(os.path.dirname(__file__), "storage")
DB_PATH = os.path.join(BASE_DIR, "adapters", "index.sqlite")
ADAPTERS_DIR = os.path.join(BASE_DIR, "adapters")

def ensure_dirs():
    os.makedirs(ADAPTERS_DIR, exist_ok=True)

def db():
    ensure_dirs()
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS adapters (
        adapter_key TEXT PRIMARY KEY,
        title TEXT,
        created_at TEXT,
        geom_fingerprint TEXT,
        pdf_path TEXT,
        dxf_path TEXT,
        meta_path TEXT,
        preview_path TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS model_links (
        model_code TEXT,
        adapter_key TEXT,
        is_existing INTEGER DEFAULT 0,
        is_new INTEGER DEFAULT 0,
        UNIQUE(model_code, adapter_key)
    )""")
    return con

def _norm_tuple(t):
    def round16(x):
        return None if x is None else round(float(x)*16)/16.0
    out = []
    for v in t:
        if isinstance(v, (int, float)):
            out.append(round16(v))
        else:
            out.append(v)
    return tuple(out)

def adapter_key_from_geom(geom_spec: Dict[str, Any]):
    tup = _norm_tuple((
        geom_spec.get("existing_L"), geom_spec.get("existing_W"),
        geom_spec.get("new_L"), geom_spec.get("new_W"),
        geom_spec.get("height"), geom_spec.get("flange_height"),
        geom_spec.get("supply_x"), geom_spec.get("supply_y"),
        geom_spec.get("return_x"), geom_spec.get("return_y"),
    ))
    fp = "|".join(map(lambda x: "None" if x is None else str(x), tup))
    import hashlib
    return hashlib.sha1(fp.encode("utf-8")).hexdigest(), fp

def get_adapter(adapter_key: str) -> Optional[Dict[str, Any]]:
    with closing(db()) as con, closing(con.cursor()) as cur:
        row = cur.execute("SELECT adapter_key, title, geom_fingerprint, pdf_path, dxf_path, meta_path, preview_path FROM adapters WHERE adapter_key=?", (adapter_key,)).fetchone()
        if not row: return None
        cols = ["adapter_key","title","geom_fingerprint","pdf_path","dxf_path","meta_path","preview_path"]
        return dict(zip(cols, row))

def find_by_geom_fingerprint(fp: str) -> Optional[Dict[str, Any]]:
    with closing(db()) as con, closing(con.cursor()) as cur:
        row = cur.execute("SELECT adapter_key, title, geom_fingerprint, pdf_path, dxf_path, meta_path, preview_path FROM adapters WHERE geom_fingerprint=?", (fp,)).fetchone()
        if not row: return None
        cols = ["adapter_key","title","geom_fingerprint","pdf_path","dxf_path","meta_path","preview_path"]
        return dict(zip(cols, row))

def insert_adapter(adapter_key: str, title: str, fp: str, paths: Dict[str,str], created_at: str):
    with closing(db()) as con, closing(con.cursor()) as cur:
        cur.execute("INSERT OR REPLACE INTO adapters(adapter_key,title,geom_fingerprint,pdf_path,dxf_path,meta_path,preview_path,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (adapter_key, title, fp, paths.get("pdf"), paths.get("dxf"), paths.get("meta"), paths.get("preview"), created_at))
        con.commit()

def link_model(model_code: str, adapter_key: str, is_existing=False, is_new=False):
    with closing(db()) as con, closing(con.cursor()) as cur:
        cur.execute("INSERT OR IGNORE INTO model_links(model_code, adapter_key, is_existing, is_new) VALUES (?,?,?,?)",
                    (model_code, adapter_key, int(is_existing), int(is_new)))
        con.commit()

def save_adapter_files(adapter_key: str, files: Dict[str, str]):
    dirp = os.path.join(ADAPTERS_DIR, adapter_key)
    os.makedirs(dirp, exist_ok=True)
    paths = {}
    for k, content in files.items():
        ext = {"meta":"json","plan":"json","preview":"svg","pdf":"pdf","dxf":"dxf"}[k]
        p = os.path.join(dirp, f"{k}.{ext}")
        mode = "wb" if k=="pdf" else "w"
        with open(p, mode) as f:
            if k=="pdf":
                f.write(content)
            else:
                f.write(content)
        paths[k] = p
    return paths
