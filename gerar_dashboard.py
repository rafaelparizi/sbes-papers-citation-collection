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


def eh_preprint(venue) -> bool:
    return "arxiv" in str(venue or "").lower()


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
                    "preprint": eh_preprint(c["citing_venue"]),
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
  :root:not([data-theme="light"]) .tag.dup { color: #ff9f95; background: #3d1c19; border-color: #7a3630; }
  :root:not([data-theme="light"]) .tag.pre { color: var(--muted); background: var(--bg); border-color: var(--line); }
  :root:not([data-theme="light"]) tr.dup td { background: #2a1d1c; }
}
:root[data-theme="dark"] .tag { color: #ffd27a; background: #3a2e12; border-color: #6b5420; }
:root[data-theme="dark"] .tag.dup { color: #ff9f95; background: #3d1c19; border-color: #7a3630; }
:root[data-theme="dark"] .tag.pre { color: var(--muted); background: var(--bg); border-color: var(--line); }
:root[data-theme="dark"] tr.dup td { background: #2a1d1c; }
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
.kpis { display: flex; gap: 12px; flex-wrap: wrap; padding: 8px 24px 16px; }
.kpi { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; min-width: 130px; }
.kpi b { display: block; font-size: 20px; }
.kpi span { color: var(--muted); font-size: 12px; }
main { display: grid; grid-template-columns: minmax(300px, 420px) 1fr; gap: 16px; padding: 0 24px 24px; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; min-width: 0; }
.filtros { display: flex; gap: 8px; padding: 12px; border-bottom: 1px solid var(--line); }
select, input {
  font: inherit; color: var(--text); background: var(--bg);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px;
}
input[type="search"] { flex: 1 1 100%; order: -1; min-width: 0; padding: 9px 12px; font-size: 15px; }
input[type="search"]:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
#ano, #ordem { padding: 7px 8px; }
.filtros { flex-wrap: wrap; align-items: center; }
.toggle { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 13px; cursor: pointer; user-select: none; }
.chips:empty { display: none; }
.chips { padding: 8px 12px; border-bottom: 1px solid var(--line); }
.chip { display: inline-flex; align-items: center; gap: 6px; background: var(--accent-soft); color: var(--text); border: 1px solid var(--accent); border-radius: 14px; padding: 2px 10px; font: inherit; font-size: 13px; cursor: pointer; }
.tag { display: inline-block; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; color: #8a5a00; background: #fff3d6; border: 1px solid #f0c865; border-radius: 4px; padding: 0 5px; margin-left: 4px; vertical-align: 1px; }
.tag.dup { color: #a3261b; background: #fde8e6; border-color: #f2a79f; }
.tag.pre { color: var(--muted); background: var(--bg); border-color: var(--line); }
tr.dup td { background: #fdf3f2; }
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
@media (max-width: 820px) {
  header, .kpis { padding-left: 16px; padding-right: 16px; }
  main { grid-template-columns: 1fr; padding: 0 16px 16px; }
  .lista { max-height: 50vh; }
}
</style>
</head>
<body>
<header>
  <h1>Citações dos artigos do SBES</h1>
  <p>Fonte: Semantic Scholar · gerado em __GERADO__</p>
</header>
<div class="kpis" id="kpis"></div>
<main>
  <section class="panel">
    <div class="filtros">
      <select id="ano" aria-label="Ano de publicação"></select>
      <select id="ordem" aria-label="Ordenar">
        <option value="">Ordem da planilha</option>
        <option value="desc">Mais citados primeiro</option>
        <option value="asc">Menos citados primeiro</option>
      </select>
      <input id="busca" type="search" placeholder="🔍  Buscar por título ou autor…">
      <label class="toggle"><input id="soCitados" type="checkbox"> Só com citações</label>
      <label class="toggle"><input id="soSimilares" type="checkbox"> Só <span class="tag">similar</span></label>
      <label class="toggle"><input id="soDuplicatas" type="checkbox"> Só com <span class="tag dup">duplicata</span></label>
    </div>
    <div class="chips" id="chips"></div>
    <div class="lista" id="lista"></div>
  </section>
  <section class="panel" id="detalhe">
    <div class="vazio">Selecione um artigo à esquerda para ver quem o citou.</div>
  </section>
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

function montarAnos() {
  const cont = {};
  DADOS.forEach((a) => { cont[a.ano] = (cont[a.ano] || 0) + 1; });
  const anos = Object.keys(cont).sort((a, b) => b - a);
  $("ano").innerHTML = `<option value="">Todos os anos (${DADOS.length})</option>` +
    anos.map((a) => `<option value="${a}">${a} (${cont[a]})</option>`).join("");
}

function filtrados() {
  const ano = $("ano").value;
  const q = $("busca").value.trim().toLowerCase();
  const soCitados = $("soCitados").checked;
  const soSimilares = $("soSimilares").checked;
  const soDuplicatas = $("soDuplicatas").checked;
  const ordem = $("ordem").value;
  const lista = DADOS.filter((a) =>
    (!ano || String(a.ano) === ano) &&
    (!soCitados || a.citantes.length > 0) &&
    (!soSimilares || a.similar) &&
    (!soDuplicatas || a.distintos < a.citantes.length) &&
    (!autorFiltro || autoresDe(a).includes(autorFiltro)) &&
    (!q || (a.titulo + " " + a.autores).toLowerCase().includes(q)));
  // empates mantêm a ordem da planilha (sort estável)
  if (ordem === "desc") lista.sort((x, y) => y.citantes.length - x.citantes.length);
  if (ordem === "asc") lista.sort((x, y) => x.citantes.length - y.citantes.length);
  return lista;
}

function renderLista() {
  const lista = filtrados();
  renderKpis(lista);
  $("chips").innerHTML = autorFiltro
    ? `<button class="chip" id="limparAutor" title="Remover filtro">Autor: ${esc(autorFiltro)} <span aria-hidden="true">✕</span></button>`
    : "";
  if (!lista.length) { $("lista").innerHTML = `<div class="vazio">Nenhum artigo encontrado.</div>`; return; }
  $("lista").innerHTML = lista.map((a) => `
    <div class="item${a.idx === selecionado ? " ativo" : ""}" data-idx="${a.idx}">
      <div class="t">
        <div>${esc(a.titulo)}${a.similar ? ` <span class="tag" title="Título no Semantic Scholar diferente do da planilha">similar</span>` : ""}${a.distintos < a.citantes.length ? ` <span class="tag dup" title="Há citações que parecem ser o mesmo trabalho (ex.: preprint e versão publicada)">duplicata</span>` : ""}</div>
        <div class="meta">${esc(a.ano)} · ${esc(a.autores)}</div>
      </div>
      <div class="badge${a.citantes.length ? " tem" : ""}" title="citações">${a.citantes.length}</div>
    </div>`).join("");
}

function renderDetalhe(a) {
  const n = a.citantes.length;
  const porAno = {};
  a.citantes.forEach((c) => { const k = c.ano ?? "s/ano"; porAno[k] = (porAno[k] || 0) + 1; });
  const anos = Object.keys(porAno).sort();
  const max = Math.max(1, ...Object.values(porAno));
  const anosValidos = a.citantes.map((c) => c.ano).filter(Boolean);
  const primeiro = anosValidos.length ? Math.min(...anosValidos) : "—";
  const venues = new Set(a.citantes.map((c) => c.venue).filter(Boolean)).size;

  const links = [
    a.doi_url && `<a href="${esc(a.doi_url)}" target="_blank" rel="noopener">DOI ↗</a>`,
    a.s2_id && `<a href="https://www.semanticscholar.org/paper/${esc(a.s2_id)}" target="_blank" rel="noopener">Semantic Scholar ↗</a>`,
  ].filter(Boolean).join("");

  const grafico = n ? `
    <h3>Citações por ano</h3>
    <div class="grafico">${anos.map((k) => `
      <div class="col" title="${esc(k)}: ${porAno[k]}">
        <span class="v">${porAno[k]}</span>
        <div class="b" style="height:${Math.round((porAno[k] / max) * 80)}px"></div>
        <span class="a">${esc(k)}</span>
      </div>`).join("")}
    </div>` : "";

  const tabela = n ? `
    <h3>Quem citou (${n})</h3>
    <div class="tabela"><table>
      <thead><tr><th>Ano</th><th>Artigo citante</th><th>Autores</th><th>Venue</th></tr></thead>
      <tbody>${a.citantes.map((c) => {
        const outros = c.grupo ? a.citantes.filter((o) => o.grupo === c.grupo && o !== c).map((o) => o.titulo) : [];
        return `
        <tr${c.grupo ? ' class="dup"' : ""}>
          <td>${esc(c.ano ?? "—")}</td>
          <td><a href="https://www.semanticscholar.org/paper/${esc(c.id)}" target="_blank" rel="noopener">${esc(c.titulo)}</a>${
            c.grupo ? ` <span class="tag dup" title="Provavelmente o mesmo trabalho que: ${esc(outros.join(" | "))}">duplicata ${c.grupo}</span>` : ""}${
            c.preprint ? ` <span class="tag pre">preprint</span>` : ""}</td>
          <td class="sm">${esc(c.autores)}</td>
          <td class="sm">${esc(c.venue || "—")}</td>
        </tr>`;
      }).join("")}
      </tbody>
    </table></div>` : `<p class="vazio" style="padding:12px 0">Nenhuma citação registrada no Semantic Scholar.</p>`;

  $("detalhe").innerHTML = `
    <div class="detalhe">
      <h2>${esc(a.titulo)}${a.similar ? ` <span class="tag">similar</span>` : ""}</h2>
      <div class="autores">${autoresDe(a).map((n) =>
        `<button class="autor" data-autor="${esc(n)}" title="Ver artigos deste autor">${esc(n)}</button>`).join(", ")} · SBES ${esc(a.ano)}</div>
      <div class="links">${links}</div>
      ${a.similar ? `<div class="nota"><b>Título no Semantic Scholar:</b> ${destacarDiferencas(a.titulo, a.s2_titulo)}</div>` : ""}
      <div class="resumo">
        ${kpi(n, "citações (registros)")}
        ${kpi(a.distintos, "trabalhos distintos")}
        ${kpi(primeiro, "primeira citação")}
        ${kpi(venues, "venues distintos")}
        ${kpi({doi: "DOI", titulo: "título", titulo_aproximado: "título aprox."}[a.match] || "não encontrado", "encontrado por")}
      </div>
      ${grafico}
      ${tabela}
    </div>`;
}

$("lista").addEventListener("click", (e) => {
  const item = e.target.closest(".item");
  if (!item) return;
  selecionado = Number(item.dataset.idx);
  renderDetalhe(DADOS.find((a) => a.idx === selecionado));
  renderLista();
});
$("detalhe").addEventListener("click", (e) => {
  const b = e.target.closest(".autor");
  if (!b) return;
  autorFiltro = b.dataset.autor;
  $("ano").value = "";  // mostra o autor em todos os anos
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
$("ano").addEventListener("change", renderLista);
$("ordem").addEventListener("change", renderLista);
$("busca").addEventListener("input", renderLista);

montarAnos();
renderLista();
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
