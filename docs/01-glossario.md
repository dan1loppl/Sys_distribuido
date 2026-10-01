# 01 — Glossário

Cada termo tem: **o que é**, **uma analogia** quando ajuda, e **onde aparece no projeto**.
Leia na ordem: os primeiros termos são a base dos seguintes.

> Ainda não leu a visão geral? Comece por [00 — A comunicação passo a passo](00-comunicacao-passo-a-passo.md):
> ela mostra a conversa inteira entre master e worker, e este glossário detalha cada termo que aparece lá.

---

## Parte 1 — Sistema distribuído

### Sistema distribuído
Vários programas, possivelmente em computadores diferentes, que trabalham juntos trocando
mensagens pela rede. Não há memória compartilhada: **tudo que um sabe do outro chega por mensagem**.
Por isso precisamos de protocolo, identidade, detecção de falhas etc.

### Nó (node)
Cada participante do sistema. Aqui há dois papéis de nó: **master** e **worker**.

### Master
O "gerente". Fica esperando conexões, mantém o **cadastro** dos workers e sabe quem está vivo.
Nas próximas sprints vai distribuir tarefas e negociar ajuda com masters de outros grupos.
→ [p2p/master.py](../p2p/master.py)

### Worker
O "funcionário". Conecta no master, se apresenta (registro) e avisa periodicamente que está
vivo (heartbeat). Na Sprint 2 passará a executar tarefas.
→ [p2p/worker.py](../p2p/worker.py)

### P2P (peer-to-peer) e balanceamento de carga
P2P = "par a par": nós de mesmo nível conversando diretamente. No projeto, **masters de grupos
diferentes** serão pares que emprestam workers entre si quando um está sobrecarregado
(balanceamento de carga **dinâmico**). Isso é Sprint 3/4 — a Sprint 1 é a base.

---

## Parte 2 — Execução: processo e thread

### Processo
Um programa em execução, com sua própria memória. `python master.py` cria um processo;
cada `python worker.py` cria outro. Processos **não enxergam a memória um do outro** — só
se comunicam pela rede. O requisito "processos distintos" quer exatamente isso.

### Thread
Uma "linha de execução" **dentro** de um processo. Um processo pode ter várias threads
rodando ao mesmo tempo, compartilhando a mesma memória.
*Analogia:* o processo é um restaurante; as threads são os funcionários trabalhando nele ao mesmo tempo.

No projeto (o nome da thread aparece em cada linha de log):

| Processo | Thread | Função |
|---|---|---|
| master | `accept` | espera novas conexões |
| master | `leitor-<worker>` | uma por worker: lê e responde as mensagens dele |
| master | `monitor` | procura workers sem heartbeat |
| master | `status` | imprime a tabela periodicamente |
| worker | `worker-main` | conecta, registra, reconecta |
| worker | `leitor` | lê tudo que chega do master |
| worker | `heartbeat` | envia "estou vivo" no ritmo certo |

### Threads de rede independentes
Cada tarefa de rede tem sua própria thread, então uma não espera a outra. O heartbeat de um
worker não fica parado porque o master está lendo outro worker, nem porque o worker está ocupado
calculando (`--simular-trabalho` demonstra isso).

### Bloqueante (blocking)
Uma chamada que "para" a thread até algo acontecer. `recv()` é bloqueante: a thread dorme até
chegarem bytes. Por isso cada conexão tem **sua** thread leitora — se uma só thread lesse todos os
workers, ficaria presa esperando o primeiro.

### Lock (trava, mutex)
Garante que só **uma thread por vez** execute um trecho de código. Usado em:
- `Connection.send` → duas threads enviando ao mesmo tempo poderiam intercalar bytes de duas mensagens;
- `WorkerRegistry` → o monitor e as leitoras alteram o cadastro ao mesmo tempo.

### Condição de corrida (race condition)
Erro que depende da "ordem de chegada" entre threads. Exemplo evitado no projeto: a conexão
**antiga** de um worker fecha **depois** que ele já reconectou; sem cuidado, isso marcaria o
worker novo como OFFLINE. Por isso `mark_offline` confere se o cadastro ainda pertence àquela conexão.

### Event (threading.Event)
Uma "bandeira" que uma thread levanta e outra espera. Usado para acordar quem espera uma resposta
e para avisar que a conexão fechou.

### Daemon thread
Thread que não impede o processo de terminar. Ao dar `Ctrl+C`, o programa encerra mesmo que alguma
thread ainda esteja no meio de algo.

---

## Parte 3 — Rede

### IP (endereço IP) / host
Endereço de um computador na rede, por exemplo `192.168.0.10`. "Host" = o computador (ou seu endereço).
- `127.0.0.1` (ou `localhost`) = **o próprio computador** (loopback). Bom para testar tudo em uma máquina.
- `0.0.0.0` (no master) = "escute em **todas** as placas de rede", para aceitar conexões vindas de outras máquinas.

### Porta (port)
Número de 0 a 65535 que identifica **qual programa** dentro do computador recebe a conexão.
*Analogia:* o IP é o endereço do prédio; a porta é o número do apartamento. O master usa a porta `5000`.

