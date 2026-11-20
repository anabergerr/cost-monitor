import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_KEY = os.getenv("ARTIFICIAL_ANALYSIS_API_KEY")
URL = "https://artificialanalysis.ai/api/v2/language/models/free"
TOP_N = 8
TOP_CAROS = 3
# Fracos demais não entram no recorte do gráfico (puxariam o eixo de custo).
MIN_INTEL_GRAFICO = 30


def custo_tarefa(modelo):
    bloco = modelo.get("artificial_analysis_intelligence_index_cost") or {}
    total = (bloco.get("cost_per_task") or {}).get("total_cost")
    if total is None:
        return None
    total = float(total)
    return total if total > 0 else None


def modelos_validos(modelos):
    validos = []
    for m in modelos:
        inteligencia = (m.get("evaluations") or {}).get(
            "artificial_analysis_intelligence_index"
        )
        custo = custo_tarefa(m)
        if not inteligencia or not custo:
            continue
        engenharia = (m.get("evaluations") or {}).get(
            "artificial_analysis_engineering_index"
        )
        validos.append({
            "nome": m.get("name", "Desconhecido"),
            "inteligencia": float(inteligencia),
            "engenharia": float(engenharia) if engenharia else None,
            "custo": custo,
        })
    return validos


def fronteira_pareto(modelos):
    ordenados = sorted(modelos, key=lambda m: (m["custo"], -m["inteligencia"]))
    frente = []
    melhor = -1
    for m in ordenados:
        if m["inteligencia"] > melhor:
            frente.append(m)
            melhor = m["inteligencia"]
    return frente


def teto_eixo(valor, passo=10):
    return math.ceil(valor / passo) * passo


def cortes_quadrante(validos):
    visiveis = [m for m in validos if m["inteligencia"] >= MIN_INTEL_GRAFICO]
    if len(visiveis) < 5:
        visiveis = list(validos)
    max_intel = max(m["inteligencia"] for m in visiveis)
    # AA: eixo Y de 0 até o teto; verde é a metade de cima.
    corte_intel = teto_eixo(max_intel) / 2
    custos = [m["custo"] for m in visiveis]
    # AA: eixo X em log; verde é a metade da esquerda.
    corte_custo = math.sqrt(min(custos) * max(custos))
    return corte_intel, corte_custo


def quadrante_verde(validos):
    corte_intel, corte_custo = cortes_quadrante(validos)
    chave = lambda m: (m["nome"], m["custo"], m["inteligencia"])
    no_pareto = {chave(m) for m in fronteira_pareto(validos)}
    verde = []
    for m in validos:
        if m["inteligencia"] < corte_intel or m["custo"] > corte_custo:
            continue
        item = dict(m)
        item["fronteira"] = chave(m) in no_pareto
        verde.append(item)
    verde.sort(key=lambda m: (-m["inteligencia"], m["custo"]))
    return {
        "corte_intel": corte_intel,
        "corte_custo": corte_custo,
        "verde": verde,
        "fronteira": [m for m in verde if m["fronteira"]],
    }


def _chave(m):
    return (m["nome"], m["custo"], m["inteligencia"])


def faixas_caras(validos, corte_custo, top_n=TOP_CAROS):
    no_pareto = {_chave(m) for m in fronteira_pareto(validos)}
    vale, nao = [], []
    for m in validos:
        if m["custo"] <= corte_custo:
            continue
        item = dict(m)
        item["fronteira"] = _chave(m) in no_pareto
        (vale if item["fronteira"] else nao).append(item)
    vale.sort(key=lambda m: (-m["inteligencia"], m["custo"]))
    nao.sort(key=lambda m: (m["inteligencia"] / m["custo"], m["inteligencia"], -m["custo"]))
    return vale[:top_n], nao[:top_n]


