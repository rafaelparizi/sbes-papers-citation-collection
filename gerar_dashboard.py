"""
Gera um mini dashboard HTML (autocontido) a partir dos resultados da coleta
do Semantic Scholar em output/.

Uso:
    python gerar_dashboard.py

Lê output/sbes_s2_paper_ids.csv e output/sbes_citations.csv e escreve
output/dashboard.html e docs/index.html (GitHub Pages). Rode de novo sempre
que a coleta avançar.
"""

import itertools
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd


SAIDA_DIR = Path(__file__).resolve().parent / "output"
ARQ_IDS = SAIDA_DIR / "sbes_s2_paper_ids.csv"
ARQ_CIT = SAIDA_DIR / "sbes_citations.csv"
ARQ_HTML = SAIDA_DIR / "dashboard.html"
ARQ_DUP = SAIDA_DIR / "possiveis_duplicatas.csv"
# cópia publicada pelo GitHub Pages (pasta docs/ do repositório)
ARQ_PAGES = Path(__file__).resolve().parent / "docs" / "index.html"


def limpar(v):
    if pd.isna(v):
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def titulo_base(t) -> str:
    """Título sem espaços repetidos e sem o ponto final (padrão do DBLP)."""
    return re.sub(r"\s+", " ", str(t)).strip().rstrip(".").strip()


# Dois artigos citantes são tratados como o mesmo trabalho (ex.: preprint no arXiv
# e versão publicada) quando os títulos são parecidos E há autor em comum.
SIMILARIDADE_DUP = 0.8


def normalizar(t) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()


def sobrenomes(autores) -> set[str]:
    return {n.split()[-1].lower() for n in str(autores or "").split(", ") if n.strip()}


def eh_preprint(venue, doi) -> bool:
    # 10.48550 é o prefixo dos DOIs atribuídos pelo arXiv
    return "arxiv" in str(venue or "").lower() or str(doi or "").startswith("10.48550/")


def agrupar_duplicatas(citantes: list[dict]) -> None:
    """Preenche 'grupo' (int ou None) nos citantes que parecem ser o mesmo trabalho."""
    pai = list(range(len(citantes)))

    def raiz(i):
        while pai[i] != i:
            pai[i] = pai[pai[i]]
            i = pai[i]
        return i

    for i, j in itertools.combinations(range(len(citantes)), 2):
        a, b = citantes[i], citantes[j]
        parecidos = SequenceMatcher(None, normalizar(a["titulo"]), normalizar(b["titulo"])).ratio() >= SIMILARIDADE_DUP
        if parecidos and sobrenomes(a["autores"]) & sobrenomes(b["autores"]):
            pai[raiz(j)] = raiz(i)

    membros = {}
    for i in range(len(citantes)):
        membros.setdefault(raiz(i), []).append(i)
    grupos = [m for m in membros.values() if len(m) > 1]
    for c in citantes:
        c["grupo"] = None
    for n, m in enumerate(grupos, 1):
        for i in m:
            citantes[i]["grupo"] = n


def ordenar_citantes(citantes: list[dict]) -> list[dict]:
    """Mais recentes primeiro, mantendo os registros de um mesmo grupo juntos."""
    ano_grupo = {}
    for c in citantes:
        if c["grupo"]:
            ano_grupo[c["grupo"]] = max(ano_grupo.get(c["grupo"], 0), c["ano"] or 0)
    chave = lambda c: (-(ano_grupo.get(c["grupo"]) or c["ano"] or 0), c["grupo"] or 0, -(c["ano"] or 0))
    return sorted(citantes, key=chave)


def montar_dados() -> list[dict]:
    ids = pd.read_csv(ARQ_IDS)
    cit = pd.read_csv(ARQ_CIT) if ARQ_CIT.exists() else pd.DataFrame(columns=["sbes_idx"])

    por_artigo = {k: g for k, g in cit.groupby("sbes_idx")}
    artigos = []
    for _, a in ids.sort_values("idx").iterrows():
        g = por_artigo.get(a["idx"])
        citantes = []
        if g is not None:
            for _, c in g.sort_values("citing_year", ascending=False, na_position="last").iterrows():
                citantes.append({
                    "id": limpar(c["citing_s2_paper_id"]),
                    "titulo": limpar(c["citing_title"]),
                    "ano": limpar(c["citing_year"]),
                    "autores": limpar(c["citing_authors"]),
                    "venue": limpar(c["citing_venue"]),
                    "doi": limpar(c["citing_doi"]),
                    "preprint": eh_preprint(c["citing_venue"], c["citing_doi"]),
                })
            agrupar_duplicatas(citantes)
            citantes = ordenar_citantes(citantes)
        artigos.append({
            "idx": int(a["idx"]),
            "ano": limpar(a["ano"]),
            "titulo": limpar(a["titulo"]),
            "autores": limpar(a["autores"]),
            "doi_url": limpar(a["doi_url"]) or limpar(a["ee_url"]),
            "s2_id": limpar(a["s2_paper_id"]),
            "match": limpar(a["metodo_match"]),
            "s2_titulo": limpar(a["s2_title"]),
            # título no Semantic Scholar não é idêntico ao da planilha
            "similar": bool(limpar(a["s2_title"])) and titulo_base(a["titulo"]) != titulo_base(a["s2_title"]),
            "citantes": citantes,
            # trabalhos distintos: cada grupo de duplicatas conta uma vez
            "distintos": len({c["grupo"] or f"_{k}" for k, c in enumerate(citantes)}),
        })
    return artigos


