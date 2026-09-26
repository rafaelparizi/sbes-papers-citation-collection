"""
Coleta de citações dos artigos do SBES no Semantic Scholar.

Etapa 1: resolve cada artigo da fase 1 (articles_first_webscraping_collection.xlsx)
         para um paperId do Semantic Scholar (por DOI em lote; fallback por título).
Etapa 2: para cada paperId, percorre o endpoint /paper/{id}/citations e salva
         título, ano, autores e paperId de cada artigo citante.

Uso:
    python semantic_scholar_citations.py                 # todos os artigos
    python semantic_scholar_citations.py --limit 10      # teste com 10 artigos
    python semantic_scholar_citations.py --inicio 10 --limit 10  # artigos 10 a 19

A chave da API é lida do arquivo .env (veja .env.example) ou da variável
de ambiente S2_API_KEY.

A coleta é retomável: o progresso fica em checkpoints .jsonl na pasta de saída,
então se o script parar (erro, 429, Ctrl+C) basta rodar de novo.
"""

import argparse
import json
import os
import re
from difflib import SequenceMatcher
import time
from pathlib import Path

import requests
import pandas as pd


ENTRADA = Path(__file__).resolve().parent / "articles_first_webscraping_collection.xlsx"
SAIDA_DIR = Path(__file__).resolve().parent / "output"

API = "https://api.semanticscholar.org/graph/v1"
def carregar_env(path: Path) -> None:
    """Lê variáveis de um arquivo .env (chave=valor) sem sobrescrever as já definidas."""
    if not path.exists():
        return
    for linha in path.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


carregar_env(Path(__file__).resolve().parent / ".env")
API_KEY = os.environ.get("S2_API_KEY") or None

HEADERS = {"User-Agent": "SBES-Citation-Collector/1.0"}
if API_KEY:
    HEADERS["x-api-key"] = API_KEY

# Limite com chave: 1 requisição por segundo, somando todos os endpoints.
# O intervalo é contado a partir do fim da resposta anterior, com folga.
# Sem chave, o limite é compartilhado entre todos os usuários e bem mais baixo.
INTERVALO = 2.0 if API_KEY else 3.0
MAX_TENTATIVAS = 5  # esgotadas as tentativas, o artigo é pulado e fica para a próxima execução


class FalhaAPI(RuntimeError):
    """A API não respondeu com sucesso após MAX_TENTATIVAS."""


PULADOS: dict[int, str] = {}  # idx -> etapa em que o artigo foi pulado nesta execução

CAMPOS_PAPER = "paperId,title,year,citationCount,externalIds"
CAMPOS_CITACAO = "paperId,title,year,authors,venue,externalIds"
PAGINA_CITACOES = 1000
LOTE_DOI = 500

RE_DOI = re.compile(r"doi\.org/(.+)$", re.IGNORECASE)
SIMILARIDADE_MIN = 0.9  # títulos com pequenas diferenças (espaços, preposições)


_ultima_req = 0.0


def requisitar(session: requests.Session, metodo: str, url: str, **kwargs):
    """Faz a requisição respeitando o intervalo mínimo e com backoff em 429/5xx."""
    global _ultima_req
    espera = 5.0
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        dt = time.time() - _ultima_req
        if dt < INTERVALO:
            time.sleep(INTERVALO - dt)

        try:
            r = session.request(metodo, url, headers=HEADERS, timeout=60, **kwargs)
        except requests.RequestException as e:
            print(f"  [rede] {e} — tentativa {tentativa}/{MAX_TENTATIVAS}")
            if tentativa < MAX_TENTATIVAS:
                time.sleep(espera)
            espera = min(espera * 2, 120)
            continue
        finally:
            _ultima_req = time.time()

        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            if tentativa < MAX_TENTATIVAS:
                print(f"  [{r.status_code}] aguardando {espera:.0f}s — tentativa {tentativa}/{MAX_TENTATIVAS}")
                time.sleep(espera)
            else:
                print(f"  [{r.status_code}] tentativa {tentativa}/{MAX_TENTATIVAS} — desistindo deste artigo")
            espera = min(espera * 2, 120)
            continue
        r.raise_for_status()
        return r.json()

    raise FalhaAPI(f"Falhou após {MAX_TENTATIVAS} tentativas: {url}")


def extrair_doi(row) -> str | None:
    for col in ("doi_url", "ee_url"):
        val = row.get(col)
        if isinstance(val, str):
            m = RE_DOI.search(val.strip())
            if m:
                return m.group(1)
    return None


