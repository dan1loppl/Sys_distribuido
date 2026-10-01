# 00 — A comunicação passo a passo

**Leia este primeiro.** Ele conta, do começo ao fim, a "conversa" entre o master e um worker,
mostrando as linhas de log reais e o que cada uma significa. Os outros documentos aprofundam
cada parte; este dá a visão completa.

---

## 1. A ideia em uma frase

> O worker **liga** para o master, **se apresenta**, e depois fica **avisando a cada 5 segundos que está vivo**.
> Se o worker some, o master **percebe**; se ele volta, o master **reconhece que é o mesmo**.

Toda a Sprint 1 é isso. O resto são os detalhes para que funcione direito pela rede.

---

## 2. Os personagens

```text
 COMPUTADOR A                                   COMPUTADOR B
┌──────────────────────────┐                   ┌──────────────────────────┐
│ MASTER  (o gerente)      │                   │ WORKER worker-01         │
│ fica esperando ligações  │ ◄═══ conexão ════ │ (o funcionário)          │
│ na porta 5000            │       TCP         │ liga para o master       │
└──────────────────────────┘                   └──────────────────────────┘
```

- Cada um é um **programa separado** (processo). Não compartilham memória.
- **Tudo** que um sabe do outro chega por **mensagem** pela rede.
- A "linha telefônica" entre eles é uma **conexão TCP**, que fica aberta enquanto o worker estiver vivo.

---

## 3. A conversa completa (visão geral)

```text
 WORKER                                                   MASTER
   │                                                        │  (1) abre a porta 5000 e espera
   │ ─────────────── (2) conecta (TCP) ───────────────────► │
   │                                                        │
   │ ── (3) register_worker: "sou o UUID abc, worker-01" ─► │  anota no cadastro
   │ ◄────────── registration_ack: "registrado!" ────────── │
   │                                                        │
   │ ── (4) heartbeat seq=1: "estou vivo" ────────────────► │  anota "último sinal = agora"
   │ ◄────────── heartbeat_ack seq=1: "recebi" ──────────── │
   │          ... 5 segundos ...                            │
   │ ── heartbeat seq=2 ──────────────────────────────────► │
   │ ◄────────── heartbeat_ack seq=2 ────────────────────── │
   │          ... e assim por diante ...                    │
   ✕ (5) worker cai                                         │  percebe → OFFLINE
   │                                                        │
   │ ─────────────── (6) conecta de novo ─────────────────► │
   │ ── register_worker: "sou o UUID abc" ────────────────► │  "eu te conheço!" → reconectado
   │ ◄────────── registration_ack: "reconnected" ────────── │  (não cria cadastro novo)
```

As próximas seções explicam cada número.

---

## 4. Passo (1) — O master abre a porta

```bash
python master.py
```

```text
MASTER 'master-01' iniciado em 0.0.0.0:5000 (uuid=6d20c5e1-...)
Timeout de heartbeat: 15.0s. Aguardando workers...
Workers de outros computadores devem usar um destes IPs: 192.168.0.10
```

- `0.0.0.0:5000`: o master aceita ligações em **qualquer placa de rede** deste computador, na **porta 5000**.
- O IP mostrado (`192.168.0.10`) é o "número de telefone" que os workers de outro computador vão usar.
- A partir daqui uma thread chamada `accept` fica **parada esperando** alguém conectar.

---

## 5. Passo (2) — O worker conecta

```bash
python worker.py --config config/worker1.json
```

O worker lê o seu arquivo de configuração:

```json
{
  "uuid": "9de65592-0141-4699-806a-d6520e46fe3e",   ← identidade (gerada uma vez e salva)
  "label": "worker-01",                              ← nome para humanos
  "master_host": "192.168.0.10",                     ← onde o master está
  "master_port": 5000                                ← em que porta
}
```

E "liga":

```text
worker:  Conectado ao master 192.168.0.10:5000 (porta local 53086)
master:  Nova conexão TCP de 192.168.0.20:53086 (conexao#1)
```

A conexão existe, mas o master **ainda não sabe quem é** (por isso aparece como `conexao#1`).

---

## 6. Como uma mensagem viaja

Antes de seguir, veja **o que de fato passa pelo fio**. O worker monta um dicionário Python:

```python
{
  "version": "1.0",
  "type": "register_worker",              # QUE tipo de mensagem é
  "request_id": "212eef82-...",           # número de protocolo desta solicitação
  "group": "grupo-01",
  "source": "worker-01",                  # quem envia
  "destination": "master",                # para quem
  "timestamp": "2026-10-01T13:06:01.712+00:00",
  "payload": {"worker_id": "9de65592-...", "label": "worker-01"}   # o conteúdo
}
```

