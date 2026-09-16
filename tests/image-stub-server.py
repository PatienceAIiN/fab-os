#!/usr/bin/env python3
"""A stub OpenAI-style image server for tests: POST /v1/images/generations -> {"data":[{"b64_json": <PNG>}]}.
The PNG is a real solid-colour image (blue when the prompt says blue) drawn with zlib only. Used by tests/agent-ladder-vm.sh
(FABOS_IMAGE_PROVIDER=local-stub) inside the VM so the daemon's real local-endpoint path (images.provider=local,
images.local_endpoint) is exercised end to end without a cloud image key. Listens on 127.0.0.1:18089 only."""
import base64, json, struct, sys, zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

def png(w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try: w, h = [max(16, min(512, int(x))) for x in str(body.get("size") or "256x256").lower().split("x")]
        except Exception: w = h = 256
        rgb = (59, 110, 245) if "blue" in str(body.get("prompt") or "").lower() else (120, 90, 200)
        out = json.dumps({"created": 0, "data": [{"b64_json": base64.b64encode(png(w, h, rgb)).decode()} for _ in range(int(body.get("n") or 1))]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18089
    HTTPServer(("127.0.0.1", port), H).serve_forever()