def recomendar(verde, fronteira):
    pool = fronteira or verde
    if not pool:
        return None
    pesado = max(pool, key=lambda m: (m["inteligencia"], m["engenharia"] or 0, -m["custo"]))
    de_boa = min(pool, key=lambda m: (m["custo"], -m["inteligencia"]))
    resto = [m for m in pool if m is not pesado and m is not de_boa]
    medio = max(
        resto,
        key=lambda m: (m["inteligencia"], m["engenharia"] or 0, -m["custo"]),
    ) if resto else pesado
    com_eng = [m for m in verde if m["engenharia"]]
    codigo = max(
        com_eng,
        key=lambda m: (m["engenharia"], m["inteligencia"], -m["custo"]),
    ) if com_eng else None
    return {
        "pesado": pesado,
        "medio": medio,
        "de_boa": de_boa,
        "codigo": codigo,
    }


def ranquear(modelos, top_n=TOP_N):
    validos = modelos_validos(modelos)
    if not validos:
        return {
            "verde": [],
            "fronteira": [],
            "dica": None,
            "corte_intel": None,
            "corte_custo": None,
            "caros_vale": [],
            "caros_nao": [],
        }
    q = quadrante_verde(validos)
    vale, nao = faixas_caras(validos, q["corte_custo"])
    return {
        "verde": q["verde"][:top_n],
        "fronteira": q["fronteira"],
        "dica": recomendar(q["verde"], q["fronteira"]),
        "corte_intel": q["corte_intel"],
        "corte_custo": q["corte_custo"],
        "caros_vale": vale,
        "caros_nao": nao,
    }


