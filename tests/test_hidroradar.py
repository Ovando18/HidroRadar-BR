"""Testes de regressão do HidroRadar-BR (rodam offline com a amostra de demonstração)."""

from __future__ import annotations

import io
import os

import openpyxl
import pandas as pd
import pytest

os.environ["HIDRORADAR_OFFLINE"] = "1"

from src import data_pipeline as dp  # noqa: E402
from src.cnpj_service import consultar_cnpj, is_valid_cnpj, parse_brasilapi  # noqa: E402
from src.excel_export import gerar_excel  # noqa: E402


# ----------------------------------------------------------------- parsers ---
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1.234,56", 1234.56), ("1234,5", 1234.5), ("1234.5", 1234.5), ("", None), (None, None), ("abc", None)],
)
def test_parse_number(raw, expected):
    result = dp.parse_number(raw)
    assert (pd.isna(result) and expected is None) or result == pytest.approx(expected)


def test_parse_coordinate_formats():
    assert dp.parse_coordinate("-22,5") == pytest.approx(-22.5)
    assert dp.parse_coordinate("22°30'00\" S") == pytest.approx(-22.5)


def test_validate_coordinates():
    assert dp.validate_coordinates(-25.2, -51.0)[2] == "Válida"
    lat, lon, status = dp.validate_coordinates(-25.2, 51.0)  # sinal omitido
    assert (lon, status) == (-51.0, "Corrigida (sinal)")
    assert dp.validate_coordinates(40.0, -3.0)[2] == "Fora do território"
    assert dp.validate_coordinates(None, None)[2] == "Ausente"


def test_ceg_key_ignores_punctuation_and_version():
    assert dp.ceg_key("PCH.PH.MG.001234-5.01") == dp.ceg_key("PCHPHMG0012345") == "PCHPHMG0012345"


def test_parse_municipios():
    assert dp.parse_municipios("Uberlândia/MG; Araguari/MG", "MG") == ["Uberlândia - MG", "Araguari - MG"]
    assert dp.parse_municipios("Guaíra - PR, Mundo Novo - MS", "PR") == ["Guaíra - PR", "Mundo Novo - MS"]
    assert dp.parse_municipios("Lençóis", "BA") == ["Lençóis - BA"]


def test_parse_proprietarios_formats():
    a = dp.parse_proprietarios("50,00% para Energia Demo S.A. (PIE); 50,00% para Outra Ltda. (APE)")
    assert [x["nome"] for x in a] == ["Energia Demo S.A.", "Outra Ltda."]
    b = dp.parse_proprietarios("Companhia X (60%); Empresa Y (40%)")
    assert [(x["nome"], x["pct"]) for x in b] == [("Companhia X", 60.0), ("Empresa Y", 40.0)]
    c = dp.parse_proprietarios("100% para CEMIG GERACAO S.A. (PIE) 12.345.678/0001-90")
    assert c[0]["cnpj"] == "12345678000190"


def test_format_power_switches_to_gw():
    assert dp.format_power(950.5) == "950,5 MW"
    assert dp.format_power(1500) == "1,50 GW"


def test_bacia_derivada_do_codigo_da_subbacia():
    assert dp._bacia_from_subbacia("64 - Rio Paraná") == "Rio Paraná"
    assert dp._bacia_from_subbacia("Sem código") is None


# ---------------------------------------------------------------- pipeline ---
@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    """Usa só a amostra de demonstração, ignorando snapshots reais commitados."""
    import shutil

    data_dir = tmp_path_factory.mktemp("data")
    shutil.copytree(dp.SAMPLE_DIR, data_dir / "sample")
    original = dp.DATA_DIR
    dp.DATA_DIR = data_dir
    try:
        yield dp.build_dataset(offline=True)
    finally:
        dp.DATA_DIR = original