- A parte de fora (version, type, request_id, group, source, destination, timestamp) é o **envelope**,
  igual em todas as mensagens.
- O **payload** é a carta dentro do envelope; muda conforme o `type`.

Ele vira **uma linha de texto** terminada em `\n` e é enviada:

```text
{"version":"1.0","type":"register_worker","request_id":"212eef82-...", ... }\n
```

### Por que o `\n` no final?

Porque o TCP **não separa mensagens**: ele entrega um fluxo contínuo de bytes. Do outro lado, o `recv()` pode receber:

```text
caso A:  {"version":"1.0","type":"regis              ← meia mensagem
caso B:  ...}\n{"version":"1.0","type":"heartbeat"...}\n   ← o fim de uma + outra inteira
```

O master guarda o que chega em um **buffer** e só processa quando encontra o `\n`:
"agora tenho uma mensagem completa". Isso é o **NDJSON** (um JSON por linha).
Detalhes: [01-glossario.md](01-glossario.md) → "Enquadramento", "NDJSON", "Buffer".

---

## 7. Passo (3) — Registro: "quem é você?"

```text
master | leitor-conexao#1 | RECV register_worker origem=worker-01 destino=master request_id=212eef82-...
master | leitor-worker-01 | SEND registration_ack origem=master-01 destino=worker-01 request_id=212eef82-... payload={"status":"registered",...}
master | leitor-worker-01 | NOVO WORKER REGISTRADO: worker-01 (uuid=9de65592-..., de 192.168.0.20:53086)
worker | worker-main      | REGISTRADO no master 'master-01' (status=registered)
```

O que aconteceu:

1. O master recebeu (`RECV`) o `register_worker`.
2. Procurou o UUID `9de65592-...` no cadastro → **não existia** → criou, status **ONLINE**.
3. Respondeu (`SEND`) `registration_ack` com `"status":"registered"`.
4. A thread mudou de nome de `leitor-conexao#1` para `leitor-worker-01`: agora ele sabe quem é.

**Repare no `request_id`:** a pergunta e a resposta têm o **mesmo** `212eef82-...`.
É assim que o worker sabe que aquela resposta é da pergunta dele (**correlação**).
É como o número de protocolo de um atendimento.

As respostas possíveis ao registro:

| O master encontrou o UUID... | Resposta | Significado |
|---|---|---|
| nunca visto | `registered` | worker novo, cadastro criado |
| cadastrado, mas OFFLINE | `reconnected` | é o mesmo worker voltando; reaproveita o cadastro |
| cadastrado e ONLINE em **outra** conexão | `rejected` | alguém já está usando essa identidade (duplicada) |

---

## 8. Passo (4) — Heartbeat: "estou vivo"

Depois de registrado, o worker inicia uma thread chamada `heartbeat`, que repete para sempre:

```text
1. manda heartbeat     ("estou vivo", número de sequência seq)
2. espera o ACK        (no máximo 3 segundos)
3. dorme               (até completar 5 segundos)
4. volta ao 1
```

No log:

```text
worker | heartbeat        | SEND heartbeat origem=worker-01 destino=master-01 request_id=fcea2011-... payload={"seq":1}
master | leitor-worker-01 | RECV heartbeat origem=worker-01 destino=master-01 request_id=fcea2011-...
master | leitor-worker-01 | SEND heartbeat_ack origem=master-01 destino=worker-01 request_id=fcea2011-... payload={"status":"ok","seq":1}
worker | leitor           | RECV heartbeat_ack ... request_id=fcea2011-...
worker | heartbeat        | heartbeat_ack seq=1 confirmado (ida e volta: 1.1 ms)
```

- **heartbeat** = "estou vivo". **ACK** (acknowledgement) = "recebi".
- O master, ao receber, anota no cadastro: **"último sinal do worker-01 = agora"**.
- "Ida e volta: 1.1 ms" é quanto tempo levou entre enviar e receber o ACK.

### Duas threads no worker trabalhando juntas

Repare: quem **envia** é a thread `heartbeat`, mas quem **recebe** o ACK é a thread `leitor`.

```text
thread heartbeat:  "vou esperar a resposta do protocolo fcea2011"  → envia → dorme esperando...
thread leitor:     recebe heartbeat_ack fcea2011 → "alguém esperava fcea2011? sim!" → acorda a heartbeat
```

Elas se encontram pelo `request_id`. Assim nenhuma fica travada esperando a outra.

### Vários workers ao mesmo tempo

No master há **uma thread leitora por worker**:

