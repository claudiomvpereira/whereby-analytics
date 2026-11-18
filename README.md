# Whereby Analytics

Projeto acadêmico (disciplina **Big Data em Python**). Ele coleta, anonimiza, trata, analisa e visualiza dados reais de videoconferências obtidos pela API REST do Whereby Embedded.

> **Status:** coleta concluída (17/08/2026 a 17/11/2026: 1.783 sessões e 13.517 participações brutas). Falta gerar os dados públicos finais com o segredo definitivo de pseudonimização.

## Privacidade

- Nenhum nome, e-mail, ID original da API ou link de reunião é publicado.
- Participantes são pseudonimizados com HMAC-SHA256 (`Teacher 001`, `Student 001`). O segredo fica numa variável de ambiente.
- Dados brutos e a tabela de correspondência ficam em `data/raw/` e `data/private/`, ambos fora do Git.
- Datas e horários são generalizados nos datasets públicos: data e hora cheia da sessão (fuso de São Paulo). As participações não têm horário absoluto, só minutos relativos ao início da aula.
- Estatísticas por pessoa só aparecem para quem tem 5 ou mais aulas.

## Estrutura

```
src/wa/client.py         cliente da API somente leitura (GET), respeita o rate limit
src/wa/collect.py        coleta incremental → data/private/whereby.sqlite (privado)
src/wa/anonymize.py      pseudonimização HMAC-SHA256 com numeração estável
src/wa/build_dataset.py  limpeza + anonimização → data/public/ (versionado)
src/wa/check_privacy.py  bloqueia qualquer nome/ID original nos arquivos públicos
tests/                   teste ponta a ponta com dados sintéticos
poc/                     prova de conceito da conexão
docs/                    relatório de viabilidade e documentação
```

## Pipeline

```
API Whereby ──collect──> data/private/ (bruto, NÃO versionado)
            ──build_dataset──> data/public/*.parquet|csv + quality_report.json
            ──check_privacy──> OK? → notebook de análise e dashboard
```

O dashboard **não** consulta a API: ele lê apenas `data/public/`.

## Como reproduzir

```bash
pip install -r requirements.txt
cp .env.example .env    # preencha WHEREBY_API_KEY e PSEUDONYM_SECRET (nunca faça commit do .env)
export $(grep -v '^#' .env | xargs)

PYTHONPATH=src python -m wa.collect --since 2026-08-17      # coleta (horas; retomável)
PYTHONPATH=src python -m wa.build_dataset --since 2026-08-17 # gera data/public/
PYTHONPATH=src python -m wa.check_privacy                    # verificação de privacidade
python -m pytest -q tests                                     # testes
```

Sem acesso à API, use diretamente os arquivos de `data/public/`.

### Notebook e dashboard

```bash
jupyter notebook notebooks/02_analise_exploratoria.ipynb   # análise exploratória
streamlit run dashboard/app.py                              # dashboard interativo
```

Veja [`docs/01_relatorio_viabilidade.md`](docs/01_relatorio_viabilidade.md).
