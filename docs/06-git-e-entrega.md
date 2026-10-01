# 06 — Git e entrega

## 1. Criar o repositório (uma pessoa do grupo)

1. No GitHub: **New repository** → nome sugerido `p2p-load-balancer` → **Private** (ou como o professor preferir).
   Não marque "Add README" (o projeto já tem um).
2. **Settings → Collaborators → Add people**:
   - os integrantes do grupo;
   - o professor: usuário **`micheljfr`**, com papel **Admin**
     (em repositório pessoal o convite dá acesso de escrita; para escolher "Admin", o repositório
     precisa estar em uma **Organization** — crie uma organização gratuita do grupo se for o caso).
3. No computador, dentro da pasta do projeto:

```bash
git init
git branch -M main
git remote add origin https://github.com/<usuario>/p2p-load-balancer.git
```

O mesmo repositório é usado até a Sprint 4.

## 2. Regras do professor (resumo)

- Commits **pequenos, frequentes, com mensagens claras**; histórico coerente com a construção real.
- Repositório com **commit único não é aceito**.
- IA só como **apoio pontual**; a autoria e a **compreensão da solução** são da equipe.

Este último ponto importa: na apresentação vocês precisam explicar qualquer parte do código.
Use os documentos `01` a `05` para estudar, e façam os commits **conforme cada integrante estuda,
testa e ajusta** cada parte — esse é o desenvolvimento real que o histórico deve mostrar.

## 3. Ordem natural de construção (sugestão de sequência de commits)

Cada passo é uma unidade que pode ser estudada, executada e testada sozinha.
Antes de cada commit, rode os testes daquela parte.

| # | Arquivos | Mensagem sugerida | Como verificar |
|---|---|---|---|
| 1 | `README.md`, `.gitignore` | `docs: descreve objetivo da sprint 1 e estrutura` | — |
| 2 | `p2p/__init__.py`, `p2p/config.py`, `config/*.json` | `feat: configuração persistente com UUID, label, host e port` | `python -m unittest tests.test_registry_config` (parte Config) |
| 3 | `p2p/ndjson.py`, `tests/test_ndjson.py` | `feat: enquadramento NDJSON com buffer de recepção` | `python -m unittest tests.test_ndjson -v` |
| 4 | `p2p/envelope.py`, `tests/test_envelope.py` | `feat: envelope de mensagens e validação` | `python -m unittest tests.test_envelope -v` |
| 5 | `p2p/dispatcher.py`, `p2p/correlation.py`, `tests/test_dispatcher_correlation.py` | `feat: dispatcher por type e correlação por request_id` | idem |
| 6 | `p2p/logs.py`, `p2p/connection.py` | `feat: conexão TCP com envio thread-safe e laço de leitura` | — |
| 7 | `p2p/registry.py` + parte Registry de `tests/test_registry_config.py` | `feat: registro de workers com reconexão e recusa de UUID duplicado` | `python -m unittest tests.test_registry_config -v` |
| 8 | `p2p/master.py`, `master.py` | `feat: master com registro, heartbeat_ack e monitor de timeout` | `python master.py` |
| 9 | `p2p/worker.py`, `worker.py` | `feat: worker com registro, heartbeat e reconexão com backoff` | master + 2 workers; `--parar-heartbeat-apos` |
| 10 | `tests/helpers.py`, `tests/test_integracao.py` | `test: integração de registro, queda, reconexão e enquadramento` | suíte completa |
| 11 | `tests/test_processos.py` | `test: master e dois workers como processos distintos` | suíte completa |
| 12 | `tools/sonda.py` | `feat: sonda para demonstrar testes de enquadramento ao vivo` | `python tools/sonda.py` |
| 13 | `docs/*` | `docs: glossário, arquitetura, protocolo, testes e roteiro` | — |

Comandos de cada commit:

```bash
git add <arquivos do passo>
git commit -m "<mensagem>"
git push            # na primeira vez: git push -u origin main
```

Se durante o estudo vocês mudarem algo (um valor padrão, uma mensagem de log, um teste novo, o nome do
grupo), isso é **mais um commit** — exatamente o tipo de evolução que o professor quer ver.

### Padrão de mensagens (Conventional Commits)

| Prefixo | Uso |
|---|---|
| `feat:` | funcionalidade nova |
| `fix:` | correção de erro |
| `test:` | testes |
| `docs:` | documentação |
| `refactor:` | reorganização sem mudar comportamento |
| `chore:` | configuração, `.gitignore` etc. |

## 4. O que **não** vai para o repositório

O `.gitignore` já exclui:
- `logs/` — gerados a cada execução;
- `__pycache__/` — cache do Python;
- `config/sonda.json` — identidade local da sonda;
- `sprints.md` e `pesquisa_base_sprint1.md` — anotações pessoais de estudo.

Os `config/master.json`, `worker1.json` e `worker2.json` **vão** para o repositório. Antes do primeiro
commit, deixe o campo `"uuid": ""` vazio (como estão agora) para que cada máquina gere o seu na primeira execução.
Depois de rodar, o Git vai mostrar esses arquivos como modificados (o UUID foi gravado) — é esperado.

## 5. Checklist de entrega da Sprint 1

- [ ] repositório criado, integrantes e `micheljfr` adicionados
- [ ] vários commits pequenos com mensagens claras
- [ ] `python -m unittest discover -s tests -t .` → OK
- [ ] README com instruções de execução
- [ ] demonstração ensaiada entre dois computadores ([05-apresentacao.md](05-apresentacao.md))
- [ ] cada integrante consegue explicar: NDJSON/buffer, envelope, dispatcher, request_id, threads, heartbeat, reconexão
