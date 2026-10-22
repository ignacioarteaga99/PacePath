"""Servidor estático con Range y CORS, como Cloudflare R2: para mirar el mapa
propio (PMTiles) en local o en GitHub Actions antes de publicarlo.

    python scripts/servidor_rango.py <carpeta> [puerto]
"""
import http.server
import os
import re
import sys


class Rango(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=sys.argv[1], **k)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "range, if-match")
        self.send_header("Access-Control-Expose-Headers", "etag, content-length, content-range")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        rango, ruta = self.headers.get("Range"), self.translate_path(self.path)
        if not rango or not os.path.isfile(ruta):
            return super().do_GET()
        a, b = re.match(r"bytes=(\d+)-(\d*)", rango).groups()
        n = os.path.getsize(ruta)
        a, b = int(a), min(int(b) if b else n - 1, n - 1)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {a}-{b}/{n}")
        self.send_header("Content-Length", str(b - a + 1))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("ETag", f'"{int(os.path.getmtime(ruta))}"')
        self.end_headers()
        with open(ruta, "rb") as f:
            f.seek(a)
            self.wfile.write(f.read(b - a + 1))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2]) if len(sys.argv) > 2 else 8799), Rango).serve_forever()