```text
master | leitor-worker-02 | RECV heartbeat origem=worker-02 ... seq=2
master | leitor-worker-01 | RECV heartbeat origem=worker-01 ... seq=2
master | leitor-worker-02 | SEND heartbeat_ack ... seq=2
master | leitor-worker-01 | SEND heartbeat_ack ... seq=2
```

As linhas aparecem **intercaladas**: cada worker é atendido pela sua thread, sem esperar o outro.
É isso que a sprint chama de "heartbeats coexistem sem bloqueio".

---

## 9. Heartbeat e timeout: por que existem

### O problema

O master **não enxerga** o worker. Se o worker cai, há dois cenários:

| Como o worker caiu | O master fica sabendo? |
|---|---|
| Alguém deu Ctrl+C ou fechou o programa | **Sim, na hora.** O sistema operacional fecha a conexão e avisa o outro lado |
| O computador travou, o Wi-Fi caiu, o cabo foi puxado | **Não.** Nenhum aviso chega. A conexão **parece** aberta, mas não há ninguém do outro lado |

O heartbeat existe **para o segundo caso**.

### A solução: um combinado

> Gerente para o funcionário: "me manda um 'estou vivo' a cada 5 segundos.
> Se eu ficar 15 segundos sem notícia, vou considerar que aconteceu algo com você."

- **Heartbeat**: o "estou vivo".
- **Timeout**: o **prazo máximo de espera**. Passou do prazo sem resposta → algo deu errado.

### Os quatro números (todos nos arquivos `config/*.json`)

| Nome | Quem usa | Valor | Em palavras |
|---|---|---|---|
| `heartbeat_interval` | worker | 5 s | "mando 'estou vivo' a cada 5 s" |
| `ack_timeout` | worker | 3 s | "espero o 'recebi' por até 3 s; depois disso, conto como perdido" |
| `max_missed_acks` | worker | 3 | "se 3 'recebi' seguidos não chegarem, o master morreu → vou reconectar" |
| `heartbeat_timeout` | master | 15 s | "se um worker ficar 15 s calado, marco OFFLINE" |

Repare que há um prazo **de cada lado**: o master vigia os workers, e cada worker vigia o master.

### Linha do tempo de um travamento

```text
10:00:00  worker-02 → heartbeat     master anota: último sinal = 10:00:00
10:00:05  worker-02 → heartbeat     master anota: último sinal = 10:00:05
10:00:10  worker-02 → heartbeat     master anota: último sinal = 10:00:10
          ⚡ o computador do worker-02 trava aqui
10:00:15  (nada chega)
10:00:20  (nada chega)
10:00:25  (nada chega)  → já são 15 s desde 10:00:10
10:00:26  master: WORKER OFFLINE: worker-02 (motivo: sem heartbeat há 15.x s)
```

Quem faz essa conta é a thread **`monitor`** do master. A cada 1 segundo ela olha o cadastro e pergunta,
para cada worker ONLINE: **"agora − último sinal > 15 s?"**. Se sim → OFFLINE, e ela fecha a conexão.

### Por que 15 e não 5?

Porque a rede às vezes atrasa um pacote. Se o prazo fosse igual ao intervalo, **um único** heartbeat
atrasado derrubaria um worker saudável. Com 15 s (3 × 5 s), só depois de ~3 heartbeats perdidos o master
conclui que o worker caiu.

---

## 10. Passo (5) — O worker cai: dois jeitos de perceber

### Jeito 1 — A conexão fecha (Ctrl+C)

```text
master | leitor-worker-02 | WORKER OFFLINE: worker-02 (motivo: conexão TCP encerrada)
```
Imediato: o `recv()` do master recebe "fim da conexão".

### Jeito 2 — Silêncio (travamento)

```text
master | monitor | WORKER OFFLINE: worker-02 (motivo: sem heartbeat há 15.3s, limite 15.0s)
```
Leva ~15 s: é o timeout do heartbeat. Repare que quem escreve é a thread `monitor`, não a leitora.

Nos dois casos, o cadastro **não é apagado**: só muda para OFFLINE, com o motivo.

```text
===== WORKERS CADASTRADOS: 2 (1 ONLINE) =====
LABEL      STATUS   ENDEREÇO             HB  RECON  UUID
worker-01  ONLINE   192.168.0.20:53086    9      0  9de65592-...
worker-02  OFFLINE  192.168.0.20:53087    4      0  c49bfb16-...
             -> motivo: conexão TCP encerrada
```
(`HB` = heartbeats recebidos; `RECON` = quantas vezes reconectou.)

---

## 11. Passo (6) — O worker volta: reconexão sem duplicar

