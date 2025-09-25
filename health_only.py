from flask import Flask, jsonify
from flask_cors import CORS
app = Flask(__name__)
CORS(app)
app.url_map.strict_slashes = False
@app.get("/api/health")
def health():
    return jsonify({"message":"Curbonomix API is running","ok":True})
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
