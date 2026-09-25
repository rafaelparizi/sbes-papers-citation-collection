# SBES Papers Citation Collection

Coleta, no [Semantic Scholar](https://www.semanticscholar.org/), dos artigos que citam os artigos publicados no SBES (Simpósio Brasileiro de Engenharia de Software), a partir do dataset da fase 1 ([Zenodo](https://zenodo.org/records/21710814)).

**Dashboard:** https://rafaelparizi.github.io/sbes-papers-citation-collection/

**Situação atual:** coletados os 118 artigos de 2025 (linhas 0 a 117 da planilha), em 25/09/2026.

## Como a coleta é realizada

A coleta é feita pelo script `semantic_scholar_citations.py`, usando a [Academic Graph API](https://api.semanticscholar.org/api-docs/graph) do Semantic Scholar, em duas etapas.

### Entrada

`articles_first_webscraping_collection.xlsx`: artigos do SBES coletados do DBLP na fase 1, com ano, título, autores e DOI. Cada artigo é identificado pela sua posição na planilha (`idx`, a partir de 0). As opções `--inicio` e `--limit` definem qual trecho da planilha é processado.

### Etapa 1: identificar cada artigo no Semantic Scholar

Para cada artigo, o objetivo é obter o `paperId` do Semantic Scholar.

1. **Por DOI, em lote.** O DOI é extraído de `doi_url` (ou de `ee_url`, se aquela estiver vazia). Os DOIs são enviados em lotes de até 500 ao endpoint `POST /paper/batch`. DOIs que o Semantic Scholar não reconhece voltam vazios; quando nenhum DOI de um lote é reconhecido, a API responde com erro 400, que é tratado como "nenhum encontrado".
2. **Por título, quando o DOI falha.** Artigos sem DOI (anais antigos, publicados só no SOL/SBC) ou cujo DOI não está associado ao registro no Semantic Scholar são buscados pelo título em `GET /paper/search/match`. O resultado só é aceito se o título retornado for compatível com o da planilha:
   - os dois títulos são normalizados (minúsculas, sem pontuação e sem espaços);
   - se ficarem idênticos, o método é registrado como `titulo`;
   - se a semelhança for de pelo menos 90% (`difflib.SequenceMatcher`), o método é registrado como `titulo_aproximado`. Isso cobre diferenças pequenas, como "GitWorkflow" × "Git Workflow";
   - abaixo disso, o artigo é registrado como `nao_encontrado`.
3. Artigos marcados como `nao_encontrado` são buscados de novo a cada execução.

### Etapa 2: coletar os artigos citantes

Para cada `paperId` encontrado, o script percorre `GET /paper/{paperId}/citations`, em páginas de 1.000 resultados, até a última página. De cada artigo citante (`citingPaper`) são salvos:

- `paperId`, para continuar navegando no Semantic Scholar;
- título, ano de publicação e venue;
- autores (nomes e `authorId`);
- DOI, quando existe.

Pares repetidos (mesmo artigo citado e mesmo artigo citante) são removidos. O total coletado de cada artigo é comparado com o `citationCount` informado pelo próprio Semantic Scholar.

### Limite de requisições e novas tentativas

- A API aceita **1 requisição por segundo** por chave, somando todos os endpoints. O script espera 1,2 s após o fim de cada resposta (3 s sem chave, porque o limite sem chave é compartilhado entre todos os usuários).
- Respostas 429 (limite excedido) e erros 5xx são repetidos até 8 vezes, com espera crescente (5 s, 10 s, 20 s… até 120 s).

### Retomada da coleta

O progresso é gravado em `output/checkpoint_ids.jsonl` (etapa 1) e `output/checkpoint_citacoes.jsonl` (etapa 2) à medida que cada artigo é concluído. Se a execução for interrompida, basta rodar de novo: o que já foi coletado não é consultado outra vez. Os arquivos finais (`sbes_s2_paper_ids.csv`, `sbes_citations.csv/.xlsx`) são regenerados a partir dos checkpoints e acumulam todas as execuções.

> Os checkpoints usam a posição do artigo na planilha (`idx`). Se a ordem das linhas mudar, apague a pasta `output/` antes de coletar de novo.

### Artigos "similares" no dashboard

No dashboard, um artigo recebe o badge **similar** quando o título no Semantic Scholar não é idêntico ao da planilha, ignorando só o ponto final (padrão do DBLP) e espaços repetidos. Isso vale para qualquer método de identificação, inclusive DOI. Nos detalhes do artigo, as palavras diferentes aparecem em negrito, e esses artigos podem ser filtrados.

### Possíveis duplicatas entre os artigos citantes

O Semantic Scholar às vezes mantém registros separados para o mesmo trabalho, por exemplo o preprint no arXiv e a versão publicada em conferência. Entre os artigos que citam um mesmo artigo SBES, dois registros são marcados como **duplicata** quando:

- os títulos normalizados têm pelo menos 80% de semelhança (`difflib.SequenceMatcher`); **e**
- há pelo menos um sobrenome de autor em comum.

Os registros continuam na contagem de citações (que segue o `citationCount` do Semantic Scholar), mas o dashboard mostra também o número de **trabalhos distintos**, marca os registros agrupados e os de venue arXiv (**preprint**) e permite filtrar os artigos que têm duplicatas. A lista para revisão fica em `output/possiveis_duplicatas.csv`.

### Recursos do dashboard

- Faixa de anos de publicação abaixo dos cards do topo: todos marcados por padrão; clicar desmarca (ou marca de novo) um ano, com atalhos para marcar ou desmarcar todos.
- Busca por título/autor, ordenação por número de citações e clique no nome de um autor para ver os artigos dele.
- Badges com explicação ao passar o mouse: **similar** (título diferente no Semantic Scholar), **título** (encontrado pela busca por título, porque o DOI não foi reconhecido), **duplicata** e **preprint**. Cada badge da lista tem um filtro correspondente.
- Nos detalhes de um artigo, o card **venues** lista as venues dos artigos citantes, todas marcadas por padrão. Clicar desmarca (ou marca de novo) uma venue, e os demais cards, o gráfico e a tabela são recalculados. Clicar no nome de uma venue na tabela mostra só aquela venue.
- A tabela de quem citou traz ano, título, autores, venue e DOI de cada artigo citante.

### Limitações

- A cobertura depende do Semantic Scholar: citações que ele não indexou não aparecem, e artigos antigos tendem a ter menos citações registradas.
- Alguns artigos citantes não têm ano de publicação no Semantic Scholar (aparecem como `s/ano` no dashboard).
- As contagens refletem a data da coleta; novas citações só aparecem em uma nova execução.
- Identificações por título, sobretudo `titulo_aproximado`, merecem conferência manual.

## Arquivos de saída (`output/`)

| Arquivo | Conteúdo |
|---|---|
| `sbes_s2_paper_ids.csv` | Artigos SBES com o `paperId` no Semantic Scholar e o método de identificação (`doi`, `titulo`, `titulo_aproximado`, `nao_encontrado`) |
| `sbes_citations.csv` / `.xlsx` | Uma linha por par (artigo SBES citado, artigo citante) |
| `possiveis_duplicatas.csv` | Artigos citantes que parecem ser o mesmo trabalho (ex.: preprint e versão publicada), para revisão |
| `dashboard.html` | Dashboard interativo (mesmo conteúdo de `docs/index.html`) |

## Uso

Chave da API (opcional, mas recomendada): copie `.env.example` para `.env` e preencha `S2_API_KEY`.

### Com Python

```bash
pip install -r requirements.txt
python semantic_scholar_citations.py --inicio 0 --limit 10   # artigos 0 a 9
python gerar_dashboard.py
python servidor.py                                          # http://localhost:8000
```

### Com Docker

```bash
docker compose up -d                                        # dashboard em http://localhost:8000
docker compose run --rm coleta --inicio 0 --limit 10        # coleta
```

Após mudar o código: `docker compose build && docker compose up -d`.

## Atualizar o dashboard público

```bash
python gerar_dashboard.py
git add output docs && git commit -m "Atualiza coleta" && git push
```
