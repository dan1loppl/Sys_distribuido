"""Sonda de testes de enquadramento contra um MASTER EM EXECUÇÃO.

Ela abre conexões TCP e envia bytes "na mão" (sem a classe Worker), para
provar ao vivo que o master aguenta situações difíceis sem cair:

    1. mensagem fragmentada em vários pedaços
    2. várias mensagens em um único envio
    3. JSON inválido (e a conexão continua utilizável)
    4. JSON válido, mas fora do envelope
    5. type desconhecido
    6. heartbeat antes do registro
    7. identidade duplicada (mesmo UUID em duas conexões)
    8. lixo + mensagem válida no mesmo envio

Uso (com o master rodando):
    python tools/sonda.py
    python tools/sonda.py --host 192.168.0.10 --port 5000 --pausa 2
    python tools/sonda.py --duplicar-config config/worker1.json   # tenta "roubar" o UUID do worker-01
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite importar o pacote p2p

from p2p import envelope as env  # noqa: E402
from p2p.config import load_config  # noqa: E402
from p2p.ndjson import NdjsonBuffer, encode  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


class RawClient:
    """Cliente TCP mínimo: envia bytes exatos e lê respostas NDJSON."""

    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.create_connection((host, port), timeout=5)
        self.buffer = NdjsonBuffer()
        self.inbox: list[dict] = []

    def send_bytes(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read(self, count: int, timeout: float = 3.0) -> list[dict]:
        """Lê até `count` mensagens (ou até o tempo acabar)."""
        deadline = time.monotonic() + timeout
        while len(self.inbox) < count and time.monotonic() < deadline:
            self.sock.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            for line in self.buffer.feed(chunk):
                self.inbox.append(json.loads(line))
        taken, self.inbox = self.inbox[:count], self.inbox[count:]
        return taken

    def close(self) -> None:
        self.sock.close()


class Sonda:
    def __init__(self, args) -> None:
        self.args = args
        identity = load_config(args.config, {"group": args.group, "uuid": "", "label": "sonda"})
        self.group = args.group or identity["group"]
        self.label = identity["label"]
        self.uuid = identity["uuid"]
        self.results: list[tuple[str, bool]] = []
        self.main: RawClient | None = None
        self.seq = 0

    # ---- utilidades ---------------------------------------------------------
    def msg(self, type_: str, payload: dict, request_id: str | None = None) -> dict:
        return env.make_message(type_, group=self.group, source=self.label,
                                destination="master", payload=payload, request_id=request_id)

    def register_msg(self, worker_id: str, label: str) -> dict:
        return self.msg(env.REGISTER_WORKER, {"worker_id": worker_id, "label": label,
                                               "hostname": socket.gethostname(), "pid": 0})

    def heartbeat_msg(self) -> dict:
        self.seq += 1
        return self.msg(env.HEARTBEAT, {"worker_id": self.uuid, "seq": self.seq})

    def connect(self) -> RawClient:
        return RawClient(self.args.host, self.args.port)

    def title(self, number: int, text: str, explanation: str) -> None:
        time.sleep(self.args.pausa)
        print(f"\n{'=' * 78}\n[{number}] {text}\n    {explanation}\n{'-' * 78}")

    def show_sent(self, data: bytes) -> None:
        print(f"  ENVIADO  ({len(data)} bytes): {data[:150]!r}{' ...' if len(data) > 150 else ''}")

    def show_received(self, replies: list[dict]) -> None:
        if not replies:
            print("  RECEBIDO: (nada)")
        for reply in replies:
            print(f"  RECEBIDO: type={reply['type']} request_id={reply['request_id'][:8]}... payload={reply['payload']}")

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok))
        print(f"  >>> {'OK' if ok else 'FALHOU'}: {detail}")

    # ---- cenários -----------------------------------------------------------
    def fragmentada(self) -> None:
        self.title(1, "MENSAGEM FRAGMENTADA",
                   "Um register_worker enviado em 6 pedaços, com pausa entre eles. O master só processa ao ver o '\\n'.")
        self.main = self.connect()
        message = self.register_msg(self.uuid, self.label)
        data = encode(message)
        size = -(-len(data) // 6)
        for i in range(0, len(data), size):
            piece = data[i:i + size]
            self.main.send_bytes(piece)
            print(f"  pedaço {i // size + 1}: {piece!r}")
            time.sleep(0.3)
        replies = self.main.read(1)
        self.show_received(replies)
        ok = (len(replies) == 1 and replies[0]["type"] == env.REGISTRATION_ACK
              and replies[0]["request_id"] == message["request_id"]
              and replies[0]["payload"].get("status") in ("registered", "reconnected"))
        self.check("fragmentação", ok, "master remontou a mensagem e respondeu com o mesmo request_id")

    def multiplas(self) -> None:
        self.title(2, "VÁRIAS MENSAGENS EM UM ÚNICO ENVIO",
                   "3 heartbeats colados em um só sendall(): o master deve separar e responder os 3.")
        messages = [self.heartbeat_msg() for _ in range(3)]
        data = b"".join(encode(m) for m in messages)
        self.show_sent(data)
        self.main.send_bytes(data)
        replies = self.main.read(3)
        self.show_received(replies)
        expected = {m["request_id"] for m in messages}
        got = {r["request_id"] for r in replies if r["type"] == env.HEARTBEAT_ACK}
        self.check("múltiplas mensagens", got == expected,
                   "3 heartbeat_ack, cada um com o request_id do seu heartbeat (correlação)")

    def json_invalido(self) -> None:
        self.title(3, "JSON INVÁLIDO",
                   "Envia texto que não é JSON. O master responde 'error' e a MESMA conexão continua funcionando.")
        data = b"{ isso nao eh json\n"
        self.show_sent(data)
        self.main.send_bytes(data)
        replies = self.main.read(1)
        self.show_received(replies)
        error_ok = bool(replies) and replies[0]["payload"].get("code") == env.INVALID_JSON

        print("  ...agora um heartbeat válido na mesma conexão:")
        heartbeat = self.heartbeat_msg()
        self.main.send_bytes(encode(heartbeat))
        after = self.main.read(1)
        self.show_received(after)
        alive_ok = bool(after) and after[0]["type"] == env.HEARTBEAT_ACK
        self.check("JSON inválido", error_ok and alive_ok, "erro invalid_json e master continua respondendo")

    def envelope_invalido(self) -> None:
        self.title(4, "ENVELOPE INVÁLIDO",
                   "JSON válido, mas sem os campos obrigatórios (version, request_id, source...).")
        data = b'{"type":"heartbeat","request_id":"sem-envelope-123"}\n'
        self.show_sent(data)
        self.main.send_bytes(data)
        replies = self.main.read(1)
        self.show_received(replies)
        ok = (bool(replies) and replies[0]["payload"].get("code") == env.INVALID_ENVELOPE
              and replies[0]["request_id"] == "sem-envelope-123")
        self.check("envelope inválido", ok, "erro invalid_envelope, correlacionado pelo request_id recebido")

    def tipo_desconhecido(self) -> None:
        self.title(5, "TYPE DESCONHECIDO",
                   "Envelope correto, mas um type que nenhum handler trata. O dispatcher recusa.")
        message = self.msg("dancar_tango", {})
        self.show_sent(encode(message))
        self.main.send_bytes(encode(message))
        replies = self.main.read(1)
        self.show_received(replies)
        ok = bool(replies) and replies[0]["payload"].get("code") == env.UNKNOWN_TYPE
        self.check("type desconhecido", ok, "erro unknown_type")

    def sem_registro(self) -> None:
        self.title(6, "HEARTBEAT ANTES DO REGISTRO",
                   "Nova conexão manda heartbeat sem register_worker. O master exige registro primeiro.")
        client = self.connect()
        try:
            message = self.heartbeat_msg()
            client.send_bytes(encode(message))
            replies = client.read(1)
            self.show_received(replies)
            ok = bool(replies) and replies[0]["payload"].get("code") == env.NOT_REGISTERED
        finally:
            client.close()
        self.check("sem registro", ok, "erro not_registered")

    def duplicada(self, worker_id: str, label: str, number: int = 7) -> None:
        self.title(number, f"IDENTIDADE DUPLICADA ({label})",
                   f"Segunda conexão tenta registrar o UUID {worker_id[:8]}..., que já está ONLINE.")
        client = self.connect()
        try:
            client.send_bytes(encode(self.register_msg(worker_id, label)))
            replies = client.read(1)
            self.show_received(replies)
            ok = (bool(replies) and replies[0]["payload"].get("status") == "rejected"
                  and replies[0]["payload"].get("reason") == "duplicate_identity")
        finally:
            client.close()
        self.check(f"identidade duplicada ({label})", ok, "registro recusado; o cadastro original continua")

    def misturado(self) -> None:
        self.title(8, "LIXO + MENSAGEM VÁLIDA NO MESMO ENVIO",
                   "Uma linha inválida e um heartbeat válido chegam juntos. O inválido é descartado, o válido é atendido.")
        heartbeat = self.heartbeat_msg()
        data = b"###lixo###\n" + encode(heartbeat)
        self.show_sent(data)
        self.main.send_bytes(data)
        replies = self.main.read(2)
        self.show_received(replies)
        types = [r["type"] for r in replies]
        ok = types == [env.ERROR, env.HEARTBEAT_ACK] and replies[1]["request_id"] == heartbeat["request_id"]
        self.check("lixo + válido", ok, "error para o lixo, heartbeat_ack para o válido")

    # ---- execução -------------------------------------------------------------
    def run(self) -> int:
        print(f"Sonda '{self.label}' (uuid={self.uuid}) -> master {self.args.host}:{self.args.port}")
        try:
            self.fragmentada()
            self.multiplas()
            self.json_invalido()
            self.envelope_invalido()
            self.tipo_desconhecido()
            self.sem_registro()
            self.duplicada(self.uuid, self.label)
            self.misturado()
            if self.args.duplicar_config:
                target = json.loads(Path(self.args.duplicar_config).read_text(encoding="utf-8"))
                if target.get("uuid"):
                    self.duplicada(target["uuid"], target.get("label", "?"), number=9)
                else:
                    print(f"\n{self.args.duplicar_config} ainda não tem uuid (rode o worker uma vez antes).")
        except (ConnectionError, OSError) as exc:
            print(f"\nERRO de conexão: {exc}. O master está rodando em {self.args.host}:{self.args.port}?")
            return 1
        finally:
            if self.main:
                self.main.close()

        passed = sum(ok for _, ok in self.results)
        print(f"\n{'=' * 78}\nRESULTADO: {passed}/{len(self.results)} cenários OK")
        for name, ok in self.results:
            print(f"  [{'OK' if ok else 'FALHOU'}] {name}")
        print("Confira no terminal do master: ele registrou cada problema e continuou rodando.")
        return 0 if passed == len(self.results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Testes de enquadramento contra um master em execução")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--group", default=None, help="grupo usado nas mensagens (padrão: o do config/master.json)")
    parser.add_argument("--config", default="config/sonda.json", help="identidade persistente da sonda")
    parser.add_argument("--pausa", type=float, default=1.0, help="segundos entre cenários (para a apresentação)")
    parser.add_argument("--duplicar-config", help="config de um worker ONLINE cujo UUID será 'roubado'")
    args = parser.parse_args()
    if args.group is None:
        master_cfg = Path("config/master.json")
        args.group = json.loads(master_cfg.read_text(encoding="utf-8")).get("group") if master_cfg.exists() else "grupo-01"
    return Sonda(args).run()


if __name__ == "__main__":
    sys.exit(main())
