"""
Pipeline de ingestão e tratamento dos dados abertos da ANEEL.

Fluxo
-----
1. **SIGA** (Sistema de Informações de Geração da ANEEL): localiza via API CKAN
   o CSV mais recente de empreendimentos de geração, filtra UHE/PCH/CGH e trata
   coordenadas, potências, datas e municípios.
2. **Base hidrográfica** (Aproveitamentos Hidrelétricos): bacia, sub-bacia e rio,
   unidos por ``CodCEG`` (left join).
3. **Agentes de geração** (relação CEG x CNPJ x Agente): agrupados por usina,
   com destaque para o agente majoritário. Na ausência do dataset, os agentes
   são extraídos do campo ``DscPropriRegimePariticipacao`` do SIGA.

Resiliência
-----------
* As URLs dos recursos são descobertas dinamicamente via ``package_show`` /
  ``package_search`` — mudanças no hash da URL do recurso não quebram o app.
* Os nomes de colunas são resolvidos por *aliases* (sem acento/caixa), tolerando
  pequenas variações de layout entre versões dos datasets.
* Cada base tem *fallback* local: ``data/snapshots/`` (gerado pelo próprio app
  ou via ``python -m src.data_pipeline --snapshot``) e ``data/<base>.csv``
  (arquivo manual opcional). Como último recurso, o SIGA usa uma amostra de
  demonstração em ``data/sample/``, sinalizada na interface.
* URLs podem ser fixadas por variáveis de ambiente ou ``st.secrets``:
  ``HIDRORADAR_SIGA_CSV_URL``, ``HIDRORADAR_HIDRO_CSV_URL`` e
  ``HIDRORADAR_AGENTES_CSV_URL``. ``HIDRORADAR_OFFLINE=1`` ignora a rede.
"""

from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests
import streamlit as st

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
CKAN_BASE = "https://dadosabertos.aneel.gov.br/api/3/action"
SIGA_PACKAGE_ID = "siga-sistema-de-informacoes-de-geracao-da-aneel"
SIGA_RESOURCE_HINTS = ("siga-empreendimentos-geracao-diario", "siga-empreendimentos-geracao")

HIDRO_PACKAGE_IDS = (
    "aproveitamentos-hidreletricos",
    "aproveitamentos-hidreletricos-inventariados",
    "inventario-hidreletrico",
    "usinas-hidreletricas",
)
HIDRO_SEARCH_TERMS = ("aproveitamentos hidreletricos", "inventario hidreletrico", "usinas hidreletricas bacia rio")
HIDRO_RESOURCE_HINTS = ("aproveitamento", "hidrel", "inventario", "usina")

AGENTES_PACKAGE_IDS = (
    "agentes-de-geracao-de-energia-eletrica",
    "relacao-de-agentes-de-geracao",
    "agentes-de-geracao",
)
AGENTES_SEARCH_TERMS = ("agentes de geracao de energia eletrica", "agentes geracao cnpj")
AGENTES_RESOURCE_HINTS = ("agente", "proprietario", "cnpj")

TIPOS_HIDRO = ("UHE", "PCH", "CGH")
NAO_INFORMADO = "Não informado"

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
SAMPLE_DIR = DATA_DIR / "sample"

HTTP_TIMEOUT = (8, 90)  # (conexão, leitura) em segundos
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
MAX_CANDIDATE_DOWNLOADS = 4
USER_AGENT = "HidroRadar-BR/1.0 (+https://github.com) python-requests"

UFS_BR = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)

# Limites aproximados do território brasileiro (com pequena margem).
LAT_MIN, LAT_MAX = -34.0, 5.5
LON_MIN, LON_MAX = -74.2, -28.6

# Codificação de bacias da ANEEL/DNAEE: 1º dígito do código da sub-bacia.
BACIAS_ANEEL = {
    "1": "Rio Amazonas",
    "2": "Rio Tocantins",
    "3": "Atlântico Norte/Nordeste",
    "4": "Rio São Francisco",
    "5": "Atlântico Leste",
    "6": "Rio Paraná",
    "7": "Rio Uruguai",
    "8": "Atlântico Sudeste",
}

