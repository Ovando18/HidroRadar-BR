"""
HidroRadar-BR — Painel de pesquisa de usinas hidrelétricas (UHE, PCH e CGH) do Brasil.

Execute com:  streamlit run app.py
"""

from __future__ import annotations

import functools
import hashlib
import html
import re

import streamlit as st

st.set_page_config(
    page_title="HidroRadar BR | UHEs, PCHs e CGHs",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

import folium  # noqa: E402
import pandas as pd  # noqa: E402
from branca.element import MacroElement, Template  # noqa: E402
from folium.plugins import MarkerCluster  # noqa: E402
from streamlit_folium import st_folium  # noqa: E402

from src.cnpj_service import consultar_cnpj, format_cnpj, is_valid_cnpj  # noqa: E402
from src.data_pipeline import (  # noqa: E402
    NAO_INFORMADO,
    TIPOS_HIDRO,
    format_power,
    load_dataset,
    normalize_search_text,
    only_digits,
)
from src.excel_export import gerar_excel  # noqa: E402

# --------------------------------------------------------------------------- #
# Design system
# --------------------------------------------------------------------------- #
PRIMARY = "#0F4C81"
TIPO_CORES = {"UHE": "#1E3A8A", "PCH": "#0284C7", "CGH": "#0D9488"}
TIPO_ROTULOS = {"UHE": "UHE · Grande porte", "PCH": "PCH · Pequeno porte", "CGH": "CGH · Central geradora"}
TIPO_RAIO = {"UHE": 8, "PCH": 6, "CGH": 5}
CARTO_POSITRON = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
CARTO_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'

FILTER_KEYS = ("f_tipos", "f_ufs", "f_muns", "f_bacias", "f_subs", "f_rios", "f_fases", "f_pot", "f_busca", "_pot_bounds")

CSS = f"""
<style>
:root {{
  --hr-bg: #F8FAFC; --hr-card: #FFFFFF; --hr-border: #E2E8F0; --hr-primary: {PRIMARY};
  --hr-text: #1E293B; --hr-muted: #64748B;
  --hr-uhe: {TIPO_CORES['UHE']}; --hr-pch: {TIPO_CORES['PCH']}; --hr-cgh: {TIPO_CORES['CGH']};
}}
.block-container {{ padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1600px; }}
h1, h2, h3 {{ color: var(--hr-text); letter-spacing: -0.01em; }}
[data-testid="stSidebar"] {{ background: var(--hr-card); }}
[data-testid="stSidebar"] .block-container {{ padding-top: 1rem; }}

.hr-header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:1rem; flex-wrap:wrap;
  border-bottom:1px solid var(--hr-border); padding-bottom:.9rem; margin-bottom:1.1rem; }}
.hr-title {{ font-size:1.55rem; font-weight:700; color:var(--hr-text); margin:0; line-height:1.2; }}
.hr-title span {{ color: var(--hr-primary); }}
.hr-subtitle {{ color:var(--hr-muted); font-size:.93rem; margin:.25rem 0 0 0; max-width: 760px; }}
.hr-badges {{ display:flex; gap:.4rem; flex-wrap:wrap; }}
.hr-badge {{ display:inline-flex; align-items:center; gap:.35rem; font-size:.76rem; font-weight:500;
  color:var(--hr-primary); background:#EFF6FF; border:1px solid #DBEAFE; border-radius:999px; padding:.22rem .7rem; }}
.hr-badge.muted {{ color:var(--hr-muted); background:var(--hr-card); border-color:var(--hr-border); }}
.hr-badge.warn {{ color:#92400E; background:#FFFBEB; border-color:#FDE68A; }}

.hr-kpi {{ background:var(--hr-card); border:1px solid var(--hr-border); border-left:4px solid var(--kpi-color, var(--hr-primary));
  border-radius:10px; padding:.85rem 1rem; box-shadow:0 1px 2px rgba(15,23,42,.04); height:100%; }}
.hr-kpi .lbl {{ font-size:.74rem; text-transform:uppercase; letter-spacing:.05em; color:var(--hr-muted); font-weight:600; }}
.hr-kpi .val {{ font-size:1.6rem; font-weight:700; color:var(--hr-text); line-height:1.25; margin-top:.15rem; }}
.hr-kpi .sub {{ font-size:.76rem; color:var(--hr-muted); }}

.hr-section {{ font-size:1.02rem; font-weight:650; color:var(--hr-text); margin:.2rem 0 .55rem 0; }}
.hr-side-title {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.07em; font-weight:700;
  color:var(--hr-muted); margin:1rem 0 .3rem 0; padding-top:.6rem; border-top:1px solid var(--hr-border); }}
.hr-side-title.first {{ border-top:none; margin-top:0; padding-top:0; }}

.hr-card {{ background:var(--hr-card); border:1px solid var(--hr-border); border-radius:10px; padding:1rem 1.1rem;
  box-shadow:0 1px 2px rgba(15,23,42,.04); margin-bottom:.8rem; }}
.hr-card h4 {{ margin:0 0 .15rem 0; font-size:1.08rem; color:var(--hr-text); }}
.hr-card .cap {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; color:var(--hr-muted);
  font-weight:700; margin:.85rem 0 .35rem 0; }}
.hr-grid {{ display:grid; grid-template-columns: 42% 58%; row-gap:.32rem; column-gap:.6rem; font-size:.86rem; }}
.hr-grid .k {{ color:var(--hr-muted); }}
.hr-grid .v {{ color:var(--hr-text); font-weight:500; word-break:break-word; }}
.hr-tag {{ display:inline-block; color:#fff; font-size:.7rem; font-weight:700; border-radius:6px; padding:.12rem .5rem;
  letter-spacing:.04em; vertical-align:middle; }}
.hr-fase {{ display:inline-block; font-size:.72rem; color:var(--hr-muted); border:1px solid var(--hr-border);
  border-radius:6px; padding:.1rem .45rem; margin-left:.3rem; vertical-align:middle; }}
.hr-link a {{ color:var(--hr-primary); font-weight:600; text-decoration:none; font-size:.84rem; }}
.hr-empty {{ color:var(--hr-muted); font-size:.9rem; text-align:center; padding:2.2rem 1rem; }}
.hr-footer {{ color:var(--hr-muted); font-size:.76rem; border-top:1px solid var(--hr-border); margin-top:1.5rem; padding-top:.8rem; }}
[data-testid="stDownloadButton"] button {{ background:var(--hr-primary); color:#fff; border:none; font-weight:600; }}
[data-testid="stDownloadButton"] button:hover {{ background:#0B3A63; color:#fff; }}
iframe[title="streamlit_folium.st_folium"] {{ border:1px solid var(--hr-border); border-radius:10px; }}
</style>
"""


# --------------------------------------------------------------------------- #
# Utilitários de interface
# --------------------------------------------------------------------------- #
def esc(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return html.escape(str(value))


def br_num(x: float, dec: int = 2) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def signature(values) -> str:
    return hashlib.md5("|".join(map(str, values)).encode()).hexdigest()[:16]


def multiselect(label: str, options: list[str], key: str, **kwargs) -> list[str]:
    """Multiselect (na sidebar) que descarta valores fora do recorte atual antes de renderizar."""
    if key in st.session_state:
        st.session_state[key] = [v for v in st.session_state[key] if v in options]
    return st.sidebar.multiselect(label, options, key=key, **kwargs)


def reset_filters() -> None:
    for k in FILTER_KEYS:
        st.session_state.pop(k, None)
    st.session_state.pop("sel_usina", None)


def any_in(series: pd.Series, selected: list[str]) -> pd.Series:
    sel = set(selected)
    return series.map(lambda items: bool(sel.intersection(items)))


# --------------------------------------------------------------------------- #
# Filtros (sidebar)
# --------------------------------------------------------------------------- #
def sidebar_filters(df: pd.DataFrame) -> pd.DataFrame:
    sb = st.sidebar
    sb.markdown(
        f"<div style='font-weight:700;font-size:1.05rem;color:{PRIMARY};margin-bottom:.2rem'>⚡ HidroRadar-BR</div>"
        "<div style='color:#64748B;font-size:.8rem;margin-bottom:.6rem'>Filtros de pesquisa</div>",
        unsafe_allow_html=True,
    )

    # 1. Porte / tipo -------------------------------------------------------
    sb.markdown("<div class='hr-side-title first'>Porte / Tipo</div>", unsafe_allow_html=True)
    tipos = sb.pills(
        "Tipo de empreendimento", list(TIPOS_HIDRO), selection_mode="multi", default=["PCH", "CGH"],
        format_func=lambda t: TIPO_ROTULOS[t], key="f_tipos", label_visibility="collapsed",
    ) or []
    if not tipos:
        sb.caption("Nenhum tipo marcado — exibindo todos.")
    out = df[df["SigTipoGeracao"].isin(tipos)] if tipos else df

    # 2. Localização política --------------------------------------------
    sb.markdown("<div class='hr-side-title'>Localização política</div>", unsafe_allow_html=True)
    ufs_opts = sorted({uf for lst in out["UFs_Lista"] for uf in lst})
    ufs = multiselect("UF (Estado)", ufs_opts, "f_ufs", placeholder="Todas as UFs")
    if ufs:
        out = out[any_in(out["UFs_Lista"], ufs)]
    ufs_set = set(ufs)
    mun_opts = sorted(
        {m for lst in out["Municipios_Lista"] for m in lst if not ufs_set or m.rsplit(" - ", 1)[-1] in ufs_set},
        key=normalize_search_text,
    )
    muns = multiselect(
        "Município", mun_opts, "f_muns",
        placeholder="Todos os municípios" if ufs else "Selecione UF(s) para refinar",
    )
    if muns:
        out = out[any_in(out["Municipios_Lista"], muns)]

    # 3. Localização hidrográfica ----------------------------------------
    sb.markdown("<div class='hr-side-title'>Localização hidrográfica</div>", unsafe_allow_html=True)
    bacias = multiselect("Bacia hidrográfica", sorted(out["NomBacia"].dropna().unique(), key=normalize_search_text), "f_bacias", placeholder="Todas as bacias")
    if bacias:
        out = out[out["NomBacia"].isin(bacias)]
    subs = multiselect("Sub-bacia", sorted(out["NomSubBacia"].dropna().unique(), key=normalize_search_text), "f_subs", placeholder="Todas as sub-bacias")
    if subs:
        out = out[out["NomSubBacia"].isin(subs)]
    rios = multiselect("Rio", sorted(out["NomRio"].dropna().unique(), key=normalize_search_text), "f_rios", placeholder="Todos os rios")
    if rios:
        out = out[out["NomRio"].isin(rios)]

    # 4. Características técnicas ----------------------------------------
    sb.markdown("<div class='hr-side-title'>Características técnicas</div>", unsafe_allow_html=True)
    fases = multiselect("Fase da usina", sorted(out["DscFaseUsina"].dropna().unique()), "f_fases", placeholder="Todas as fases")
    if fases:
        out = out[out["DscFaseUsina"].isin(fases)]
    out = power_slider(sb, out)

    # 5. Busca textual ---------------------------------------------------
    sb.markdown("<div class='hr-side-title'>Busca textual</div>", unsafe_allow_html=True)
    termo = sb.text_input(
        "Buscar", key="f_busca", placeholder="Usina, CEG, rio, agente ou CNPJ", label_visibility="collapsed",
    ).strip()
    if termo:
        q = normalize_search_text(termo)
        mask = out["Busca"].str.contains(q, regex=False)
        digits = only_digits(termo)
        if len(digits) >= 4:
            mask |= out["Busca"].str.contains(digits, regex=False)
        out = out[mask]

    sb.markdown("<div class='hr-side-title'></div>", unsafe_allow_html=True)
    sb.button("Limpar filtros", on_click=reset_filters, icon=":material/filter_alt_off:", width="stretch")
    return out


def power_slider(sb, df: pd.DataFrame) -> pd.DataFrame:
    pot = df["PotOutorgadaMW"].dropna()
    if pot.empty or pot.max() <= pot.min():
        st.session_state.pop("f_pot", None)
        st.session_state.pop("_pot_bounds", None)
        return df
    hi_raw = float(pot.max())
    step = 0.01 if hi_raw <= 10 else 0.1 if hi_raw <= 1000 else 1.0
    lo = float(int(pot.min() / step) * step)
    hi = float(round((int(hi_raw / step) + 1) * step, 2))
    lo, hi = round(lo, 2), round(hi, 2)
    bounds = (lo, hi)

    prev_bounds, cur = st.session_state.get("_pot_bounds"), st.session_state.get("f_pot")
    if cur is None or prev_bounds is None or tuple(cur) == tuple(prev_bounds):
        new = bounds  # usuário não restringiu: acompanha o recorte
    else:
        a, b = max(lo, min(float(cur[0]), hi)), min(hi, max(float(cur[1]), lo))
        new = (a, b) if a < b else bounds
    st.session_state["f_pot"] = new
    st.session_state["_pot_bounds"] = bounds

    sel = sb.slider("Potência outorgada (MW)", min_value=lo, max_value=hi, step=step, key="f_pot", format="%.2f")
    if tuple(sel) == bounds:
        return df
    return df[df["PotOutorgadaMW"].between(sel[0], sel[1])]


# --------------------------------------------------------------------------- #
# Cabeçalho e KPIs
# --------------------------------------------------------------------------- #
def header(meta: dict) -> None:
    badges = [f"<span class='hr-badge'>🗓 Base ANEEL: {esc(meta['data_base'])}</span>",
              f"<span class='hr-badge muted'>Fonte: {esc(meta['fonte_siga'])}</span>"]
    if meta.get("demo"):
        badges.append("<span class='hr-badge warn'>Modo demonstração</span>")
    st.markdown(
        f"""<div class='hr-header'>
              <div><p class='hr-title'>Hidro<span>Radar</span> BR</p>
              <p class='hr-subtitle'>Painel de pesquisa de usinas hidrelétricas do Brasil — UHEs, PCHs e CGHs —
              com dados regulatórios, hidrográficos e cadastrais do Portal de Dados Abertos da ANEEL.</p></div>
              <div class='hr-badges'>{''.join(badges)}</div>
            </div>""",
        unsafe_allow_html=True,
    )
    if meta.get("demo"):
        st.warning(
            "Não foi possível acessar o Portal de Dados Abertos da ANEEL nem um snapshot local. "
            "Exibindo uma **amostra fictícia de demonstração**.\n\n"
            "Os servidores da ANEEL costumam recusar conexões de fora do Brasil (como o Streamlit Cloud). "
            "Para usar dados reais, rode `python -m src.data_pipeline` em um computador no Brasil e "
            "faça commit da pasta `data/snapshots/` (veja o README).",
            icon=":material/info:",
        )


def kpi(col, label: str, value: str, sub: str, color: str) -> None:
    col.markdown(
        f"<div class='hr-kpi' style='--kpi-color:{color}'><div class='lbl'>{label}</div>"
        f"<div class='val'>{value}</div><div class='sub'>{sub}</div></div>",
        unsafe_allow_html=True,
    )


def kpis(df: pd.DataFrame) -> None:
    c = st.columns(5)
    counts = df["SigTipoGeracao"].value_counts()
    pot_tipo = df.groupby("SigTipoGeracao")["PotOutorgadaMW"].sum()
    kpi(c[0], "Usinas filtradas", fmt_int(len(df)), f"{fmt_int(df['Coord_Valida'].sum())} georreferenciadas", PRIMARY)
    for col, t in zip(c[1:4], TIPOS_HIDRO):
        kpi(col, f"Qtd. {t}", fmt_int(counts.get(t, 0)), format_power(pot_tipo.get(t, 0.0)) + " outorgados", TIPO_CORES[t])
    kpi(c[4], "Potência outorgada", format_power(df["PotOutorgadaMW"].sum()), "soma do recorte", "#475569")


# --------------------------------------------------------------------------- #
# Mapa
# --------------------------------------------------------------------------- #
LEGEND_JS = Template(
    """
{% macro script(this, kwargs) %}
(function() {
  var legend = L.control({position: 'bottomleft'});
  legend.onAdd = function () {
    var div = L.DomUtil.create('div');
    div.style.cssText = 'background:#fff;border:1px solid #E2E8F0;border-radius:8px;padding:7px 10px;' +
      'font:12px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#1E293B;box-shadow:0 1px 3px rgba(15,23,42,.08)';
    div.innerHTML = {{ this.items_html|tojson }};
    return div;
  };
  legend.addTo({{ this._parent.get_name() }});
})();
{% endmacro %}
"""
)


class Legend(MacroElement):
    def __init__(self, tipos):
        super().__init__()
        self._template = LEGEND_JS
        self.items_html = "".join(
            f"<div><span style='display:inline-block;width:10px;height:10px;border-radius:50%;"
            f"background:{TIPO_CORES[t]};margin-right:6px;border:1.5px solid #fff;box-shadow:0 0 0 1px {TIPO_CORES[t]}'></span>"
            f"{TIPO_ROTULOS[t]}</div>"
            for t in tipos
        )


BIND_TOOLTIPS_JS = Template(
    """
{% macro script(this, kwargs) %}
{% for name in this.layer_names %}
{{ name }}.eachLayer(function (layer) {
  layer.bindTooltip(layer.feature.properties.tip, {sticky: true});
});
{% endfor %}
{% endmacro %}
"""
)


class BindPointTooltips(MacroElement):
    """
    Vincula o tooltip a cada ponto das camadas GeoJSON. Assim o ``st_folium`` lê o
    tooltip do marcador clicado (``sourceTarget``) e a ficha identifica o CEG exato.
    (O ``on_each_feature`` do folium gera JS inválido nesta combinação.)
    """

    def __init__(self, layers):
        super().__init__()
        self._template = BIND_TOOLTIPS_JS
        self.layer_names = [layer.get_name() for layer in layers]


def cluster_icon_js(color: str) -> str:
    return f"""
    function(cluster) {{
      var n = cluster.getChildCount();
      var s = n < 10 ? 28 : n < 100 ? 34 : 42;
      return L.divIcon({{
        html: '<div style="width:' + s + 'px;height:' + s + 'px;line-height:' + s + 'px;border-radius:50%;' +
              'background:{color};opacity:.88;color:#fff;font:600 12px sans-serif;text-align:center;' +
              'box-shadow:0 0 0 4px {color}33">' + n + '</div>',
        className: 'hr-cluster', iconSize: L.point(s, s)
      }});
    }}"""


def tooltip_html(r) -> str:
    return (
        f"<div style='font:12px/1.45 sans-serif;color:#1E293B;min-width:200px'>"
        f"<b style='font-size:13px'>{esc(r.NomEmpreendimento)}</b><br>"
        f"<span style='color:{TIPO_CORES[r.SigTipoGeracao]};font-weight:700'>{r.SigTipoGeracao}</span>"
        f" · {esc(br_num(r.PotOutorgadaMW, 2))} MW<br>"
        f"{esc(r.Bacia_Rio)}<br>{esc(r.Municipios_Texto)}<br>"
        f"<span style='color:#64748B'>CEG {esc(r.CodCEG)}</span></div>"
    )


def build_map(_pts: pd.DataFrame) -> folium.Map:
    """
    Constrói o mapa do recorte. Não é cacheado de propósito: um objeto ``folium.Map``
    não pode ser renderizado duas vezes (os scripts dos clusters se perdem na 2ª vez).
    """
    m = folium.Map(location=[-14.5, -52.0], zoom_start=4, tiles=None, control_scale=True, prefer_canvas=True)
    folium.TileLayer(tiles=CARTO_POSITRON, attr=CARTO_ATTR, name="CartoDB Positron", subdomains="abcd", max_zoom=20).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    tipos_presentes = [t for t in TIPOS_HIDRO if t in set(_pts["SigTipoGeracao"])]
    geo_layers: list[folium.GeoJson] = []
    for tipo in tipos_presentes:
        sub = _pts[_pts["SigTipoGeracao"] == tipo]
        cluster = MarkerCluster(
            name=tipo, icon_create_function=cluster_icon_js(TIPO_CORES[tipo]),
            options={"showCoverageOnHover": False, "maxClusterRadius": 45, "disableClusteringAtZoom": 11},
        ).add_to(m)
        # Uma única camada GeoJSON por tipo (~20x mais rápida que um objeto folium por
        # marcador). O tooltip é vinculado a cada ponto para que o clique informe o CEG.
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(r.Longitude), float(r.Latitude)]},
                "properties": {"tip": tooltip_html(r)},
            }
            for r in sub.itertuples(index=False)
        ]
        geo = folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            name=TIPO_ROTULOS[tipo],
            marker=folium.CircleMarker(
                radius=TIPO_RAIO[tipo], color="#FFFFFF", weight=1.5, fill=True,
                fill_color=TIPO_CORES.get(tipo, PRIMARY), fill_opacity=0.92,
            ),
            control=False,
        ).add_to(cluster)
        geo_layers.append(geo)
    if geo_layers:
        BindPointTooltips(geo_layers).add_to(m)
    if tipos_presentes:
        Legend(tipos_presentes).add_to(m)
    folium.LayerControl(position="topright", collapsed=True).add_to(m)
    if len(_pts) == 1:
        m.location, m.options["zoom"] = [float(_pts["Latitude"].iloc[0]), float(_pts["Longitude"].iloc[0])], 11
    elif len(_pts) > 1:
        m.fit_bounds(
            [[_pts["Latitude"].min(), _pts["Longitude"].min()], [_pts["Latitude"].max(), _pts["Longitude"].max()]],
            padding=(24, 24),
        )
    return m


