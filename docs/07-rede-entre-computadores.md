# 07 — Como conectar worker e master em computadores diferentes

Guia rápido para quando o **master** está em um computador e os **workers** em outro.

---

## O erro mais comum

No worker aparece:

```text
Master indisponível em 127.0.0.1:5000 ([WinError 10061] Nenhuma conexão pôde ser feita
porque a máquina de destino as recusou ativamente)
```

**Por quê?** O worker está procurando o master em `127.0.0.1`, que significa
**"este próprio computador"**. Mas o master está em **outro** computador.

```text
Computador B (worker)                     Computador A (master)
worker → 127.0.0.1:5000                   master esperando na porta 5000
   └──► procura no próprio B  ✗           (o worker nunca chega aqui)
```

**Solução:** dizer ao worker o **IP do computador do master**.

---

## Passo a passo

### No computador do MASTER

**1. Inicie o master:**
```bash
python master.py
```

**2. Anote o IP** que ele mostra ao iniciar:
```text
Workers de outros computadores devem usar um destes IPs: 192.168.0.10, 172.28.80.1, 192.168.56.1
```
Use o IP da rede **Wi-Fi/Ethernet** (geralmente `192.168.x.x` ou `10.x.x.x`).
Ignore `192.168.56.x` (VirtualBox) e `172.x.x.x` (WSL/Hyper-V): são placas virtuais.

> Também dá para ver o IP com o comando `ipconfig` → "Endereço IPv4".

### No computador dos WORKERS

**3. Teste se o caminho está livre** (troque pelo IP anotado):
```powershell
Test-NetConnection 192.168.0.10 -Port 5000
```
- `TcpTestSucceeded : True` → tudo certo, siga para o passo 4.
- `False` → veja a seção **"Se não funcionar"**.

**4. Rode os workers informando o IP do master:**
```bash
python worker.py --config config/worker1.json --master-host 192.168.0.10
python worker.py --config config/worker2.json --master-host 192.168.0.10
```

**5. Confira no master:** deve aparecer `NOVO WORKER REGISTRADO`.

O IP fica **salvo** no arquivo de configuração. Das próximas vezes basta:
```bash
python worker.py --config config/worker1.json
```

---

## Se não funcionar

### 1º — Liberar o firewall (no computador do MASTER)

Abra o **PowerShell como administrador** e rode:
```powershell
New-NetFirewallRule -DisplayName "P2P Master 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
```
Depois repita o teste do passo 3.

> Na primeira vez que o master roda, o Windows pode mostrar um aviso de firewall.
> Clique em **Permitir acesso** e marque **Redes privadas**.

### 2º — Usar o hotspot do celular

Redes de faculdade muitas vezes **impedem um computador de falar com outro**. Nesse caso:

1. Ligue o **hotspot do celular**.
2. Conecte **os dois computadores** nele.
3. Reinicie o master e use o **novo IP** que ele mostrar.

---

## O que cada erro significa

| Mensagem / resultado | Significado | O que fazer |
|---|---|---|
| `recusou ativamente` (WinError 10061) | chegou no computador, mas nada escuta na porta | conferir o `--master-host` e se o master está rodando |
| `Test-NetConnection` demora ~20 s e dá `False` | algo está bloqueando no caminho | liberar o firewall ou usar o hotspot |
| `REGISTRO RECUSADO ... duplicate_identity` | dois workers com o mesmo UUID | não rode o mesmo arquivo de config em dois lugares; crie outro: `--config config/worker3.json --label worker-03` |

---

## Lembretes

- **O IP pode mudar** ao trocar de rede ou reiniciar o roteador. Confira sempre o IP que o master mostra.
- **O outro computador precisa ter o projeto:**
  ```bash
  git clone https://github.com/dan1loppl/Sys_distribuido.git
  cd Sys_distribuido
  ```
- **Mesma porta nos dois lados:** se o master usar `--port 5050`, os workers precisam de `--master-port 5050`.
- **Tudo em um computador só?** Aí sim `127.0.0.1` está certo — não precisa de `--master-host`.
