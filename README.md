# P2P com Balanceamento de Carga Dinâmico — Sprint 1

**Sprint 1: base de comunicação e identidade.**
Um **master** e vários **workers** rodam como processos separados (na mesma máquina ou em
computadores diferentes) e conversam por **TCP** usando um protocolo próprio em **NDJSON**.

O que já funciona:

- master e worker são executáveis separados, com **configuração persistente** (UUID, label, host, port);
- `send`/`recv` com **buffer NDJSON**, **validação do envelope**, **dispatcher por `type`** e **correlação de respostas** por `request_id`;
- `register_worker` → `registration_ack` e `heartbeat` → `heartbeat_ack`, cada um em **threads de rede independentes**;
- detecção de queda (conexão fechada **ou** falta de heartbeat) e **reconexão sem duplicar cadastro**;
- **identidade duplicada** recusada; JSON inválido, fragmentação e mensagens coladas **não derrubam** os processos;
- logs com **grupo, origem, destino, type e request_id** no terminal e em `logs/<label>.log`.

> Balanceamento de carga, tarefas e empréstimo de workers entre masters chegam nas Sprints 2–4.
> A Sprint 1 constrói a fundação que elas vão usar.

---

## Requisitos

- **Python 3.10 ou mais novo** (testado no 3.14). Só biblioteca padrão: **nada para instalar**.
- Opcional: `pip install pytest` se preferir rodar os testes com pytest.

## Execução rápida (um computador, três terminais)

```bash
# Terminal 1
python master.py

# Terminal 2
python worker.py --config config/worker1.json

# Terminal 3
python worker.py --config config/worker2.json
```

Na primeira execução, cada processo gera seu UUID e o **salva** no próprio arquivo de config.
Feche um worker com `Ctrl+C`: o master marca `OFFLINE`. Rode de novo: o master mostra
`WORKER RECONECTADO` e continua com **um** cadastro por worker.

Teste de enquadramento ao vivo (com o master rodando):

```bash
python tools/sonda.py
```

## Entre dois computadores

1. No computador do master, descubra o IP (`ipconfig` no Windows) — o master também imprime os IPs ao iniciar.
2. No computador dos workers:

```bash
python worker.py --config config/worker1.json --master-host 192.168.0.10
python worker.py --config config/worker2.json --master-host 192.168.0.10
```

O `--master-host` fica salvo no arquivo de config. Detalhes de rede e firewall em
[docs/05-apresentacao.md](docs/05-apresentacao.md).

## Testes automatizados

```bash
python -m unittest discover -s tests -t . -v
```

54 testes: unitários (buffer, envelope, dispatcher, correlação, registro, config), integração
com TCP real, e um teste que sobe **master + 2 workers como processos distintos**.
Mapa de cada teste para o requisito da sprint em [docs/04-testes.md](docs/04-testes.md).

## Opções de linha de comando

| Comando | Opção | Efeito |
|---|---|---|
| `master.py` | `--config`, `--label`, `--group`, `--host`, `--port` | escolhe/altera a configuração (alterações são salvas) |
| `worker.py` | `--config`, `--label`, `--group`, `--master-host`, `--master-port` | idem |
| `worker.py` | `--simular-trabalho` | ocupa a thread principal com cálculo; prova que o heartbeat não trava |
| `worker.py` | `--parar-heartbeat-apos N` | após N s para os heartbeats sem fechar a conexão (simula travamento) |
| `tools/sonda.py` | `--host`, `--port`, `--pausa`, `--duplicar-config` | roda os cenários de enquadramento contra um master real |

## Estrutura

```text
master.py              executável do master (só lê argumentos e chama p2p.master)
worker.py              executável do worker
p2p/
  config.py            configuração persistente: UUID, label, host, port
  ndjson.py            enquadramento: bytes do TCP -> linhas completas
  envelope.py          formato das mensagens, tipos, códigos de erro, validação
  dispatcher.py        type -> função que trata
  correlation.py       request_id -> quem está esperando a resposta
  connection.py        socket + lock de envio + laço de leitura
  registry.py          cadastro de workers (ONLINE/OFFLINE, reconexão, duplicidade)
  master.py            lógica do master (threads accept, leitoras, monitor, status)
  worker.py            lógica do worker (threads main, leitor, heartbeat)
  logs.py              formato padronizado dos logs
config/                master.json, worker1.json, worker2.json
tools/sonda.py         cliente "malicioso" para demonstrar os testes de enquadramento
tests/                 testes unitários, de integração e de processos
docs/                  documentação didática (comece pelo glossário)
```

## Documentação

| Documento | Conteúdo |
|---|---|
| [00 — Comunicação passo a passo](docs/00-comunicacao-passo-a-passo.md) | **comece aqui**: a conversa master ↔ worker do início ao fim, com os logs reais explicados |
| [01 — Glossário](docs/01-glossario.md) | todos os termos: processo, thread, socket, TCP, NDJSON, envelope, UUID, heartbeat... |
| [02 — Arquitetura](docs/02-arquitetura.md) | componentes, threads, fluxos, estados, e ordem para ler o código |
| [03 — Protocolo](docs/03-protocolo.md) | contrato das mensagens: envelope, tipos, payloads, erros, exemplos |
| [04 — Testes](docs/04-testes.md) | o que cada teste prova e como rodar |
| [05 — Apresentação](docs/05-apresentacao.md) | roteiro da demonstração entre computadores + perguntas prováveis |
| [06 — Git e entrega](docs/06-git-e-entrega.md) | repositório, colaboradores e histórico de commits |