def resolve_click(output: dict | None, pts: pd.DataFrame) -> str | None:
    """Identifica a usina clicada pelo CEG do tooltip ou, alternativamente, pela coordenada."""
    if not output:
        return None
    tip = output.get("last_object_clicked_tooltip") or ""
    m = re.search(r"CEG\s+([A-Z0-9.\-]+)", str(tip))
    if m and m.group(1) in set(pts["CodCEG"]):
        return m.group(1)
    click = output.get("last_object_clicked") or {}
    if click.get("lat") is None or pts.empty:
        return None
    d = (pts["Latitude"] - click["lat"]) ** 2 + (pts["Longitude"] - click["lng"]) ** 2
    idx = d.idxmin()
    # O clique traz a posição do mouse, não o centro do marcador: aceita o ponto mais próximo.
    return pts.loc[idx, "CodCEG"] if d.loc[idx] < 0.01 else None


def handle_map_click(pts: pd.DataFrame) -> None:
    """
    Processa o último clique no mapa *antes* de desenhá-lo. O valor do componente já
    está em ``st.session_state["mapa"]`` no início do rerun, então a ficha e o
    destaque refletem o clique no mesmo ciclo, sem rerun adicional.
    """
    output = st.session_state.get("mapa")
    if not output or not output.get("last_object_clicked"):
        return
    token = (str(output.get("last_object_clicked")), str(output.get("last_object_clicked_tooltip")))
    if token == st.session_state.get("_last_click"):
        return
    st.session_state["_last_click"] = token
    ceg = resolve_click(output, pts)
    if ceg:
        st.session_state["sel_usina"] = ceg


