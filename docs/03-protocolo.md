# 03 — Protocolo (versão 1.0)

Contrato de comunicação entre master e workers. Na Sprint 4 este documento é a base do
"mesmo contrato" entre grupos.

## 1. Transporte e enquadramento

- **TCP**. O worker conecta; o master escuta (padrão: porta `5000`).
- **NDJSON**: cada mensagem é **um objeto JSON em uma única linha**, codificado em **UTF-8**, terminado por `\n` (byte `0x0A`).
- `\r\n` é tolerado; linhas vazias são ignoradas.
- Tamanho máximo de uma linha: **1 MiB**. Linha maior é descartada até o próximo `\n` (erro `frame_too_large`).
- Uma conexão TCP por worker, mantida aberta enquanto ele estiver ativo.

## 2. Envelope

Todos os campos são **obrigatórios**.

| Campo | Tipo | Descrição |
|---|---|---|
| `version` | string | versão do protocolo. `"1.0"`. Aceita qualquer `1.x`; outra versão principal → `unsupported_version` |
| `type` | string | tipo da mensagem (seção 3) |
| `request_id` | string | id único da solicitação (UUID v4). **Respostas repetem o `request_id` da solicitação** |
| `group` | string | grupo do remetente (ex.: `grupo-01`) |
| `source` | string | label do remetente (ex.: `worker-01`) |
| `destination` | string | label do destinatário. Antes de conhecer o label do master, o worker usa `"master"` |
| `timestamp` | string | data/hora ISO 8601 em UTC, ex.: `2026-10-01T13:09:43.345+00:00` |
| `payload` | objeto | conteúdo específico do `type` (pode ser `{}`) |

Regras de validação: `type`, `request_id`, `group`, `source`, `destination` não podem ser vazios
nem ter mais de 128 caracteres; `payload` deve ser objeto.

Exemplo (formatado para leitura — na rede é uma linha só):

```json
{
  "version": "1.0",
  "type": "heartbeat",
  "request_id": "fcea2011-993d-449d-9cd2-dd0e35ce24b5",
  "group": "grupo-01",
  "source": "worker-01",
  "destination": "master-01",
  "timestamp": "2026-10-01T13:06:01.723+00:00",
  "payload": {"worker_id": "9de65592-0141-4699-806a-d6520e46fe3e", "seq": 1}
}
```

## 3. Tipos de mensagem

| type | Direção | Espécie | Responde com |
|---|---|---|---|
| `register_worker` | worker → master | solicitação | `registration_ack` (ou `error`) |
| `registration_ack` | master → worker | resposta | — |
| `heartbeat` | worker → master | solicitação | `heartbeat_ack` (ou `error`) |
| `heartbeat_ack` | master → worker | resposta | — |
| `error` | master → remetente | resposta | — (nunca se responde a um `error`) |

### 3.1 `register_worker`

Primeira mensagem do worker em cada conexão.

| payload | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `worker_id` | string (UUID) | sim | identidade persistente do worker |
| `label` | string | sim | nome amigável |
| `hostname` | string | não | nome do computador do worker |
| `pid` | inteiro | não | id do processo do worker |

```json
{"version":"1.0","type":"register_worker","request_id":"212eef82-654e-425e-afdb-2c90c58f5f3b","group":"grupo-01","source":"worker-01","destination":"master","timestamp":"2026-10-01T13:06:01.712+00:00","payload":{"worker_id":"9de65592-0141-4699-806a-d6520e46fe3e","label":"worker-01","hostname":"NOTEBOOK-B","pid":4120}}
```

### 3.2 `registration_ack`

| payload | Presente quando | Descrição |
|---|---|---|
| `status` | sempre | `registered` (novo), `reconnected` (estava OFFLINE), `already_registered` (repetiu na mesma conexão), `rejected` |
| `reason` | `rejected` | `duplicate_identity` (UUID ONLINE em outra conexão) ou `connection_already_bound` (a conexão já pertence a outro UUID) |
| `detail` | `rejected` | explicação legível |
| `worker_id`, `label` | aceito | eco da identidade registrada |
| `master_id` | aceito | UUID do master |
| `heartbeat_timeout` | aceito | segundos sem sinal até o master declarar OFFLINE |
| `reconnections` | aceito | quantas reconexões esse cadastro já teve |