def buscar_pagina(page):
    req = urllib.request.Request(
        f"{URL}?page={page}",
        headers={"x-api-key": API_KEY},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API {exc.code}: {body}") from exc


def buscar_modelos():
    modelos = []
    page = 1
    while True:
        payload = buscar_pagina(page)
        modelos.extend(payload.get("data") or [])
        if not (payload.get("pagination") or {}).get("has_more"):
            break
        page += 1
        # ponytail: hard cap 20 pages (~4k models); raise if AA grows past that
        if page > 20:
            break
    return modelos


def buscar_melhores_modelos():
    if not API_KEY:
        raise ValueError("Defina a variável de ambiente ARTIFICIAL_ANALYSIS_API_KEY.")
    return ranquear(buscar_modelos())


def _esc(texto):
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_custo(custo):
    return f"${custo:.2f}/tarefa"


def _metricas(modelo):
    eng = f"{modelo['engenharia']:.1f}" if modelo.get("engenharia") else "—"
    return (
        f"intel {modelo['inteligencia']:.1f} · eng {eng} · "
        f"{_fmt_custo(modelo['custo'])}"
    )


def _linha_dica(emoji, rotulo, modelo):
    return (
        f"{emoji} {rotulo} → <b>{_esc(modelo['nome'])}</b>"
        f"  <i>{_esc(_metricas(modelo))}</i>"
    )


def _bloco(itens):
    if not itens:
        return ["<i>nenhum hoje</i>"]
    linhas = []
    for i, m in enumerate(itens, 1):
        marca = "  ·  📈 fronteira" if m.get("fronteira") else ""
        linhas.append(
            f"{i}. <b>{_esc(m['nome'])}</b>  ·  {_esc(_metricas(m))}{marca}"
        )
    return linhas


_LANES = (
    ("pesado", "💪", "Tarefa complexa", "Arquitetura, bug difícil, refactor grande"),
    ("medio", "⚖️", "Tarefa média", "Feature, review, PR do dia"),
    ("de_boa", "🌱", "Tarefa simples", "Script, boilerplate, ajuste rápido"),
)


def _hoje():
    return datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y")


def _mesmo_modelo(a, b):
    if not a or not b:
        return False
    return (a["nome"], a["custo"], a["inteligencia"]) == (
        b["nome"], b["custo"], b["inteligencia"]
    )


def formatar_ranking(dados):
    hoje = _hoje()
    dica = dados.get("dica")
    if not dica:
        return (
            f"💻 <b>O que usar hoje pra código</b> · {_esc(hoje)}\n\n"
            "Nenhum modelo no quadrante verde."
        )
    corte_intel = dados["corte_intel"]
    corte_custo = dados["corte_custo"]
    linhas = [
        f"💻 <b>O que usar hoje pra código</b> · {_esc(hoje)}",
        (
            f"<i>Quadrante verde · intel ≥ {corte_intel:.0f} · "
            f"até {_fmt_custo(corte_custo)}</i>"
        ),
        "",
    ]
    for chave, emoji, rotulo, _quando in _LANES:
        linhas.append(_linha_dica(emoji, rotulo, dica[chave]))
    codigo = dica.get("codigo")
    if codigo and not any(_mesmo_modelo(codigo, dica[k]) for k, *_ in _LANES):
        linhas.append(_linha_dica("🛠️", "Melhor em engenharia", codigo))
    linhas.extend(["", "🟢 <b>No verde</b>"])
    linhas.extend(_bloco(dados["verde"]))
    linhas.extend(["", "💎 <b>Caros, mas vale</b>"])
    linhas.extend(_bloco(dados.get("caros_vale") or []))
    linhas.extend(["", "🚫 <b>Caros, nem pensar</b>"])
    linhas.extend(_bloco(dados.get("caros_nao") or []))
    return "\n".join(linhas)


_CSS = """
:root {
  --bg: #f3f5f0;
  --ink: #1c2418;
  --muted: #5e6a57;
  --card: #fff;
  --line: #d5ddd0;
  --green: #2c6a3c;
  --green-bg: #e7f3e6;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font: 16px/1.45 system-ui, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--ink);
}
main { max-width: 860px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
.kicker {
  color: var(--green);
  font-weight: 600;
  letter-spacing: .04em;
  text-transform: uppercase;
  font-size: .78rem;
  margin: 0 0 .35rem;
}
h1 { font-size: 1.8rem; margin: 0 0 .4rem; }
.meta { color: var(--muted); margin: 0 0 1.75rem; }
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: .9rem;
  margin-bottom: 2.2rem;
}
.card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 1rem 1.05rem 1.1rem;
}
.card .emoji { font-size: 1.25rem; margin: 0 0 .3rem; }
.card h2 {
  font-size: 1rem;
  font-weight: 700;
  margin: 0 0 .15rem;
}
.card .quando {
  font-size: .8rem;
  color: var(--muted);
  margin: 0 0 .7rem;
}
.card .nome { font-size: 1.05rem; font-weight: 700; margin: 0 0 .8rem; }
dl {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: .4rem;
  margin: 0;
}
dt {
  font-size: .7rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .03em;
}
dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
h2.lista { font-size: 1.1rem; margin: 1.8rem 0 .3rem; }
.sublista { color: var(--muted); font-size: .85rem; margin: 0 0 .8rem; }
.tabela {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 14px;
  overflow: hidden;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th, td {
  text-align: left;
  padding: .7rem .85rem;
  border-bottom: 1px solid var(--line);
  font-variant-numeric: tabular-nums;
}
th {
  font-size: .75rem;
  color: var(--muted);
  font-weight: 600;
  background: #f7faf5;
}
tr:last-child td { border-bottom: 0; }
.badge {
  display: inline-block;
  margin-left: .35rem;
  padding: .1rem .45rem;
  border-radius: 999px;
  background: var(--green-bg);
  color: var(--green);
  font-size: .7rem;
  font-weight: 600;
}
.vazio { color: var(--muted); }
.como {
  margin-top: 2.4rem;
  padding-top: 1.6rem;
  border-top: 1px solid var(--line);
}
.como h2 { font-size: 1.1rem; margin: 0 0 .85rem; }
.como h3 { font-size: .95rem; margin: 1.15rem 0 .3rem; }
.como p, .como li { font-size: .92rem; margin: 0 0 .55rem; }
.como ul { margin: 0 0 .55rem; padding-left: 1.15rem; }
.como a { color: var(--green); }
footer { margin-top: 2rem; color: var(--muted); font-size: .85rem; }
footer a { color: var(--green); }
"""


def _card(emoji, rotulo, quando, modelo):
    eng = f"{modelo['engenharia']:.1f}" if modelo.get("engenharia") else "—"
    return (
        '<article class="card">'
        f'<p class="emoji">{emoji}</p>'
        f"<h2>{_esc(rotulo)}</h2>"
        f'<p class="quando">{_esc(quando)}</p>'
        f'<p class="nome">{_esc(modelo["nome"])}</p>'
        "<dl>"
        f"<div><dt>Intel</dt><dd>{modelo['inteligencia']:.1f}</dd></div>"
        f"<div><dt>Eng</dt><dd>{_esc(eng)}</dd></div>"
        f"<div><dt>Custo</dt><dd>{_esc(_fmt_custo(modelo['custo']))}</dd></div>"
        "</dl></article>"
    )


def _tabela(itens, vazio):
    if not itens:
        return f'<p class="vazio">{_esc(vazio)}</p>'
    linhas = [
        '<div class="tabela"><table><thead><tr>'
        "<th>#</th><th>Modelo</th><th>Intel</th><th>Eng</th><th>Custo</th>"
        "</tr></thead><tbody>"
    ]
    for i, m in enumerate(itens, 1):
        eng = f"{m['engenharia']:.1f}" if m.get("engenharia") else "—"
        badge = ' <span class="badge">fronteira</span>' if m.get("fronteira") else ""
        linhas.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{_esc(m['nome'])}{badge}</td>"
            f"<td>{m['inteligencia']:.1f}</td>"
            f"<td>{_esc(eng)}</td>"
            f"<td>{_esc(_fmt_custo(m['custo']))}</td>"
            "</tr>"
        )
    linhas.append("</tbody></table></div>")
    return "".join(linhas)