```bash
python worker.py --config config/worker2.json      ← o MESMO arquivo → o MESMO UUID
```

```text
master | leitor-worker-02 | WORKER RECONECTADO: worker-02 (uuid=c49bfb16-..., reconexão nº 1, sem duplicar cadastro)

===== WORKERS CADASTRADOS: 2 (2 ONLINE) =====
worker-01  ONLINE ...  RECON 0
worker-02  ONLINE ...  RECON 1      ← mesmo cadastro, agora com 1 reconexão
```

Por que não duplicou? Porque o master usa o **UUID** como chave do cadastro, e o UUID está **salvo no
arquivo de config**. O worker que voltou diz "sou o c49bfb16", o master encontra esse UUID OFFLINE e o
reaproveita.

Se o worker estiver rodando e o **master** cair, o worker percebe (conexão fechada ou 3 ACKs perdidos) e
fica tentando reconectar sozinho, esperando 1 s, 2 s, 4 s, 8 s, 10 s, 10 s... (**backoff**).

---

## 12. E se chegar uma mensagem com problema?

```text
master | leitor-sonda | Mensagem inválida de sonda descartada [invalid_json] JSON inválido (...) | conteúdo: b'{ isso nao eh json'
master | leitor-sonda | SEND error ... payload={"code":"invalid_json","detail":"..."}
```

O master **descarta só aquela linha**, responde uma mensagem `error` explicando o motivo, e **continua**
lendo a mesma conexão e atendendo todos os outros. Nada derruba o processo.

---

## 13. Como ler qualquer linha de log

```text
2026-10-01 10:06:01.723 | INFO  | grupo-01 | master-01 | leitor-worker-01 | RECV heartbeat grupo=grupo-01 origem=worker-01 destino=master-01 request_id=fcea2011-... payload={"seq":1}
└──────── quando ──────┘ └nível┘ └ grupo ─┘ └processo┘ └──── thread ────┘ └dir┘ └ type ─┘               └─ quem enviou ─┘ └─ para quem ──┘ └── nº do protocolo ──┘ └ conteúdo ┘
```

- `RECV` = recebi esta mensagem; `SEND` = enviei.
- Para seguir uma conversa entre dois terminais, **procure o mesmo `request_id`** nos dois.
- Os logs também ficam salvos em `logs/master-01.log`, `logs/worker-01.log` etc.

---

## 14. Faça você mesmo (15 minutos)

Abra 3 terminais no VS Code (Terminal → Split Terminal).

| # | Faça | Observe |
|---|---|---|
| 1 | T1: `python master.py` | "Aguardando workers..." |
| 2 | T2: `python worker.py --config config/worker1.json` | `register_worker` / `registration_ack` com o **mesmo request_id** nos dois terminais |
| 3 | T3: `python worker.py --config config/worker2.json` | tabela com 2 workers ONLINE |
| 4 | espere 15 s | heartbeats dos dois **intercalados** no master |
| 5 | abra `config/worker1.json` | o `uuid` foi preenchido e salvo |
| 6 | T3: `Ctrl+C` | master: `OFFLINE (conexão TCP encerrada)` na hora |
| 7 | T3: rode o worker2 de novo | `WORKER RECONECTADO`, tabela continua com **2** |
| 8 | T3: `Ctrl+C`, depois `python worker.py --config config/worker2.json --parar-heartbeat-apos 10` | após ~25 s: `OFFLINE (sem heartbeat...)` escrito pela thread `monitor`, e logo depois reconecta sozinho |
| 9 | T3: `Ctrl+C`, depois `python tools/sonda.py` | mensagens quebradas, coladas e inválidas; o master avisa e continua de pé |

Se conseguir explicar com suas palavras o que aconteceu em cada linha da tabela, você entendeu a Sprint 1.

---

## 15. Resumo em um quadro

```text
 CONECTAR   worker liga para IP:porta do master (TCP)
     ↓
 REGISTRAR  register_worker (UUID + label)  →  registration_ack (registered / reconnected / rejected)
     ↓
 MANTER     a cada 5 s: heartbeat  →  heartbeat_ack        (mesmo request_id = correlação)
     ↓
 VIGIAR     master: 15 s sem heartbeat → OFFLINE            (timeout)
            worker: 3 ACKs perdidos → reconectar
     ↓
 VOLTAR     mesmo UUID → mesmo cadastro (reconnected), sem duplicar
```

Próximos passos de leitura:
[01 — Glossário](01-glossario.md) (cada termo) →
[02 — Arquitetura](02-arquitetura.md) (threads e código) →
[03 — Protocolo](03-protocolo.md) (formato exato das mensagens).
