# ⚡ HidroRadar-BR

**Painel de pesquisa de usinas hidrelétricas do Brasil — UHE, PCH e CGH.**

Aplicação Streamlit que integra automaticamente os dados abertos da **ANEEL**
(SIGA, base hidrográfica e agentes de geração) e permite filtrar, mapear, inspecionar
e exportar empreendimentos hidrelétricos, com enriquecimento cadastral por CNPJ
sob demanda (BrasilAPI / Receita Federal).

---

## Funcionalidades

| Área | O que faz |
|---|---|
| **Pipeline de dados** | Descobre via API CKAN as URLs atualizadas dos CSVs da ANEEL, trata encoding/separador, vírgulas decimais, coordenadas (graus decimais ou GMS, com correção de sinal), municípios múltiplos e datas. Cache de 24 h. |
| **Integração hidrográfica** | *Left join* por `CodCEG` com a base de Aproveitamentos Hidrelétricos (bacia, sub-bacia, rio). Quando a bacia falta, ela é derivada do código ANEEL da sub-bacia (ex.: `64` → Rio Paraná). Nulos viram `"Não informado"`; coluna auxiliar `Bacia_Rio`. |
| **Agentes / CNPJ** | *Merge* com a relação CEG × CNPJ × Agente, agrupando consórcios e destacando o agente majoritário. Na ausência desse dataset, os agentes são extraídos do campo `DscPropriRegimePariticipacao` do SIGA. |
| **Filtros encadeados** | Tipo (PCH e CGH marcados por padrão, UHE a um clique) → UF → Município → Bacia → Sub-bacia → Rio → Fase → Faixa de potência (ajustada ao recorte) → busca textual (nome, CEG, rio, agente ou CNPJ). Botão **Limpar filtros**. |
| **KPIs** | Total filtrado, quantidade de UHE/PCH/CGH e potência outorgada (MW, ou GW acima de 1.000 MW). |
| **Mapa** | Folium + CartoDB Positron (OpenStreetMap como camada alternativa), clusters por tipo com cores próprias, tooltip com nome, tipo, rio/bacia, município/UF e potência. Clique no ponto → ficha + destaque no mapa. |
| **Ficha do empreendimento** | Dados regulatórios e hidrográficos, link para o Google Maps e dados cadastrais via BrasilAPI (razão social, situação, endereço, telefone, e-mail copiáveis, quadro societário). Em consórcios, alterna entre os CNPJs participantes. |
| **Tabela + Excel** | Tabela com seleção de linha (abre a ficha) e exportação `.xlsx` com 3 abas formatadas: `Usinas_Filtradas`, `Resumo_por_UF`, `Resumo_por_Bacia`. A planilha é gerada só no clique. |

## Estrutura