```json
{"version":"1.0","type":"registration_ack","request_id":"212eef82-654e-425e-afdb-2c90c58f5f3b","group":"grupo-01","source":"master-01","destination":"worker-01","timestamp":"2026-10-01T13:06:01.714+00:00","payload":{"status":"registered","worker_id":"9de65592-0141-4699-806a-d6520e46fe3e","label":"worker-01","master_id":"6d20c5e1-3b62-4891-9a35-babcc0e30f09","heartbeat_timeout":15.0,"reconnections":0}}
```

Recusa por identidade duplicada:

```json
{"...":"...","type":"registration_ack","payload":{"status":"rejected","reason":"duplicate_identity","worker_id":"9de65592-...","detail":"UUID já está ONLINE em 192.168.0.20:50332"}}
```

Ao receber `rejected`, o worker fecha a conexão e tenta de novo com backoff (o UUID pode estar
preso a uma conexão meio-aberta que o master ainda vai expirar).

### 3.3 `heartbeat`

Enviado a cada `heartbeat_interval` (padrão 5 s) após o registro aceito.

| payload | Tipo | Descrição |
|---|---|---|
| `worker_id` | string (UUID) | deve ser igual ao registrado nesta conexão |
| `seq` | inteiro | contador que recomeça em 1 a cada conexão |

### 3.4 `heartbeat_ack`

| payload | Descrição |
|---|---|
| `status` | `"ok"` |
| `seq` | eco do `seq` recebido |

### 3.5 `error`

| payload | Descrição |
|---|---|
| `code` | código da tabela abaixo |
| `detail` | explicação legível |

O `request_id` do `error` é o da mensagem que causou o problema, **quando ele pôde ser lido**; se a
linha nem era JSON, é um id novo.

## 4. Códigos de erro

| code | Quando | Efeito |
|---|---|---|
| `invalid_json` | linha não é UTF-8 ou não é JSON | linha descartada; conexão mantida |
| `invalid_envelope` | JSON fora do envelope (campo ausente, tipo errado, vazio) | idem |
| `unsupported_version` | `version` com outra versão principal | idem |
| `unknown_type` | `type` sem handler | idem |
| `invalid_payload` | payload sem campo exigido / UUID inválido | idem |
| `not_registered` | `heartbeat` antes do registro, ou cadastro já expirado | idem; o worker reconecta e registra de novo |
| `identity_mismatch` | `heartbeat` com `worker_id` diferente do registrado na conexão | idem |
| `frame_too_large` | linha maior que 1 MiB | bytes descartados até o próximo `\n` |

**Nenhum erro de protocolo derruba o master ou o worker.**

## 5. Temporização (valores padrão)

| Parâmetro | Onde | Padrão | Significado |
|---|---|---|---|
| `heartbeat_interval` | worker | 5 s | intervalo entre heartbeats |
| `ack_timeout` | worker | 3 s | espera máxima por um ACK |
| `max_missed_acks` | worker | 3 | ACKs perdidos seguidos até considerar o master morto |
| `reconnect_initial_delay` / `reconnect_max_delay` | worker | 1 s / 10 s | backoff exponencial de reconexão |
| `heartbeat_timeout` | master | 15 s | sem sinal por mais que isso → OFFLINE (≈ 3 heartbeats perdidos) |
| `monitor_interval` | master | 1 s | frequência da verificação de timeout |

Regra prática: `heartbeat_timeout` deve ser **bem maior** que `heartbeat_interval` (≈ 3×), para um
único heartbeat atrasado não derrubar um worker saudável.

## 6. Sequência completa de uma sessão

```text
worker-01                                   master-01
   │── TCP connect ──────────────────────────►│
   │── register_worker        (R1) ──────────►│  registered
   │◄───────────── registration_ack (R1) ─────│
   │── heartbeat seq=1        (R2) ──────────►│  último sinal = agora
   │◄───────────── heartbeat_ack seq=1 (R2) ──│
   │        ... a cada 5 s ...                │
   ✕  (Ctrl+C)                                │  recv() = EOF -> OFFLINE
   │── TCP connect ──────────────────────────►│
   │── register_worker        (R9) ──────────►│  reconnected (mesmo cadastro)
   │◄───────────── registration_ack (R9) ─────│
```