def render_map(df: pd.DataFrame) -> None:
    pts = df[df["Coord_Valida"]][
        ["CodCEG", "NomEmpreendimento", "SigTipoGeracao", "Latitude", "Longitude", "PotOutorgadaMW", "Bacia_Rio", "Municipios_Texto"]
    ]
    handle_map_click(pts)
    selected = st.session_state.get("sel_usina")
    fmap = build_map(pts)

    highlight = folium.FeatureGroup(name="Selecionada")
    if selected is not None:
        row = pts[pts["CodCEG"] == selected]
        if not row.empty:
            folium.CircleMarker(
                location=(float(row["Latitude"].iloc[0]), float(row["Longitude"].iloc[0])), radius=15,
                color="#F59E0B", weight=3, fill=False, interactive=False,
            ).add_to(highlight)

    st_folium(
        fmap, key="mapa", height=560, use_container_width=True, feature_group_to_add=highlight,
        returned_objects=["last_object_clicked", "last_object_clicked_tooltip"],
    )

    sem = int((~df["Coord_Valida"]).sum())
    st.caption(
        f"{fmt_int(len(pts))} usinas no mapa"
        + (f" · {sem} sem coordenadas válidas (listadas na tabela)" if sem else "")
        + " · Clique em um ponto para abrir a ficha do empreendimento."
    )


