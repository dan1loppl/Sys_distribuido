# 04 — Testes

Há duas formas de testar, e as duas valem para a apresentação:

1. **Automatizada** — `python -m unittest discover -s tests -t . -v` (55 testes, ~6 s).
2. **Ao vivo** — `python tools/sonda.py` contra um master rodando, mostrando cada cenário na tela.

## 1. Como rodar

```bash
# todos os testes, com o nome de cada um
python -m unittest discover -s tests -t . -v

# só um arquivo
python -m unittest tests.test_ndjson -v

# só um teste
python -m unittest tests.test_integracao.QuedaEReconexaoTest.test_queda_detectada_e_reconexao_sem_duplicar

# com pytest (opcional: pip install pytest)
pytest -v
```

Saída esperada no final: `Ran 55 tests in ...s` e `OK`.

## 2. Requisitos da Sprint 1 → onde são provados

| Requisito / critério de "pronto" | Teste automatizado | Demonstração ao vivo |
|---|---|---|
| master e ≥2 workers como **processos distintos** | `test_processos.test_master_e_dois_workers_em_processos_distintos` | 3 terminais (ou 2 computadores) |
| UUID/label/host/port **persistentes** | `ConfigTest.test_uuid_e_gerado_e_persistido`, `..._sobrescritas_..._sao_salvas` | abrir `config/worker1.json` antes/depois de reiniciar |
| **Fragmentação** | `BufferTest.test_mensagem_fragmentada_byte_a_byte`, `..._caractere_utf8_cortado_ao_meio`, `EnquadramentoTest.test_mensagem_fragmentada_pela_rede`, `..._mensagem_maior_que_o_recv` | sonda cenário 1 |
| **Múltiplas mensagens em um recv** | `BufferTest.test_varias_mensagens_em_um_recv`, `EnquadramentoTest.test_varias_mensagens_em_um_envio` | sonda cenários 2 e 8 |
| **JSON inválido** não derruba | `EnvelopeTest.test_json_invalido`, `EnquadramentoTest.test_json_invalido_nao_derruba_e_conexao_continua`, `..._bytes_aleatorios_e_linha_gigante` | sonda cenário 3 |
| **Validação do envelope** | `EnvelopeTest.*`, `EnquadramentoTest.test_envelope_invalido_tipo_desconhecido_e_payload_invalido` | sonda cenário 4 |
| **Dispatcher por type** | `DispatcherTest.*` | sonda cenário 5 |
| **Correlação de respostas** | `CorrelationTest.*`, `test_varias_mensagens_em_um_envio` (ids na ordem certa) | sonda cenário 2; mesmo `request_id` nos logs dos dois lados |
| register_worker / registration_ack | `test_dois_workers_registram_...` | log `NOVO WORKER REGISTRADO` |
| heartbeat / heartbeat_ack **coexistem sem bloqueio** | `test_dois_workers_registram_e_mandam_heartbeats_ao_mesmo_tempo`, `test_heartbeat_continua_com_outro_worker_lento` | heartbeats intercalados no log; `--simular-trabalho` |
| **Queda detectada** (conexão fechada) | `test_queda_detectada_e_reconexao_sem_duplicar`, teste de processos (`kill`) | `Ctrl+C` em um worker |
| **Queda detectada** (sem heartbeat) | `test_worker_travado_detectado_por_timeout_de_heartbeat`, `RegistryTest.test_timeout_de_heartbeat` | `--parar-heartbeat-apos 15` |
| **Reconexão não duplica** | `RegistryTest.test_reconexao_nao_duplica`, `test_queda_detectada_e_reconexao_sem_duplicar`, `test_worker_que_para_heartbeat_volta_sozinho`, teste de processos | religar o worker; tabela continua com 2 |
| **Identidade duplicada** | `RegistryTest.test_identidade_duplicada_e_recusada`, `test_identidade_duplicada_recusada` | sonda cenários 7 e 9 (`--duplicar-config`) |
| Worker se recupera se o **master reinicia** | `test_worker_reconecta_quando_o_master_reinicia` | fechar e reabrir o master |
| Logs com grupo/origem/destino/type/request_id | teste de processos (procura `RECV heartbeat grupo=... origem=...`) | qualquer terminal / `logs/*.log` |

