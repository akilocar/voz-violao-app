#!/usr/bin/env python3
"""Servidor HTTP do container de processamento.

POST /process?intensidade=0.6&estabilizar=1
  corpo: o vídeo original
  resposta: o vídeo melhorado (video/mp4), com Content-Length
GET /health -> "ok"
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

AQUI = os.path.dirname(os.path.abspath(__file__))
# Um vídeo por vez por container: o FFmpeg já usa todos os núcleos.
TRAVA = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))

    def _texto(self, code, msg):
        corpo = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            return self._texto(200, "ok")
        self._texto(404, "não encontrado")

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/process":
            return self._texto(404, "não encontrado")
        q = parse_qs(url.query)
        intensidade = q.get("intensidade", ["0.6"])[0]
        estabilizar = q.get("estabilizar", ["1"])[0] == "1"
        tamanho = int(self.headers.get("Content-Length", "0"))
        if tamanho <= 0:
            return self._texto(411, "Content-Length obrigatório")

        with TRAVA, tempfile.TemporaryDirectory() as tmp:
            entrada = os.path.join(tmp, "entrada")
            saida = os.path.join(tmp, "saida.mp4")
            with open(entrada, "wb") as f:
                restante = tamanho
                while restante > 0:
                    bloco = self.rfile.read(min(1 << 20, restante))
                    if not bloco:
                        break
                    f.write(bloco)
                    restante -= len(bloco)
            if restante:
                return self._texto(400, "upload incompleto")

            cmd = [sys.executable, os.path.join(AQUI, "melhorar.py"), entrada, saida,
                   "--intensidade", intensidade]
            if not estabilizar:
                cmd.append("--sem-estabilizar")
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                return self._texto(500, "falha no processamento:\n" + r.stderr[-2000:])

            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(os.path.getsize(saida)))
            self.end_headers()
            with open(saida, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", porta), Handler).serve_forever()
