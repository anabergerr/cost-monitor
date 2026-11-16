import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

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


def ranquear(modelos, top_n=TOP_N, max_preco=MAX_PRECO):
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
    ranking = sorted(validos, key=lambda x: x["score"], reverse=True)
    return ranking[:top_n]


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


def formatar_ranking(top):
    linhas = ["Top modelos de IA por custo-benefício hoje:"]
    for i, m in enumerate(top, 1):
        linhas.append(
            f"{i}. {m['nome']} - Inteligência: {m['inteligencia']:.1f} | "
            f"Preço: ${m['preco']:.2f}/1M | Razão: {m['score']:.2f}"
        )
    return "\n".join(linhas)


def enviar_telegram(texto):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise ValueError("Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": texto}).encode()
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
    ranking = ranquear([
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
    ])
    assert [m["nome"] for m in ranking] == ["Barato", "Caro"]
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
