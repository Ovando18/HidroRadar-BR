"""
Enriquecimento cadastral sob demanda via BrasilAPI (``/api/cnpj/v1/{cnpj}``).

A consulta é feita **apenas** para a usina selecionada pelo usuário (clique no
mapa ou seleção na ficha) — nunca em lote. Respostas bem-sucedidas ficam em
cache por 7 dias; erros **não** são cacheados (exceções não entram no cache do
Streamlit), de modo que falhas temporárias podem ser tentadas novamente.
"""

from __future__ import annotations

import re
from typing import Any

import requests
import streamlit as st

BRASILAPI_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
TIMEOUT_S = 5
USER_AGENT = "HidroRadar-BR/1.0"


class CNPJLookupError(Exception):
    """Erro amigável de consulta de CNPJ."""


def clean_cnpj(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def format_cnpj(value: Any) -> str:
    d = clean_cnpj(value)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else str(value or "")


def is_valid_cnpj(value: Any) -> bool:
    """Valida os dígitos verificadores do CNPJ."""
    d = clean_cnpj(value)
    if len(d) != 14 or d == d[0] * 14:
        return False

    def _dv(base: str) -> str:
        weights = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-len(base):]
        rest = sum(int(n) * w for n, w in zip(base, weights)) % 11
        return "0" if rest < 2 else str(11 - rest)

    return d[12] == _dv(d[:12]) and d[13] == _dv(d[:13])


def _format_phone(raw: Any) -> str | None:
    d = re.sub(r"\D", "", str(raw or ""))
    if len(d) < 10:
        return None
    ddd, num = d[:2], d[2:]
    return f"({ddd}) {num[:-4]}-{num[-4:]}"


def _format_cep(raw: Any) -> str:
    d = re.sub(r"\D", "", str(raw or ""))
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else str(raw or "")


def _txt(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@st.cache_data(ttl=604800, show_spinner=False)
def _fetch_cnpj(cnpj: str) -> dict:
    """Chamada HTTP cacheada (7 dias). Lança ``CNPJLookupError`` em caso de falha."""
    try:
        resp = requests.get(
            BRASILAPI_URL.format(cnpj=cnpj), timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
    except requests.Timeout as exc:
        raise CNPJLookupError("A BrasilAPI não respondeu em 5 s. Tente novamente em instantes.") from exc
    except requests.RequestException as exc:
        raise CNPJLookupError("Não foi possível conectar à BrasilAPI.") from exc

    if resp.status_code == 404:
        raise CNPJLookupError("CNPJ não encontrado na base da Receita Federal (BrasilAPI).")
    if resp.status_code == 429:
        raise CNPJLookupError("Limite de consultas da BrasilAPI atingido. Aguarde alguns segundos e tente novamente.")
    if resp.status_code >= 500:
        raise CNPJLookupError(f"BrasilAPI temporariamente indisponível (HTTP {resp.status_code}).")
    if not resp.ok:
        raise CNPJLookupError(f"Consulta recusada pela BrasilAPI (HTTP {resp.status_code}).")
    try:
        return resp.json()
    except ValueError as exc:
        raise CNPJLookupError("Resposta inválida da BrasilAPI.") from exc


def parse_brasilapi(data: dict) -> dict:
    """Converte o JSON da BrasilAPI no formato exibido pela ficha."""
    tipo_log = _txt(data.get("descricao_tipo_de_logradouro"))
    logradouro = _txt(data.get("logradouro"))
    if tipo_log and not logradouro.upper().startswith(tipo_log.upper()):
        logradouro = f"{tipo_log} {logradouro}"
    numero = _txt(data.get("numero"))
    complemento = _txt(data.get("complemento"))
    endereco = ", ".join(p for p in (logradouro, numero) if p)
    if complemento:
        endereco = f"{endereco} – {complemento}"

    telefones = [t for t in (_format_phone(data.get("ddd_telefone_1")), _format_phone(data.get("ddd_telefone_2"))) if t]
    socios = [
        {"nome": _txt(s.get("nome_socio")), "qualificacao": _txt(s.get("qualificacao_socio"))}
        for s in (data.get("qsa") or [])
        if s.get("nome_socio")
    ]
    municipio, uf = _txt(data.get("municipio")), _txt(data.get("uf"))
    return {
        "ok": True,
        "erro": None,
        "cnpj": format_cnpj(data.get("cnpj")),
        "razao_social": _txt(data.get("razao_social")) or "—",
        "nome_fantasia": _txt(data.get("nome_fantasia")) or "—",
        "situacao": _txt(data.get("descricao_situacao_cadastral")) or "—",
        "data_situacao": _txt(data.get("data_situacao_cadastral")),
        "logradouro": endereco or "—",
        "bairro": _txt(data.get("bairro")) or "—",
        "municipio_uf": f"{municipio}/{uf}" if municipio and uf else (municipio or uf or "—"),
        "cep": _format_cep(data.get("cep")) or "—",
        "telefones": telefones,
        "email": _txt(data.get("email")).lower() or None,
        "atividade": _txt(data.get("cnae_fiscal_descricao")),
        "socios": socios[:10],
        "socios_total": len(socios),
    }


def consultar_cnpj(cnpj: Any) -> dict:
    """
    Consulta segura: nunca lança exceção. Retorna ``{"ok": False, "erro": "..."}``
    em caso de CNPJ inválido ou falha na API.
    """
    digits = clean_cnpj(cnpj)
    if not is_valid_cnpj(digits):
        return {"ok": False, "erro": "CNPJ inválido ou ausente.", "cnpj": format_cnpj(cnpj)}
    try:
        return parse_brasilapi(_fetch_cnpj(digits))
    except CNPJLookupError as exc:
        return {"ok": False, "erro": str(exc), "cnpj": format_cnpj(digits)}