## 3. Arquivos de teste

| Arquivo | Tipo | O que cobre |
|---|---|---|
| `tests/test_ndjson.py` | unitário | buffer: fragmentação byte a byte, várias mensagens, UTF-8 cortado, CRLF, linha gigante |
| `tests/test_envelope.py` | unitário | montagem, resposta com mesmo `request_id`, JSON inválido, campos ausentes, versões |
| `tests/test_dispatcher_correlation.py` | unitário | roteamento por `type`, resposta em outra thread, fora de ordem, timeout, órfã |
| `tests/test_registry_config.py` | unitário | registro, duplicidade, reconexão, fechamento atrasado, timeout com relógio falso, config persistente |
| `tests/test_integracao.py` | integração (TCP real) | master + `Worker` + cliente "cru" na mesma máquina, com tempos curtos |
| `tests/test_processos.py` | ponta a ponta | `python master.py` + 2 × `python worker.py` como processos de verdade |
| `tests/helpers.py` | apoio | configs rápidas, `RawClient`, `wait_until` |

### Técnicas usadas (vale explicar se perguntarem)

- **Porta 0**: nos testes o master abre a porta 0 e o sistema escolhe uma livre. Assim os testes não
  brigam com um master que esteja rodando na 5000.
- **Relógio falso** (`FakeClock`): testa o timeout de 15 s sem esperar 15 s.
- **Tempos curtos**: heartbeat a cada 0,2 s e timeout de 1 s, para os testes durarem segundos.
- **`wait_until`**: em vez de `sleep` fixo, espera até a condição ficar verdadeira (com limite).
- **`RawClient`**: envia bytes exatos, inclusive inválidos, coisa que a classe `Worker` nunca faria.

## 4. A sonda (demonstração ao vivo)

```bash
python tools/sonda.py                                         # master local na 5000
python tools/sonda.py --host 192.168.0.10 --pausa 3           # master em outro PC, 3 s entre cenários
python tools/sonda.py --duplicar-config config/worker1.json   # tenta usar o UUID do worker-01 (precisa estar ONLINE)
```

| # | Cenário | Esperado |
|---|---|---|
| 1 | `register_worker` em 6 pedaços com pausas | um `registration_ack` com o mesmo `request_id` |
| 2 | 3 heartbeats em um único `sendall` | 3 `heartbeat_ack`, cada um com o `request_id` do seu heartbeat |
| 3 | `{ isso nao eh json` + heartbeat válido | `error invalid_json`, depois `heartbeat_ack` na mesma conexão |
| 4 | JSON sem envelope | `error invalid_envelope` com o `request_id` recebido |
| 5 | `type` desconhecido | `error unknown_type` |
| 6 | heartbeat sem registro | `error not_registered` |
| 7 | mesmo UUID em outra conexão | `registration_ack rejected / duplicate_identity` |
| 8 | lixo + heartbeat no mesmo envio | `error` e `heartbeat_ack` |
| 9 | (opcional) UUID de um worker real | `rejected`; o worker real segue normal |

Durante a sonda, olhe o terminal do master: cada problema aparece como `WARNING` e o master
**continua** atendendo os workers normais.

## 5. Teste manual com dois computadores (checklist)

- [ ] master no PC A mostra os IPs ao iniciar
- [ ] PC B: `Test-NetConnection <IP-do-A> -Port 5000` → `TcpTestSucceeded : True`
- [ ] worker-01 e worker-02 no PC B registram; a coluna ENDEREÇO da tabela mostra o IP do PC B
- [ ] `Ctrl+C` no worker-02 → `WORKER OFFLINE` imediato
- [ ] religar worker-02 → `WORKER RECONECTADO`, tabela com 2 cadastros
- [ ] `--parar-heartbeat-apos 15` → OFFLINE por timeout após ~15 s, depois volta sozinho
- [ ] `python tools/sonda.py --host <IP-do-A>` no PC B → 8/8 OK
