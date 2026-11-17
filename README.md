# Whereby Analytics

Projeto acadêmico (disciplina **Big Data em Python**). Ele coleta, anonimiza, trata, analisa e visualiza dados reais de videoconferências obtidos pela API REST do Whereby Embedded.

> **Status:** etapa 1 concluída (viabilidade técnica + prova de conceito). O desenvolvimento das próximas etapas está em andamento.

## Privacidade

- Nenhum nome, e-mail, ID original da API ou link de reunião é publicado.
- Participantes são pseudonimizados com HMAC-SHA256 (`Teacher 001`, `Student 001`). O segredo fica numa variável de ambiente.
- Dados brutos e a tabela de correspondência ficam em `data/raw/` e `data/private/`, ambos fora do Git.
- Datas e horários são generalizados nos datasets públicos (ano-semana, hora cheia).

## Estrutura

```
poc/          prova de conceito da conexão (somente leitura)
docs/         relatório de viabilidade e documentação
```

## Configuração

```bash
cp .env.example .env   # preencha WHEREBY_API_KEY e PSEUDONYM_SECRET (nunca faça commit do .env)
pip install requests python-dotenv
python poc/poc_connection.py
```

Veja [`docs/01_relatorio_viabilidade.md`](docs/01_relatorio_viabilidade.md).