def _secao(titulo, sub, itens, vazio):
    return (
        f'<h2 class="lista">{_esc(titulo)}</h2>'
        f'<p class="sublista">{_esc(sub)}</p>'
        f"{_tabela(itens, vazio)}"
    )


def _como_funciona(dados):
    corte_intel = dados.get("corte_intel")
    corte_custo = dados.get("corte_custo")
    if corte_intel and corte_custo:
        hoje = (
            f" Hoje isso deu intel ≥ {corte_intel:.0f} e até "
            f"{_esc(_fmt_custo(corte_custo))}."
        )
    else:
        hoje = ""
    return (
        '<section class="como">'
        "<h2>Como isso é determinado</h2>"
        "<h3>De onde vêm os dados</h3>"
        "<p>Nada aqui é chute. Os números vêm da API do "
        '<a href="https://artificialanalysis.ai">Artificial Analysis</a>, '
        "que roda as mesmas provas em vários modelos e publica:</p>"
        "<ul>"
        "<li><b>Intel</b> — Intelligence Index, capacidade geral.</li>"
        "<li><b>Eng</b> — Engineering Index, o recorte de código.</li>"
        "<li><b>Custo</b> — USD pra rodar a prova de inteligência "
        "(cost per task), não o preço por milhão de tokens.</li>"
        "</ul>"
        "<p>Este site só baixa isso de manhã, recorta e escolhe as faixas.</p>"
        "<h3>O quadrante verde</h3>"
        "<p>É a mesma ideia do gráfico do AA: inteligente o bastante "
        "e barato o bastante. A metade de cima do eixo de inteligência "
        "e a metade esquerda do eixo de custo (em escala log)."
        f"{hoje} Modelo caro demais ou fraco demais fica de fora. "
        "Quem está na <b>fronteira</b> é o que ninguém no verde supera "
        "em inteligência pagando menos.</p>"
        "<h3>Simples, média e complexa</h3>"
        "<p>As três dicas saem da fronteira, pensadas pra código:</p>"
        "<ul>"
        "<li><b>Tarefa complexa</b> — o mais inteligente que ainda cabe "
        "no verde (arquitetura, bug difícil, refactor grande).</li>"
        "<li><b>Tarefa média</b> — o próximo dessa lista "
        "(feature, review, PR do dia).</li>"
        "<li><b>Tarefa simples</b> — o mais barato que ainda presta "
        "(script, boilerplate, ajuste rápido).</li>"
        "</ul>"
        "<p>Se o maior Engineering Index do verde for outro modelo, "
        "ele aparece à parte como melhor em engenharia.</p>"
        "<h3>Fora do verde</h3>"
        "<p><b>Caros, mas vale</b> são os da fronteira à direita: "
        "paga mais e ganha inteligência de verdade. Top 3.</p>"
        "<p><b>Caros, nem pensar</b> custam acima do corte e nem estão "
        "na fronteira — existe opção mais barata pelo menos tão "
        "inteligente. Os piores negócios do dia.</p>"
        "</section>"
    )