```
HidroRadar-BR/
├── app.py                      # Aplicação Streamlit (layout, filtros, mapa, ficha, tabela)
├── src/
│   ├── data_pipeline.py        # Coleta CKAN ANEEL, limpeza e merge SIGA + Hidrografia + Agentes
│   ├── cnpj_service.py         # Consulta BrasilAPI (cache 7 dias, só sucessos são cacheados)
│   └── excel_export.py         # Geração do .xlsx formatado (3 abas) em memória
├── data/
│   ├── snapshots/              # Fallback automático (gerado quando a API responde)
│   └── sample/                 # Amostra FICTÍCIA de demonstração (último recurso)
├── tests/test_hidroradar.py    # Testes de regressão (pytest, rodam offline)
├── .streamlit/
│   ├── config.toml             # Tema visual clean/institucional
│   └── secrets.toml.example    # Configurações opcionais
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## Fontes de dados

| Base | Origem | Uso |
|---|---|---|
| **SIGA — Sistema de Informações de Geração** | [Portal de Dados Abertos ANEEL](https://dadosabertos.aneel.gov.br/dataset/siga-sistema-de-informacoes-de-geracao-da-aneel) — `package_show?id=siga-sistema-de-informacoes-de-geracao-da-aneel`, recurso `siga-empreendimentos-geracao-diario.csv` ou `siga-empreendimentos-geracao.csv` | Base principal (filtrada para UHE, PCH e CGH) |
| **Aproveitamentos Hidrelétricos** | Portal de Dados Abertos ANEEL (descoberto via `package_show` / `package_search`) | Bacia, sub-bacia e rio por `CodCEG` |
| **Agentes de Geração** | Portal de Dados Abertos ANEEL (descoberto via `package_show` / `package_search`) | Relação CEG × CNPJ × Agente |
| **Cadastro CNPJ** | [BrasilAPI](https://brasilapi.com.br/docs#tag/CNPJ) — `/api/cnpj/v1/{cnpj}` | Razão social, endereço, contato e QSA (sob demanda) |

### Resiliência da coleta

1. **Descoberta dinâmica**: o app consulta a API CKAN e escolhe o recurso CSV pelo nome e pela data de modificação. Mudanças no hash da URL não quebram nada.
2. **Colunas por *alias***: nomes como `NumCoordNEmpreendimento` / `NumCoordNorteEmpreendimento` ou `DscMuninicpios` / `DscMunicipios` são reconhecidos sem diferenciar acentos e maiúsculas.
3. **Validação de layout**: um recurso só é aceito se tiver as colunas necessárias (ex.: `CodCEG` + bacia/rio).
4. **Fallback em camadas**, nesta ordem: API ANEEL → `data/snapshots/*.csv.gz` → `data/siga.csv` (arquivo manual opcional) → amostra de demonstração (sinalizada na interface com o selo **Modo demonstração**).
5. **URLs fixas opcionais**: se a ANEEL renomear algum dataset, defina `HIDRORADAR_SIGA_CSV_URL`, `HIDRORADAR_HIDRO_CSV_URL` ou `HIDRORADAR_AGENTES_CSV_URL` (variável de ambiente ou `st.secrets`).

> **Importante:** os identificadores dos datasets de *Aproveitamentos Hidrelétricos* e de *Agentes de Geração* variam entre publicações da ANEEL. Por isso o app os procura por ID e por busca textual. Se a seção **"Sobre os dados e qualidade da base"** mostrar uma dessas fontes como *Indisponível*, copie a URL do CSV no portal e configure a variável correspondente.

## Rodando localmente

Requisitos: **Python 3.11+**.

```bash
git clone https://github.com/<seu-usuario>/HidroRadar-BR.git
```

```bash
cd HidroRadar-BR
```

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

(no Windows: `.venv\Scripts\activate`)

```bash
pip install -r requirements.txt
```

```bash
streamlit run app.py
```

O app abre em `http://localhost:8501`. A primeira carga baixa as bases da ANEEL (pode levar alguns segundos); as seguintes usam o cache de 24 h. Para forçar a atualização, use **"Recarregar bases da ANEEL"** na seção *Sobre os dados*.

### Gerar/atualizar o fallback local (recomendado antes do deploy)

```bash
python -m src.data_pipeline
```

Esse comando baixa as bases e grava `data/snapshots/*.csv.gz` (apenas as usinas hídricas, arquivos pequenos). **Faça commit desses arquivos**: se a API da ANEEL oscilar no Streamlit Cloud, o app usa o snapshot em vez da amostra de demonstração.

### Testes

```bash
pip install -r requirements-dev.txt
```

```bash
pytest
```

## Deploy no Streamlit Community Cloud

1. Publique o repositório no GitHub (veja abaixo).
2. Acesse [share.streamlit.io](https://share.streamlit.io) e entre com sua conta GitHub.
3. Clique em **Create app** → **Deploy a public app from GitHub**.
4. Preencha:
   - **Repository:** `<seu-usuario>/HidroRadar-BR`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** por exemplo `hidroradar-br`
5. Em **Advanced settings**, selecione **Python 3.12** (ou 3.11). *Secrets* são opcionais (veja `.streamlit/secrets.toml.example`).
6. Clique em **Deploy**. O Cloud instala o `requirements.txt` automaticamente e aplica o tema de `.streamlit/config.toml`.

**Checklist pós-deploy**

- [ ] O selo do cabeçalho mostra **"Fonte: API ANEEL (SIGA)"** (e não *Modo demonstração*).
- [ ] Em *Sobre os dados*, bacia/rio e agentes aparecem como **API ANEEL**. Se aparecer *Indisponível*, configure a URL em *Secrets*.
- [ ] O clique numa usina abre a ficha e a consulta de CNPJ retorna dados.
- [ ] A exportação `.xlsx` baixa com as 3 abas.
- [ ] (Opcional) `data/snapshots/` commitado para o fallback.

## Notas técnicas

- **Cache:** bases ANEEL com `@st.cache_data(ttl=86400)`; consultas de CNPJ com `ttl=604800`. Erros de CNPJ não são cacheados, então uma falha temporária pode ser tentada de novo.
- **BrasilAPI:** chamada apenas para a usina selecionada (nunca em lote), com timeout de 5 s e mensagens específicas para 404, 429 (limite de requisições) e 5xx.
- **Mapa:** cada tipo de usina é uma camada GeoJSON dentro de um `MarkerCluster` (cerca de 0,2 s para 2.600 pontos), com tooltip vinculado a cada ponto para identificar o CEG exato no clique.
- **Coordenadas:** valores fora do território brasileiro são descartados do mapa (a usina continua na tabela e no Excel). Longitudes/latitudes com sinal omitido são corrigidas e sinalizadas como *Corrigida (sinal)*.

## Licença e aviso

Dados públicos da Agência Nacional de Energia Elétrica (ANEEL) e da Receita Federal (via BrasilAPI).
Ferramenta independente de pesquisa, **sem vínculo oficial com a ANEEL**. A amostra em `data/sample/` é **fictícia** e serve apenas para demonstração offline.