def test_pipeline_offline_uses_demo_sample(dataset):
    df, meta = dataset
    assert meta["demo"] is True
    assert set(df["SigTipoGeracao"]) <= set(dp.TIPOS_HIDRO)
    assert df["CodCEG"].is_unique
    assert {"NomBacia", "NomSubBacia", "NomRio", "Bacia_Rio", "Agente_Principal", "Busca"} <= set(df.columns)
    assert not df[["NomBacia", "NomRio"]].isna().any().any()  # nulos viram "Não informado"


def test_excel_has_three_styled_sheets(dataset):
    df, _ = dataset
    wb = openpyxl.load_workbook(io.BytesIO(gerar_excel(df)))
    assert wb.sheetnames == ["Usinas_Filtradas", "Resumo_por_UF", "Resumo_por_Bacia"]
    ws = wb["Usinas_Filtradas"]
    assert ws.max_row == len(df) + 1
    assert ws["A1"].fill.fgColor.rgb.endswith("0F4C81")
    assert ws["A1"].font.color.rgb.endswith("FFFFFF")
    assert gerar_excel(df.iloc[0:0])  # recorte vazio não quebra


# -------------------------------------------------------------------- CNPJ ---
def test_cnpj_validation_and_parsing():
    assert is_valid_cnpj("00.000.000/0001-91")
    assert not is_valid_cnpj("11.111.111/1111-11")
    assert consultar_cnpj("123")["ok"] is False
    info = parse_brasilapi({
        "cnpj": "00000000000191", "razao_social": "EMPRESA", "descricao_situacao_cadastral": "ATIVA",
        "logradouro": "RUA A", "numero": "10", "cep": "70040912", "ddd_telefone_1": "6134939002",
        "email": "Contato@Exemplo.com", "qsa": [{"nome_socio": "FULANO", "qualificacao_socio": "Diretor"}],
    })
    assert info["cnpj"] == "00.000.000/0001-91"
    assert info["cep"] == "70040-912"
    assert info["telefones"] == ["(61) 3493-9002"]
    assert info["email"] == "contato@exemplo.com"


# --------------------------------------------------------------------- app ---
def test_app_smoke_and_chained_filters():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("../app.py", default_timeout=60).run()
    assert not at.exception
    at.multiselect(key="f_ufs").select("MG").run()
    assert all(m.endswith("- MG") for m in at.multiselect(key="f_muns").options)
    at.multiselect(key="f_bacias").select("Rio Paraná").run()
    assert not at.exception
    assert len(at.selectbox(key="sel_usina").options) >= 1
    at.selectbox(key="sel_usina").select_index(0).run()
    assert not at.exception
    next(b for b in at.button if "Limpar" in b.label).click().run()
    assert at.multiselect(key="f_ufs").value == []
    assert at.session_state["f_tipos"] == ["PCH", "CGH"]


def test_merge_hidro_barragens_usa_rio_mais_frequente():
    siga = dp.transform_siga(pd.DataFrame({
        "CodCEG": ["PCH.PH.MG.000001-1.01"], "NomEmpreendimento": ["X"], "SigTipoGeracao": ["PCH"],
        "DscSubBacia": ["61 - Rio Grande"],
    }))
    fsb = dp.map_columns(pd.DataFrame({
        "IdeNucleoCEG": [1, 1, 1], "CodCEG": ["PCH.PH.MG.000001-1.01"] * 3,
        "NomRioEmpreendimento": ["RIO CAPIVARI", "Rio Capivari", "RIBEIRAO X"],
        "DatEmissaoRelatorio": ["2024-01-01", "2025-01-01", "2023-01-01"],
    }), dp.HIDRO_ALIASES)
    assert dp._accept_hidro(fsb)
    out = dp.merge_hidro(siga, fsb[["CodCEG", "NomRio"]])
    assert out.loc[0, "NomRio"] == "Rio Capivari"
    assert out.loc[0, "NomBacia"] == "Rio Paraná"  # derivada do código da sub-bacia