### Socket
O "ponto de conexão" que o programa usa para falar pela rede. É um objeto Python (`socket.socket`)
com operações como `bind`, `listen`, `accept`, `connect`, `send`, `recv`, `close`.

### TCP
Protocolo de transporte **confiável e ordenado**: os bytes chegam, sem perda e na mesma ordem em que
foram enviados (ou a conexão quebra e você fica sabendo). Mas atenção: TCP entrega um
**fluxo contínuo de bytes**, **não mensagens separadas** — veja "Enquadramento".

### Cliente e servidor
O **servidor** espera conexões (o master); o **cliente** toma a iniciativa de conectar (o worker).

### bind / listen / accept / connect
Sequência para abrir uma conexão TCP:

```text
MASTER (servidor)                              WORKER (cliente)
bind(("0.0.0.0", 5000))   reserva a porta
listen()                  começa a aceitar
accept()  ... espera ...  <------ connect ---- create_connection(("192.168.0.10", 5000))
   devolve um socket novo, só desse worker
```
→ `Master.start` / `Master._accept_loop` e `Worker._session`.

### send / sendall / recv
- `send(dados)` envia bytes, **mas pode enviar só parte deles**.
- `sendall(dados)` repete `send` até enviar tudo — é o que usamos.
- `recv(65536)` devolve **o que já chegou**, até 65536 bytes: pode ser meia mensagem, uma, ou várias.
  Se devolver `b""` (vazio), o outro lado **fechou** a conexão (EOF).

### EOF / conexão encerrada / reset
- EOF: o outro lado fechou educadamente (ex.: `Ctrl+C`). `recv` devolve `b""`.
- Reset (`ConnectionResetError`): a conexão foi derrubada abruptamente (processo morto).
Ambos fazem o master marcar o worker **OFFLINE** imediatamente.

### Conexão "meio-aberta" (half-open)
Quando a rede cai sem aviso (cabo puxado, Wi-Fi caiu, máquina travou), **nenhum dos lados recebe EOF**:
o socket parece aberto, mas ninguém está do outro lado. TCP sozinho pode demorar muito para perceber.
**É exatamente por isso que existe o heartbeat.**

### Firewall
Programa do sistema que bloqueia conexões. No Windows, na primeira vez que o master abre a porta,
aparece um aviso pedindo permissão — é preciso permitir em **redes privadas**.

---

## Parte 4 — Protocolo e mensagens

### Protocolo
O "contrato" entre os nós: formato das mensagens, tipos, campos obrigatórios, quem responde o quê.
O nosso está em [03-protocolo.md](03-protocolo.md). Na Sprint 4 grupos diferentes usarão o mesmo contrato.

### JSON
Formato de texto para dados estruturados: `{"type": "heartbeat", "seq": 3}`.
Em Python, `json.dumps(dict)` → texto e `json.loads(texto)` → dict.

### Enquadramento (framing)
Como saber **onde uma mensagem termina** dentro de um fluxo de bytes. Sem isso:

```text
enviado:      [mensagem 1][mensagem 2]
recv #1:      [mensagem 1][mens          <- duas pela metade?
recv #2:      agem 2]
```
Há várias soluções (prefixo de tamanho, delimitador...). Usamos **delimitador `\n`** (NDJSON).

### NDJSON (Newline Delimited JSON)
**Um JSON por linha**, terminado em `\n`:

```text
{"type":"heartbeat","seq":1}\n{"type":"heartbeat","seq":2}\n
```
Funciona porque `json.dumps` nunca coloca uma quebra de linha "crua" dentro do JSON
(um `\n` em um texto vira os caracteres `\` e `n`). → [p2p/ndjson.py](../p2p/ndjson.py)

### Buffer
Área onde guardamos bytes que chegaram mas **ainda não formam uma mensagem completa**.
A cada `recv`, juntamos ao buffer e retiramos todas as linhas completas; o resto fica esperando.
→ `NdjsonBuffer.feed`

### Fragmentação
Uma mensagem que chega **em vários `recv`**. O buffer guarda os pedaços até o `\n`.

### Múltiplas mensagens em um recv
Várias mensagens que chegam **juntas em um só `recv`**. O buffer separa todas pelo `\n`.

### Envelope
A "carta" padrão: campos fixos que **toda** mensagem tem (`version`, `type`, `request_id`, `group`,
`source`, `destination`, `timestamp`, `payload`). *Analogia:* o envelope tem remetente, destinatário
e assunto; o `payload` é a carta dentro dele. → [p2p/envelope.py](../p2p/envelope.py)

### Payload
O conteúdo específico de cada tipo de mensagem. Ex.: o heartbeat leva `{"worker_id": ..., "seq": 7}`.

### Validação do envelope
Conferir, antes de usar, se a mensagem tem todos os campos com os tipos certos e versão compatível.
Se não tiver, ela é descartada e o master responde `error` — o processo **continua rodando**.

### type
Campo que diz **que tipo de mensagem** é: `register_worker`, `registration_ack`, `heartbeat`,
`heartbeat_ack`, `error`.

### Dispatcher e handler
- **Handler**: a função que trata um tipo de mensagem (ex.: `_handle_heartbeat`).
- **Dispatcher**: a "recepção" que olha o `type` e encaminha para o handler certo.
  Um `type` sem handler gera erro `unknown_type`. → [p2p/dispatcher.py](../p2p/dispatcher.py)

### request_id e correlação de respostas
Cada **solicitação** recebe um identificador único (`request_id`). A **resposta** repete esse mesmo id.
Assim quem perguntou sabe que aquela resposta é da pergunta dele, mesmo com várias perguntas em
andamento ou respostas fora de ordem. → [p2p/correlation.py](../p2p/correlation.py)

### ACK (acknowledgement)
"Confirmação de recebimento". `registration_ack` confirma o registro; `heartbeat_ack` confirma o heartbeat.

### Timeout
Tempo máximo de espera. Usamos dois:
- `ack_timeout` (worker): quanto espera por um ACK antes de considerar perdido;
- `heartbeat_timeout` (master): quanto tempo sem sinal até declarar o worker OFFLINE.

### Resposta órfã
Uma resposta que chega **depois** do timeout, quando ninguém mais espera por ela. É registrada
no log e descartada.

### Idempotência
Fazer a mesma operação duas vezes tem o mesmo efeito que fazer uma. Ex.: repetir `register_worker`
na mesma conexão devolve `already_registered` e não muda nada. Será essencial na Sprint 2 (retries).

---

## Parte 5 — Identidade e vivacidade

### UUID (Universally Unique Identifier)
Identificador de 128 bits gerado aleatoriamente, como `550e8400-e29b-41d4-a716-446655440000`.
A chance de dois UUIDs aleatórios coincidirem é desprezível, então cada nó pode gerar o seu sem
consultar ninguém. É a **identidade real** do worker.

### Label
Nome **amigável** para humanos (`worker-01`). Aparece nos logs. Não é a chave do cadastro:
dois nós com o mesmo label geram apenas um aviso; dois com o **mesmo UUID** são identidade duplicada.

### Configuração persistente
O UUID é **salvo no arquivo** de config na primeira execução e **reutilizado** nas seguintes.
Sem isso, cada reinício geraria um UUID novo e o master acharia que é outro worker (cadastro duplicado).
→ [p2p/config.py](../p2p/config.py)

### Escrita atômica
Salvar a config em um arquivo temporário e depois trocar de uma vez (`os.replace`). Se o processo
morrer no meio, o arquivo antigo continua íntegro (nunca fica "meio escrito").

### Registro (register_worker) e cadastro (registry)
O worker se apresenta com UUID e label; o master guarda no **registry**: status, endereço,
último sinal, contadores. → [p2p/registry.py](../p2p/registry.py)

### ONLINE / OFFLINE
Estado do cadastro de um worker no master. OFFLINE guarda o **motivo** (conexão encerrada ou
sem heartbeat). O cadastro nunca é apagado — é reaproveitado na reconexão.

### Heartbeat ("batimento cardíaco")
Mensagem periódica "estou vivo" do worker. Se os batimentos param por mais que `heartbeat_timeout`,
o master conclui que o worker caiu ou travou, mesmo com a conexão TCP aparentemente aberta.

### Monitor
Thread do master que, a cada `monitor_interval`, procura workers cujo último sinal é mais antigo
que `heartbeat_timeout`, marca OFFLINE e fecha a conexão deles.

### Relógio monotônico
`time.monotonic()`: um relógio que só anda para frente. Usado para medir "há quanto tempo sem sinal",
porque o relógio comum pode ser ajustado (horário de verão, sincronização) e dar saltos.

### Reconexão
Quando a conexão cai, o worker tenta conectar de novo **sozinho** e se registra com o **mesmo UUID**;
o master reaproveita o cadastro (`reconnected`) em vez de criar outro.

### Backoff exponencial
Esperar cada vez mais entre tentativas: 1 s, 2 s, 4 s, 8 s, 10 s, 10 s... Evita inundar o master de
tentativas quando ele está fora do ar.

### Identidade duplicada
Duas conexões ao mesmo tempo dizendo ter o **mesmo UUID** (ex.: o mesmo arquivo de config usado em
dois processos). O master **recusa** a segunda (`rejected / duplicate_identity`) e mantém a primeira.

---

## Parte 6 — Engenharia

### Log
Registro em texto do que aconteceu, com data/hora. O nosso traz grupo, processo, thread, e para cada
mensagem: `type`, origem, destino e `request_id`. Vai para o terminal e para `logs/<label>.log`.

### Teste unitário x teste de integração
- Unitário: testa uma peça isolada, sem rede (ex.: o buffer).
- Integração: testa peças funcionando juntas de verdade (master + worker via TCP).

### Commit
Um "ponto salvo" no histórico do Git, com uma mensagem explicando a mudança. O professor exige muitos
commits pequenos mostrando a construção. Veja [06-git-e-entrega.md](06-git-e-entrega.md).