def salvar_duplicatas(artigos: list[dict]) -> int:
    linhas = [
        {
            "sbes_idx": a["idx"], "sbes_titulo": a["titulo"], "grupo": c["grupo"],
            "citing_s2_paper_id": c["id"], "citing_title": c["titulo"], "citing_year": c["ano"],
            "citing_venue": c["venue"], "citing_authors": c["autores"], "preprint": c["preprint"],
        }
        for a in artigos for c in a["citantes"] if c["grupo"]
    ]
    pd.DataFrame(linhas).to_csv(ARQ_DUP, index=False)
    return len({(l["sbes_idx"], l["grupo"]) for l in linhas})


HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Citações SBES</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/shepherd.js@11.2.0/dist/css/shepherd.css">
<style>
:root {
  --bg: #f6f7f9; --panel: #ffffff; --text: #1d2330; --muted: #5d6677;
  --line: #e2e5eb; --accent: #2f5fd0; --accent-soft: #e8eefb; --bar: #2f5fd0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14171d; --panel: #1c2028; --text: #e6e9ef; --muted: #99a2b3;
    --line: #2c323d; --accent: #7fa2ff; --accent-soft: #243150; --bar: #7fa2ff;
  }
  :root:not([data-theme="light"]) .tag { color: #ffd27a; background: #3a2e12; border-color: #6b5420; }
  :root:not([data-theme="light"]) .tag.tit { color: #ff9f95; background: #3d1c19; border-color: #7a3630; }
  :root:not([data-theme="light"]) .tag.dup { color: #cfb2ff; background: #2c2140; border-color: #5b4585; }
  :root:not([data-theme="light"]) .tag.pre { color: var(--muted); background: var(--bg); border-color: var(--line); }
  :root:not([data-theme="light"]) tr.dup td { background: #241d30; }
}
:root[data-theme="dark"] .tag { color: #ffd27a; background: #3a2e12; border-color: #6b5420; }
:root[data-theme="dark"] .tag.tit { color: #ff9f95; background: #3d1c19; border-color: #7a3630; }
:root[data-theme="dark"] .tag.dup { color: #cfb2ff; background: #2c2140; border-color: #5b4585; }
:root[data-theme="dark"] .tag.pre { color: var(--muted); background: var(--bg); border-color: var(--line); }
:root[data-theme="dark"] tr.dup td { background: #241d30; }
:root[data-theme="dark"] {
  --bg: #14171d; --panel: #1c2028; --text: #e6e9ef; --muted: #99a2b3;
  --line: #2c323d; --accent: #7fa2ff; --accent-soft: #243150; --bar: #7fa2ff;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
header { padding: 18px 24px 8px; }
header h1 { margin: 0; font-size: 20px; }
header p { margin: 4px 0 0; color: var(--muted); }
header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
#abrirTour { font: inherit; font-size: 13px; color: var(--accent); background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; cursor: pointer; }
#abrirTour:hover { border-color: var(--accent); }
/* Tour (Shepherd.js) com as cores do dashboard */
.shepherd-element { background: var(--panel); color: var(--text); border: 1px solid var(--line); border-radius: 8px; max-width: 360px; box-shadow: 0 8px 28px rgba(0,0,0,.18); }
.shepherd-element .shepherd-arrow:before { background: var(--panel) !important; }
.shepherd-has-title .shepherd-content .shepherd-header { background: var(--panel); padding: 14px 16px 0; }
.shepherd-title { color: var(--text); font-size: 15px; font-weight: 600; }
.shepherd-cancel-icon { color: var(--muted); }
.shepherd-text { color: var(--text); font-size: 14px; line-height: 1.5; padding: 8px 16px 4px; }
.shepherd-footer { padding: 8px 16px 14px; gap: 6px; flex-wrap: wrap; }
.shepherd-button { font: inherit; font-size: 13px; border-radius: 6px; padding: 6px 12px; margin: 0; background: var(--accent); color: var(--panel); }
.shepherd-button:not(:disabled):hover { background: var(--accent); filter: brightness(1.1); color: var(--panel); }
.shepherd-button.secundario { background: var(--bg); color: var(--text); border: 1px solid var(--line); }
.shepherd-button.secundario:not(:disabled):hover { background: var(--bg); color: var(--text); border-color: var(--accent); }
.shepherd-button.nao-ver { background: none; color: var(--muted); padding: 6px 4px; margin-right: auto; text-decoration: underline; }
.shepherd-button.nao-ver:not(:disabled):hover { background: none; color: var(--text); }
.shepherd-modal-overlay-container.shepherd-modal-is-visible { opacity: .35; }
.kpis { display: flex; gap: 12px; flex-wrap: wrap; padding: 8px 24px 16px; }
.kpi { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; min-width: 130px; }
.kpi b { display: block; font-size: 20px; }
.kpi span { color: var(--muted); font-size: 12px; }
main { display: grid; grid-template-columns: minmax(300px, 420px) 1fr; gap: 16px; padding: 0 24px 24px; align-items: start; }
main.com-graficos { grid-template-columns: minmax(280px, 380px) minmax(0, 1fr) minmax(280px, 360px); }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; min-width: 0; }
.filtros { display: flex; gap: 8px; padding: 12px; border-bottom: 1px solid var(--line); }
select, input {
  font: inherit; color: var(--text); background: var(--bg);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px;
}
input[type="search"] { flex: 1 1 100%; order: -1; min-width: 0; padding: 9px 12px; font-size: 15px; }
input[type="search"]:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
#ordem { padding: 7px 8px; }
.filtros { flex-wrap: wrap; align-items: center; }
.toggle { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 13px; cursor: pointer; user-select: none; }
.chips:empty { display: none; }
.chips { padding: 8px 12px; border-bottom: 1px solid var(--line); }
.chip { display: inline-flex; align-items: center; gap: 6px; background: var(--accent-soft); color: var(--text); border: 1px solid var(--accent); border-radius: 14px; padding: 2px 10px; font: inherit; font-size: 13px; cursor: pointer; }
.tag { display: inline-block; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; color: #8a5a00; background: #fff3d6; border: 1px solid #f0c865; border-radius: 4px; padding: 0 5px; margin-left: 4px; vertical-align: 1px; }
.tag.tit { color: #a3261b; background: #fde8e6; border-color: #f2a79f; }
.tag.dup { color: #6b3fa0; background: #f1e9fb; border-color: #c9aef0; }
.tag.pre { color: var(--muted); background: var(--bg); border-color: var(--line); }
tr.dup td { background: #f7f2fd; }
[data-tip] { cursor: help; }
#tip { position: fixed; z-index: 10; max-width: 280px; padding: 6px 9px; border-radius: 6px; font-size: 12px; line-height: 1.4; font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--panel); background: var(--text); pointer-events: none; opacity: 0; transition: opacity .12s; }
#tip.on { opacity: 1; }
.nota { background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; margin: 10px 0 0; font-size: 13px; }
.nota b { color: var(--muted); font-weight: 600; }
.nota strong { font-weight: 700; color: var(--text); background: #fff3d6; border-radius: 3px; padding: 0 2px; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .nota strong { background: #3a2e12; } }
:root[data-theme="dark"] .nota strong { background: #3a2e12; }
.autor { background: none; border: 0; padding: 0; font: inherit; color: var(--accent); cursor: pointer; }
.autor:hover { text-decoration: underline; }
.lista { max-height: calc(100vh - 230px); overflow: auto; }
.item { padding: 10px 12px; border-bottom: 1px solid var(--line); cursor: pointer; display: flex; gap: 10px; }
.item:hover { background: var(--accent-soft); }
.item.ativo { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
.item .t { flex: 1; min-width: 0; }
.item .meta { color: var(--muted); font-size: 12px; }
.badge { align-self: center; min-width: 32px; text-align: center; font-weight: 600; border-radius: 12px; padding: 2px 8px; background: var(--bg); border: 1px solid var(--line); }
.badge.tem { background: var(--accent); border-color: var(--accent); color: var(--panel); }
.vazio { padding: 24px; color: var(--muted); }
.detalhe { padding: 18px 20px; }
.detalhe h2 { margin: 0 0 6px; font-size: 18px; }
.detalhe .autores { color: var(--muted); margin-bottom: 8px; }
.links a { color: var(--accent); margin-right: 14px; text-decoration: none; }
.links a:hover { text-decoration: underline; }
.resumo { display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0; }
h3 { font-size: 14px; margin: 18px 0 8px; }
.grafico { display: flex; align-items: flex-end; gap: 6px; height: 110px; padding: 4px 0; border-bottom: 1px solid var(--line); overflow-x: auto; }
.col { display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; min-width: 34px; }
.col .v { font-size: 11px; color: var(--muted); }
.col .b { width: 22px; background: var(--bar); border-radius: 3px 3px 0 0; }
.col .a { font-size: 11px; color: var(--muted); margin-top: 4px; }
.tabela { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px; }
td a { color: var(--text); text-decoration: none; }
td a:hover { color: var(--accent); text-decoration: underline; }
td.sm { color: var(--muted); font-size: 12px; }
td.doi { min-width: 120px; word-break: break-all; }
td.doi a { color: var(--accent); }
.venue { background: none; border: 0; padding: 0; font: inherit; color: var(--text); text-align: left; cursor: pointer; }
.venue:hover { color: var(--accent); text-decoration: underline; }
.kpi-venues { flex: 1 1 260px; max-width: 520px; }
.vchips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.vchip { font: inherit; font-size: 12px; color: var(--text); background: var(--bg); border: 1px solid var(--line); border-radius: 12px; padding: 2px 9px; cursor: pointer; }
.vchip b, .kpi .vchip b { display: inline; font-size: 12px; color: var(--muted); font-weight: 600; margin-left: 2px; }
.kpi-venues > span { display: block; }
.vchip:hover { border-color: var(--accent); }
.vchip { color: var(--muted); }
.vchip.on { background: var(--accent); border-color: var(--accent); color: var(--panel); font-weight: 600; }
.vchip.on::before { content: "✓ "; }
.vchip.on b, .kpi .vchip.on b { color: var(--panel); }
.anos-barra { padding: 0 24px 16px; }
.anos-topo { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.graficos { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.graficos[hidden] { display: none; }
.gcard { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; min-width: 0; }
.gcard h3 { margin: 0; font-size: 14px; }
.gcard .sub { color: var(--muted); font-size: 12px; margin: 2px 0 10px; }
.gcard .grafico { height: 150px; border-bottom-color: var(--line); gap: 4px; }
.gcard .col { min-width: 26px; }
.gcard .col .b { width: 18px; }
.gcard .col .b { border-radius: 4px 4px 0 0; }
.hbarras { display: grid; grid-template-columns: max-content 1fr max-content; gap: 8px 10px; align-items: center; }
.hbarras .r { font-size: 12px; color: var(--muted); }
.hbarras .t { height: 14px; background: var(--bg); border-radius: 0 4px 4px 0; }
.hbarras .t div { height: 100%; background: var(--bar); border-radius: 0 4px 4px 0; min-width: 2px; }
.hbarras .v { font-size: 12px; font-weight: 600; }
.anos-cab { color: var(--muted); font-size: 12px; margin-bottom: 2px; }
.anos-cab button { background: none; border: 0; padding: 0; font: inherit; color: var(--accent); cursor: pointer; }
.anos-cab button:hover { text-decoration: underline; }
.limpar-venues { background: none; border: 0; padding: 0; font: inherit; color: var(--accent); cursor: pointer; }
@media (max-width: 820px) {
  header, .kpis, .anos-barra { padding-left: 16px; padding-right: 16px; }
  main.com-graficos { grid-template-columns: 1fr; }
  main { grid-template-columns: 1fr; padding: 0 16px 16px; }
  .lista { max-height: 50vh; }
}
</style>
</head>
<body>
<header>
  <div>
    <h1>Citações dos artigos do SBES</h1>
    <p>Fonte: Semantic Scholar · gerado em __GERADO__</p>
  </div>
  <button id="abrirTour" type="button">Ver tour</button>
</header>
<div class="kpis" id="kpis"></div>
<div class="anos-barra">
  <div class="anos-topo">
    <div class="anos-cab">Anos de publicação <span id="anosAcoes"></span></div>
    <label class="toggle"><input id="verGraficos" type="checkbox"> Ver gráficos</label>
  </div>
  <div class="vchips" id="anos"></div>
</div>
<main>
  <section class="panel">
    <div class="filtros">
      <select id="ordem" aria-label="Ordenar">
        <option value="">Ordem da planilha</option>
        <option value="desc">Mais citados primeiro</option>
        <option value="asc">Menos citados primeiro</option>
      </select>
      <input id="busca" type="search" placeholder="🔍  Buscar por título ou autor…">
      <label class="toggle"><input id="soCitados" type="checkbox"> Só com citações</label>
      <label class="toggle"><input id="soSimilares" type="checkbox"> Só <span class="tag" data-tip="O título no Semantic Scholar não é idêntico ao da planilha (ignorando ponto final e espaços).">similar</span></label>
      <label class="toggle"><input id="soDuplicatas" type="checkbox"> Só com <span class="tag dup" data-tip="Há citações que parecem ser o mesmo trabalho em registros separados (ex.: preprint no arXiv e versão publicada).">duplicata</span></label>
      <label class="toggle"><input id="soTitulo" type="checkbox"> Só encontrados por <span class="tag tit" data-tip="Encontrado no Semantic Scholar pela busca por título, porque o DOI da planilha não foi reconhecido.">título</span></label>
    </div>
    <div class="chips" id="chips"></div>
    <div class="lista" id="lista"></div>
  </section>
  <section class="panel" id="detalhe">
    <div class="vazio">Selecione um artigo à esquerda para ver quem o citou.</div>
  </section>
  <section class="graficos" id="graficos" hidden></section>
</main>
<script>
const DADOS = __DADOS__;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
// Destaca em negrito as palavras de `novo` que não aparecem, na mesma ordem, em `orig` (LCS por palavra)
function destacarDiferencas(orig, novo) {
  const a = String(orig ?? "").trim().replace(/\.$/, "").split(/\s+/);
  const b = String(novo ?? "").trim().replace(/\.$/, "").split(/\s+/);
  const dp = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const igual = new Array(b.length).fill(false);
  for (let i = 0, j = 0; i < a.length && j < b.length; ) {
    if (a[i] === b[j]) { igual[j] = true; i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  return b.map((w, j) => (igual[j] ? esc(w) : `<strong>${esc(w)}</strong>`)).join(" ");
}

let selecionado = null;
let autorFiltro = null;
const TIP = {
  similar: "O título no Semantic Scholar não é idêntico ao da planilha (ignorando ponto final e espaços).",
  dup: "Há citações que parecem ser o mesmo trabalho em registros separados (ex.: preprint no arXiv e versão publicada).",
  titulo: (a) => a.match === "titulo_aproximado"
    ? "Encontrado pela busca por título, com título apenas parecido (≥ 90% de semelhança): vale conferir."
    : "Encontrado no Semantic Scholar pela busca por título, porque o DOI da planilha não foi reconhecido.",
  preprint: "Preprint: publicado no arXiv (venue arXiv ou DOI com prefixo 10.48550).",
};

// Tooltip flutuante para qualquer elemento com data-tip
const tip = document.createElement("div");
tip.id = "tip";
tip.setAttribute("role", "tooltip");
document.body.appendChild(tip);
document.addEventListener("mouseover", (e) => {
  const alvo = e.target.closest("[data-tip]");
  if (!alvo) { tip.classList.remove("on"); return; }
  tip.textContent = alvo.dataset.tip;
  tip.classList.add("on");
  const r = alvo.getBoundingClientRect();
  const t = tip.getBoundingClientRect();
  let x = Math.min(Math.max(8, r.left + r.width / 2 - t.width / 2), window.innerWidth - t.width - 8);
  let y = r.top - t.height - 8;
  if (y < 8) y = r.bottom + 8;
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
});
document.addEventListener("scroll", () => tip.classList.remove("on"), true);

const venuesOff = new Set();  // venues desmarcadas no card (vazio = todas marcadas)
const porTitulo = (a) => a.match === "titulo" || a.match === "titulo_aproximado";
const autoresDe = (a) => String(a.autores ?? "").split(", ").filter(Boolean);

function kpi(valor, rotulo) {
  return `<div class="kpi"><b>${valor}</b><span>${rotulo}</span></div>`;
}

function renderKpis(lista) {
  const total = lista.reduce((s, a) => s + a.citantes.length, 0);
  const citados = lista.filter((a) => a.citantes.length).length;
  const unicos = new Set(lista.flatMap((a) => a.citantes.map((c) => c.id))).size;
  $("kpis").innerHTML =
    kpi(lista.length, "artigos SBES") +
    kpi(citados, "com ao menos 1 citação") +
    kpi(total, "citações") +
    kpi(unicos, "artigos citantes distintos");
}

// Anos: todos marcados por padrão; clique desmarca (ou marca de novo)
const anosOff = new Set();
const contAnos = {};
DADOS.forEach((a) => { contAnos[a.ano] = (contAnos[a.ano] || 0) + 1; });
const TODOS_ANOS = Object.keys(contAnos).sort((a, b) => b - a);

function renderAnos() {
  const acoes = [];
  if (anosOff.size) acoes.push(`<button data-acao="todos">marcar todos</button>`);
  if (anosOff.size < TODOS_ANOS.length) acoes.push(`<button data-acao="nenhum">desmarcar todos</button>`);
  $("anosAcoes").innerHTML = TODOS_ANOS.length > 1 ? `· ${acoes.join(" · ")}` : "";
  $("anos").innerHTML = TODOS_ANOS.map((a) => `
    <button class="vchip${anosOff.has(a) ? "" : " on"}" data-ano="${a}" aria-pressed="${!anosOff.has(a)}">${a} <b>${contAnos[a]}</b></button>`).join("");
}

function filtrados() {
  const q = $("busca").value.trim().toLowerCase();
  const soCitados = $("soCitados").checked;
  const soSimilares = $("soSimilares").checked;
  const soDuplicatas = $("soDuplicatas").checked;
  const soTitulo = $("soTitulo").checked;
  const ordem = $("ordem").value;
  const lista = DADOS.filter((a) =>
    !anosOff.has(String(a.ano)) &&
    (!soCitados || a.citantes.length > 0) &&
    (!soSimilares || a.similar) &&
    (!soDuplicatas || a.distintos < a.citantes.length) &&
    (!soTitulo || porTitulo(a)) &&
    (!autorFiltro || autoresDe(a).includes(autorFiltro)) &&
    (!q || (a.titulo + " " + a.autores).toLowerCase().includes(q)));
  // empates mantêm a ordem da planilha (sort estável)
  if (ordem === "desc") lista.sort((x, y) => y.citantes.length - x.citantes.length);
  if (ordem === "asc") lista.sort((x, y) => x.citantes.length - y.citantes.length);
  return lista;
}

// ---- Gráficos (acompanham os filtros ativos) ----
function barrasV(itens, altura = 120) {
  const max = Math.max(1, ...itens.map((i) => i.v));
  return `<div class="grafico">${itens.map((i) => `
    <div class="col" data-tip="${esc(i.tip)}">
      <span class="v">${i.v}</span>
      <div class="b" style="height:${Math.round((i.v / max) * altura)}px"></div>
      <span class="a">${esc(i.r)}</span>
    </div>`).join("")}</div>`;
}

function barrasH(itens) {
  const max = Math.max(1, ...itens.map((i) => i.v));
  return `<div class="hbarras">${itens.map((i) => `
    <span class="r">${esc(i.r)}</span>
    <div class="t" data-tip="${esc(i.tip)}"><div style="width:${(i.v / max) * 100}%"></div></div>
    <span class="v">${i.v}</span>`).join("")}</div>`;
}

const plural = (n, um, varios) => `${n} ${n === 1 ? um : varios}`;
const cartao = (titulo, sub, corpo) =>
  `<div class="gcard"><h3>${titulo}</h3><div class="sub">${sub}</div>${corpo}</div>`;

function renderGraficos(lista) {
  const ver = $("verGraficos").checked;
  $("graficos").hidden = !ver;
  document.querySelector("main").classList.toggle("com-graficos", ver);
  if (!ver) return;
  if (!lista.length) { $("graficos").innerHTML = `<div class="gcard vazio">Nenhum artigo com os filtros atuais.</div>`; return; }

  const anos = [...new Set(lista.map((a) => a.ano))].sort((x, y) => x - y);
  const porAno = (f) => anos.map((ano) => lista.filter((a) => a.ano === ano).reduce((s, a) => s + f(a), 0));

  const nArt = porAno(() => 1);
  const nCit = porAno((a) => a.citantes.length);
  const g1 = barrasV(anos.map((ano, k) => ({ r: ano, v: nArt[k], tip: `${ano}: ${nArt[k]} artigos` })));
  const g2 = barrasV(anos.map((ano, k) => ({ r: ano, v: nCit[k],
    tip: `${ano}: ${nCit[k]} citações em ${nArt[k]} artigos (média ${(nCit[k] / nArt[k]).toFixed(2)})` })));

  const rotMatch = { doi: "DOI", titulo: "título", titulo_aproximado: "título aprox.", nao_encontrado: "não encontrado" };
  const contMatch = {};
  lista.forEach((a) => { const k = a.match || "nao_encontrado"; contMatch[k] = (contMatch[k] || 0) + 1; });
  const g3 = barrasH(Object.keys(rotMatch).filter((k) => contMatch[k]).map((k) => ({
    r: rotMatch[k], v: contMatch[k],
    tip: `${rotMatch[k]}: ${contMatch[k]} artigos (${Math.round((contMatch[k] / lista.length) * 100)}%)` })));

  const nAut = lista.map((a) => autoresDe(a).length);
  const faixas = [1, 2, 3, 4, 5, 6, 7, 8];
  const hist = faixas.map((f) => nAut.filter((n) => (f === 8 ? n >= 8 : n === f)).length);
  const media = nAut.reduce((s, n) => s + n, 0) / nAut.length;
  const distintos = new Set(lista.flatMap(autoresDe)).size;
  const g4 = barrasV(faixas.map((f, k) => ({ r: f === 8 ? "8+" : f, v: hist[k],
    tip: `${hist[k]} artigos com ${f === 8 ? "8 ou mais" : f} autor${f === 1 ? "" : "es"}` })));

  $("graficos").innerHTML =
    cartao("Artigos por ano", `${plural(lista.length, "artigo SBES", "artigos SBES")}`, g1) +
    cartao("Citações por ano de publicação", `${plural(nCit.reduce((s, n) => s + n, 0), "citação recebida", "citações recebidas")} pelos artigos de cada ano`, g2) +
    cartao("Como os artigos foram encontrados", "no Semantic Scholar", g3) +
    cartao("Número de autores por artigo", `média ${media.toFixed(1)} · ${plural(distintos, "autor distinto", "autores distintos")}`, g4);
}

function renderLista() {
  const lista = filtrados();
  renderKpis(lista);
  renderGraficos(lista);
  $("chips").innerHTML = autorFiltro
    ? `<button class="chip" id="limparAutor" title="Remover filtro">Autor: ${esc(autorFiltro)} <span aria-hidden="true">✕</span></button>`
    : "";
  if (!lista.length) { $("lista").innerHTML = `<div class="vazio">Nenhum artigo encontrado.</div>`; return; }
  $("lista").innerHTML = lista.map((a) => `
    <div class="item${a.idx === selecionado ? " ativo" : ""}" data-idx="${a.idx}">
      <div class="t">
        <div>${esc(a.titulo)}${a.similar ? ` <span class="tag" data-tip="${TIP.similar}">similar</span>` : ""}${a.distintos < a.citantes.length ? ` <span class="tag dup" data-tip="${TIP.dup}">duplicata</span>` : ""}${porTitulo(a) ? ` <span class="tag tit" data-tip="${TIP.titulo(a)}">título</span>` : ""}</div>
        <div class="meta">${esc(a.ano)} · ${esc(a.autores)}</div>
      </div>
      <div class="badge${a.citantes.length ? " tem" : ""}" title="citações">${a.citantes.length}</div>
    </div>`).join("");
}

function renderDetalhe(a) {
  const links = [
    a.doi_url && `<a href="${esc(a.doi_url)}" target="_blank" rel="noopener">DOI ↗</a>`,
    a.s2_id && `<a href="https://www.semanticscholar.org/paper/${esc(a.s2_id)}" target="_blank" rel="noopener">Semantic Scholar ↗</a>`,
  ].filter(Boolean).join("");

  $("detalhe").innerHTML = `
    <div class="detalhe">
      <h2>${esc(a.titulo)}${a.similar ? ` <span class="tag" data-tip="${TIP.similar}">similar</span>` : ""}${a.distintos < a.citantes.length ? ` <span class="tag dup" data-tip="${TIP.dup}">duplicata</span>` : ""}${porTitulo(a) ? ` <span class="tag tit" data-tip="${TIP.titulo(a)}">título</span>` : ""}</h2>
      <div class="autores">${autoresDe(a).map((n) =>
        `<button class="autor" data-autor="${esc(n)}" title="Ver artigos deste autor">${esc(n)}</button>`).join(", ")} · SBES ${esc(a.ano)}</div>
      <div class="links">${links}</div>
      ${a.similar ? `<div class="nota"><b>Título no Semantic Scholar:</b> ${destacarDiferencas(a.titulo, a.s2_titulo)}</div>` : ""}
      <div id="painel"></div>
    </div>`;
  renderPainel(a);
}

const SEM_VENUE = "__sem_venue__";
const venueDe = (c) => c.venue || SEM_VENUE;

// Cards, gráfico e tabela, recalculados conforme as venues selecionadas
function renderPainel(a) {
  const cont = {};
  a.citantes.forEach((c) => { cont[venueDe(c)] = (cont[venueDe(c)] || 0) + 1; });
  [...venuesOff].forEach((v) => { if (!(v in cont)) venuesOff.delete(v); });
  const opcoes = Object.keys(cont).sort((x, y) => cont[y] - cont[x] || x.localeCompare(y));
  const linhas = a.citantes.filter((c) => !venuesOff.has(venueDe(c)));

  const n = linhas.length;
  const distintos = new Set(linhas.map((c) => (c.grupo ? `g${c.grupo}` : c.id))).size;
  const anosValidos = linhas.map((c) => c.ano).filter(Boolean);
  const primeiro = anosValidos.length ? Math.min(...anosValidos) : "—";
  const nVenues = new Set(linhas.map((c) => c.venue).filter(Boolean)).size;
  const encontrado = {doi: "DOI", titulo: "título", titulo_aproximado: "título aprox."}[a.match] || "não encontrado";

  const cardVenues = opcoes.length ? `
    <div class="kpi kpi-venues">
      <span>venues ${venuesOff.size ? `· <button class="limpar-venues">marcar todas</button>` : "(clique para desmarcar)"}</span>
      <div class="vchips">${opcoes.map((v) => `
        <button class="vchip${venuesOff.has(v) ? "" : " on"}" data-venue="${esc(v)}" aria-pressed="${!venuesOff.has(v)}">${
          v === SEM_VENUE ? "(sem venue)" : esc(v)} <b>${cont[v]}</b></button>`).join("")}
      </div>
    </div>` : "";

  const porAno = {};
  linhas.forEach((c) => { const k = c.ano ?? "s/ano"; porAno[k] = (porAno[k] || 0) + 1; });
  const anos = Object.keys(porAno).sort();
  const max = Math.max(1, ...Object.values(porAno));
  const grafico = n ? `
    <h3>Citações por ano</h3>
    <div class="grafico">${anos.map((k) => `
      <div class="col" title="${esc(k)}: ${porAno[k]}">
        <span class="v">${porAno[k]}</span>
        <div class="b" style="height:${Math.round((porAno[k] / max) * 80)}px"></div>
        <span class="a">${esc(k)}</span>
      </div>`).join("")}
    </div>` : "";

  const tabela = !a.citantes.length
    ? `<p class="vazio" style="padding:12px 0">Nenhuma citação registrada no Semantic Scholar.</p>`
    : `
    <h3>Quem citou (${venuesOff.size ? `${n} de ${a.citantes.length}` : n})</h3>
    <div class="tabela"><table>
      <thead><tr><th>Ano</th><th>Artigo citante</th><th>Autores</th><th>Venue</th><th>DOI</th></tr></thead>
      <tbody>${linhas.map((c) => {
        const outros = c.grupo ? a.citantes.filter((o) => o.grupo === c.grupo && o !== c).map((o) => o.titulo) : [];
        return `
        <tr${c.grupo ? ' class="dup"' : ""}>
          <td>${esc(c.ano ?? "—")}</td>
          <td><a href="https://www.semanticscholar.org/paper/${esc(c.id)}" target="_blank" rel="noopener">${esc(c.titulo)}</a>${
            c.grupo ? ` <span class="tag dup" data-tip="Provavelmente o mesmo trabalho que: ${esc(outros.join(" | "))}">duplicata ${c.grupo}</span>` : ""}${
            c.preprint ? ` <span class="tag pre" data-tip="${TIP.preprint}">preprint</span>` : ""}</td>
          <td class="sm">${esc(c.autores)}</td>
          <td class="sm">${c.venue
            ? `<button class="venue" data-venue="${esc(c.venue)}" title="Mostrar só esta venue">${esc(c.venue)}</button>`
            : "—"}</td>
          <td class="sm doi">${c.doi
            ? `<a href="https://doi.org/${esc(c.doi)}" target="_blank" rel="noopener">${esc(c.doi)}</a>`
            : "—"}</td>
        </tr>`;
      }).join("")}
      </tbody>
    </table></div>`;

  $("painel").innerHTML = `
    <div class="resumo">
      ${kpi(n, "citações (registros)")}
      ${kpi(distintos, "trabalhos distintos")}
      ${kpi(primeiro, "primeira citação")}
      ${kpi(nVenues, "venues distintos")}
      ${kpi(encontrado, "encontrado por")}
      ${cardVenues}
    </div>
    ${grafico}
    ${tabela}`;
}

$("lista").addEventListener("click", (e) => {
  const item = e.target.closest(".item");
  if (!item) return;
  selecionado = Number(item.dataset.idx);
  venuesOff.clear();
  renderDetalhe(DADOS.find((a) => a.idx === selecionado));
  renderLista();
});
$("detalhe").addEventListener("click", (e) => {
  const atual = () => DADOS.find((a) => a.idx === selecionado);
  const chip = e.target.closest(".vchip");
  if (chip) {
    // clique desmarca (ou volta a marcar) a venue
    const k = chip.dataset.venue;
    venuesOff.has(k) ? venuesOff.delete(k) : venuesOff.add(k);
    renderPainel(atual());
    return;
  }
  const v = e.target.closest(".venue");
  if (v) {
    // na tabela: mostra só esta venue (clicar de novo volta a mostrar todas)
    const a = atual();
    const outras = new Set(a.citantes.map(venueDe).filter((k) => k !== v.dataset.venue));
    const soEsta = [...outras].every((k) => venuesOff.has(k)) && !venuesOff.has(v.dataset.venue);
    venuesOff.clear();
    if (!soEsta) outras.forEach((k) => venuesOff.add(k));
    renderPainel(a);
    return;
  }
  if (e.target.closest(".limpar-venues")) {
    venuesOff.clear();
    renderPainel(atual());
    return;
  }
  const b = e.target.closest(".autor");
  if (!b) return;
  autorFiltro = b.dataset.autor;
  anosOff.clear();  // mostra o autor em todos os anos
  renderAnos();
  renderLista();
});
$("chips").addEventListener("click", (e) => {
  if (!e.target.closest("#limparAutor")) return;
  autorFiltro = null;
  renderLista();
});
$("soCitados").addEventListener("change", renderLista);
$("soSimilares").addEventListener("change", renderLista);
$("soDuplicatas").addEventListener("change", renderLista);
$("soTitulo").addEventListener("change", renderLista);
$("anos").addEventListener("click", (e) => {
  const b = e.target.closest(".vchip");
  if (!b) return;
  anosOff.has(b.dataset.ano) ? anosOff.delete(b.dataset.ano) : anosOff.add(b.dataset.ano);
  renderAnos();
  renderLista();
});
$("anosAcoes").addEventListener("click", (e) => {
  const acao = e.target.dataset.acao;
  if (!acao) return;
  anosOff.clear();
  if (acao === "nenhum") TODOS_ANOS.forEach((a) => anosOff.add(a));
  renderAnos();
  renderLista();
});
$("ordem").addEventListener("change", renderLista);
$("busca").addEventListener("input", renderLista);

try { $("verGraficos").checked = localStorage.getItem("verGraficos") === "1"; } catch (e) {}
$("verGraficos").addEventListener("change", () => {
  try { localStorage.setItem("verGraficos", $("verGraficos").checked ? "1" : "0"); } catch (e) {}
  renderLista();
});

renderAnos();
renderLista();
</script>
<script src="https://cdn.jsdelivr.net/npm/shepherd.js@11.2.0/dist/js/shepherd.min.js"></script>
<script>
// ---- Tour guiado (Shepherd.js) ----
// Abre sozinho na primeira visita; "Não quero mais ver" grava a preferência no navegador.
const CHAVE_TOUR = "tourOculto";
const tourOculto = () => { try { return localStorage.getItem(CHAVE_TOUR) === "1"; } catch (e) { return false; } };

function criarTour() {
  const tour = new Shepherd.Tour({
    useModalOverlay: true,
    defaultStepOptions: {
      cancelIcon: { enabled: true, label: "Fechar" },
      scrollTo: { behavior: "smooth", block: "center" },
      modalOverlayOpeningPadding: 6,
      modalOverlayOpeningRadius: 8,
    },
  });
  const naoVer = {
    text: "Não quero mais ver",
    classes: "nao-ver",
    action() {
      try { localStorage.setItem(CHAVE_TOUR, "1"); } catch (e) {}
      this.complete();
    },
  };
  const voltar = { text: "Voltar", classes: "secundario", action() { this.back(); } };
  const proximo = { text: "Próximo", action() { this.next(); } };
  const concluir = { text: "Concluir", action() { this.complete(); } };

  const passos = [
    { id: "inicio", title: "Bem-vindo ao dashboard",
      text: "Este painel mostra quem citou os artigos do SBES, segundo o Semantic Scholar. O tour leva menos de um minuto." },
    { id: "cards", title: "Resumo", attachTo: { element: "#kpis", on: "bottom" },
      text: "Totais dos artigos visíveis: quantos artigos, quantos foram citados, total de citações e artigos citantes distintos. Mudam conforme os filtros." },
    { id: "anos", title: "Anos de publicação", attachTo: { element: ".anos-barra", on: "bottom" },
      text: "Todos os anos vêm marcados. Clique em um ano para desmarcá-lo; use <b>desmarcar todos</b> e depois marque só o ano que quer ver." },
    { id: "graficos", title: "Gráficos", attachTo: { element: ".anos-topo .toggle", on: "left" },
      text: "Marque <b>Ver gráficos</b> para abrir uma terceira coluna com artigos e citações por ano, como os artigos foram encontrados e número de autores." },
    { id: "filtros", title: "Busca e filtros", attachTo: { element: ".filtros", on: "right" },
      text: "Busque por título ou autor, ordene por número de citações e filtre pelos badges. Passe o mouse sobre um badge para ver o que ele significa." },
    { id: "lista", title: "Artigos", attachTo: { element: "#lista", on: "right" },
      text: "Clique em um artigo para ver os detalhes. O número à direita é a quantidade de citações." },
    { id: "detalhe", title: "Detalhes e quem citou", attachTo: { element: "#detalhe", on: "left" },
      beforeShowPromise: () => new Promise((ok) => {
        if (selecionado === null) document.querySelector("#lista .item")?.click();
        setTimeout(ok, 50);
      }),
      text: "Clique no nome de um autor para ver os artigos dele. No card <b>venues</b>, clique para desmarcar uma venue; os cards, o gráfico e a tabela são recalculados." },
    { id: "fim", title: "Pronto!",
      text: "Para rever este tour, use o botão <b>Ver tour</b> no topo da página." },
  ];
  passos.forEach((p, i) => {
    const botoes = [naoVer];
    if (i > 0) botoes.push(voltar);
    botoes.push(i === passos.length - 1 ? concluir : proximo);
    tour.addStep({ ...p, buttons: botoes });
  });
  return tour;
}

if (window.Shepherd) {
  $("abrirTour").addEventListener("click", () => criarTour().start());
  if (!tourOculto()) criarTour().start();
} else {
  $("abrirTour").hidden = true;  // sem internet: o dashboard funciona, só o tour fica indisponível
}
</script>
</body>
</html>
"""


def main():
    artigos = montar_dados()
    dados = json.dumps(artigos, ensure_ascii=False).replace("</", "<\\/")
    gerado = pd.Timestamp.now().strftime("%d/%m/%Y %H:%M")
    html = HTML.replace("__DADOS__", dados).replace("__GERADO__", gerado)
    ARQ_HTML.write_text(html, encoding="utf-8")
    ARQ_PAGES.parent.mkdir(parents=True, exist_ok=True)
    ARQ_PAGES.write_text(html, encoding="utf-8")
    total = sum(len(a["citantes"]) for a in artigos)
    grupos = salvar_duplicatas(artigos)
    print(f"Dashboard gerado: {ARQ_HTML} ({len(artigos)} artigos, {total} citações)")
    print(f"Possíveis duplicatas: {grupos} grupo(s) em {ARQ_DUP.name}")


if __name__ == "__main__":
    main()
