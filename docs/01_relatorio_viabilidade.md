# Whereby Analytics — Relatório de Viabilidade Técnica (Etapa 1)

Data da investigação: 17/11/2026 · Fonte: documentação oficial (docs.whereby.com) + prova de conceito somente leitura.

## 1. Conclusão

**O projeto é viável.** A API REST do Whereby Embedded expõe o histórico de salas, sessões e participantes desde **26/03/2025** (cerca de 20 meses). A prova de conceito encontrou **9.854 sessões** e estima cerca de **52 mil registros de participação**. Isso é muito acima dos 1.000 registros exigidos.

## 2. Endpoints (todos `GET`, base `https://api.whereby.dev/v1`)

| Endpoint | Uso no projeto | Paginação / filtros documentados |
|---|---|---|
| `GET /insights/rooms` | Lista de salas com totais agregados | `cursor`, `limit` (1–100, padrão 10), `sortBy`, `roomName[exact\|startsWith\|contains]`, `createdAt[from\|to]` |
| `GET /insights/room-sessions` | **Sessões = reuniões realizadas** (início e fim reais) | `roomName` (obrigatório), `roomSessionId`, `sortBy`, `cursor`, `limit` ≤ 100 |
| `GET /insights/participants` | **Participações** (entrada e saída de cada conexão) | `roomSessionId` ou `externalId` (um dos dois é obrigatório), `sortBy=joinedAt\|leftAt:asc\|desc`, `cursor`, `limit` ≤ 100 |
| `GET /meetings` | Inventário de salas e o vínculo com `meetingId` (uso auxiliar) | `cursor`, `limit` ≤ 100 (padrão 50) |
| `GET /insights/participant` | Qualidade de rede por participante (opcional, custo alto) | `roomSessionId` + `participantId` |

**Não serão usados:** `POST`/`DELETE` em `/meetings`, nenhum endpoint de `/recordings` ou `/transcriptions` (gravações e transcrições estão fora do escopo por privacidade) e os endpoints `PUT` de `/rooms`.

### Campos observados na API real

- **Sala:** `roomName`, `createdAt`, `endAt`, `endedAt`*, `firstSessionStartedAt`, `lastSessionStartedAt`, `totalParticipantMinutes`, `totalUniqueParticipants`, `totalSessions`, `totalRecorder*`, `totalStreamer*`
- **Sessão:** `roomSessionId`, `roomName`, `roomMode`*, `startedAt`, `endedAt`, `totalParticipantMinutes`, `totalUniqueParticipants`, `totalRecorder*`, `totalStreamer*`
- **Participante:** `roomSessionId`, `participantId`, `deviceId`, `userId`, `roleName`, `displayName`, `joinedAt`, `leftAt`, `externalId`, `userAgent`

\* Retornados pela API, mas ausentes da documentação. O código vai tratá-los como opcionais.

Os campos `rating` e `totalRatings` aparecem na documentação, mas não vieram nas respostas. A funcionalidade de avaliação de chamadas (beta) não está ativa na conta.

## 3. Relacionamento entre as entidades

```
room (roomName) 1───N session (roomSessionId) 1───N participation (participantId, joinedAt, leftAt)
meeting (meetingId) 1───1 room (roomName)
```

Uma sessão começa quando há pelo menos 2 pessoas na sala. Cada reconexão de um participante gera um novo registro ("segmento").

## 4. Limitações identificadas

1. **Plano:** só o *Whereby Embedded* tem acesso à API e aos webhooks. O *Whereby Meetings* não tem. A conta atual é Embedded e funcionou.
2. **Rate limit:** sistema de pontos (1 a 10 pontos por requisição). O plano Build tem 100 pontos/min e o Enterprise, 1.000 pontos/min. A cota é **compartilhada com a produção**. A prova de conceito recebeu dois `429`. Por isso a coleta completa precisa ser lenta (cerca de 20 a 30 requisições/min), incremental e com checkpoints, levando algumas horas. Assim não disputamos a cota com o sistema da empresa.
3. **Retenção:** a documentação **não informa** um prazo de retenção. O dado mais antigo encontrado é de 26/03/2025.
4. **Defasagem:** os dados de insights levam de 10 a 15 minutos para aparecer.
5. **Salas permanentes:** os `endDate` são 2040 e 2099, então as salas são fixas e reutilizadas (196 salas). Isso tem duas consequências:
   - **Não há horário previsto** na API. A **análise 6 (previsto × realizado) não é viável** só com o Whereby.
   - A sala funciona como um proxy de turma ou professor.
6. **Identidade dos participantes:** `externalId` e `userId` vêm vazios. A única chave estável de pessoa é o `displayName`, que é texto livre digitado pelo usuário, e o `deviceId`, que identifica o navegador. Por isso o acompanhamento de frequência por pessoa é **aproximado**: precisa normalizar o nome antes de gerar o hash e documentar essa limitação.
7. **Papéis:** `roleName` traz `host` (professor) ou `granted_visitor` (aluno admitido). Isso permite separar Teacher e Student.
8. **Chave de API:** a documentação não oferece escopo somente leitura. Por isso usamos uma chave dedicada, com validade curta, e o código bloqueia qualquer método diferente de `GET`.

## 5. Viabilidade das análises

