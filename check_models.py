import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_KEY = os.getenv("ARTIFICIAL_ANALYSIS_API_KEY")
URL = "https://artificialanalysis.ai/api/v2/language/models/free"
TOP_N = 5
MAX_PRECO = None  # Ex.: 5.0 para filtrar modelos até $5 por 1M de tokens (blend 3:1)


def preco_blended(pricing):
    if not pricing:
        return None
    blended = pricing.get("price_1m_blended_3_to_1")
    if blended:
        return float(blended)
    inp = pricing.get("price_1m_input_tokens")
    out = pricing.get("price_1m_output_tokens")
    if inp is None or out is None:
        return None
    return (3 * float(inp) + float(out)) / 4


def modelos_validos(modelos, max_preco=MAX_PRECO):
    validos = []
    for m in modelos:
        inteligencia = (m.get("evaluations") or {}).get(
            "artificial_analysis_intelligence_index"
        )
        preco = preco_blended(m.get("pricing") or {})
        if not inteligencia or not preco or preco <= 0:
            continue
        if max_preco is not None and preco > max_preco:
            continue
        validos.append({
            "nome": m.get("name", "Desconhecido"),
            "inteligencia": float(inteligencia),
            "preco": preco,
            "score": float(inteligencia) / preco,
        })
    return validos


def recomendar(validos):
    if not validos:
        return None
    return {
        "pesado": max(validos, key=lambda m: (m["inteligencia"], -m["preco"])),
        "medio": max(validos, key=lambda m: m["score"]),
        "de_boa": min(validos, key=lambda m: (m["preco"], -m["inteligencia"])),
    }


def ranquear(modelos, top_n=TOP_N, max_preco=MAX_PRECO):
    validos = modelos_validos(modelos, max_preco)
    return {
        "baratos": sorted(validos, key=lambda x: x["preco"])[:top_n],
        "custo_beneficio": sorted(validos, key=lambda x: x["score"], reverse=True)[:top_n],
        "dica": recomendar(validos),
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


def _linha_dica(rotulo, modelo):
    return (
        f"{rotulo} → <b>{_esc(modelo['nome'])}</b>"
        f"  <i>${modelo['preco']:.2f}/1M</i>"
    )


def _nomes(itens):
    if not itens:
        return "<i>nenhum hoje</i>"
    return " · ".join(
        f"{_esc(m['nome'])} (${m['preco']:.2f})" for m in itens[:3]
    )


def formatar_ranking(dados):
    hoje = datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y")
    dica = dados.get("dica")
    if not dica:
        return f"<b>O que usar hoje</b> · {_esc(hoje)}\n\nNenhum modelo hoje."
    return "\n".join([
        f"<b>O que usar hoje</b> · {_esc(hoje)}",
        "",
        _linha_dica("Pesada, muito contexto", dica["pesado"]),
        _linha_dica("Tarefa média", dica["medio"]),
        _linha_dica("De boa", dica["de_boa"]),
        "",
        "<b>Mais baratas</b>",
        _nomes(dados["baratos"]),
        "",
        "<b>Melhor custo-benefício</b>",
        _nomes(dados["custo_beneficio"]),
    ])


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


def _self_check():
    assert abs(preco_blended({
        "price_1m_input_tokens": 1.4,
        "price_1m_output_tokens": 4.4,
    }) - 2.15) < 1e-9
    assert preco_blended({"price_1m_blended_3_to_1": 2.15}) == 2.15
    dados = ranquear([
        {
            "name": "Caro",
            "evaluations": {"artificial_analysis_intelligence_index": 50},
            "pricing": {"price_1m_input_tokens": 10, "price_1m_output_tokens": 10},
        },
        {
            "name": "Barato",
            "evaluations": {"artificial_analysis_intelligence_index": 50},
            "pricing": {"price_1m_input_tokens": 1, "price_1m_output_tokens": 1},
        },
        {
            "name": "Sem preco",
            "evaluations": {"artificial_analysis_intelligence_index": 99},
            "pricing": {"price_1m_input_tokens": 0, "price_1m_output_tokens": 0},
        },
        {
            "name": "Superbarato",
            "evaluations": {"artificial_analysis_intelligence_index": 5},
            "pricing": {"price_1m_input_tokens": 0.1, "price_1m_output_tokens": 0.1},
        },
    ])
    assert [m["nome"] for m in dados["baratos"]] == ["Superbarato", "Barato", "Caro"]
    assert dados["dica"]["pesado"]["nome"] == "Barato"
    assert dados["dica"]["de_boa"]["nome"] == "Superbarato"
    assert dados["dica"]["medio"]["nome"] in ("Barato", "Superbarato")
    texto = formatar_ranking(dados)
    assert "muito contexto" in texto
    assert "De boa" in texto
    assert "Mais baratas" in texto
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
