"""Dashboard Whereby Analytics (Streamlit + Plotly).

Lê APENAS os datasets anonimizados de data/public/ — não acessa a API.

    streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
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
        hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(color="#0b0b0b", size=13)),
        paper_bgcolor="white", plot_bgcolor="white", bargap=0.15,
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
def barras_faixas(valores, cortes, nomes, cor, titulo, eixo, unidade, hover):
    """Barra simples por faixas, com a % em cima de cada barra."""
    cont = pd.cut(valores, cortes, labels=nomes).value_counts().reindex(nomes).fillna(0).astype(int)
    df = cont.rename("n").rename_axis("faixa").reset_index()
    df["pct"] = df["n"] / max(df["n"].sum(), 1) * 100
    fig = px.bar(df, x="faixa", y="n", text=df["pct"].map("{:.0f}%".format), color_discrete_sequence=[cor],
                 title=titulo, labels={"faixa": eixo, "n": unidade})
    fig.update_traces(textposition="outside", cliponaxis=False, customdata=df["pct"],
                      hovertemplate=hover + "<extra></extra>")
    fig.update_xaxes(tickangle=0)
    return style(fig, 340), df


with tab2:
    c1, c2 = st.columns(2)

    # 1) duração
    med = s["duration_min"].median()
    fig = px.histogram(s, x="duration_min", color_discrete_sequence=[BLUE], title="Quanto tempo as aulas duraram",
                       labels={"duration_min": "Duração da aula (min)"})
    fig.update_traces(xbins=dict(start=0, end=max(100, s["duration_min"].max()) + 2, size=2),
                      hovertemplate="Aulas com cerca de %{x:.0f} min: %{y}<extra></extra>")
    fig.add_vline(med, line_dash="dash", line_color="#0b0b0b",
                  annotation_text=f"metade das aulas dura até {med:.0f} min", annotation_position="top right")
    c1.plotly_chart(style(fig).update_yaxes(title="Número de aulas"), width="stretch")
    c1.caption(
        "Cada barra conta quantas aulas tiveram aquela duração. O pico perto de **50 min** é a aula padrão; "
        "o grupo perto de **30 min** parece outro formato de aula; as barras perto de **0** são aulas que "
        "caíram ou foram abertas e fechadas logo em seguida."
    )

    # 2) permanência
    if len(pp):
        nomes = ["Menos da metade", "Metade a 90%", "Aula toda (90% ou mais)"]
        perm = (pp.assign(faixa=pd.cut(pp["pct_aula"], [-1, 50, 90, 101], labels=nomes))
                  .groupby(["papel", "faixa"], observed=False).size().rename("n").reset_index())
        perm["pct"] = perm["n"] / perm.groupby("papel")["n"].transform("sum") * 100
        perm["quem"] = perm["papel"].map({"Aluno": "alunos", "Professor": "professores"})
        perm["frase"] = perm["faixa"].map(dict(zip(nomes, [
            "ficaram <b>menos da metade</b> da aula",
            "ficaram <b>entre metade e 90%</b> da aula",
            "ficaram <b>praticamente a aula toda</b> (90% ou mais)"]))).astype(str)
        fmt = lambda v: f"{v:,}".replace(",", ".")
        perm["n_txt"] = perm["n"].map(fmt)
        perm["total_txt"] = perm.groupby("papel")["n"].transform("sum").map(fmt)
        fig = px.bar(perm, y="papel", x="pct", color="faixa", orientation="h",
                     color_discrete_map=dict(zip(nomes, ["#9ec5f4", "#3987e5", "#184f95"])),
                     category_orders={"faixa": nomes, "papel": ["Aluno", "Professor"]},
                     text=perm["pct"].map(lambda v: f"{v:.0f}%" if v >= 5 else ""),
                     title="Quanto da aula cada pessoa ficou na sala", labels={"papel": "", "pct": "% das presenças"},
                     custom_data=["quem", "frase", "n_txt", "total_txt"])
        fig.update_traces(textposition="inside", insidetextanchor="middle", marker_line=dict(width=2, color="white"),
                          hovertemplate=("<b>%{x:.0f}% dos %{customdata[0]}</b><br>%{customdata[1]}<br>"
                                         "<span style='color:#52514e'>%{customdata[2]} de %{customdata[3]} presenças</span>"
                                         "<extra></extra>"))
        fig.update_xaxes(range=[0, 100], ticksuffix="%", title=None)
        c2.plotly_chart(style(fig).update_layout(legend=dict(y=-0.1)), width="stretch")
        al_toda = (pp.loc[pp.role == "student", "pct_aula"] >= 90).mean()
        c2.caption(
            f"Para cada aluno em cada aula, comparamos o tempo na sala com a duração da aula. "
            f"**{al_toda:.0%} dos alunos ficaram praticamente a aula toda.** Os professores quase sempre ficam 100%."
        )
    else:
        c2.info("Sem dados de participantes para o filtro selecionado.")

    st.divider()
    c1, c2 = st.columns(2)

    # 3) pontualidade da aula (proxy de previsto x realizado)
    fig, df = barras_faixas(
        s["slot_offset_min"], [-31, -2, 2, 5, 10, 31],
        ["Adiantada", "No horário<br>(±2 min)", "2 a 5 min<br>depois", "5 a 10 min<br>depois", "Mais de 10<br>min depois"],
        BLUE, "As aulas começaram no horário?", "Início em relação ao horário cheio ou meia hora", "Aulas",
        "%{x}: %{y} aulas (%{customdata:.0f}%)")
    c1.plotly_chart(fig, width="stretch")
    c1.caption(
        "A API não informa o horário agendado. Por isso usamos a grade da escola: as aulas são marcadas em "
        f"horários cheios ou de meia hora (19h00, 19h30…). **{df.loc[1, 'pct']:.0f}% começaram no horário.** "
        "Ex.: uma aula que começou às 19h04 entra em \"2 a 5 min depois\"."
    )

    # 4) entrada do aluno x professor
    prof = pp[pp.role == "teacher"].groupby("session_id")["first_join"].min().rename("prof")
    al = pp[pp.role == "student"].join(prof, on="session_id").dropna(subset=["prof"])
    if len(al):
        fig, df = barras_faixas(
            al["first_join"] - al["prof"], [-1e9, 0, 2, 5, 10, 1e9],
            ["Antes ou<br>junto", "Até 2 min<br>depois", "2 a 5 min<br>depois", "5 a 10 min<br>depois", "Mais de 10<br>min depois"],
            ORANGE, "Os alunos chegaram depois do professor?", "Entrada do aluno em relação ao professor",
            "Presenças de alunos", "%{x}: %{y} presenças (%{customdata:.0f}%)")
        c2.plotly_chart(fig, width="stretch")
        c2.caption(
            "Compara o momento em que cada aluno entrou com o momento em que o professor entrou na mesma aula. "
            f"**{df.loc[0, 'pct'] + df.loc[1, 'pct']:.0f}% dos alunos chegaram antes do professor ou até 2 min depois.**"
        )

# ----------------------------------------------------------------------------- aba 3
with tab3:
    c1, c2 = st.columns(2)
    cont = s["api_unique_participants"].value_counts().sort_index().rename("Aulas").reset_index()
    fig = px.bar(cont, x="api_unique_participants", y="Aulas", color_discrete_sequence=[BLUE],
                 title="Participantes únicos por aula", labels={"api_unique_participants": "Participantes"})
    fig.update_traces(hovertemplate="%{x} participantes: %{y} aulas<extra></extra>")
    c1.plotly_chart(style(fig), width="stretch")

    freq = pp[pp.role == "student"].groupby("person_id")["session_id"].nunique()
    if len(freq):
        faixas = ["1 aula", "2 a 4", "5 a 9", "10 a 19", "20 ou mais"]
        fx = pd.cut(freq, [0, 1, 4, 9, 19, float("inf")], labels=faixas).value_counts().reindex(faixas)
        fx = fx.rename("Alunos").rename_axis("faixa").reset_index()
        fx["pct"] = fx["Alunos"] / fx["Alunos"].sum() * 100
        fig = px.bar(fx, x="faixa", y="Alunos", color_discrete_sequence=[ORANGE], text=fx["pct"].map("{:.0f}%".format),
                     title="Quantas aulas cada aluno assistiu no período", labels={"faixa": "Aulas assistidas"})
        fig.update_traces(textposition="outside", cliponaxis=False,
                          hovertemplate="%{y} alunos assistiram %{x}<extra></extra>")
        c2.plotly_chart(style(fig), width="stretch")
        c2.caption(
            f"Cada barra conta **alunos**. Ex.: {fx.loc[0, 'Alunos']} alunos ({fx.loc[0, 'pct']:.0f}%) aparecem em só 1 aula "
            f"(aula experimental, reposição ou nome digitado de outro jeito); {fx.iloc[-1]['Alunos']} alunos são assíduos, "
            f"com 20 aulas ou mais. Mediana: {freq.median():.0f} aulas por aluno."
        )

    st.subheader("Professores (pseudonimizados)")
    st.caption(
        "Uma linha por professor, ordenada por número de aulas. Professores com menos de 5 aulas "
        "não aparecem (proteção contra reidentificação)."
    )
    t_pp = pp[pp.role == "teacher"].merge(s[["session_id", "n_students"]], on="session_id")
    alunos_prof = (t_pp[["person_id", "session_id"]]
                   .merge(pp[pp.role == "student"][["session_id", "person_id"]].rename(columns={"person_id": "aluno"}),
                          on="session_id")
                   .groupby("person_id")["aluno"].nunique())
    carga = (t_pp.groupby("person_id")
               .agg(aulas=("session_id", "nunique"), horas=("duration_min", "sum"),
                    duracao=("duration_min", "median"), alunos_aula=("n_students", "mean"))
               .join(alunos_prof.rename("alunos"))
               .query("aulas >= 5").sort_values("aulas", ascending=False))
    carga["horas"] = carga["horas"] / 60
    carga = carga.reset_index().rename(columns={
        "person_id": "Professor", "aulas": "Aulas dadas", "horas": "Horas de aula",
        "duracao": "Duração típica (min)", "alunos_aula": "Alunos por aula (média)", "alunos": "Alunos diferentes atendidos"})
    st.dataframe(carga.round(1), hide_index=True, width="stretch")

# ----------------------------------------------------------------------------- aba 4
with tab4:
    q1, q3 = s["duration_min"].quantile([0.25, 0.75])
    iqr = q3 - q1
    lo, hi = max(q1 - 1.5 * iqr, 0), q3 + 1.5 * iqr
    s["atipica_duracao"] = ~s["duration_min"].between(lo, hi)
    s["situacao"] = np.select(
        [s["is_short"], s["duration_min"] < lo, s["duration_min"] > hi],
        ["Muito curta (< 10 min)", "Mais curta que o normal", "Mais longa que o normal"],
        "Dentro do esperado",
    )

    st.markdown(
        f"Cada ponto é **uma aula**. A faixa azul-clara mostra a duração considerada normal "
        f"(**{lo:.0f} a {hi:.0f} min**, calculada pelo critério estatístico IQR). "
        "Pontos fora da faixa são aulas atípicas. Passe o mouse sobre um ponto para ver os detalhes."
    )
    c1, c2 = st.columns([1.5, 1])
    cores = {"Dentro do esperado": "#9ec5f4", "Mais curta que o normal": ORANGE,
             "Mais longa que o normal": "#184f95", "Muito curta (< 10 min)": "#b3261e"}
    fig = go.Figure()
    fig.add_hrect(y0=lo, y1=hi, fillcolor="#cde2fb", opacity=0.45, line_width=0, layer="below")
    for nome, cor in cores.items():
        d = s[s["situacao"] == nome]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(
            x=d["date"], y=d["duration_min"], mode="markers", name=f"{nome} ({len(d)})",
            marker=dict(size=8 if nome != "Dentro do esperado" else 6, color=cor,
                        line=dict(width=1, color="white")),
            customdata=np.stack([d["session_id"], d["room_id"], d["dia"].astype(str),
                                 d["start_hour"], d["api_unique_participants"]], axis=1),
            hovertemplate=(
                "<b>%{customdata[0]}</b> · %{customdata[1]}<br>"
                "%{x|%d/%m/%Y} (%{customdata[2]}), às %{customdata[3]}h<br>"
                "Duração: <b>%{y:.0f} min</b><br>"
                "Participantes: %{customdata[4]}<br>"
                f"<i>{nome}</i><extra></extra>"
            ),
        ))
    fig.update_layout(title="Duração de cada aula ao longo do período", hovermode="closest")
    fig.update_xaxes(tickformat="%d/%m")
    fig.update_yaxes(title="Duração (min)")
    c1.plotly_chart(style(fig, 400), width="stretch")

    resumo = pd.DataFrame({
        "Critério": ["Muito curtas (< 10 min)", "Fora da faixa normal (IQR)", "Só 1 participante",
                     "Conexões que são reconexões"],
        "Aulas": [f"{s['is_short'].sum()} ({s['is_short'].mean():.1%})",
                  f"{s['atipica_duracao'].sum()} ({s['atipica_duracao'].mean():.1%})",
                  f"{(s['api_unique_participants'] <= 1).sum()}",
                  f"{p['is_reconnection'].mean():.1%}" if len(p) else "—"],
        "O que pode indicar": ["queda de conexão, teste ou aluno ausente",
                               f"aula fora de {lo:.0f}–{hi:.0f} min; o grupo de ~30 min parece outro formato de aula",
                               "professor esperou e ninguém entrou",
                               "internet instável (a pessoa saiu e voltou)"],
    })
    st.subheader("Resumo das aulas atípicas")
    st.dataframe(resumo, hide_index=True, width="stretch")
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

    with st.expander("Ver lista das aulas atípicas"):
        tab = s.loc[s["situacao"] != "Dentro do esperado",
                    ["session_id", "room_id", "date", "dia", "start_hour", "duration_min", "api_unique_participants", "situacao"]]
        tab = tab.sort_values("date").assign(date=lambda d: d["date"].dt.strftime("%d/%m/%Y"),
                                             dia=lambda d: d["dia"].astype(str))
        st.dataframe(tab.rename(columns={
            "session_id": "Aula", "room_id": "Sala", "date": "Data", "dia": "Dia", "start_hour": "Hora",
            "duration_min": "Duração (min)", "api_unique_participants": "Participantes", "situacao": "Situação"}),
            hide_index=True, width="stretch")
