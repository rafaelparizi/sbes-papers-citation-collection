"""
Servidor local do dashboard de citações.

Uso:
    python servidor.py              # http://localhost:8000
    python servidor.py --porta 8080

Serve a pasta output/ e regenera o dashboard.html sempre que os CSVs da coleta
forem mais novos que ele. Basta recarregar a página depois de uma coleta.
"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import gerar_dashboard


def dashboard_desatualizado() -> bool:
    if not gerar_dashboard.ARQ_IDS.exists():
        return False
    if not gerar_dashboard.ARQ_HTML.exists():
        return True
    mtime_html = gerar_dashboard.ARQ_HTML.stat().st_mtime
    fontes = [gerar_dashboard.ARQ_IDS, gerar_dashboard.ARQ_CIT]
    return any(f.exists() and f.stat().st_mtime > mtime_html for f in fontes)


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html", "/dashboard.html"):
            if dashboard_desatualizado():
                gerar_dashboard.main()
            if not gerar_dashboard.ARQ_HTML.exists():
                self.send_error(404, "Nenhum resultado ainda: rode a coleta primeiro.")
                return
            self.path = "/dashboard.html"
        super().do_GET()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--porta", type=int, default=8000)
    args = ap.parse_args()

    gerar_dashboard.SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    if gerar_dashboard.ARQ_IDS.exists():
        gerar_dashboard.main()  # garante o HTML com a versão atual do template
    handler = partial(Handler, directory=str(gerar_dashboard.SAIDA_DIR))
    servidor = ThreadingHTTPServer((args.host, args.porta), handler)
    print(f"Dashboard em http://localhost:{args.porta}  (Ctrl+C para parar)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