# --------------------------------------------------------------------------- #
# Ficha do empreendimento
# --------------------------------------------------------------------------- #
def grid(rows: list[tuple[str, str]]) -> str:
    return "<div class='hr-grid'>" + "".join(f"<div class='k'>{k}</div><div class='v'>{v}</div>" for k, v in rows) + "</div>"


def render_ficha(df: pd.DataFrame) -> None:
    st.markdown("<div class='hr-section'>Ficha do empreendimento</div>", unsafe_allow_html=True)
    if df.empty:
        st.markdown("<div class='hr-card hr-empty'>Nenhuma usina no recorte atual.</div>", unsafe_allow_html=True)
        return

    ordered = df.sort_values("NomEmpreendimento", key=lambda s: s.map(normalize_search_text))
    options = ordered["CodCEG"].tolist()
    labels = dict(zip(ordered["CodCEG"], ordered["SigTipoGeracao"] + " · " + ordered["NomEmpreendimento"] + " (" + ordered["SigUFPrincipal"] + ")"))
    if st.session_state.get("sel_usina") not in labels:
        st.session_state["sel_usina"] = None
    ceg = st.selectbox(
        "Selecionar usina", options, key="sel_usina", format_func=lambda c: labels.get(c, c),
        index=None, placeholder="Clique no mapa ou selecione aqui…", label_visibility="collapsed",
    )
    if ceg is None:
        st.markdown(
            "<div class='hr-card hr-empty'>🗺️<br>Clique em uma usina no mapa ou escolha na lista acima "
            "para ver os dados regulatórios, hidrográficos e cadastrais.</div>",
            unsafe_allow_html=True,
        )
        return

    r = df[df["CodCEG"] == ceg].iloc[0]
    cor = TIPO_CORES.get(r["SigTipoGeracao"], PRIMARY)
    data_op = r["DatEntradaOperacao"].strftime("%d/%m/%Y") if pd.notna(r["DatEntradaOperacao"]) else "—"
    if r["Coord_Valida"]:
        coord = f"{r['Latitude']:.5f}, {r['Longitude']:.5f}"
        gmaps = f"<span class='hr-link'><a href='https://www.google.com/maps?q={r['Latitude']:.6f},{r['Longitude']:.6f}' target='_blank'>Abrir no Google Maps ↗</a></span>"
        if r["Status_Coord"] != "Válida":
            coord += f" <span style='color:#92400E;font-size:.75rem'>({esc(r['Status_Coord'])})</span>"
    else:
        coord, gmaps = f"<span style='color:#92400E'>{esc(r['Status_Coord'])}</span>", ""

    regulatorio = grid([
        ("Código CEG", f"<code>{esc(r['CodCEG'])}</code>"),
        ("Potência outorgada", f"{br_num(r['PotOutorgadaKw'], 2)} kW<br><b>{br_num(r['PotOutorgadaMW'], 3)} MW</b>"),
        ("Potência fiscalizada", f"{br_num(r['PotFiscalizadaKw'], 2)} kW · {br_num(r['PotFiscalizadaMW'], 3)} MW"),
        ("Entrada em operação", data_op),
        ("Município(s)/UF", esc(r["Municipios_Texto"])),
        ("Coordenadas", coord),
    ])
    hidro = grid([
        ("Bacia hidrográfica", esc(r["NomBacia"])),
        ("Sub-bacia", esc(r["NomSubBacia"])),
        ("Rio", esc(r["NomRio"])),
    ])
    st.markdown(
        f"""<div class='hr-card' style='border-top:4px solid {cor}'>
              <div><span class='hr-tag' style='background:{cor}'>{esc(r['SigTipoGeracao'])}</span>
              <span class='hr-fase'>{esc(r['DscFaseUsina'])}</span></div>
              <h4 style='margin-top:.45rem'>{esc(r['NomEmpreendimento'])}</h4>
              {gmaps}
              <div class='cap'>Dados regulatórios (ANEEL)</div>{regulatorio}
              <div class='cap'>Localização hidrográfica</div>{hidro}
            </div>""",
        unsafe_allow_html=True,
    )
    render_cadastro(r)