# Aliases de colunas (o nome canônico é sempre testado primeiro).
SIGA_ALIASES: dict[str, list[str]] = {
    "CodCEG": ["CEG", "CodigoCEG", "IdeCEG"],
    "NomEmpreendimento": ["NomUsina", "NomeEmpreendimento", "Empreendimento"],
    "SigTipoGeracao": ["TipoGeracao", "SigTipo"],
    "DscFaseUsina": ["FaseUsina", "DscFase", "Fase"],
    "SigUFPrincipal": ["SigUF", "UF", "UFPrincipal"],
    "DscMuninicpios": ["DscMunicipios", "Municipios", "DscMunicipio", "NomMunicipio"],
    "MdaPotenciaOutorgadaKw": ["MdaPotenciaOutorgadaKW", "PotenciaOutorgadaKw", "PotenciaOutorgada"],
    "MdaPotenciaFiscalizadaKw": ["MdaPotenciaFiscalizadaKW", "PotenciaFiscalizadaKw", "PotenciaFiscalizada"],
    "DscPropriRegimePariticipacao": ["DscPropriRegimeParticipacao", "DscProprietario", "Proprietarios"],
    "DatEntradaOperacao": ["DataEntradaOperacao", "DatOperacao"],
    "NumCoordNorteEmpreendimento": ["NumCoordNEmpreendimento", "Latitude", "NumLatitude", "CoordN"],
    "NumCoordLesteEmpreendimento": ["NumCoordEEmpreendimento", "Longitude", "NumLongitude", "CoordE"],
    "DscSubBacia": ["SubBacia", "NomSubBacia"],
    "DatGeracaoConjuntoDados": ["DataGeracaoConjuntoDados", "DatGeracao"],
}
SIGA_REQUIRED = ("CodCEG", "NomEmpreendimento", "SigTipoGeracao")

HIDRO_ALIASES: dict[str, list[str]] = {
    "CodCEG": ["CEG", "CodigoCEG", "IdeCEG", "CodCEGUsina"],
    "NomBacia": ["Bacia", "DscBacia", "NomeBacia", "NomBaciaHidrografica", "BaciaHidrografica"],
    "NomSubBacia": ["SubBacia", "DscSubBacia", "NomeSubBacia", "NomSubBaciaHidrografica"],
    "NomRio": ["Rio", "DscRio", "NomeRio", "NomCursoDagua", "DscCursoDagua", "CursoDagua", "NomCorpoHidrico"],
}

AGENTES_ALIASES: dict[str, list[str]] = {
    "CodCEG": ["CEG", "CodigoCEG", "IdeCEG", "CodCEGUsina"],
    "NumCNPJ": ["CNPJ", "NumCpfCnpj", "NumCPFCNPJ", "NumCNPJAgente", "CNPJAgente", "NumCnpjEmpresa", "NumCNPJEmpresa"],
    "NomAgente": [
        "NomAgenteProprietario", "NomRazaoSocial", "RazaoSocial", "NomEmpresa",
        "NomProprietario", "NomTitular", "Agente", "NomAgenteGeracao",
    ],
    "PctParticipacao": [
        "PerParticipacao", "MdaPercentualParticipacao", "PercentualParticipacao",
        "VlrPercentualParticipacao", "MdaPctParticipacao", "Participacao", "PctParticipacaoAgente",
    ],
}


# --------------------------------------------------------------------------- #
# Utilitários de texto e números
# --------------------------------------------------------------------------- #
def _norm(text: Any) -> str:
    """Normaliza para comparação: sem acento, minúsculo, apenas [a-z0-9]."""
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def normalize_search_text(text: Any) -> str:
    """Normaliza texto livre para busca (sem acento, minúsculo, espaços simples)."""
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.lower()).strip()


_LOWER_WORDS = {"de", "da", "do", "das", "dos", "e", "d'", "del", "la"}


