"""Dashboard Whereby Analytics (Streamlit + Plotly).

Lê APENAS os datasets anonimizados de data/public/ — não acessa a API.

    streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = Path(os.getenv("WA_PUBLIC_DIR", ROOT / "data" / "public"))

BLUE, ORANGE, MUTED, GRID = "#2a78d6", "#eb6834", "#52514e", "#e6e5e0"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIAS = {"Monday": "Seg", "Tuesday": "Ter", "Wednesday": "Qua", "Thursday": "Qui",
        "Friday": "Sex", "Saturday": "Sáb", "Sunday": "Dom"}
ORDEM_DIAS = list(DIAS.values())
PAPEL = {"teacher": "Professor", "student": "Aluno"}

st.set_page_config(page_title="Whereby Analytics", page_icon="📊", layout="wide")


@st.cache_data
def load():
    s = pd.read_parquet(PUBLIC / "sessions.parquet")
    p = pd.read_parquet(PUBLIC / "participations.parquet")
    q = json.loads((PUBLIC / "quality_report.json").read_text())
    s["date"] = pd.to_datetime(s["date"])
    s["dia"] = pd.Categorical(s["weekday"].map(DIAS), ORDEM_DIAS, ordered=True)
    s["turno"] = pd.cut(s["start_hour"], [-1, 11, 17, 23], labels=["Manhã", "Tarde", "Noite"])
    return s, p, q


def style(fig: go.Figure, height=320) -> go.Figure:
    fig.update_layout(
        template="plotly_white", height=height, margin=dict(l=10, r=10, t=40, b=10),
        font=dict(color=MUTED), title_font=dict(size=15, color="#0b0b0b"),
        legend=dict(orientation="h", yanchor="top", y=-0.12, x=0, title=None),
        hoverlabel=dict(bgcolor="white"), bargap=0.15,
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


s_all, p_all, quality = load()

# ----------------------------------------------------------------------------- filtros
st.title("Whereby Analytics")
st.caption(
    "Aulas online de uma escola de inglês · dados reais da API Whereby, **anonimizados** "
    "(Teacher/Student NNN) · horários em America/Sao_Paulo"
)
with st.sidebar:
    st.header("Filtros")
    dmin, dmax = s_all["date"].min().date(), s_all["date"].max().date()
    periodo = st.date_input("Período", (dmin, dmax), min_value=dmin, max_value=dmax)
    dias = st.multiselect("Dia da semana", ORDEM_DIAS, default=ORDEM_DIAS)
    turnos = st.multiselect("Turno", ["Manhã", "Tarde", "Noite"], default=["Manhã", "Tarde", "Noite"])
    salas = st.multiselect("Sala (turma)", sorted(s_all["room_id"].unique()), placeholder="Todas")
    st.divider()
    st.caption("Fonte: `data/public/` (sem dados pessoais). Agosto e novembro são meses parciais.")

ini, fim = (periodo if isinstance(periodo, tuple) and len(periodo) == 2 else (dmin, dmax))
mask = (
    s_all["date"].between(pd.Timestamp(ini), pd.Timestamp(fim))
    & s_all["dia"].isin(dias)
    & s_all["turno"].isin(turnos)
)
if salas:
    mask &= s_all["room_id"].isin(salas)
s = s_all[mask].copy()
p = p_all[p_all["session_id"].isin(s["session_id"])].copy()
if s.empty:
    st.warning("Nenhuma aula com os filtros selecionados.")
    st.stop()

pp = (p.dropna(subset=["person_id"])
        .groupby(["session_id", "person_id", "role"], as_index=False)
        .agg(stay_min=("stay_min", "sum"), first_join=("join_offset_min", "min"), conexoes=("segment_n", "max")))
pp = pp.merge(s[["session_id", "duration_min"]], on="session_id")
pp["pct_aula"] = (pp["stay_min"] / pp["duration_min"]).clip(upper=1.0) * 100
pp["papel"] = pp["role"].map(PAPEL)

# ----------------------------------------------------------------------------- KPIs
k = st.columns(6)
k[0].metric("Aulas realizadas", f"{len(s):,}".replace(",", "."))
k[1].metric("Duração mediana", f"{s['duration_min'].median():.0f} min")
k[2].metric("Participantes por aula", f"{s['api_unique_participants'].mean():.1f}")
k[3].metric("Horas de aula", f"{s['duration_min'].sum() / 60:,.0f} h".replace(",", "."))
k[4].metric("Alunos distintos", f"{pp.loc[pp.role == 'student', 'person_id'].nunique():,}".replace(",", "."))
k[5].metric("Aulas < 10 min", f"{s['is_short'].mean():.1%}", help="Possíveis quedas, testes ou ausências")

tab1, tab2, tab3, tab4 = st.tabs(["📅 Volume e horários", "⏱️ Duração e permanência", "👥 Participação", "⚠️ Qualidade e atípicas"])

# ----------------------------------------------------------------------------- aba 1
with tab1:
    c1, c2 = st.columns([1.4, 1])
    semanal = s.groupby(s["date"].dt.to_period("W-SUN").dt.start_time).size().rename("Aulas").reset_index()
    fig = px.bar(semanal, x="date", y="Aulas", title="Aulas por semana", color_discrete_sequence=[BLUE])
    fig.update_traces(hovertemplate="Semana de %{x|%d/%m}<br>%{y} aulas<extra></extra>")
    fig.update_xaxes(title=None, tickformat="%d/%m")
    c1.plotly_chart(style(fig), width="stretch")

    diario = s.groupby("dia", observed=False).size().rename("Aulas").reset_index()
    fig = px.bar(diario, x="dia", y="Aulas", title="Aulas por dia da semana", color_discrete_sequence=[BLUE])
    fig.update_traces(hovertemplate="%{x}: %{y} aulas<extra></extra>")
    fig.update_xaxes(title=None)
    c2.plotly_chart(style(fig), width="stretch")

    heat = s.pivot_table(index="dia", columns="start_hour", values="session_id", aggfunc="count", observed=False).fillna(0)
    heat = heat.reindex(columns=range(int(s_all.start_hour.min()), int(s_all.start_hour.max()) + 1), fill_value=0)
    fig = go.Figure(go.Heatmap(
        z=heat.values, x=[f"{h}h" for h in heat.columns], y=heat.index.astype(str), colorscale=SEQ,
        xgap=2, ygap=2, colorbar=dict(title="Aulas"),
        hovertemplate="%{y}, %{x}: %{z:.0f} aulas<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(style(fig.update_layout(title="Mapa de calor: dia × hora de início"), 340), width="stretch")

# ----------------------------------------------------------------------------- aba 2
with tab2:
    c1, c2 = st.columns(2)
    fig = px.histogram(s, x="duration_min", nbins=40, title="Distribuição da duração das aulas",
                       color_discrete_sequence=[BLUE], labels={"duration_min": "Duração (min)"})
    fig.add_vline(s["duration_min"].median(), line_dash="dash", line_color="#0b0b0b",
                  annotation_text=f"mediana {s['duration_min'].median():.0f} min")
    fig.update_traces(hovertemplate="%{x} min: %{y} aulas<extra></extra>")
    c1.plotly_chart(style(fig).update_yaxes(title="Aulas"), width="stretch")

    if len(pp):
        fig = px.box(pp, x="papel", y="pct_aula", color="papel", points=False,
                     color_discrete_map={"Professor": BLUE, "Aluno": ORANGE},
                     title="Permanência (% da duração da aula)", labels={"pct_aula": "% da aula", "papel": ""})
        c2.plotly_chart(style(fig).update_layout(showlegend=False), width="stretch")
    else:
        c2.info("Sem dados de participantes para o filtro selecionado.")

    c1, c2 = st.columns(2)
    fig = px.histogram(s, x="slot_offset_min", nbins=30, color_discrete_sequence=[BLUE],
                       title="Início real vs. horário da grade (:00/:30)",
                       labels={"slot_offset_min": "Minutos após o horário cheio/meia hora"})
    fig.add_vline(0, line_color="#0b0b0b")
    c1.plotly_chart(style(fig).update_yaxes(title="Aulas"), width="stretch")
    c1.caption("Aproximação de *previsto × realizado*: a API não informa o horário agendado.")

    prof = pp[pp.role == "teacher"].groupby("session_id")["first_join"].min().rename("prof")
    al = pp[pp.role == "student"].join(prof, on="session_id").dropna(subset=["prof"])
    if len(al):
        al["atraso"] = (al["first_join"] - al["prof"]).clip(-15, 30)
        fig = px.histogram(al, x="atraso", nbins=45, color_discrete_sequence=[ORANGE],
                           title="Entrada do aluno em relação ao professor",
                           labels={"atraso": "Minutos (negativo = antes do professor)"})
        fig.add_vline(0, line_color="#0b0b0b")
        c2.plotly_chart(style(fig).update_yaxes(title="Alunos-aula"), width="stretch")

# ----------------------------------------------------------------------------- aba 3
with tab3:
    c1, c2 = st.columns(2)
    cont = s["api_unique_participants"].value_counts().sort_index().rename("Aulas").reset_index()
    fig = px.bar(cont, x="api_unique_participants", y="Aulas", color_discrete_sequence=[BLUE],
                 title="Participantes únicos por aula", labels={"api_unique_participants": "Participantes"})
    fig.update_traces(hovertemplate="%{x} participantes: %{y} aulas<extra></extra>")
    c1.plotly_chart(style(fig), width="stretch")

    freq = pp[pp.role == "student"].groupby("person_id")["session_id"].nunique().rename("aulas").reset_index()
    if len(freq):
        fig = px.histogram(freq, x="aulas", color_discrete_sequence=[ORANGE],
                           title="Frequência: aulas assistidas por aluno", labels={"aulas": "Aulas no período"})
        c2.plotly_chart(style(fig).update_yaxes(title="Alunos"), width="stretch")

    st.subheader("Professores (pseudonimizados)")
    st.caption("Grupos com menos de 5 aulas não são exibidos individualmente (proteção contra reidentificação).")
    carga = (pp[pp.role == "teacher"].groupby("person_id")
               .agg(aulas=("session_id", "nunique"), permanencia_mediana_pct=("pct_aula", "median"))
               .query("aulas >= 5").sort_values("aulas", ascending=False).round(1))
    st.dataframe(carga, width="stretch")

# ----------------------------------------------------------------------------- aba 4
with tab4:
    q1, q3 = s["duration_min"].quantile([0.25, 0.75])
    iqr = q3 - q1
    s["atipica_duracao"] = ~s["duration_min"].between(q1 - 1.5 * iqr, q3 + 1.5 * iqr)
    c1, c2 = st.columns([1.4, 1])
    fig = px.scatter(s, x="date", y="duration_min", color=s["atipica_duracao"].map({True: "Atípica (IQR)", False: "Esperada"}),
                     color_discrete_map={"Esperada": BLUE, "Atípica (IQR)": ORANGE}, opacity=0.6,
                     title="Duração de cada aula (atípicas pelo critério IQR)",
                     labels={"date": "", "duration_min": "Duração (min)"},
                     hover_data={"session_id": True, "room_id": True, "date": "|%d/%m/%Y"})
    fig.update_traces(marker=dict(size=7, line=dict(width=1, color="white")))
    c1.plotly_chart(style(fig, 360), width="stretch")

    resumo = pd.DataFrame({
        "Critério": ["Muito curtas (< 10 min)", "Duração atípica (IQR)", "Apenas 1 participante",
                     "Conexões que são reconexões"],
        "Valor": [f"{s['is_short'].sum()} ({s['is_short'].mean():.1%})",
                  f"{s['atipica_duracao'].sum()} ({s['atipica_duracao'].mean():.1%})",
                  f"{(s['api_unique_participants'] <= 1).sum()}",
                  f"{p['is_reconnection'].mean():.1%}" if len(p) else "—"],
    })
    c2.subheader("Aulas atípicas")
    c2.dataframe(resumo, hide_index=True, width="stretch")
    c2.subheader("Tratamento dos dados")
    inc = quality.get("inconsistencies", {})
    c2.dataframe(pd.DataFrame({
        "Regra": ["Duplicatas removidas (sessões)", "Duplicatas removidas (conexões)",
                  "Sessões em andamento excluídas", "Durações inválidas excluídas",
                  "Saídas ausentes imputadas", "Saída antes da entrada (removidas)"],
        "Registros": [quality["duplicates_removed"]["sessions"], quality["duplicates_removed"]["participations"],
                      inc.get("sessions_ongoing_without_end", 0), inc.get("sessions_invalid_duration", 0),
                      inc.get("participations_without_left_imputed", 0), inc.get("participations_left_before_join", 0)],
    }), hide_index=True, width="stretch")

    with st.expander("Ver aulas atípicas (tabela)"):
        cols = ["session_id", "room_id", "date", "weekday", "start_hour", "duration_min", "api_unique_participants"]
        st.dataframe(s.loc[s["atipica_duracao"] | s["is_short"], cols].sort_values("date"),
                     hide_index=True, width="stretch")