def render_cadastro(r: pd.Series) -> None:
    st.markdown("<div class='hr-section' style='margin-top:.3rem'>Dados cadastrais e contato</div>", unsafe_allow_html=True)
    agentes: list[dict] = list(r["Agentes_Lista"] or [])
    ceg = r["CodCEG"]

    if len(agentes) > 1:
        def _lbl(i: int) -> str:
            a = agentes[i]
            pct = f" — {br_num(a['pct'], 2).rstrip('0').rstrip(',')}%" if pd.notna(a["pct"]) else ""
            return f"{a['nome']}{pct}" + (f" · {format_cnpj(a['cnpj'])}" if a["cnpj"] else "")

        st.caption(f"Consórcio / múltiplos agentes ({len(agentes)}). Agente majoritário selecionado por padrão.")
        idx = st.selectbox("Agente participante", range(len(agentes)), format_func=_lbl, key=f"agente_{ceg}")
        agente = agentes[idx]
    else:
        agente = agentes[0] if agentes else {"nome": NAO_INFORMADO, "cnpj": "", "pct": float("nan")}

    cnpj = agente["cnpj"]
    if not is_valid_cnpj(cnpj):
        st.markdown(
            f"<div class='hr-card'>{grid([('Agente', esc(agente['nome'])), ('Origem', esc(r['Origem_Agentes']))])}</div>",
            unsafe_allow_html=True,
        )
        st.caption("CNPJ não disponível na base da ANEEL para este agente. Informe-o para consultar a Receita Federal (BrasilAPI).")
        cnpj = st.text_input("CNPJ do agente", key=f"cnpj_manual_{ceg}", placeholder="00.000.000/0000-00")
        if not cnpj:
            return
        if not is_valid_cnpj(cnpj):
            st.error("CNPJ inválido (verifique os dígitos).", icon=":material/error:")
            return

    with st.spinner("Consultando BrasilAPI…"):
        info = consultar_cnpj(cnpj)

    if not info["ok"]:
        st.markdown(
            f"<div class='hr-card'>{grid([('Agente', esc(agente['nome'])), ('CNPJ', esc(format_cnpj(cnpj)))])}</div>",
            unsafe_allow_html=True,
        )
        st.warning(info["erro"], icon=":material/cloud_off:")
        return

    situacao_cor = "#047857" if info["situacao"].upper() == "ATIVA" else "#B45309"
    st.markdown(
        f"""<div class='hr-card'>
             {grid([
                ('CNPJ', f"<code>{esc(info['cnpj'])}</code>"),
                ('Razão social', esc(info['razao_social'])),
                ('Nome fantasia', esc(info['nome_fantasia'])),
                ('Situação cadastral', f"<b style='color:{situacao_cor}'>{esc(info['situacao'])}</b>"),
                ('Endereço', esc(info['logradouro'])),
                ('Bairro', esc(info['bairro'])),
                ('Município/UF', esc(info['municipio_uf'])),
                ('CEP', esc(info['cep'])),
             ])}
           </div>""",
        unsafe_allow_html=True,
    )
    contatos = []
    if info["telefones"]:
        contatos.append(("Telefone(s)", " | ".join(info["telefones"])))
    if info["email"]:
        contatos.append(("E-mail", info["email"]))
    if contatos:
        for rotulo, valor in contatos:
            st.caption(rotulo)
            st.code(valor, language=None)
    else:
        st.caption("Telefone e e-mail não informados na Receita Federal.")
    if info["socios"]:
        with st.expander(f"Quadro societário ({info['socios_total']})"):
            st.markdown("\n".join(f"- **{s['nome']}** — {s['qualificacao']}" for s in info["socios"]))
            if info["socios_total"] > len(info["socios"]):
                st.caption(f"Exibindo {len(info['socios'])} de {info['socios_total']} sócios.")


