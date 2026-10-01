# 05 — Roteiro da apresentação

Objetivo: mostrar, **entre dois computadores**, cada critério de "pronto" da Sprint 1 — e saber
explicar o que está acontecendo. Tempo estimado: 10–12 minutos.

## 1. Preparação (antes da aula)

### 1.1 Nos dois computadores
- Python 3.10+ instalado (`python --version`).
- Repositório clonado (`git clone ...`) e testes passando: `python -m unittest discover -s tests -t .`

### 1.2 Rede
Os dois computadores precisam estar na **mesma rede** e um precisa alcançar o outro.

1. **PC A (master)**: descubra o IP com `ipconfig` → "Endereço IPv4" do adaptador Wi-Fi/Ethernet
   (ex.: `192.168.0.10`). O master também lista os IPs ao iniciar — ignore os de adaptadores virtuais
   (`172.x` do WSL/Hyper-V, `192.168.56.x` do VirtualBox).
2. **Firewall do Windows**: na primeira execução do master aparece "Permitir acesso" → marque **Redes privadas**.
   Se a rede estiver como "Pública", mude para "Privada" (Configurações → Rede → propriedades do Wi-Fi)
   ou permita também em públicas.
3. **PC B (workers)**: confirme que alcança a porta:
   ```powershell
   Test-NetConnection 192.168.0.10 -Port 5000
   ```
   `TcpTestSucceeded : True` = tudo certo. `False` = firewall ou rede bloqueando.
4. **Rede da faculdade bloqueia conexões entre aparelhos?** (comum: "isolamento de clientes")
   Plano B: **roteador do celular** (hotspot) com os dois notebooks conectados nele.

### 1.3 Configuração
- PC A: `config/master.json` → ajuste `group` para o nome do seu grupo.
- PC B: rode os workers uma vez já apontando para o master (o IP fica salvo):
  ```bash
  python worker.py --config config/worker1.json --master-host 192.168.0.10 --group grupo-01
  python worker.py --config config/worker2.json --master-host 192.168.0.10 --group grupo-01
  ```
- Dica: **não** rode o mesmo arquivo de worker nos dois PCs — se os UUIDs forem iguais, o master recusa
  (identidade duplicada). Para um worker extra no PC A, crie outro: `--config config/worker3.json --label worker-03`.

### 1.4 Terminais
Fonte grande, janelas lado a lado. No VS Code: Terminal → "Split Terminal".

```text
PC A (projetor)               PC B
┌───────────────────┐         ┌──────────────┬──────────────┐
│ MASTER            │         │ worker-01    │ worker-02    │
│                   │         ├──────────────┴──────────────┤
│                   │         │ sonda / comandos            │
└───────────────────┘         └─────────────────────────────┘
```

## 2. Roteiro

### Passo 0 — Contexto (30 s)
"Na Sprint 1 construímos a base de comunicação e identidade: master e workers em processos e computadores
diferentes, conversando por TCP com um protocolo NDJSON nosso. Ainda não há tarefas — isso é a Sprint 2."

Mostrar rapidamente: estrutura de pastas e `config/worker1.json` (UUID, label, host, port).

### Passo 1 — Iniciar o master (PC A)
```bash
python master.py
```
Mostrar: `MASTER 'master-01' iniciado em 0.0.0.0:5000`, os IPs listados, `Aguardando workers...`

> Explicar: `0.0.0.0` = escuta em todas as placas de rede; a thread `accept` está esperando conexões.

### Passo 2 — Worker 1 e Worker 2 (PC B)
```bash
python worker.py --config config/worker1.json
python worker.py --config config/worker2.json
```
No master, apontar:
- `Nova conexão TCP de 192.168.0.20:...` → **IP do outro computador**: é rede de verdade;
- `RECV register_worker ... origem=worker-01 destino=master request_id=...`
- `SEND registration_ack ... request_id=<o mesmo>` → **correlação**;
- `NOVO WORKER REGISTRADO` e a tabela com os dois.

### Passo 3 — Heartbeats coexistindo
Deixar rodar ~10 s. Apontar no master as linhas de `worker-01` e `worker-02` **intercaladas**, cada uma
escrita por uma thread diferente (`leitor-worker-01`, `leitor-worker-02`). No worker, `heartbeat_ack seq=N
confirmado (ida e volta: X ms)`.

> Explicar: cada worker tem sua thread leitora no master; no worker, a thread `heartbeat` envia e a thread
> `leitor` recebe — ninguém bloqueia ninguém.

(Opcional) Reiniciar um worker com `--simular-trabalho`: o `MainThread` calcula e os heartbeats continuam.

### Passo 4 — Queda detectada (Ctrl+C)
`Ctrl+C` no worker-02. No master: `WORKER OFFLINE: worker-02 (motivo: conexão TCP encerrada)` e a tabela
`2 cadastros (1 ONLINE)`. O worker-01 segue normal.

### Passo 5 — Reconexão sem duplicar
```bash
python worker.py --config config/worker2.json
```
No master: `WORKER RECONECTADO: worker-02 (... reconexão nº 1, sem duplicar cadastro)`.
Tabela: **ainda 2 cadastros**, coluna RECON = 1, mesmo UUID.

