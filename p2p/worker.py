"""Worker: conecta no master, se registra, manda heartbeats e reconecta se cair.

Threads do worker:

    worker-main   laço de conexão: conecta -> registra -> espera cair -> reconecta
    leitor        recv -> buffer -> entrega respostas a quem espera (correlação)
    heartbeat     a cada N s envia heartbeat e espera o heartbeat_ack

A thread heartbeat NÃO lê o socket: ela envia e fica esperando o
heartbeat_ack chegar pela thread leitora (via PendingRequests). Assim nada
fica bloqueado: na Sprint 2, uma thread de trabalho poderá calcular à vontade
enquanto o heartbeat continua saindo no ritmo certo.

Reconexão com backoff exponencial: espera 1 s, 2 s, 4 s, 8 s, 10 s, 10 s...
para não inundar o master de tentativas quando ele está fora do ar.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time

from . import envelope as env
from .connection import Connection
from .correlation import PendingRequests
from .dispatcher import Dispatcher
from .envelope import ProtocolError
from .registry import REJECTED

DISCONNECTED = "DESCONECTADO"
CONNECTING = "CONECTANDO"
REGISTERED = "REGISTRADO"


class Worker:
    def __init__(self, config: dict, logger: logging.Logger, stop_heartbeat_after: float | None = None) -> None:
        self.config = config
        self.logger = logger
        self.group: str = config["group"]
        self.label: str = config["label"]
        self.id: str = config["uuid"]
        self.master_address = (config["master_host"], int(config["master_port"]))
        self.heartbeat_interval = float(config["heartbeat_interval"])
        self.ack_timeout = float(config["ack_timeout"])
        self.max_missed_acks = int(config["max_missed_acks"])

        # Simulação de falha para a apresentação: depois de N segundos conectado,
        # para de mandar heartbeat SEM fechar a conexão (como um processo travado).
        self.stop_heartbeat_after = stop_heartbeat_after

        self.pending = PendingRequests()
        # Na Sprint 1 o master não envia solicitações ao worker; os handlers
        # de task_assignment etc. serão registrados aqui na Sprint 2.
        self.dispatcher = Dispatcher()

        self.master_label = "master"  # trocado pelo label real após o registration_ack
        self.state = DISCONNECTED
        self.registration_history: list[str] = []  # "registered", "reconnected", ...
        self.heartbeats_acked = 0
        self.registered_event = threading.Event()

        self._stop = threading.Event()
        self._conn: Connection | None = None
        self._main_thread: threading.Thread | None = None

    # ======================================================================
    # Ciclo de vida
    # ======================================================================
    def start(self) -> None:
        self._main_thread = threading.Thread(target=self.run, name="worker-main", daemon=True)
        self._main_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._conn:
            self._conn.close()
        if self._main_thread and self._main_thread is not threading.current_thread():
            self._main_thread.join(timeout=5)
        self.logger.info("Worker encerrado.")

    def run(self) -> None:
        """Laço de conexão: tenta manter o worker sempre conectado."""
        initial = float(self.config["reconnect_initial_delay"])
        maximum = float(self.config["reconnect_max_delay"])
        delay = initial
        self.logger.info("WORKER '%s' iniciado (uuid=%s). Master em %s:%s",
                         self.label, self.id, *self.master_address)

        while not self._stop.is_set():
            was_registered = self._session()
            if self._stop.is_set():
                break
            if was_registered:
                delay = initial  # a sessão funcionou: recomeça o backoff do início
            self.logger.info("Nova tentativa de conexão em %.1fs...", delay)
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, maximum)

    # ======================================================================
    # Uma sessão = uma conexão TCP, do connect até cair
    # ======================================================================
    def _session(self) -> bool:
        """Conecta, registra e mantém heartbeats. Devolve True se chegou a registrar."""
        self.state = CONNECTING
        try:
            sock = socket.create_connection(self.master_address, timeout=5)
        except OSError as exc:
            self.state = DISCONNECTED
            self.logger.warning("Master indisponível em %s:%s (%s)", *self.master_address, exc)
            return False
        sock.settimeout(None)

        conn = Connection(sock, self.master_address, self.logger)
        conn.peer_label = self.master_label
        self._conn = conn
        self.logger.info("Conectado ao master %s:%s (porta local %s)",
                         *self.master_address, sock.getsockname()[1])

        reader = threading.Thread(
            target=conn.serve, args=(self._on_message, self._on_invalid),
            name="leitor", daemon=True)
        reader.start()

        heartbeat = None
        registered = False
        try:
            registered = self._register(conn)
            if registered:
                heartbeat = threading.Thread(
                    target=self._heartbeat_loop, args=(conn,), name="heartbeat", daemon=True)
                heartbeat.start()
                conn.wait_closed()  # fica aqui enquanto a conexão estiver viva
        finally:
            conn.close()
            self.pending.cancel_all()  # acorda quem ainda esperava resposta
            reader.join(timeout=2)
            if heartbeat:
                heartbeat.join(timeout=2)
            self.state = DISCONNECTED
            self.registered_event.clear()
            if not self._stop.is_set():
                self.logger.warning("Conexão com o master perdida.")
        return registered

    def _register(self, conn: Connection) -> bool:
        message = self._message(env.REGISTER_WORKER, {
            "worker_id": self.id,
            "label": self.label,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
        })
        reply = self.request(conn, message)

        if reply is None:
            self.logger.warning("Sem resposta ao register_worker em %.1fs", self.ack_timeout)
            return False
        if reply["type"] == env.ERROR:
            self.logger.error("Registro recusado com erro: %s", reply["payload"])
            return False

        status = reply["payload"].get("status")
        self.registration_history.append(status)
        if status == REJECTED:
            self.logger.error(
                "REGISTRO RECUSADO pelo master: %s (%s). Outro processo está usando este UUID? "
                "Se este worker acabou de cair, o master libera o UUID após o timeout de heartbeat.",
                reply["payload"].get("reason"), reply["payload"].get("detail"))
            return False

        self.master_label = reply["source"]
        conn.peer_label = self.master_label
        self.state = REGISTERED
        self.registered_event.set()
        self.logger.info("REGISTRADO no master '%s' (status=%s)", self.master_label, status)
        return True

    def _heartbeat_loop(self, conn: Connection) -> None:
        seq = 0
        missed = 0
        started = time.monotonic()

        while not conn.closed and not self._stop.is_set():
            if self.stop_heartbeat_after is not None and time.monotonic() - started >= self.stop_heartbeat_after:
                self.logger.warning("SIMULAÇÃO DE FALHA: heartbeats interrompidos, conexão mantida aberta. "
                                    "O master deve detectar a ausência pelo timeout.")
                self.stop_heartbeat_after = None  # simula só uma vez (após reconectar volta ao normal)
                conn.wait_closed()
                return

            seq += 1
            sent_at = time.monotonic()
            reply = self.request(conn, self._message(env.HEARTBEAT, {"worker_id": self.id, "seq": seq}))

            if conn.closed:
                return
            if reply is None:
                missed += 1
                self.logger.warning("heartbeat seq=%d sem resposta (%d/%d)", seq, missed, self.max_missed_acks)
                if missed >= self.max_missed_acks:
                    self.logger.error("Master não responde; fechando conexão para reconectar.")
                    conn.close()
                    return
            elif reply["type"] == env.ERROR:
                self.logger.warning("heartbeat recusado: %s", reply["payload"])
                if reply["payload"].get("code") == env.NOT_REGISTERED:
                    conn.close()  # reconecta e registra de novo
                    return
            else:
                missed = 0
                self.heartbeats_acked += 1
                rtt_ms = (time.monotonic() - sent_at) * 1000
                self.logger.info("heartbeat_ack seq=%d confirmado (ida e volta: %.1f ms)", seq, rtt_ms)

            # Espera até o próximo heartbeat, mas acorda na hora se a conexão cair.
            remaining = self.heartbeat_interval - (time.monotonic() - sent_at)
            if remaining > 0 and conn.wait_closed(remaining):
                return

    # ======================================================================
    # Mensagens
    # ======================================================================
    def request(self, conn: Connection, message: dict) -> dict | None:
        """Envia uma solicitação e espera a resposta com o mesmo request_id."""
        waiter = self.pending.expect(message["request_id"])  # reserva ANTES de enviar
        if not conn.send(message):
            self.pending.cancel(message["request_id"])  # libera a reserva
            return None
        return waiter.wait(self.ack_timeout)

    def _on_message(self, message: dict) -> None:
        if message["type"] in env.RESPONSE_TYPES:
            if not self.pending.resolve(message):
                self.logger.warning("Resposta órfã %s request_id=%s (ninguém esperava; chegou após o timeout?)",
                                    message["type"], message["request_id"])
            return
        self.dispatcher.dispatch(self._conn, message)

    def _on_invalid(self, error: ProtocolError) -> None:
        # O worker só registra: não responde com "error" para evitar
        # um pingue-pongue infinito de erros entre os dois lados.
        raw = f" | conteúdo: {error.raw!r}" if error.raw is not None else ""
        self.logger.warning("Mensagem inválida do master descartada [%s] %s%s", error.code, error.detail, raw)

    def _message(self, type_: str, payload: dict) -> dict:
        return env.make_message(type_, group=self.group, source=self.label,
                                destination=self.master_label, payload=payload)