# --------------------------------------------------------------------------- #
# Tabela e exportação
# --------------------------------------------------------------------------- #
TABLE_COLS = {
    "CodCEG": "CEG", "SigTipoGeracao": "Tipo", "NomEmpreendimento": "Nome da Usina", "SigUFPrincipal": "UF",
    "Municipio_Principal": "Município", "NomBacia": "Bacia", "NomRio": "Rio", "DscFaseUsina": "Fase",
    "PotOutorgadaMW": "Potência Outorgada (MW)", "Agente_Principal": "Agente Principal", "CNPJ_Principal": "CNPJ",
}


def render_table(df: pd.DataFrame) -> None:
    left, right = st.columns([0.7, 0.3], vertical_alignment="bottom")
    left.markdown(
        f"<div class='hr-section'>Relação de empreendimentos filtrados "
        f"<span style='color:#64748B;font-weight:500'>({fmt_int(len(df))})</span></div>",
        unsafe_allow_html=True,
    )
    snapshot = df.copy()
    right.download_button(
        "📥 Exportar Dados Filtrados (.xlsx)",
        data=functools.partial(gerar_excel, snapshot),
        file_name=f"hidroradar_usinas_{pd.Timestamp.now():%Y%m%d_%H%M}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=df.empty, width="stretch", type="primary",
    )

    view = df[list(TABLE_COLS)].rename(columns=TABLE_COLS).sort_values(["Tipo", "Nome da Usina"])
    event = st.dataframe(
        view, hide_index=True, width="stretch", height=430, on_select="rerun", selection_mode="single-row",
        key=f"tabela_{signature(view['CEG'])}",
        column_config={
            "CEG": st.column_config.TextColumn(width="medium"),
            "Tipo": st.column_config.TextColumn(width="small"),
            "Nome da Usina": st.column_config.TextColumn(width="large"),
            "UF": st.column_config.TextColumn(width="small"),
            "Potência Outorgada (MW)": st.column_config.NumberColumn(format="%.3f", width="small"),
            "Agente Principal": st.column_config.TextColumn(width="large"),
        },
    )
    rows = (event.selection.rows if event and hasattr(event, "selection") else []) or []
    if rows:
        ceg = view.iloc[rows[0]]["CEG"]
        if ceg != st.session_state.get("_table_pick"):
            st.session_state["_table_pick"] = ceg
            st.session_state["_pending_sel"] = ceg  # aplicado no próximo ciclo, antes do selectbox
            st.rerun()
    st.caption("Dica: selecione uma linha para abrir a ficha da usina. A planilha inclui todas as colunas técnicas, hidrográficas e cadastrais.")


