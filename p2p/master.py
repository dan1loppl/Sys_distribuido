"""Master: aceita conexões de workers, mantém o cadastro e detecta quedas.

Threads do master (cada uma independente das outras):

    accept            espera novas conexões TCP; para cada uma cria uma thread leitora
    leitor-<worker>   uma por worker: recv -> buffer -> dispatcher -> resposta
    monitor           a cada 1 s procura workers sem heartbeat há mais de N s
    status            imprime a tabela de workers de tempos em tempos

Como cada worker tem a sua thread leitora, um worker lento ou com mensagens
ruins não atrasa os heartbeats dos outros.

Duas formas de detectar a queda de um worker:
    1. A conexão TCP fecha (Ctrl+C, processo morto): recv() devolve b"" ou erro.
    2. A conexão continua aberta mas o worker para de falar (travou, rede caiu
       sem aviso): o monitor percebe a falta de heartbeat e fecha a conexão.
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import uuid

from . import envelope as env
from .connection import Connection
from .dispatcher import Dispatcher
from .envelope import ProtocolError
from .registry import ALREADY_REGISTERED, OFFLINE, RECONNECTED, REGISTERED, REJECTED, WorkerRegistry


class Master:
    def __init__(self, config: dict, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.group: str = config["group"]
        self.label: str = config["label"]
        self.id: str = config["uuid"]
        self.heartbeat_timeout = float(config["heartbeat_timeout"])

        self.registry = WorkerRegistry()
        self.dispatcher = Dispatcher()
        self.dispatcher.register(env.REGISTER_WORKER, self._handle_register)
        self.dispatcher.register(env.HEARTBEAT, self._handle_heartbeat)

        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._connections: set[Connection] = set()
        self._connections_lock = threading.Lock()
        self.address: tuple[str, int] | None = None

    # ======================================================================
    # Ciclo de vida
    # ======================================================================
    def start(self) -> None:
        """Abre a porta e inicia as threads. Não bloqueia."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":
            # Permite reabrir a porta logo após reiniciar (no Windows o
            # SO_REUSEADDR tem outro significado e não é necessário).
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.config["host"], int(self.config["port"])))
        server.listen()
        server.settimeout(0.5)  # accept() acorda a cada 0,5 s para checar se deve parar
        self._server = server
        self.address = server.getsockname()

        self.logger.info("MASTER '%s' iniciado em %s:%s (uuid=%s)",
                         self.label, self.address[0], self.address[1], self.id)
        self.logger.info("Timeout de heartbeat: %.1fs. Aguardando workers...", self.heartbeat_timeout)
        if self.address[0] == "0.0.0.0":
            self.logger.info("Workers de outros computadores devem usar um destes IPs: %s",
                             ", ".join(local_ips()) or "(não identificado; use ipconfig)")

        self._spawn(self._accept_loop, "accept")
        self._spawn(self._monitor_loop, "monitor")
        if float(self.config.get("status_interval", 0)) > 0:
            self._spawn(self._status_loop, "status")

    def serve_forever(self) -> None:
        """Usado pelo master.py: inicia e fica rodando até Ctrl+C."""
        self.start()
        try:
            while not self._stop.wait(0.5):  # espera com timeout: Ctrl+C funciona no Windows
                pass
        except KeyboardInterrupt:
            self.logger.info("Ctrl+C recebido, encerrando o master...")
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            self._server.close()
        with self._connections_lock:
            connections = list(self._connections)
        for conn in connections:
            conn.close()
        for thread in self._threads:
            thread.join(timeout=2)
        self.logger.info("Master encerrado.")

    def _spawn(self, target, name: str, *args) -> threading.Thread:
        # daemon=True: a thread não impede o processo de terminar.
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)
        return thread

    # ======================================================================
    # Threads
    # ======================================================================
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, address = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # socket do servidor foi fechado no stop()
            sock.settimeout(None)  # a conexão em si é bloqueante
            conn = Connection(sock, address, self.logger)
            with self._connections_lock:
                self._connections.add(conn)
            self.logger.info("Nova conexão TCP de %s (%s)", conn.address_text, conn.name)
            self._spawn(self._connection_thread, f"leitor-conexao#{conn.id}", conn)

    def _connection_thread(self, conn: Connection) -> None:
        """Uma por conexão: roda o laço de leitura até a conexão acabar."""
        conn.serve(
            on_message=lambda message: self._on_message(conn, message),
            on_invalid=lambda error: self._on_invalid(conn, error),
        )
        # Chegou aqui: a conexão terminou.
        with self._connections_lock:
            self._connections.discard(conn)
        if conn.worker_id:
            record = self.registry.mark_offline(conn.worker_id, conn, "conexão TCP encerrada")
            if record:
                self.logger.warning("WORKER OFFLINE: %s (motivo: %s)", record.label, record.offline_reason)
                self.print_status()
        else:
            self.logger.info("Conexão %s (%s) encerrada", conn.name, conn.address_text)

    def _monitor_loop(self) -> None:
        interval = float(self.config["monitor_interval"])
        while not self._stop.wait(interval):
            for record in self.registry.expire(self.heartbeat_timeout):
                self.logger.warning("WORKER OFFLINE: %s (motivo: %s, limite %.1fs)",
                                    record.label, record.offline_reason, self.heartbeat_timeout)
                if record.connection:
                    record.connection.close()  # o worker percebe e tenta reconectar
                self.print_status()

    def _status_loop(self) -> None:
        interval = float(self.config["status_interval"])
        while not self._stop.wait(interval):
            self.print_status()

    # ======================================================================
    # Mensagens
    # ======================================================================
    def _on_message(self, conn: Connection, message: dict) -> None:
        if message["group"] != self.group:
            self.logger.warning("Mensagem de outro grupo (%s) em %s", message["group"], conn.name)
        if message["type"] in env.RESPONSE_TYPES:
            # Na Sprint 1 o master não faz solicitações, então não espera respostas.
            self.logger.warning("Resposta %s inesperada de %s; ignorada", message["type"], conn.name)
            return
        self.dispatcher.dispatch(conn, message)

    def _on_invalid(self, conn: Connection, error: ProtocolError) -> None:
        """Mensagem com problema: registra, avisa o remetente e CONTINUA rodando."""
        raw = f" | conteúdo: {error.raw!r}" if error.raw is not None else ""
        self.logger.warning("Mensagem inválida de %s descartada [%s] %s%s",
                            conn.name, error.code, error.detail, raw)
        conn.send(self._message(
            env.ERROR,
            destination=conn.peer_label or "desconhecido",
            request_id=error.request_id,
            payload={"code": error.code, "detail": error.detail},
        ))

    def _handle_register(self, conn: Connection, message: dict) -> None:
        payload = message["payload"]
        worker_id = _require_uuid(payload, "worker_id", message)
        label = _require_text(payload, "label", message)

        if conn.worker_id and conn.worker_id != worker_id:
            self._reply(conn, message, env.REGISTRATION_ACK, {
                "status": REJECTED, "reason": "connection_already_bound",
                "detail": "esta conexão já pertence a outro worker",
            })
            return

        others = [r for r in self.registry.find_by_label(label) if r.worker_id != worker_id]
        if others:
            self.logger.warning("Label '%s' já usado por outro UUID (%s). Labels deveriam ser únicos.",
                                label, others[0].worker_id)

        status, record = self.registry.register(
            worker_id, label, conn, conn.address_text,
            hostname=str(payload.get("hostname", "")), pid=payload.get("pid"),
        )

        if status == REJECTED:
            self.logger.warning(
                "IDENTIDADE DUPLICADA: %s tentou usar o UUID %s, que já está ONLINE como '%s' (%s). Registro recusado.",
                conn.address_text, worker_id, record.label, record.address)
            self._reply(conn, message, env.REGISTRATION_ACK, {
                "status": REJECTED, "reason": "duplicate_identity", "worker_id": worker_id,
                "detail": f"UUID já está ONLINE em {record.address}",
            })
            return

        conn.worker_id = worker_id
        conn.peer_label = label
        threading.current_thread().name = f"leitor-{label}"

        self._reply(conn, message, env.REGISTRATION_ACK, {
            "status": status,
            "worker_id": worker_id,
            "label": label,
            "master_id": self.id,
            "heartbeat_timeout": self.heartbeat_timeout,
            "reconnections": record.reconnections,
        })

        if status == REGISTERED:
            self.logger.info("NOVO WORKER REGISTRADO: %s (uuid=%s, de %s)", label, worker_id, conn.address_text)
        elif status == RECONNECTED:
            self.logger.info("WORKER RECONECTADO: %s (uuid=%s, reconexão nº %d, sem duplicar cadastro)",
                             label, worker_id, record.reconnections)
        elif status == ALREADY_REGISTERED:
            self.logger.info("Registro repetido de %s na mesma conexão; nada muda", label)
        if status != ALREADY_REGISTERED:
            self.print_status()

    def _handle_heartbeat(self, conn: Connection, message: dict) -> None:
        if not conn.worker_id:
            raise ProtocolError(env.NOT_REGISTERED, "envie register_worker antes do heartbeat")
        if message["payload"].get("worker_id") != conn.worker_id:
            raise ProtocolError(env.IDENTITY_MISMATCH,
                                "worker_id do heartbeat difere do registrado nesta conexão")
        if not self.registry.touch(conn.worker_id, conn):
            # O monitor já marcou OFFLINE (heartbeat atrasado demais).
            raise ProtocolError(env.NOT_REGISTERED, "cadastro expirado; registre-se novamente")

        self._reply(conn, message, env.HEARTBEAT_ACK, {
            "status": "ok",
            "seq": message["payload"].get("seq"),
        })

    # ======================================================================
    # Auxiliares
    # ======================================================================
    def _message(self, type_: str, *, destination: str, payload: dict, request_id: str | None = None) -> dict:
        return env.make_message(type_, group=self.group, source=self.label,
                                destination=destination, payload=payload, request_id=request_id)

    def _reply(self, conn: Connection, request: dict, type_: str, payload: dict) -> None:
        conn.send(env.make_reply(request, type_, group=self.group, source=self.label, payload=payload))

    def print_status(self) -> None:
        records = self.registry.snapshot()
        online = sum(1 for r in records if r.status != OFFLINE)
        lines = [f"===== WORKERS CADASTRADOS: {len(records)} ({online} ONLINE) ====="]
        if records:
            lines.append(f"{'LABEL':<16}{'STATUS':<9}{'ENDEREÇO':<23}{'HOSTNAME':<18}"
                         f"{'HB':>5}{'RECON':>7}  {'ÚLT. SINAL':<11}UUID")
        for r in records:
            if r.status == OFFLINE:
                seen = "-"
            else:
                seen = f"{self.registry.seconds_since_seen(r):.1f}s"
            lines.append(f"{r.label:<16}{r.status:<9}{r.address:<23}{r.hostname[:17]:<18}"
                         f"{r.heartbeats:>5}{r.reconnections:>7}  {seen:<11}{r.worker_id}")
            if r.status == OFFLINE and r.offline_reason:
                lines.append(f"{'':<16}-> motivo: {r.offline_reason}")
        self.logger.info("\n" + "\n".join(lines))


def _require_text(payload: dict, field: str, message: dict) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(env.INVALID_PAYLOAD, f"payload.{field} obrigatório (texto)", message["request_id"])
    return value.strip()


def _require_uuid(payload: dict, field: str, message: dict) -> str:
    value = _require_text(payload, field, message)
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ProtocolError(env.INVALID_PAYLOAD, f"payload.{field} não é um UUID válido",
                            message["request_id"]) from None


def local_ips() -> list[str]:
    """IPs IPv4 desta máquina (para mostrar aos workers onde conectar)."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        # Truque: "conectar" UDP não envia nada, mas revela o IP da rota padrão.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            ips.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))