def clean_name(value: Any, *, fill: str | None = NAO_INFORMADO) -> str | None:
    """Remove espaços extras e converte textos em CAIXA ALTA para Título."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return fill
    s = re.sub(r"\s+", " ", str(value)).strip(" -;,")
    if not s or s.lower() in {"nan", "none", "null", "-", "n/a", "na", "nao informado", "não informado"}:
        return fill
    if s.isupper() and len(s) > 3:
        words = s.lower().split(" ")
        s = " ".join(
            w if (i > 0 and w in _LOWER_WORDS)
            else w.upper() if (("." in w or "/" in w) and len(w) <= 6)  # siglas como "S.A." e "S/A"
            else w[:1].upper() + w[1:]
            for i, w in enumerate(words)
        )
    return s


def parse_number(value: Any) -> float:
    """Converte números em formato brasileiro ('1.234,56') ou internacional."""
    if value is None or value is pd.NA:
        return np.nan
    if isinstance(value, (int, float, np.number)):
        return float(value)
    s = re.sub(r"[\s ]", "", str(value))
    if not s or s.lower() in {"nan", "none", "null", "-"}:
        return np.nan
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    s = re.sub(r"[^0-9eE+\-.]", "", s)
    try:
        return float(s)
    except ValueError:
        return np.nan


_DMS_RE = re.compile(
    r"^\s*(?P<sign>-)?(?P<deg>\d{1,3})\s*[°º:\s]\s*(?P<min>\d{1,2})\s*['’′:\s]\s*"
    r"(?P<sec>\d{1,2}(?:[.,]\d+)?)?\s*[\"”″]?\s*(?P<hem>[NSEWLO])?\s*$",
    re.IGNORECASE,
)


def parse_coordinate(value: Any) -> float:
    """Aceita graus decimais (com vírgula ou ponto) ou graus/minutos/segundos."""
    if value is None or value is pd.NA:
        return np.nan
    s = str(value).strip()
    m = _DMS_RE.match(s)
    if m and not re.fullmatch(r"-?\d+(?:[.,]\d+)?", s):
        deg = float(m["deg"])
        minutes = float(m["min"] or 0)
        sec = float((m["sec"] or "0").replace(",", "."))
        dec = deg + minutes / 60 + sec / 3600
        if m["sign"] or (m["hem"] and m["hem"].upper() in {"S", "W", "O"}):
            dec = -dec
        return dec
    return parse_number(s)


def validate_coordinates(lat: float, lon: float) -> tuple[float, float, str]:
    """Valida (e, quando seguro, corrige o sinal de) coordenadas no Brasil."""
    if pd.isna(lat) or pd.isna(lon) or (lat == 0 and lon == 0):
        return np.nan, np.nan, "Ausente"
    status = "Válida"
    # Sinal negativo omitido na longitude (todo o Brasil está a oeste de Greenwich).
    if LON_MIN <= -lon <= LON_MAX:
        lon, status = -lon, "Corrigida (sinal)"
    # Latitude sul sem sinal, apenas fora da faixa que seria válida no hemisfério norte.
    if lat > LAT_MAX and LAT_MIN <= -lat <= LAT_MAX:
        lat, status = -lat, "Corrigida (sinal)"
    if LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX:
        return float(lat), float(lon), status
    return np.nan, np.nan, "Fora do território"


_CEG_CORE_RE = re.compile(r"([A-Z]{3})\.?([A-Z]{2})\.?([A-Z]{2})\.?(\d{6})-?(\d)")


def ceg_key(value: Any) -> str:
    """Chave de junção para o CEG, ignorando pontuação e o sufixo de versão (.01)."""
    if value is None or value is pd.NA:
        return ""
    s = str(value).upper().strip()
    m = _CEG_CORE_RE.search(s)
    if m:
        return "".join(m.groups())
    return re.sub(r"[^A-Z0-9]", "", s)


def only_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def format_cnpj(value: Any) -> str:
    d = only_digits(value)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return str(value or "").strip()


def format_power(mw: float) -> str:
    """Formata potência em MW, ou GW acima de 1.000 MW (padrão pt-BR)."""
    if mw is None or pd.isna(mw):
        return "—"
    if abs(mw) >= 1000:
        return _br(mw / 1000, 2) + " GW"
    return _br(mw, 1 if abs(mw) >= 100 else 2) + " MW"


def _br(x: float, decimals: int = 2) -> str:
    return f"{x:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------------------------- #
# Municípios e agentes
# --------------------------------------------------------------------------- #
_MUN_UF_RE = re.compile(r"^(?P<nome>.+?)\s*(?:[-–(]\s*|\s)(?P<uf>[A-Za-z]{2})\)?$")


def parse_municipios(value: Any, uf_principal: str | None) -> list[str]:
    """
    Converte ``"Cidade A - MG, Cidade B - SP"`` (ou ``"Cidade A/MG; Cidade B/SP"``)
    em ``["Cidade A - MG", "Cidade B - SP"]``. Usa a UF principal quando ausente.
    """
    uf_principal = (uf_principal or "").upper().strip() or None
    if value is None or value is pd.NA or not str(value).strip() or str(value).lower() == "nan":
        return []
    result: list[str] = []
    for token in re.split(r"[;,|]|\s+e\s+(?=[A-ZÀ-Ú])", str(value)):
        pieces = [p.strip() for p in token.split("/") if p.strip()]
        merged: list[str] = []
        for p in pieces:  # "Cidade/MG" → "Cidade - MG"
            if p.upper() in UFS_BR and merged and not _MUN_UF_RE.match(merged[-1].strip()):
                merged[-1] = f"{merged[-1]} - {p.upper()}"
            else:
                merged.append(p)
        for piece in merged:
            nome, uf = piece, None
            m = _MUN_UF_RE.match(piece)
            if m and m["uf"].upper() in UFS_BR:
                nome, uf = m["nome"], m["uf"].upper()
            nome = clean_name(nome, fill=None)
            if not nome:
                continue
            uf = uf or uf_principal
            label = f"{nome} - {uf}" if uf else nome
            if label not in result:
                result.append(label)
    return result


_CNPJ_RE = re.compile(r"\d{2}\.?\d{3}\.?\d{3}\s*/?\s*\d{4}\s*-?\s*\d{2}")
_PCT_FIRST_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%\s*(?:para|p/|-|:)?\s*(.+?)(?=\s*(?:;|,?\s*\d{1,3}(?:[.,]\d+)?\s*%)|$)", re.I)
_PCT_LAST_RE = re.compile(r"([^;()%]+?)\s*\(\s*(\d{1,3}(?:[.,]\d+)?)\s*%\s*\)")
_REGIME_RE = re.compile(r"\s*\((PIE|APE|SP|APE-COM|COM|REG|PIE/APE|AUTOPRODUTOR)\)\s*$", re.I)


def parse_proprietarios(value: Any) -> list[dict]:
    """Extrai agentes do campo textual ``DscPropriRegimePariticipacao`` do SIGA."""
    if value is None or value is pd.NA or not str(value).strip() or str(value).lower() == "nan":
        return []
    text = re.sub(r"\s+", " ", str(value)).strip()
    # "Empresa X (60%)" tem prioridade; senão "60% para Empresa X".
    pairs: list[tuple[str, float]] = [(n, parse_number(p)) for n, p in _PCT_LAST_RE.findall(text)]
    if not pairs:
        pairs = [(n, parse_number(p)) for p, n in _PCT_FIRST_RE.findall(text)]
    if not pairs:
        pairs = [(part, np.nan) for part in re.split(r";", text) if part.strip()]
    agentes = []
    for nome, pct in pairs:
        cnpj_match = _CNPJ_RE.search(nome)
        cnpj = only_digits(cnpj_match.group()) if cnpj_match else ""
        nome = _CNPJ_RE.sub("", nome)
        nome = re.sub(r"\(\s*(CNPJ)?\s*:?\s*\)", "", nome, flags=re.I)
        nome = _REGIME_RE.sub("", nome).strip(" -;,")
        nome = clean_name(nome, fill=None)
        if nome:
            agentes.append({"nome": nome, "cnpj": cnpj, "pct": pct})
    return agentes


def _summarize_agents(agentes: list[dict]) -> dict:
    agentes = sorted(agentes, key=lambda a: (-(a["pct"] if pd.notna(a["pct"]) else -1), a["nome"]))
    principal = agentes[0] if agentes else None

    def _fmt(a: dict) -> str:
        return f"{a['nome']} ({_br(a['pct'], 2).rstrip('0').rstrip(',')}%)" if pd.notna(a["pct"]) else a["nome"]

    return {
        "Agentes_Lista": agentes,
        "Agente_Principal": principal["nome"] if principal else NAO_INFORMADO,
        "CNPJ_Principal": format_cnpj(principal["cnpj"]) if principal and principal["cnpj"] else "",
        "Agentes_Resumo": "; ".join(_fmt(a) for a in agentes) if agentes else NAO_INFORMADO,
        "CNPJs_Todos": "; ".join(format_cnpj(a["cnpj"]) for a in agentes if a["cnpj"]),
        "Qtd_Agentes": len(agentes),
    }


# --------------------------------------------------------------------------- #
# Acesso à API CKAN e leitura de CSV
# --------------------------------------------------------------------------- #
@dataclass
class _NetState:
    """Disjuntor simples: se o portal cair, não insiste nas demais chamadas."""

    api_down: bool = False
    offline: bool = False
    avisos: list[str] = field(default_factory=list)


def _config(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value.strip()
    try:
        value = st.secrets.get(name)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - st.secrets lança se não houver secrets.toml
        value = None
    return str(value).strip() if value else None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/csv, */*"})
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(
            total=2, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",)
        )
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _ckan(session: requests.Session, net: _NetState, action: str, **params: Any) -> Any:
    if net.offline or net.api_down:
        return None
    try:
        r = session.get(f"{CKAN_BASE}/{action}", params=params, timeout=HTTP_TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        payload = r.json()
        return payload.get("result") if payload.get("success") else None
    except (requests.ConnectionError, requests.Timeout) as exc:
        net.api_down = True
        net.avisos.append(f"Portal de Dados Abertos da ANEEL indisponível ({type(exc).__name__}).")
    except (requests.RequestException, ValueError) as exc:
        logger.warning("CKAN %s falhou: %s", action, exc)
        if getattr(getattr(exc, "response", None), "status_code", 0) in (401, 403):
            net.api_down = True
            net.avisos.append("Acesso ao Portal de Dados Abertos da ANEEL negado (HTTP 403).")
    return None


def _csv_resources(package: dict, hints: Iterable[str]) -> list[dict]:
    """Recursos CSV do pacote, ordenados por aderência às dicas e data de modificação."""
    hints = [h.lower() for h in hints]
    resources = []
    for res in package.get("resources") or []:
        fmt = (res.get("format") or "").lower()
        url = res.get("url") or ""
        if "csv" not in fmt and not url.lower().split("?")[0].endswith(".csv"):
            continue
        size = res.get("size") or 0
        if isinstance(size, (int, float)) and size > MAX_DOWNLOAD_BYTES:
            continue
        label = f"{res.get('name', '')} {url}".lower()
        rank = next((i for i, h in enumerate(hints) if h in label), len(hints))
        modified = res.get("last_modified") or res.get("metadata_modified") or res.get("created") or ""
        resources.append((rank, _neg_str(modified), res))
    resources.sort(key=lambda t: (t[0], t[1]))
    return [r for _, _, r in resources]


def _neg_str(s: str) -> tuple:
    """Permite ordenar strings ISO de forma decrescente dentro de uma tupla."""
    return tuple(-ord(c) for c in s)


def read_csv_bytes(content: bytes) -> pd.DataFrame:
    """Lê CSV da ANEEL detectando encoding (utf-8-sig/latin1) e separador."""
    for encoding in ("utf-8-sig", "latin1"):
        try:
            head = content[:20000].decode(encoding)
        except UnicodeDecodeError:
            continue
        first_line = head.splitlines()[0] if head else ""
        sep = max((";", ",", "\t", "|"), key=first_line.count)
        try:
            return pd.read_csv(
                io.BytesIO(content), sep=sep, encoding=encoding, dtype=str,
                keep_default_na=False, na_values=[""], on_bad_lines="skip", engine="python",
            )
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    raise ValueError("Não foi possível interpretar o CSV (encoding/separador desconhecido).")


def _download_csv(session: requests.Session, net: _NetState, url: str) -> pd.DataFrame | None:
    if net.offline:
        return None
    try:
        with session.get(url, timeout=HTTP_TIMEOUT, stream=True) as r:
            r.raise_for_status()
            buf = io.BytesIO()
            for chunk in r.iter_content(chunk_size=1 << 20):
                buf.write(chunk)
                if buf.tell() > MAX_DOWNLOAD_BYTES:
                    raise ValueError("arquivo acima do limite de tamanho")
        return read_csv_bytes(buf.getvalue())
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Download falhou (%s): %s", url, exc)
        net.avisos.append(f"Falha ao baixar recurso da ANEEL: {type(exc).__name__}.")
        return None


def map_columns(df: pd.DataFrame, aliases: dict[str, list[str]]) -> pd.DataFrame:
    """Renomeia colunas para os nomes canônicos usando correspondência tolerante."""
    lookup = {_norm(c): c for c in df.columns}
    rename: dict[str, str] = {}
    for canon, names in aliases.items():
        for name in (canon, *names):
            src = lookup.get(_norm(name))
            if src is not None and src not in rename and canon not in rename.values():
                rename[src] = canon
                break
    return df.rename(columns=rename)


def _discover_dataset(
    session: requests.Session,
    net: _NetState,
    *,
    env_var: str,
    package_ids: Iterable[str],
    search_terms: Iterable[str],
    hints: Iterable[str],
    aliases: dict[str, list[str]],
    accept: callable,
) -> tuple[pd.DataFrame | None, str | None]:
    """Procura no CKAN um recurso CSV cujas colunas satisfaçam ``accept``."""
    override = _config(env_var)
    if override:
        df = _download_csv(session, net, override)
        if df is not None:
            df = map_columns(df, aliases)
            if accept(df):
                return df, override

    packages: list[dict] = []
    seen: set[str] = set()
    for pid in package_ids:
        pkg = _ckan(session, net, "package_show", id=pid)
        if pkg and pkg.get("id") not in seen:
            seen.add(pkg["id"])
            packages.append(pkg)
    if not packages:
        for term in search_terms:
            res = _ckan(session, net, "package_search", q=term, rows=6)
            for pkg in (res or {}).get("results", []):
                if pkg.get("id") not in seen:
                    seen.add(pkg["id"])
                    packages.append(pkg)
            if packages:
                break

    attempts = 0
    for pkg in packages:
        for res in _csv_resources(pkg, hints):
            if attempts >= MAX_CANDIDATE_DOWNLOADS or net.api_down:
                return None, None
            attempts += 1
            df = _download_csv(session, net, res["url"])
            if df is None:
                continue
            df = map_columns(df, aliases)
            if accept(df):
                return df, res["url"]
    return None, None


# --------------------------------------------------------------------------- #
# Fallback local
# --------------------------------------------------------------------------- #
def _read_local(names: Iterable[str]) -> tuple[pd.DataFrame | None, str | None]:
    for name in names:
        path = DATA_DIR / name
        if path.exists() and path.stat().st_size > 0:
            try:
                return read_csv_bytes(_read_maybe_gzip(path)), _display_path(path)
            except (OSError, ValueError) as exc:
                logger.warning("Falha ao ler fallback %s: %s", path, exc)
    return None, None


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return path.name


def _read_maybe_gzip(path: Path) -> bytes:
    import gzip

    raw = path.read_bytes()
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def _write_snapshot(df: pd.DataFrame, name: str) -> None:
    """Grava snapshot comprimido para servir de fallback em futuras falhas da API."""
    try:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(SNAPSHOT_DIR / name, sep=";", index=False, encoding="utf-8", compression="gzip")
    except OSError as exc:  # sistema de arquivos somente leitura, etc.
        logger.info("Snapshot %s não gravado: %s", name, exc)


# --------------------------------------------------------------------------- #
# Carga de cada base
# --------------------------------------------------------------------------- #
def _load_siga(session: requests.Session, net: _NetState) -> tuple[pd.DataFrame, dict]:
    info: dict[str, Any] = {"fonte": None, "url": None, "modificado": None, "demo": False}
    df: pd.DataFrame | None = None

    override = _config("HIDRORADAR_SIGA_CSV_URL")
    if override:
        df = _download_csv(session, net, override)
        info.update(fonte="ANEEL (URL configurada)", url=override)

    if df is None:
        pkg = _ckan(session, net, "package_show", id=SIGA_PACKAGE_ID)
        if pkg:
            for res in _csv_resources(pkg, SIGA_RESOURCE_HINTS)[:2]:
                df = _download_csv(session, net, res["url"])
                if df is not None:
                    info.update(
                        fonte="API ANEEL (SIGA)", url=res["url"],
                        modificado=res.get("last_modified") or res.get("metadata_modified"),
                    )
                    break

    if df is not None:
        df = map_columns(df, SIGA_ALIASES)
        if not all(c in df.columns for c in SIGA_REQUIRED):
            net.avisos.append("Layout do CSV do SIGA não reconhecido; usando fallback local.")
            df = None
        else:
            df = df[df["SigTipoGeracao"].astype(str).str.strip().str.upper().isin(TIPOS_HIDRO)].copy()
            _write_snapshot(df, "siga_hidro.csv.gz")

    if df is None:
        df, path = _read_local(["snapshots/siga_hidro.csv.gz", "siga.csv", "siga-empreendimentos-geracao.csv"])
        if df is not None:
            info.update(fonte=f"Fallback local ({path})", url=None)

    if df is None:
        df, path = _read_local(["sample/siga_amostra.csv"])
        info.update(fonte="Amostra de demonstração", demo=True)
        if df is None:
            raise RuntimeError("Nenhuma fonte de dados do SIGA disponível (API e fallback local).")

    return map_columns(df, SIGA_ALIASES), info


def _accept_hidro(df: pd.DataFrame) -> bool:
    return "CodCEG" in df.columns and any(c in df.columns for c in ("NomBacia", "NomRio", "NomSubBacia"))


def _accept_agentes(df: pd.DataFrame) -> bool:
    return "CodCEG" in df.columns and any(c in df.columns for c in ("NumCNPJ", "NomAgente"))


def _load_aux(
    session: requests.Session, net: _NetState, *, kind: str, demo: bool
) -> tuple[pd.DataFrame | None, str]:
    if kind == "hidro":
        cfg = dict(
            env_var="HIDRORADAR_HIDRO_CSV_URL", package_ids=HIDRO_PACKAGE_IDS, search_terms=HIDRO_SEARCH_TERMS,
            hints=HIDRO_RESOURCE_HINTS, aliases=HIDRO_ALIASES, accept=_accept_hidro,
        )
        local, snap, sample = ["snapshots/hidro.csv.gz", "aproveitamentos_hidreletricos.csv", "hidro.csv"], "hidro.csv.gz", "sample/hidro_amostra.csv"
        aliases, accept = HIDRO_ALIASES, _accept_hidro
    else:
        cfg = dict(
            env_var="HIDRORADAR_AGENTES_CSV_URL", package_ids=AGENTES_PACKAGE_IDS, search_terms=AGENTES_SEARCH_TERMS,
            hints=AGENTES_RESOURCE_HINTS, aliases=AGENTES_ALIASES, accept=_accept_agentes,
        )
        local, snap, sample = ["snapshots/agentes.csv.gz", "agentes_geracao.csv", "agentes.csv"], "agentes.csv.gz", "sample/agentes_amostra.csv"
        aliases, accept = AGENTES_ALIASES, _accept_agentes

    if demo:  # mantém a amostra coerente: não mistura SIGA de demonstração com bases reais
        df, path = _read_local([sample])
        return (map_columns(df, aliases), "Amostra de demonstração") if df is not None else (None, "Indisponível")

    df, url = _discover_dataset(session, net, **cfg)
    if df is not None:
        keep = [c for c in aliases if c in df.columns]
        df = df[keep].copy()
        _write_snapshot(df, snap)
        return df, "API ANEEL"

    df, path = _read_local(local)
    if df is not None:
        df = map_columns(df, aliases)
        if accept(df):
            return df, f"Fallback local ({path})"
    return None, "Indisponível"


# --------------------------------------------------------------------------- #
# Transformações
# --------------------------------------------------------------------------- #
def _bacia_from_subbacia(sub: str | None) -> str | None:
    """Deriva a bacia pelo 1º dígito do código ANEEL da sub-bacia (ex.: '64 - Rio Paraná')."""
    if not sub:
        return None
    m = re.search(r"\b([1-8])\d\b", str(sub))
    return BACIAS_ANEEL.get(m.group(1)) if m else None


def _parse_dates(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    iso = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    iso_t = pd.to_datetime(s.str[:19], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    br = pd.to_datetime(s.str[:10], format="%d/%m/%Y", errors="coerce")
    return iso.fillna(iso_t).fillna(br)


def transform_siga(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    for col in SIGA_ALIASES:
        if col not in df.columns:
            df[col] = pd.NA

    df["CodCEG"] = df["CodCEG"].astype("string").str.strip()
    df["CEG_Key"] = df["CodCEG"].map(ceg_key)
    df["SigTipoGeracao"] = df["SigTipoGeracao"].astype("string").str.strip().str.upper()
    df = df[df["SigTipoGeracao"].isin(TIPOS_HIDRO)].copy()

    df["NomEmpreendimento"] = df["NomEmpreendimento"].map(lambda v: clean_name(v, fill="Sem nome"))
    df["DscFaseUsina"] = df["DscFaseUsina"].map(clean_name).map(
        lambda s: s[:1].upper() + s[1:].lower() if s and s != NAO_INFORMADO else s
    )
    df["SigUFPrincipal"] = df["SigUFPrincipal"].astype("string").str.strip().str.upper().fillna("")

    df["PotOutorgadaKw"] = df["MdaPotenciaOutorgadaKw"].map(parse_number)
    df["PotFiscalizadaKw"] = df["MdaPotenciaFiscalizadaKw"].map(parse_number)
    df["PotOutorgadaMW"] = df["PotOutorgadaKw"] / 1000.0
    df["PotFiscalizadaMW"] = df["PotFiscalizadaKw"] / 1000.0
    df["DatEntradaOperacao"] = _parse_dates(df["DatEntradaOperacao"])

    lat = df["NumCoordNorteEmpreendimento"].map(parse_coordinate)
    lon = df["NumCoordLesteEmpreendimento"].map(parse_coordinate)
    coords = [validate_coordinates(a, b) for a, b in zip(lat, lon)]
    df["Latitude"] = [c[0] for c in coords]
    df["Longitude"] = [c[1] for c in coords]
    df["Status_Coord"] = [c[2] for c in coords]
    df["Coord_Valida"] = df["Latitude"].notna()

    df["Municipios_Lista"] = [
        parse_municipios(m, uf) for m, uf in zip(df["DscMuninicpios"], df["SigUFPrincipal"])
    ]
    df["UFs_Lista"] = [
        sorted({uf} | {m.rsplit(" - ", 1)[-1] for m in muns if " - " in m} - {""})
        for uf, muns in zip(df["SigUFPrincipal"], df["Municipios_Lista"])
    ]
    df["Municipios_Texto"] = df["Municipios_Lista"].map(lambda l: "; ".join(l) if l else NAO_INFORMADO)
    df["Municipio_Principal"] = df["Municipios_Lista"].map(lambda l: l[0].rsplit(" - ", 1)[0] if l else NAO_INFORMADO)
    df["DscSubBacia"] = df["DscSubBacia"].map(lambda v: clean_name(v, fill=None))
    return df


def merge_hidro(df: pd.DataFrame, hidro: pd.DataFrame | None) -> pd.DataFrame:
    out = df.copy()
    if hidro is not None and not hidro.empty:
        h = hidro.copy()
        for c in ("NomBacia", "NomSubBacia", "NomRio"):
            if c not in h.columns:
                h[c] = pd.NA
        h["CEG_Key"] = h["CodCEG"].map(ceg_key)
        h = h[h["CEG_Key"] != ""]
        # Prefere o registro mais completo de cada CEG.
        h["_score"] = h[["NomBacia", "NomSubBacia", "NomRio"]].notna().sum(axis=1)
        h = h.sort_values("_score", ascending=False).drop_duplicates("CEG_Key")
        out = out.merge(h[["CEG_Key", "NomBacia", "NomSubBacia", "NomRio"]], on="CEG_Key", how="left")
    else:
        out["NomBacia"] = pd.NA
        out["NomSubBacia"] = pd.NA
        out["NomRio"] = pd.NA

    # Complementa com a sub-bacia do próprio SIGA e a bacia derivada do código.
    out["NomSubBacia"] = out["NomSubBacia"].where(out["NomSubBacia"].notna(), out["DscSubBacia"])
    derived = out["NomSubBacia"].map(_bacia_from_subbacia)
    out["NomBacia"] = out["NomBacia"].where(out["NomBacia"].notna(), derived)

    for col in ("NomBacia", "NomSubBacia", "NomRio"):
        out[col] = out[col].map(clean_name)

    def _bacia_rio(rio: str, bacia: str) -> str:
        if rio != NAO_INFORMADO and bacia != NAO_INFORMADO:
            return f"{rio} · Bacia {bacia}" if not bacia.lower().startswith("bacia") else f"{rio} · {bacia}"
        if rio != NAO_INFORMADO:
            return rio
        if bacia != NAO_INFORMADO:
            return f"Bacia {bacia}" if not bacia.lower().startswith("bacia") else bacia
        return NAO_INFORMADO

    out["Bacia_Rio"] = [_bacia_rio(r, b) for r, b in zip(out["NomRio"], out["NomBacia"])]
    return out


def merge_agentes(df: pd.DataFrame, agentes: pd.DataFrame | None) -> pd.DataFrame:
    out = df.copy()
    by_key: dict[str, list[dict]] = {}
    if agentes is not None and not agentes.empty:
        a = agentes.copy()
        for c in ("NumCNPJ", "NomAgente", "PctParticipacao"):
            if c not in a.columns:
                a[c] = pd.NA
        a["CEG_Key"] = a["CodCEG"].map(ceg_key)
        for key, nome, cnpj, pct in zip(a["CEG_Key"], a["NomAgente"], a["NumCNPJ"], a["PctParticipacao"]):
            if not key:
                continue
            cnpj_d = only_digits(cnpj)
            entry = {
                "nome": clean_name(nome, fill=None) or format_cnpj(cnpj_d) or NAO_INFORMADO,
                "cnpj": cnpj_d if len(cnpj_d) == 14 else "",
                "pct": parse_number(pct),
            }
            lst = by_key.setdefault(key, [])
            if not any(e["nome"] == entry["nome"] and e["cnpj"] == entry["cnpj"] for e in lst):
                lst.append(entry)

    rows = []
    for key, txt in zip(out["CEG_Key"], out["DscPropriRegimePariticipacao"]):
        lista = by_key.get(key)
        origem = "Dataset de Agentes ANEEL"
        if not lista:
            lista = parse_proprietarios(txt)
            origem = "SIGA (proprietários)" if lista else NAO_INFORMADO
        summary = _summarize_agents(lista)
        summary["Origem_Agentes"] = origem
        rows.append(summary)
    agent_df = pd.DataFrame(rows, index=out.index)
    return pd.concat([out, agent_df], axis=1)


def _build_search_index(df: pd.DataFrame) -> pd.Series:
    parts = (
        df["NomEmpreendimento"].astype(str) + " | " + df["CodCEG"].astype(str) + " | "
        + df["NomRio"].astype(str) + " | " + df["Agentes_Resumo"].astype(str) + " | "
        + df["Municipios_Texto"].astype(str)
    )
    digits = df["CodCEG"].map(only_digits) + " " + df["Agentes_Lista"].map(lambda l: " ".join(a["cnpj"] for a in l))
    return parts.map(normalize_search_text) + " || " + digits


# --------------------------------------------------------------------------- #
# Ponto de entrada (cacheado)
# --------------------------------------------------------------------------- #
def build_dataset(offline: bool | None = None) -> tuple[pd.DataFrame, dict]:
    """Executa o pipeline completo (sem cache). Retorna (DataFrame, metadados)."""
    if offline is None:
        offline = (_config("HIDRORADAR_OFFLINE") or "").lower() in {"1", "true", "sim", "yes"}
    net = _NetState(offline=offline)
    session = _session()

    raw, siga_info = _load_siga(session, net)
    df = transform_siga(raw)

    hidro, hidro_src = _load_aux(session, net, kind="hidro", demo=siga_info["demo"])
    agentes, agentes_src = _load_aux(session, net, kind="agentes", demo=siga_info["demo"])
    df = merge_hidro(df, hidro)
    df = merge_agentes(df, agentes)
    df["Busca"] = _build_search_index(df)
    df = df.sort_values(["SigTipoGeracao", "NomEmpreendimento"]).drop_duplicates("CodCEG").reset_index(drop=True)

    data_base = None
    if "DatGeracaoConjuntoDados" in raw.columns:
        dates = _parse_dates(raw["DatGeracaoConjuntoDados"].dropna())
        if dates.notna().any():
            data_base = dates.max().strftime("%d/%m/%Y")
    if data_base is None and siga_info.get("modificado"):
        try:
            data_base = pd.to_datetime(siga_info["modificado"]).strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            pass

    meta = {
        "fonte_siga": siga_info["fonte"],
        "url_siga": siga_info["url"],
        "fonte_hidro": hidro_src,
        "fonte_agentes": agentes_src,
        "demo": siga_info["demo"],
        "data_base": data_base or "não informada",
        "carregado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total_registros": int(len(df)),
        "sem_coordenadas": int((~df["Coord_Valida"]).sum()),
        "avisos": list(dict.fromkeys(net.avisos)),
    }
    return df, meta


@st.cache_data(ttl=86400, show_spinner=False)
def load_dataset() -> tuple[pd.DataFrame, dict]:
    """Versão cacheada (24 h) do pipeline, usada pela aplicação Streamlit."""
    return build_dataset()


def main() -> None:  # pragma: no cover - utilitário de linha de comando
    import argparse

    parser = argparse.ArgumentParser(description="Pipeline HidroRadar-BR (ANEEL)")
    parser.add_argument("--snapshot", action="store_true", help="baixa as bases e grava data/snapshots/")
    parser.add_argument("--offline", action="store_true", help="usa somente fallback local")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    df, meta = build_dataset(offline=args.offline)
    print(f"Registros: {len(df)} | " + " | ".join(f"{k}={v}" for k, v in meta.items() if k != "avisos"))
    for aviso in meta["avisos"]:
        print("AVISO:", aviso)
    print(df["SigTipoGeracao"].value_counts().to_string())


if __name__ == "__main__":  # pragma: no cover
    main()
