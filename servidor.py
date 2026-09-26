"""
Servidor local do dashboard de citações.

Uso:
    python servidor.py              # http://localhost:8000
    python servidor.py --porta 8080

Serve a pasta output/ e responde na hora com o dashboard.html existente. Quando
os dados da coleta, as planilhas do Qualis ou o próprio gerador são mais novos
que o dashboard, gera uma versão nova em segundo plano; basta recarregar a
página depois de alguns segundos.
"""

import argparse
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import gerar_dashboard

_gerando = threading.Lock()


def fontes() -> list[Path]:
    g = gerar_dashboard
    return [g.ARQ_IDS, g.ARQ_CIT, g.ARQ_ERROS, g.ARQ_VENUES_S2, g.ARQ_QUALIS, g.ARQ_QUALIS_PER,
            g.ARQ_APELIDOS, Path(g.__file__)]


def dashboard_desatualizado() -> bool:
    if not gerar_dashboard.ARQ_IDS.exists():
        return False
    if not gerar_dashboard.ARQ_HTML.exists():
        return True
    mtime_html = gerar_dashboard.ARQ_HTML.stat().st_mtime
    return any(f.exists() and f.stat().st_mtime > mtime_html for f in fontes())


def regenerar_em_segundo_plano() -> None:
    """Gera o dashboard numa thread, se estiver desatualizado e nenhuma geração estiver em curso."""
    if not dashboard_desatualizado() or not _gerando.acquire(blocking=False):
        return

    def tarefa():
        try:
            print("Gerando dashboard em segundo plano...")
            gerar_dashboard.main()
        except Exception as e:  # o servidor continua servindo a versão anterior
            print(f"Erro ao gerar o dashboard: {e}")
        finally:
            _gerando.release()

    threading.Thread(target=tarefa, daemon=True).start()


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html", "/dashboard.html"):
            regenerar_em_segundo_plano()
            if not gerar_dashboard.ARQ_HTML.exists():
                self._aguarde()
                return
            self.path = "/dashboard.html"
        super().do_GET()

    def _aguarde(self):
        gerando = _gerando.locked()
        msg = ("Gerando o dashboard… a página recarrega sozinha em alguns segundos."
               if gerando else "Nenhum resultado ainda: rode a coleta primeiro.")
        corpo = (f'<!doctype html><meta charset="utf-8">{"<meta http-equiv=refresh content=5>" if gerando else ""}'
                 f'<title>Citações SBES</title><p style="font-family:sans-serif;padding:24px">{msg}</p>').encode("utf-8")
        self.send_response(503 if gerando else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--porta", type=int, default=8000)
    args = ap.parse_args()

    gerar_dashboard.SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    regenerar_em_segundo_plano()  # não bloqueia: o servidor já atende com a versão existente
    handler = partial(Handler, directory=str(gerar_dashboard.SAIDA_DIR))
    servidor = ThreadingHTTPServer((args.host, args.porta), handler)
    print(f"Dashboard em http://localhost:{args.porta}  (Ctrl+C para parar)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