def formatar_html(dados):
    hoje = _hoje()
    dica = dados.get("dica")
    if not dica:
        meta = f"{_esc(hoje)} · nenhum modelo no quadrante verde"
        corpo = '<p class="vazio">Nenhum modelo no quadrante verde.</p>'
    else:
        meta = (
            f"{_esc(hoje)} · quadrante verde · intel ≥ {dados['corte_intel']:.0f} · "
            f"até {_esc(_fmt_custo(dados['corte_custo']))}"
        )
        cards = [
            _card(emoji, rotulo, quando, dica[chave])
            for chave, emoji, rotulo, quando in _LANES
        ]
        codigo = dica.get("codigo")
        if codigo and not any(_mesmo_modelo(codigo, dica[k]) for k, *_ in _LANES):
            cards.append(_card(
                "🛠️",
                "Melhor em engenharia",
                "Quando o índice de código manda mais que o resto",
                codigo,
            ))
        corpo = (
            f'<section class="cards">{"".join(cards)}</section>'
            + _secao(
                "No verde",
                "Inteligente e barato o bastante pra ser o default.",
                dados.get("verde") or [],
                "Nenhum modelo no quadrante verde.",
            )
            + _secao(
                "Caros, mas vale",
                "Top 3 da fronteira à direita: paga mais, ganha inteligência.",
                dados.get("caros_vale") or [],
                "Nenhum caro na fronteira hoje.",
            )
            + _secao(
                "Caros, nem pensar",
                "Paga mais e não sobe na fronteira. Esquece.",
                dados.get("caros_nao") or [],
                "Nenhum furo caro hoje.",
            )
        )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="pt-BR">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>O que usar hoje pra código</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n<main>\n"
        '<p class="kicker">Pra quem escreve código</p>\n'
        "<h1>O que usar hoje</h1>\n"
        f'<p class="meta">{meta}</p>\n'
        f"{corpo}\n"
        f"{_como_funciona(dados)}\n"
        "<footer>Fonte: "
        '<a href="https://artificialanalysis.ai">Artificial Analysis</a>'
        " · atualizado automaticamente</footer>\n"
        "</main>\n</body>\n</html>\n"
    )


def exportar_pagina(dados, pasta=None):
    pasta = pasta or os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, ".nojekyll"), "w", encoding="utf-8"):
        pass
    caminho = os.path.join(pasta, "index.html")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(formatar_html(dados))
    return caminho


def enviar_telegram(texto):
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise ValueError("Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram {exc.code}: {err}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram recusou: {payload}")


def _modelo(nome, intel, custo, eng=None):
    return {
        "name": nome,
        "evaluations": {
            "artificial_analysis_intelligence_index": intel,
            "artificial_analysis_engineering_index": eng,
        },
        "artificial_analysis_intelligence_index_cost": {
            "cost_per_task": {"total_cost": custo},
        },
    }