> Explicar: o UUID está salvo no arquivo de config; o master usa o UUID como chave, então reaproveita o cadastro.

### Passo 6 — Queda sem fechar a conexão (timeout de heartbeat)
`Ctrl+C` no worker-02 atual e religue com a simulação:
```bash
python worker.py --config config/worker2.json --parar-heartbeat-apos 10
```
Após 10 s o worker avisa `SIMULAÇÃO DE FALHA`. ~15 s depois, no master (thread `monitor`):
`WORKER OFFLINE: worker-02 (motivo: sem heartbeat há 15.x s)`. Em seguida o worker percebe, reconecta
e volta `reconnected`.

> Explicar: se a máquina trava ou a rede cai, o TCP não avisa ninguém. Só o heartbeat revela isso.

### Passo 7 — Testes de enquadramento ao vivo
No PC B (com o master rodando no A):
```bash
python tools/sonda.py --host 192.168.0.10 --pausa 2
python tools/sonda.py --host 192.168.0.10 --pausa 0 --duplicar-config config/worker1.json
```
Mostrar a sonda (`8/8` e `9/9` OK) **e** o master: cada problema vira `WARNING`, e ele continua atendendo os workers.

### Passo 8 — Testes automatizados
```bash
python -m unittest discover -s tests -t . -v
```
`Ran 55 tests ... OK`. Citar o teste de processos (sobe master + 2 workers de verdade e derruba um).

### Passo 9 — Logs e Git
- `logs/master-01.log` e `logs/worker-01.log`: pegar um `request_id` e achar nos dois arquivos.
- `git log --oneline`: histórico incremental.

## 3. Perguntas prováveis e respostas curtas

**Por que não basta mandar o JSON direto pelo socket?**
Porque TCP é um fluxo de bytes: um `recv` pode trazer meia mensagem ou várias. O `\n` do NDJSON marca
onde cada uma termina, e o buffer guarda os pedaços até ela ficar completa.

**E se o JSON tiver um "\n" dentro de um texto?**
O `json.dumps` troca a quebra de linha pelos dois caracteres `\` e `n`. O único byte de quebra de linha
real é o delimitador no fim da mensagem.

**Para que o UUID se já existe o label?**
O label é para humanos e pode se repetir ou mudar; o UUID é gerado uma vez, salvo no arquivo e identifica
o worker de forma única. É a chave do cadastro.

**Como vocês evitam cadastro duplicado na reconexão?**
A chave é o UUID persistente. Se o UUID já existe e está OFFLINE, o cadastro é reaproveitado (`reconnected`).
Se está ONLINE em outra conexão, é identidade duplicada e a nova conexão é recusada.

**Por que recusar o novo em vez de derrubar o antigo?**
Para que um processo com config copiada (ou mal-intencionado) não consiga expulsar um worker legítimo.
Se o antigo estiver realmente morto, o timeout de heartbeat libera o UUID e a próxima tentativa do worker
é aceita automaticamente.

**Para que o heartbeat, se o TCP já avisa quando cai?**
Só avisa quando o outro lado fecha a conexão. Em travamento ou queda de rede, a conexão fica "meio-aberta"
e nada chega. O heartbeat detecta o silêncio.

**Como o heartbeat não bloqueia?**
Threads: no master, uma por conexão; no worker, a thread de heartbeat é separada da que lê o socket e da
principal. A resposta chega pela thread leitora e é entregue pelo `request_id`.

**O que é o request_id e a correlação?**
Toda solicitação tem um id único; a resposta repete o id. O worker guarda "estou esperando o id X" e,
quando chega um ACK com X, acorda quem esperava. Resposta com id desconhecido (chegou após o timeout) é órfã.

**O que acontece com uma mensagem inválida?**
É descartada **só aquela linha**: o master registra o `WARNING`, responde `error` com um código
(`invalid_json`, `invalid_envelope`...) e segue lendo a mesma conexão.

**O que é o dispatcher?**
Uma tabela `type → função`. Para a Sprint 2, basta registrar novos handlers (`task_request` etc.).

**Por que lock no envio?**
Mais de uma thread pode enviar pelo mesmo socket; sem o lock, os bytes de duas mensagens poderiam se misturar.

**E se o master cair?**
Os workers percebem (EOF ou ACKs perdidos), entram em reconexão com backoff (1, 2, 4, 8, 10 s) e se
registram de novo sozinhos quando ele volta.

**O registro sobrevive a um reinício do master?**
Não — fica em memória (a sprint não pede). Como os workers reconectam e se registram sozinhos, o master
reconstrói a lista em segundos.

## 4. Plano B

| Problema | Saída |
|---|---|
| Rede da sala bloqueia | hotspot do celular |
| Firewall não libera a tempo | rodar tudo no PC A com `127.0.0.1` (processos distintos continuam valendo) e mostrar o teste de processos |
| Porta 5000 ocupada | `python master.py --port 5050` e `--master-port 5050` nos workers |
| Worker recusado por duplicidade sem querer | os dois PCs estão usando o mesmo arquivo de config; use outro arquivo (`--config config/worker3.json --label worker-03`) |