# --------------------------------------------------------------------------- #
# Aplicação
# --------------------------------------------------------------------------- #
def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    if "_pending_sel" in st.session_state:
        st.session_state["sel_usina"] = st.session_state.pop("_pending_sel")

    with st.spinner("Carregando bases da ANEEL (SIGA, hidrografia e agentes)…"):
        try:
            df, meta = load_dataset()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Falha ao carregar os dados: {exc}", icon=":material/error:")
            st.stop()

    header(meta)
    filtered = sidebar_filters(df)
    kpis(filtered)
    st.write("")

    col_map, col_ficha = st.columns([0.65, 0.35], gap="medium")
    with col_map:
        st.markdown("<div class='hr-section'>Mapa dos empreendimentos</div>", unsafe_allow_html=True)
        render_map(filtered)
    with col_ficha:
        render_ficha(filtered)

    st.write("")
    render_table(filtered)

    with st.expander("Sobre os dados e qualidade da base"):
        st.markdown(
            f"""
- **SIGA (empreendimentos):** {meta['fonte_siga']}
- **Bacia / sub-bacia / rio:** {meta['fonte_hidro']} (complementado pelo código ANEEL da sub-bacia quando disponível)
- **Agentes / CNPJ:** {meta['fonte_agentes']} (na ausência, extraídos do campo de proprietários do SIGA)
- **Dados cadastrais:** BrasilAPI (Receita Federal), consultados sob demanda apenas para a usina selecionada
- **Registros UHE/PCH/CGH:** {fmt_int(meta['total_registros'])} · sem coordenadas válidas: {fmt_int(meta['sem_coordenadas'])}
- **Carregado em:** {meta['carregado_em']} (cache de 24 h)
"""
        )
        for aviso in meta.get("avisos", []):
            st.caption(f"⚠️ {aviso}")
        if st.button("Recarregar bases da ANEEL", icon=":material/refresh:"):
            load_dataset.clear()
            st.rerun()

    st.markdown(
        "<div class='hr-footer'>HidroRadar-BR · Dados públicos da Agência Nacional de Energia Elétrica (ANEEL) — "
        "Portal de Dados Abertos. Informações cadastrais via BrasilAPI. Ferramenta de pesquisa sem vínculo oficial com a ANEEL.</div>",
        unsafe_allow_html=True,
    )


main()