def normalizar_titulo(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()


def comparar_titulos(original: str, encontrado: str) -> str | None:
    """Retorna 'titulo' (igual), 'titulo_aproximado' (>= SIMILARIDADE_MIN) ou None."""
    a = normalizar_titulo(original).replace(" ", "")
    b = normalizar_titulo(encontrado).replace(" ", "")
    if a == b:
        return "titulo"
    if SequenceMatcher(None, a, b).ratio() >= SIMILARIDADE_MIN:
        return "titulo_aproximado"
    return None


def carregar_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def anexar_jsonl(path: Path, registros: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for reg in registros:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Etapa 1: artigo SBES -> paperId
# ---------------------------------------------------------------------------

def resolver_ids(session: requests.Session, df: pd.DataFrame, ckpt: Path) -> pd.DataFrame:
    # artigos não encontrados em execuções anteriores são tentados de novo
    feitos = {r["idx"]: r for r in carregar_jsonl(ckpt)}
    feitos = {k: r for k, r in feitos.items() if r["metodo_match"] != "nao_encontrado"}
    pendentes = df[~df["idx"].isin(feitos)]
    print(f"[Etapa 1] {len(feitos)} já resolvidos, {len(pendentes)} pendentes")

    # 1a) em lote por DOI
    com_doi = pendentes[pendentes["doi"].notna()]
    for i in range(0, len(com_doi), LOTE_DOI):
        lote = com_doi.iloc[i:i + LOTE_DOI]
        try:
            resp = requisitar(
                session, "POST", f"{API}/paper/batch",
                params={"fields": CAMPOS_PAPER},
                json={"ids": [f"DOI:{d}" for d in lote["doi"]]},
            ) or [None] * len(lote)
        except requests.HTTPError as e:
            # a API responde 400 quando nenhum DOI do lote é reconhecido
            if e.response is None or e.response.status_code != 400:
                raise
            resp = [None] * len(lote)
        except FalhaAPI:
            print(f"  lote DOI {i // LOTE_DOI + 1}: API indisponível — lote pulado")
            PULADOS.update({int(x): "identificação (DOI)" for x in lote["idx"]})
            continue

        regs = []
        for (_, row), paper in zip(lote.iterrows(), resp):
            if paper and paper.get("paperId"):
                regs.append({
                    "idx": int(row["idx"]),
                    "s2_paper_id": paper["paperId"],
                    "s2_title": paper.get("title"),
                    "s2_year": paper.get("year"),
                    "s2_citation_count": paper.get("citationCount"),
                    "metodo_match": "doi",
                })
        anexar_jsonl(ckpt, regs)
        feitos.update({r["idx"]: r for r in regs})
        print(f"  lote DOI {i // LOTE_DOI + 1}: {len(regs)}/{len(lote)} encontrados")

    # 1b) fallback por título (sem DOI ou DOI não indexado)
    restantes = df[~df["idx"].isin(feitos) & ~df["idx"].isin(PULADOS)]
    print(f"  {len(restantes)} artigos para busca por título")
    for n, (_, row) in enumerate(restantes.iterrows(), 1):
        titulo = str(row["titulo"]).strip().rstrip(".")
        try:
            resp = requisitar(
                session, "GET", f"{API}/paper/search/match",
                params={"query": titulo, "fields": CAMPOS_PAPER},
            )
        except FalhaAPI:
            print(f"  idx {int(row['idx'])}: API indisponível — pulado")
            PULADOS[int(row["idx"])] = "identificação (título)"
            continue
        paper = (resp or {}).get("data", [None])[0] if resp else None
        match = comparar_titulos(titulo, paper.get("title", "")) if paper else None

        # 2ª tentativa: busca geral, para títulos com erros de digitação no Semantic Scholar
        # (ex.: "Identifyng Implicit Process Vairables..."), que a busca exata não encontra
        if not match:
            try:
                busca = requisitar(
                    session, "GET", f"{API}/paper/search",
                    params={"query": titulo, "fields": CAMPOS_PAPER, "limit": 5},
                ) or {}
            except FalhaAPI:
                busca = {}
            for candidato in busca.get("data") or []:
                m = comparar_titulos(titulo, candidato.get("title", ""))
                if m:
                    paper, match = candidato, m
                    break

        if match:
            reg = {
                "idx": int(row["idx"]),
                "s2_paper_id": paper["paperId"],
                "s2_title": paper.get("title"),
                "s2_year": paper.get("year"),
                "s2_citation_count": paper.get("citationCount"),
                "metodo_match": match,
            }
        else:
            # registra a falha para não repetir a busca em execuções futuras
            reg = {
                "idx": int(row["idx"]),
                "s2_paper_id": None,
                "s2_title": paper.get("title") if paper else None,
                "s2_year": paper.get("year") if paper else None,
                "s2_citation_count": None,
                "metodo_match": "nao_encontrado",
            }
        anexar_jsonl(ckpt, [reg])
        feitos[reg["idx"]] = reg
        if n % 25 == 0:
            print(f"  título {n}/{len(restantes)}")

    mapa = pd.DataFrame(list(feitos.values()))
    return df.merge(mapa, on="idx", how="left")


# ---------------------------------------------------------------------------
# Etapa 2: paperId -> artigos citantes
# ---------------------------------------------------------------------------

def coletar_citacoes(session: requests.Session, paper_id: str) -> list[dict]:
    citacoes = []
    offset = 0
    while True:
        resp = requisitar(
            session, "GET", f"{API}/paper/{paper_id}/citations",
            params={"fields": CAMPOS_CITACAO, "offset": offset, "limit": PAGINA_CITACOES},
        )
        if not resp:
            break
        for item in resp.get("data", []):
            cp = item.get("citingPaper") or {}
            if not cp.get("paperId"):
                continue
            ext = cp.get("externalIds") or {}
            citacoes.append({
                "cited_s2_paper_id": paper_id,
                "citing_s2_paper_id": cp["paperId"],
                "citing_title": cp.get("title"),
                "citing_year": cp.get("year"),
                "citing_authors": ", ".join(a.get("name", "") for a in cp.get("authors") or []),
                "citing_author_ids": ", ".join(str(a.get("authorId") or "") for a in cp.get("authors") or []),
                "citing_venue": cp.get("venue"),
                "citing_doi": ext.get("DOI"),
            })
        if "next" not in resp:
            break
        offset = resp["next"]
    return citacoes


def enriquecer_venues(session: requests.Session, ckpt_cit: Path, arq: Path) -> None:
    """Busca, em lote, a venue estruturada (tipo, ISSN, nomes alternativos) dos artigos citantes.

    Usada para classificar periódicos pelo Qualis. Fica em cache: só artigos novos são consultados.
    """
    feitos = {r["paperId"] for r in carregar_jsonl(arq)}
    ids = sorted({c["citing_s2_paper_id"] for r in carregar_jsonl(ckpt_cit) for c in r["citacoes"]} - feitos)
    if not ids:
        return
    print(f"[Venues] buscando ISSN e tipo de venue de {len(ids)} artigo(s) citante(s)")
    for i in range(0, len(ids), LOTE_DOI):
        lote = ids[i:i + LOTE_DOI]
        try:
            resp = requisitar(session, "POST", f"{API}/paper/batch",
                              params={"fields": "venue,publicationVenue,journal"}, json={"ids": lote})
        except FalhaAPI:
            print("  API indisponível — venues ficam para a próxima execução")
            return
        regs = []
        for pid, p in zip(lote, resp or [None] * len(lote)):
            pv = (p or {}).get("publicationVenue") or {}
            regs.append({
                "paperId": pid,
                "pv_nome": pv.get("name"),
                "pv_tipo": pv.get("type"),
                "issn": pv.get("issn"),
                "nomes_alternativos": pv.get("alternate_names") or [],
                "journal": ((p or {}).get("journal") or {}).get("name"),
            })
        anexar_jsonl(arq, regs)


def atualizar_erros(arq: Path, df_total: pd.DataFrame, ckpt_ids: Path, ckpt_cit: Path) -> int:
    """Mantém erros_coleta.csv: soma os pulados desta execução e remove os que já foram concluídos."""
    ids = {r["idx"]: r for r in carregar_jsonl(ckpt_ids)}
    citados = {r["cited_s2_paper_id"] for r in carregar_jsonl(ckpt_cit)}

    def concluido(idx: int) -> bool:
        r = ids.get(idx)
        if not r or r["metodo_match"] == "nao_encontrado":
            return False
        return r["s2_paper_id"] in citados

    anteriores = pd.read_csv(arq) if arq.exists() else pd.DataFrame(columns=["idx", "etapa", "quando"])
    agora = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    novos = pd.DataFrame([{"idx": i, "etapa": e, "quando": agora} for i, e in PULADOS.items()],
                         columns=["idx", "etapa", "quando"])
    erros = pd.concat([anteriores[["idx", "etapa", "quando"]], novos]).drop_duplicates("idx", keep="last")
    feito = erros["idx"].map(lambda i: concluido(int(i))).astype(bool)
    erros = erros.loc[~feito].astype({"idx": int})
    if erros.empty:
        arq.unlink(missing_ok=True)  # o arquivo só existe quando há erros
        return 0
    info = df_total[["idx", "ano", "titulo", "autores", "doi_url", "ee_url"]]
    erros = info.merge(erros, on="idx", how="inner").sort_values("idx")
    erros.to_csv(arq, index=False)
    return len(erros)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entrada", type=Path, default=ENTRADA)
    ap.add_argument("--saida", type=Path, default=SAIDA_DIR)
    ap.add_argument("--inicio", type=int, default=0, help="posição (idx, a partir de 0) do primeiro artigo a processar")
    ap.add_argument("--limit", type=int, default=None, help="processa só N artigos a partir de --inicio")
    args = ap.parse_args()

    args.saida.mkdir(parents=True, exist_ok=True)
    ckpt_ids = args.saida / "checkpoint_ids.jsonl"
    ckpt_cit = args.saida / "checkpoint_citacoes.jsonl"

    if args.entrada.suffix.lower() == ".csv":
        df = pd.read_csv(args.entrada)
    else:
        df = pd.read_excel(args.entrada)
    df.insert(0, "idx", range(len(df)))
    df_total = df
    fim = args.inicio + args.limit if args.limit else None
    df = df.iloc[args.inicio:fim].copy()
    df["doi"] = df.apply(extrair_doi, axis=1)

    print(f"Entrada: {args.entrada.name} (artigos {args.inicio} a {args.inicio + len(df) - 1}, {len(df)} artigos)")
    print(f"Chave de API: {'sim' if API_KEY else 'não (mais lento; defina S2_API_KEY)'}")

    session = requests.Session()

    # Etapa 1
    df = resolver_ids(session, df, ckpt_ids)
    resolvidos = df[df["s2_paper_id"].notna()]
    print(f"[Etapa 1] {len(resolvidos)}/{len(df)} artigos com paperId")

    # Etapa 2 — checkpoint guarda um registro por artigo citado já concluído
    concluidos = {r["cited_s2_paper_id"] for r in carregar_jsonl(ckpt_cit)}
    pendentes = [p for p in resolvidos["s2_paper_id"].unique() if p not in concluidos]
    print(f"[Etapa 2] {len(concluidos)} já coletados, {len(pendentes)} pendentes")

    for n, pid in enumerate(pendentes, 1):
        try:
            cits = coletar_citacoes(session, pid)
        except FalhaAPI:
            # nada é gravado: o artigo volta como pendente na próxima execução
            print(f"  {n}/{len(pendentes)} paperId {pid}: API indisponível — pulado")
            PULADOS.update({int(x): "citações" for x in resolvidos.loc[resolvidos["s2_paper_id"] == pid, "idx"]})
            continue
        anexar_jsonl(ckpt_cit, [{"cited_s2_paper_id": pid, "citacoes": cits}])
        if n % 10 == 0 or n == len(pendentes):
            print(f"  {n}/{len(pendentes)} artigos — último com {len(cits)} citações")

    enriquecer_venues(session, ckpt_cit, args.saida / "venues_s2.jsonl")

    # Saídas finais acumulam tudo o que já está nos checkpoints (todas as execuções)
    mapa = pd.DataFrame(carregar_jsonl(ckpt_ids)).drop_duplicates("idx", keep="last")
    df = df_total.merge(mapa, on="idx", how="inner").sort_values("idx")
    df.to_csv(args.saida / "sbes_s2_paper_ids.csv", index=False)

    # Uma linha por par (artigo SBES citado, artigo citante)
    linhas = [c for r in carregar_jsonl(ckpt_cit) for c in r["citacoes"]]
    cit = pd.DataFrame(linhas, columns=[
        "cited_s2_paper_id", "citing_s2_paper_id", "citing_title", "citing_year",
        "citing_authors", "citing_author_ids", "citing_venue", "citing_doi",
    ]).drop_duplicates(["cited_s2_paper_id", "citing_s2_paper_id"])

    info_sbes = df[["idx", "ano", "titulo", "autores", "doi_url", "s2_paper_id"]].rename(columns={
        "idx": "sbes_idx", "ano": "sbes_ano", "titulo": "sbes_titulo",
        "autores": "sbes_autores", "doi_url": "sbes_doi_url", "s2_paper_id": "cited_s2_paper_id",
    })
    cit = info_sbes.merge(cit, on="cited_s2_paper_id", how="inner")

    cit.to_csv(args.saida / "sbes_citations.csv", index=False)
    cit.to_excel(args.saida / "sbes_citations.xlsx", index=False)
    print(f"\nConcluído: {len(cit)} citações de {cit['cited_s2_paper_id'].nunique()} artigos SBES")
    print(f"Arquivos em: {args.saida}")
    erros = atualizar_erros(args.saida / "erros_coleta.csv", df_total, ckpt_ids, ckpt_cit)
    if erros:
        print(f"\n{erros} artigo(s) com erro ao coletar (em erros_coleta.csv); rode a coleta de novo para completá-los.")
        for idx, etapa in sorted(PULADOS.items()):
            print(f"  - idx {idx}: {etapa}")


if __name__ == "__main__":
    main()