| # | Análise | Viável? | Base |
|---|---|---|---|
| 1 | Reuniões por dia, semana e mês | ✅ | `session.startedAt` |
| 2 | Duração média | ✅ | `endedAt − startedAt` |
| 3 | Distribuição de horários | ✅ | `startedAt` (UTC → America/Sao_Paulo) |
| 4 | Participantes por reunião | ✅ | `totalUniqueParticipants` e contagem de participações |
| 5 | Tempo de permanência | ✅ | `leftAt − joinedAt`, somando os segmentos |
| 6 | Previsto × realizado | ❌ | Não há agenda na API; possível só com uma fonte externa (fora do escopo) |
| 7 | Sessões curtas ou atípicas | ✅ | Duração, IQR/z-score, sessões com 1 participante, reconexões |
| 8 | Frequência de participação | ⚠️ aproximada | Pseudônimo a partir do `displayName` normalizado |

Análises extras possíveis: atraso do aluno em relação ao professor (`joinedAt`), taxa de reconexão, `roomMode` e sazonalidade.

## 6. Webhooks (dados futuros)

São configurados no painel (*Configure → Webhooks*) e assinados com HMAC-SHA256 no header `Whereby-Signature`. Os eventos são `room.client.joined/left`, `room.session.started/ended`, entre outros. Há no máximo 2 tentativas de reenvio, e os eventos podem chegar duplicados ou fora de ordem.

**Recomendação: não usar.** O histórico da API já basta, e um webhook exigiria um endpoint público e uma configuração na conta de produção, o que contraria o requisito de independência.

## 7. Privacidade e anonimização

- O pseudônimo é `HMAC-SHA256(PSEUDONYM_SECRET, tipo + valor_normalizado)`. A ordem estável dos hashes define a numeração (Teacher 001, Student 001, Room 001, Session 000001). A tabela de correspondência fica só em `data/private/`, que está no `.gitignore`.
- O que é **removido** dos dados públicos: `displayName`, `roomName`, `roomUrl`, `meetingId`, `roomSessionId`, `participantId`, `deviceId`, `userId`, `externalId` e `userAgent`. Do `userAgent`, no máximo derivamos a família do navegador e do sistema operacional.
- **Risco de reidentificação:** nome da sala + data/hora exata + professor permite cruzar com a agenda da escola. Nos dados públicos:
  - os horários vão para o fuso de São Paulo, arredondados para a hora cheia;
  - a data é trocada por ano-semana, mais o dia da semana;
  - as durações são mantidas em minutos inteiros.
- Grupos com menos de 5 sessões ficam fora dos gráficos por pessoa (supressão de células pequenas).

## 8. Arquitetura e modelo de dados

```
whereby-analytics/
├── src/wa/            config.py, client.py (GET-only), collect.py, anonymize.py, clean.py
├── data/raw/          JSONL bruto (privado, .gitignore)
├── data/private/      SQLite com checkpoints e tabela de pseudônimos (privado)
├── data/public/       Parquet/CSV anonimizados (versionados)
├── notebooks/         02_limpeza.ipynb, 03_eda.ipynb
├── dashboard/app.py   Streamlit + Plotly
└── docs/              relatório, prints, dicionário de dados
```

Fluxo: **API → raw (privado) → anonimização → clean → public → EDA/dashboard**. O GitHub só recebe `data/public/`.

**Datasets públicos:**

- `rooms.parquet`: `room_id` (Room 001), `created_month`, `total_sessions`, `room_mode`
- `sessions.parquet`: `session_id`, `room_id`, `start_week`, `weekday`, `start_hour_local`, `duration_min`, `n_unique_participants`, `participant_minutes`, `room_mode`
- `participations.parquet`: `session_id`, `person_id` (Teacher/Student NNN), `role`, `join_offset_min`, `stay_min`, `segment_n`, `browser_family`

## 9. Plano de execução

1. **Coleta:** cliente só de leitura, paginação, backoff para 429, checkpoint em SQLite e coleta incremental. Gera `raw/`.
2. **Anonimização:** HMAC, mapeamento privado e generalização de datas. Inclui um teste automático que falha se algum dado público contiver nome ou ID original.
3. **Limpeza:** ausentes, duplicatas (eventos repetidos, segmentos sobrepostos), inconsistências (`leftAt < joinedAt`, durações negativas ou acima de 6 h), conversão de fuso e relatório de qualidade.
4. **EDA (notebook):** análises 1, 2, 3, 4, 5, 7 e 8 com Matplotlib/Seaborn, além das conclusões.
5. **Dashboard Streamlit:** pelo menos 3 KPIs (sessões, duração média, participantes/sessão, minutos totais) e pelo menos 4 gráficos, com filtros por período e sala.
6. **Documentação:** README, relatório, prints e dicionário de dados. Revisão final de privacidade antes de tornar o repositório público.

## 10. Prova de conceito (resultado real, sem dados pessoais)

| Métrica | Valor |
|---|---|
| Salas (`/insights/rooms`) | 196, todas com sessões |
| Meetings (`/meetings`) | 184 |
| Sessões (Σ `totalSessions`) | **9.854** |
| Minutos-participante | 2.424.062 |
| Período | 26/03/2025 → 13/11/2026 (última sessão em 17/11/2026) |
| Amostra (5 salas) | 316 sessões; 80 participações em 15 sessões (5,3 por sessão) |
| Papéis na amostra | `host` 17, `granted_visitor` 63 |
| Participações estimadas | cerca de 52.500 |

Script: `poc/poc_connection.py`. Ele só faz `GET`, não grava nada em disco e oculta os campos sensíveis.
