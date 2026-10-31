"""MeowCloud sample API. Keep health/node contract for workflows 01/03."""
import os
import socket
from flask import Flask, jsonify

app = Flask(__name__)
VERSION = os.environ.get("APP_VERSION", "dev")


@app.get("/")
@app.get("/health")
def health():
    return jsonify({
        "service": "MeowCloud Backend API (Docker)",
        "node": socket.gethostname(),
        "db_primary": "192.168.150.71",
        "status": "UP"
    })


@app.get("/version")
def version():
    return jsonify({
        "service": "MeowCloud Backend API (Docker)",
        "node": socket.gethostname(),
        "version": VERSION
    })
