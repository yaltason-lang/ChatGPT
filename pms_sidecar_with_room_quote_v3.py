from __future__ import annotations

import os

from pms_sidecar_with_room_quote_v2 import app
from pms_booking_adapter_v1 import install_booking_adapter

install_booking_adapter(app)

if __name__ == "__main__":
    host = (os.environ.get("PMS_AVAILABILITY_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    try:
        port = int((os.environ.get("PMS_AVAILABILITY_PORT") or "8082").strip())
    except Exception:
        port = 8082
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
