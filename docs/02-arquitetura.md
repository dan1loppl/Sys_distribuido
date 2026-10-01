# 02 — Arquitetura

## 1. Visão geral

```text
   COMPUTADOR A                                      COMPUTADOR B
 ┌──────────────────────────────┐            ┌───────────────────────────┐
 │ python master.py             │            │ python worker.py          │
 │ MASTER  (0.0.0.0:5000)       │◄── TCP ────│   --config worker1.json   │
 │                              │            │ WORKER worker-01          │
 │  registry:                   │            └───────────────────────────┘
 │   worker-01  ONLINE          │            ┌───────────────────────────┐
 │   worker-02  ONLINE          │◄── TCP ────│ python worker.py          │
 │                              │            │   --config worker2.json   │
 └──────────────────────────────┘            │ WORKER worker-02          │
                                             └───────────────────────────┘
```

- Cada caixa é um **processo** separado. Eles só se comunicam pela rede.
- Cada worker mantém **uma conexão TCP** com o master, aberta enquanto estiver vivo.
- Toda mensagem é uma linha **NDJSON** com o mesmo **envelope**.

## 2. Camadas do código

De baixo para cima — cada camada só usa as de baixo:

```text
 ┌────────────────────────────────────────────────────────────────────┐
 │  master.py / worker.py (raiz)   linha de comando: lê argumentos    │
 ├────────────────────────────────────────────────────────────────────┤
 │  p2p/master.py   p2p/worker.py  regras de cada papel               │
 │  p2p/registry.py                cadastro (só o master usa)         │
 ├────────────────────────────────────────────────────────────────────┤
 │  p2p/dispatcher.py              type -> handler                    │
 │  p2p/correlation.py             request_id -> quem espera          │
 ├────────────────────────────────────────────────────────────────────┤
 │  p2p/connection.py              socket + envio seguro + laço recv  │
 ├────────────────────────────────────────────────────────────────────┤
 │  p2p/envelope.py                formato e validação das mensagens  │
 │  p2p/ndjson.py                  bytes <-> linhas (enquadramento)   │
 ├────────────────────────────────────────────────────────────────────┤
 │  p2p/config.py  p2p/logs.py     apoio: identidade e logs           │
 └────────────────────────────────────────────────────────────────────┘
```

## 3. O caminho de uma mensagem

### Envio
```text
dict Python ──make_message()──► envelope completo ──encode()──► bytes + "\n" ──sendall()──► rede
                                (gera request_id)                (ndjson.py)     (com lock)
```

### Recebimento (laço `Connection.serve`, uma thread por conexão)
```text
rede ──recv()──► bytes soltos ──NdjsonBuffer.feed()──► linhas completas
                                                           │
                       ┌───────────────────────────────────┘
                       ▼
                parse_line():  UTF-8? ──não──► error invalid_json
                               JSON?  ──não──► error invalid_json
                               envelope ok? ─não─► error invalid_envelope / unsupported_version
                       │ sim
                       ▼
       é resposta (…_ack, error)?
          ├─ sim ─► PendingRequests.resolve()  ──► acorda a thread que esperava esse request_id
          └─ não ─► Dispatcher.dispatch()      ──► handler do type (ou error unknown_type)
```
Qualquer erro em uma linha **descarta só aquela linha**. O laço continua; a conexão continua; o processo continua.

## 4. Threads

### Master

```text
 MainThread ── espera Ctrl+C
 accept ────── accept() ─► nova conexão ─► cria thread leitora ─► volta ao accept()
 leitor-worker-01 ── recv ─► dispatcher ─► send(ack)      (uma thread por conexão)
 leitor-worker-02 ── recv ─► dispatcher ─► send(ack)
 monitor ───── a cada 1 s: registry.expire(15 s) ─► OFFLINE + fecha conexão
 status ────── a cada 20 s: imprime a tabela
```

Por que uma thread por worker? `recv()` bloqueia. Com uma thread só, o master ficaria preso
esperando o worker-01 e não ouviria o worker-02. Com uma por conexão, **os heartbeats coexistem
sem bloqueio** (requisito da sprint).

### Worker

```text
 MainThread ── espera Ctrl+C (ou calcula, com --simular-trabalho)
 worker-main ─ connect ─► register ─► espera a conexão cair ─► backoff ─► connect ...
 leitor ────── recv ─► resposta? ─► PendingRequests.resolve()
 heartbeat ─── a cada 5 s: send(heartbeat) ─► espera ACK (até 3 s) ─► dorme o resto
```

A thread `heartbeat` **envia**, mas quem **lê** a resposta é a `leitor`. As duas se encontram pelo
`request_id` (correlação). Assim nenhuma thread fica presa no `recv` de outra.

## 5. Fluxos principais

### 5.1 Registro

```text
WORKER                                              MASTER
  │ connect() ───────────────────────────────────────► accept()  -> thread leitor-conexao#N
  │ register_worker  {worker_id, label, hostname, pid}  request_id=R1
  │ ─────────────────────────────────────────────────► registry.register(uuid)
  │                                                     novo?        -> "registered"
  │                                                     OFFLINE?     -> "reconnected"
  │                                                     ONLINE outra conexão? -> "rejected"
  │ ◄───────────────────────────── registration_ack {status}  request_id=R1
  │ status ok -> inicia thread heartbeat
```