def _self_check():
    assert custo_tarefa(_modelo("x", 10, 1.5)) == 1.5
    assert custo_tarefa({
        "artificial_analysis_intelligence_index_cost": {"cost_per_task": {"total_cost": 0}},
    }) is None
    dados = ranquear([
        _modelo("Fable", 64, 30, 90),
        _modelo("Astra", 62, 4, 85),
        _modelo("Sol", 57, 1, 80),
        _modelo("Terra", 51, 0.96, 70),
        _modelo("Hydrogen", 54, 3.2, 75),
        _modelo("Haiku", 38, 0.45, 50),
        _modelo("Ember", 36, 0.15, 40),
        _modelo("Luna", 28, 0.15, 20),
        _modelo("Nemotron", 13, 0.5, 10),
        _modelo("BaratoBurro", 5, 0.05, None),
        {
            "name": "Sem custo",
            "evaluations": {"artificial_analysis_intelligence_index": 99},
        },
    ])
    nomes = [m["nome"] for m in dados["verde"]]
    assert nomes == ["Sol", "Terra", "Haiku", "Ember"]
    assert [m["nome"] for m in dados["caros_vale"]] == ["Fable", "Astra"]
    assert [m["nome"] for m in dados["caros_nao"]] == ["Hydrogen"]
    assert dados["dica"]["pesado"]["nome"] == "Sol"
    assert dados["dica"]["medio"]["nome"] == "Terra"
    assert dados["dica"]["de_boa"]["nome"] == "Ember"
    assert dados["dica"]["codigo"]["nome"] == "Sol"
    assert abs(dados["corte_intel"] - 35) < 1e-9
    assert abs(dados["corte_custo"] - math.sqrt(0.15 * 30)) < 1e-9
    texto = formatar_ranking(dados)
    assert "🟢" in texto
    assert "💪" in texto
    assert "🌱" in texto
    assert "💻" in texto
    assert "Quadrante verde" in texto
    assert "Tarefa complexa" in texto
    assert "Tarefa média" in texto
    assert "Tarefa simples" in texto
    assert "Melhor em engenharia" not in texto
    assert "$1.00/tarefa" in texto
    assert "📈 fronteira" in texto
    assert "Caros, mas vale" in texto
    assert "Caros, nem pensar" in texto
    assert "BaratoBurro" not in texto
    assert "Fable" in texto
    assert "Hydrogen" in texto
    html = formatar_html(dados)
    assert "O que usar hoje" in html
    assert "Pra quem escreve código" in html
    assert "Arquitetura, bug difícil, refactor grande" in html
    assert "Script, boilerplate, ajuste rápido" in html
    assert "Sol" in html
    assert "Terra" in html
    assert "Ember" in html
    assert "Fable" in html
    assert "Astra" in html
    assert "Hydrogen" in html
    assert "Caros, mas vale" in html
    assert "Caros, nem pensar" in html
    assert "BaratoBurro" not in html
    assert "fronteira" in html
    assert "$1.00/tarefa" in html
    assert "nenhum modelo no quadrante verde" in formatar_html({"dica": None})
    assert "Como isso é determinado" in html
    assert "Intelligence Index" in html
    assert "Engineering Index" in html
    assert "cost per task" in html
    assert "intel ≥ 35" in html
    outro = ranquear([
        _modelo("Fable", 64, 30, 90),
        _modelo("Sol", 57, 1, 80),
        _modelo("Terra", 51, 0.96, 70),
        _modelo("Codex", 40, 1.2, 99),
        _modelo("Ember", 36, 0.15, 40),
    ])
    assert outro["dica"]["codigo"]["nome"] == "Codex"
    assert "Melhor em engenharia" in formatar_ranking(outro)
    old = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    try:
        try:
            enviar_telegram("x")
            raise AssertionError("esperava erro sem token")
        except ValueError:
            pass
    finally:
        if old is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = old
    print("self-check ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        _self_check()
    else:
        dados = buscar_melhores_modelos()
        texto = formatar_ranking(dados)
        print(texto)
        exportar_pagina(dados)
        enviar_telegram(texto)
