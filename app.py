import os
from flask import Flask, jsonify, Blueprint, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.url_map.strict_slashes = False

api = Blueprint("api", __name__)

@api.get("/health")
def health():
    return jsonify({"message": "Curbonomix API is running", "ok": True})

@api.post("/echo")
def echo():
    return jsonify({"you_sent": request.get_json(silent=True)}), 200

app.register_blueprint(api, url_prefix="/api")

@app.get("/")
def root():
    return jsonify({"ok": True, "service": "Curbonomix", "try": "/api/health"}), 200

@app.get("/favicon.ico")
def favicon():
    return ("", 204)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    print("Routes:")
    for r in app.url_map.iter_rules():
        print(f"  {r.rule}")
    # Bind to 0.0.0.0 so ALL local clients can reach it
    app.run(host="0.0.0.0", port=port)
