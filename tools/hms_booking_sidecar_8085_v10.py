from __future__ import annotations

import hashlib
import os
from pathlib import Path

from flask import Flask, jsonify
import pms_booking_adapter_v5328 as booking_adapter

EXPECTED_WRITER_SHA256 = "23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac"
WRITER_PATH = Path(booking_adapter.__file__).resolve()
WRITER_SHA256 = hashlib.sha256(WRITER_PATH.read_bytes()).hexdigest()

if WRITER_SHA256 != EXPECTED_WRITER_SHA256:
    raise RuntimeError(
        "Writer SHA mismatch: expected " + EXPECTED_WRITER_SHA256 + ", actual " + WRITER_SHA256
    )

app = Flask("riverwood_hms_booking_writer_8085")
booking_adapter.install_booking_adapter(app)


@app.get("/riverwood-writer-health")
def riverwood_writer_health():
    return jsonify(
        {
            "ok": True,
            "service": "riverwood-hms-booking-writer",
            "listener": "127.0.0.1:8085",
            "adapter_version": getattr(booking_adapter, "ADAPTER_VERSION", ""),
            "writer_path": str(WRITER_PATH),
            "writer_sha256": WRITER_SHA256,
        }
    )


if __name__ == "__main__":
    host = (os.environ.get("HMS_BOOKING_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int((os.environ.get("HMS_BOOKING_PORT") or "8085").strip())
    except Exception:
        port = 8085
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
