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
        }
    q = quadrante_verde(validos)
    return {
        "verde": q["verde"][:top_n],
        "fronteira": q["fronteira"],
        "dica": recomendar(q["verde"], q["fronteira"]),
        "corte_intel": q["corte_intel"],
        "corte_custo": q["corte_custo"],
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


def formatar_ranking(dados):
    hoje = datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y")
    dica = dados.get("dica")
    if not dica:
        return f"🟢 <b>O que usar hoje</b> · {_esc(hoje)}\n\nNenhum modelo no quadrante verde."
    corte_intel = dados["corte_intel"]
    corte_custo = dados["corte_custo"]
    linhas = [
        f"🟢 <b>O que usar hoje</b> · {_esc(hoje)}",
        (
            f"<i>Quadrante verde · intel ≥ {corte_intel:.0f} · "
            f"até {_fmt_custo(corte_custo)}</i>"
        ),
        "",
        _linha_dica("💪", "Pesada, ainda no verde", dica["pesado"]),
        _linha_dica("⚖️", "Tarefa média", dica["medio"]),
        _linha_dica("🌱", "De boa", dica["de_boa"]),
    ]
    if dica.get("codigo"):
        linhas.append(_linha_dica("💻", "Pra código", dica["codigo"]))
    linhas.extend(["", "🟢 <b>No verde</b>"])
    linhas.extend(_bloco(dados["verde"]))
    return "\n".join(linhas)


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
    assert "ainda no verde" in texto
    assert "$1.00/tarefa" in texto
    assert "📈 fronteira" in texto
    assert "BaratoBurro" not in texto
    assert "Fable" not in texto
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
        texto = formatar_ranking(buscar_melhores_modelos())
        print(texto)
        enviar_telegram(texto)
