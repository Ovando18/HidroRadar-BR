"""Geração do arquivo .xlsx (em memória) com os empreendimentos filtrados."""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

PRIMARY = "0F4C81"
BORDER_COLOR = "E2E8F0"
ZEBRA = "F8FAFC"

# Coluna interna -> (cabeçalho na planilha, formato numérico)
EXPORT_COLUMNS: dict[str, tuple[str, str | None]] = {
    "CodCEG": ("Código CEG", None),
    "SigTipoGeracao": ("Tipo", None),
    "NomEmpreendimento": ("Nome da Usina", None),
    "DscFaseUsina": ("Fase", None),
    "SigUFPrincipal": ("UF Principal", None),
    "Municipios_Texto": ("Município(s)", None),
    "NomBacia": ("Bacia Hidrográfica", None),
    "NomSubBacia": ("Sub-bacia", None),
    "NomRio": ("Rio", None),
    "PotOutorgadaKw": ("Potência Outorgada (kW)", "#,##0.00"),
    "PotOutorgadaMW": ("Potência Outorgada (MW)", "#,##0.000"),
    "PotFiscalizadaKw": ("Potência Fiscalizada (kW)", "#,##0.00"),
    "PotFiscalizadaMW": ("Potência Fiscalizada (MW)", "#,##0.000"),
    "DatEntradaOperacao": ("Entrada em Operação", "DD/MM/YYYY"),
    "Latitude": ("Latitude", "0.000000"),
    "Longitude": ("Longitude", "0.000000"),
    "Status_Coord": ("Status da Coordenada", None),
    "Agente_Principal": ("Agente Principal", None),
    "CNPJ_Principal": ("CNPJ Agente Principal", None),
    "Qtd_Agentes": ("Qtd. Agentes", "0"),
    "Agentes_Resumo": ("Agentes (participação)", None),
    "CNPJs_Todos": ("CNPJs Participantes", None),
    "Origem_Agentes": ("Origem dos Agentes", None),
    "DscPropriRegimePariticipacao": ("Proprietários / Regime (SIGA)", None),
}


def _style_sheet(ws: Worksheet, formats: dict[int, str | None]) -> None:
    header_fill = PatternFill("solid", fgColor=PRIMARY)
    header_font = Font(bold=True, color="FFFFFF", name="Calibri", size=11)
    zebra_fill = PatternFill("solid", fgColor=ZEBRA)
    side = Side(style="thin", color=BORDER_COLOR)
    border = Border(left=side, right=side, top=side, bottom=side)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    ws.row_dimensions[1].height = 32

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = border
            fmt = formats.get(cell.column)
            if fmt:
                cell.number_format = fmt
            if cell.row % 2 == 0:
                cell.fill = zebra_fill

    for idx, column in enumerate(ws.columns, start=1):
        values = [str(c.value) if c.value is not None else "" for c in column]
        width = max((len(v) for v in values[1:]), default=0)
        width = max(width, min(len(values[0]), 28))
        ws.column_dimensions[get_column_letter(idx)].width = min(max(width + 2, 10), 60)

    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


def _write(writer: pd.ExcelWriter, df: pd.DataFrame, sheet: str, formats: dict[str, str | None]) -> None:
    df.to_excel(writer, sheet_name=sheet, index=False)
    ws = writer.sheets[sheet]
    col_formats = {i: formats.get(col) for i, col in enumerate(df.columns, start=1)}
    _style_sheet(ws, col_formats)


def build_usinas_sheet(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in EXPORT_COLUMNS if c in df.columns]
    out = df[cols].copy()
    if "DatEntradaOperacao" in out.columns:
        out["DatEntradaOperacao"] = pd.to_datetime(out["DatEntradaOperacao"], errors="coerce").dt.date
    return out.rename(columns={c: EXPORT_COLUMNS[c][0] for c in cols})


def build_resumo_uf(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["UF", "Tipo", "Qtd. Usinas", "Potência Outorgada (MW)", "Potência Fiscalizada (MW)"])
    g = (
        df.groupby(["SigUFPrincipal", "SigTipoGeracao"], dropna=False)
        .agg(qtd=("CodCEG", "count"), pot=("PotOutorgadaMW", "sum"), fisc=("PotFiscalizadaMW", "sum"))
        .reset_index()
        .sort_values(["SigUFPrincipal", "SigTipoGeracao"])
    )
    total = pd.DataFrame(
        [{"SigUFPrincipal": "TOTAL", "SigTipoGeracao": "—", "qtd": g["qtd"].sum(), "pot": g["pot"].sum(), "fisc": g["fisc"].sum()}]
    )
    g = pd.concat([g, total], ignore_index=True)
    return g.rename(columns={
        "SigUFPrincipal": "UF", "SigTipoGeracao": "Tipo", "qtd": "Qtd. Usinas",
        "pot": "Potência Outorgada (MW)", "fisc": "Potência Fiscalizada (MW)",
    })


def build_resumo_bacia(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Bacia Hidrográfica", "Rio", "Qtd. Usinas", "Qtd. UHE", "Qtd. PCH", "Qtd. CGH", "Potência Outorgada (MW)"])
    tmp = df.assign(
        _uhe=(df["SigTipoGeracao"] == "UHE").astype(int),
        _pch=(df["SigTipoGeracao"] == "PCH").astype(int),
        _cgh=(df["SigTipoGeracao"] == "CGH").astype(int),
    )
    g = (
        tmp.groupby(["NomBacia", "NomRio"], dropna=False)
        .agg(qtd=("CodCEG", "count"), uhe=("_uhe", "sum"), pch=("_pch", "sum"), cgh=("_cgh", "sum"), pot=("PotOutorgadaMW", "sum"))
        .reset_index()
        .sort_values(["NomBacia", "pot"], ascending=[True, False])
    )
    return g.rename(columns={
        "NomBacia": "Bacia Hidrográfica", "NomRio": "Rio", "qtd": "Qtd. Usinas", "uhe": "Qtd. UHE",
        "pch": "Qtd. PCH", "cgh": "Qtd. CGH", "pot": "Potência Outorgada (MW)",
    })


def gerar_excel(df: pd.DataFrame) -> bytes:
    """Gera o .xlsx com as abas Usinas_Filtradas, Resumo_por_UF e Resumo_por_Bacia."""
    buffer = io.BytesIO()
    usinas_formats = {header: fmt for header, fmt in EXPORT_COLUMNS.values()}
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        _write(writer, build_usinas_sheet(df), "Usinas_Filtradas", usinas_formats)
        _write(writer, build_resumo_uf(df), "Resumo_por_UF", {
            "Qtd. Usinas": "#,##0", "Potência Outorgada (MW)": "#,##0.000", "Potência Fiscalizada (MW)": "#,##0.000",
        })
        _write(writer, build_resumo_bacia(df), "Resumo_por_Bacia", {
            "Qtd. Usinas": "#,##0", "Qtd. UHE": "#,##0", "Qtd. PCH": "#,##0", "Qtd. CGH": "#,##0",
            "Potência Outorgada (MW)": "#,##0.000",
        })
        ws = writer.sheets["Resumo_por_UF"]
        last = ws.max_row
        for cell in ws[last]:
            cell.font = Font(bold=True, color=PRIMARY)
        wb = writer.book
        wb.properties.title = "HidroRadar-BR — Empreendimentos Hidrelétricos"
        wb.properties.creator = "HidroRadar-BR"
        wb.properties.description = f"Fonte: ANEEL (SIGA e Dados Abertos). Gerado em {datetime.now():%d/%m/%Y %H:%M}."
    return buffer.getvalue()