### 5.2 Heartbeat

```text
WORKER (thread heartbeat)        WORKER (thread leitor)          MASTER (leitor-worker-01)
  expect(R7)
  send heartbeat seq=7 R7 ──────────────────────────────────────► registry.touch() (último sinal = agora)
  wait(3 s) ...                                           ◄────── heartbeat_ack seq=7 R7
                                 resolve(R7) ─► acorda ─┐
  ◄─────────────────────────────────────────────────────┘
  dorme até completar 5 s
```

### 5.3 Detecção de queda — dois caminhos

```text
(a) Processo fechado / morto           (b) Processo travado / rede caiu sem aviso
    o SO fecha o socket                    a conexão parece aberta, mas nada chega
    master: recv() -> b"" ou reset         master/monitor: último sinal > 15 s
    -> mark_offline("conexão TCP           -> expire(): OFFLINE("sem heartbeat há X s")
       encerrada")                         -> fecha a conexão
    detecção: imediata                     detecção: até heartbeat_timeout + 1 s
```
Demonstração: (a) `Ctrl+C` no worker; (b) `python worker.py ... --parar-heartbeat-apos 15`.

Do lado do worker, a perda do master é percebida por EOF/reset ou por `max_missed_acks` ACKs
perdidos seguidos; em ambos os casos ele volta ao laço de reconexão.

### 5.4 Reconexão sem duplicar

```text
 cadastro do UUID abc:   ONLINE ──(cai)──► OFFLINE ──(register com uuid abc)──► ONLINE
                                                                                reconnections = 1
 número de cadastros: 2 ─────────────────────────────────────────────────────► continua 2
```

A chave do cadastro é o **UUID**, que vem do arquivo de config (persistente). Por isso o mesmo
worker, ao voltar, cai no **mesmo** cadastro.

### 5.5 Identidade duplicada

```text
 conexão A: register uuid abc ─► registered (ONLINE)
 conexão B: register uuid abc ─► rejected / duplicate_identity     (A continua intacta)
```

E se o worker caiu sem aviso (conexão meio-aberta) e voltou? O UUID ainda parece ONLINE, então a
volta é recusada **temporariamente**. O worker não desiste: tenta de novo com backoff. Quando o
monitor expira o cadastro antigo (sem heartbeat), a próxima tentativa recebe `reconnected`.

## 6. Estado e concorrência

| Estrutura | Quem mexe | Proteção |
|---|---|---|
| `WorkerRegistry._workers` (master) | leitoras, monitor, status | `threading.Lock` em todos os métodos; leituras devolvem **cópias** |
| `Connection.sock` (envio) | quem envia (leitoras, monitor / heartbeat, main) | `_send_lock` em volta do `sendall` |
| `PendingRequests._pending` (worker) | heartbeat, main, leitor | `threading.Lock` |
| `Master._connections` | accept, leitoras, stop | `threading.Lock` |

## 7. Decisões de projeto (e por quê)

| Decisão | Motivo |
|---|---|
| Só biblioteca padrão do Python | nada para instalar na hora da apresentação; foco no conceito |
| NDJSON com delimitador `\n` | simples de ler/depurar (dá para ver no log e até digitar à mão) |
| Uma thread por conexão | fácil de entender; suficiente para dezenas de workers |
| Chave do cadastro = UUID | label e IP mudam; o UUID persistente não |
| Recusar duplicado (em vez de derrubar o antigo) | um impostor não consegue "expulsar" um worker legítimo |
| Master responde `error`; worker só registra no log | evita pingue-pongue infinito de erros |
| Monitor fecha a conexão do expirado | o worker percebe na hora e reconecta; e o cadastro fica consistente |
| Registro mantido em memória | a sprint não exige persistir o cadastro; reiniciar o master zera a lista e os workers se registram de novo sozinhos |
| Linha > 1 MiB descartada | um par defeituoso não consegue esgotar a memória do master |

## 8. Ordem sugerida para ler o código

1. [p2p/ndjson.py](../p2p/ndjson.py) — o problema do TCP e o buffer (curto, fundamental)
2. [p2p/envelope.py](../p2p/envelope.py) — como é uma mensagem e o que é validado
3. [p2p/config.py](../p2p/config.py) — UUID persistente
4. [p2p/dispatcher.py](../p2p/dispatcher.py) e [p2p/correlation.py](../p2p/correlation.py)
5. [p2p/connection.py](../p2p/connection.py) — onde tudo isso se junta com o socket
6. [p2p/registry.py](../p2p/registry.py) — regras de registro/reconexão/duplicidade
7. [p2p/master.py](../p2p/master.py) — threads e handlers do master
8. [p2p/worker.py](../p2p/worker.py) — laço de conexão, registro e heartbeat
9. [tests/test_integracao.py](../tests/test_integracao.py) — tudo funcionando junto

## 9. Como a Sprint 2 vai se encaixar

- Novos `type`s (`task_request`, `task_assignment`, `no_task`, `task_result`, `task_ack`...) são novos
  **handlers** registrados no dispatcher — o laço de rede não muda.
- O worker ganha uma thread de trabalho; o heartbeat continua na dele (já demonstrado com `--simular-trabalho`).
- A correlação por `request_id` já serve para "pedir tarefa e esperar a resposta".
