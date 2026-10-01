"""Utilitários dos testes de integração."""

import json
import logging
import socket
import time
import uuid

from p2p import envelope as env
from p2p.config import MASTER_DEFAULTS, WORKER_DEFAULTS
from p2p.ndjson import NdjsonBuffer, encode

GROUP = "grupo-teste"


def quiet_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"teste.{name}.{uuid.uuid4().hex[:6]}")
    logger.propagate = False
    logger.addHandler(logging.NullHandler())
    return logger


def master_config(**changes) -> dict:
    """Tempos curtos para os testes rodarem rápido. port=0: o SO escolhe uma porta livre."""
    config = {**MASTER_DEFAULTS, "group": GROUP, "uuid": str(uuid.uuid4()), "host": "127.0.0.1",
              "port": 0, "heartbeat_timeout": 1.0, "monitor_interval": 0.1, "status_interval": 0,
              "log_dir": None}
    config.update(changes)
    return config


def worker_config(port: int, label: str, worker_id: str | None = None, **changes) -> dict:
    config = {**WORKER_DEFAULTS, "group": GROUP, "uuid": worker_id or str(uuid.uuid4()), "label": label,
              "master_host": "127.0.0.1", "master_port": port, "heartbeat_interval": 0.2,
              "ack_timeout": 0.5, "max_missed_acks": 2, "reconnect_initial_delay": 0.1,
              "reconnect_max_delay": 0.3, "log_dir": None}
    config.update(changes)
    return config


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class RawClient:
    """Cliente TCP "na mão", para enviar bytes exatos ao master."""

    def __init__(self, port: int, label: str = "cliente-cru", worker_id: str | None = None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.buffer = NdjsonBuffer()
        self.inbox: list[dict] = []
        self.label = label
        self.worker_id = worker_id or str(uuid.uuid4())

    def message(self, type_: str, payload: dict) -> dict:
        return env.make_message(type_, group=GROUP, source=self.label, destination="master", payload=payload)

    def register_message(self) -> dict:
        return self.message(env.REGISTER_WORKER, {"worker_id": self.worker_id, "label": self.label})

    def heartbeat_message(self, seq: int = 1) -> dict:
        return self.message(env.HEARTBEAT, {"worker_id": self.worker_id, "seq": seq})

    def send(self, data) -> None:
        self.sock.sendall(data if isinstance(data, bytes) else encode(data))

    def read(self, count: int = 1, timeout: float = 3.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while len(self.inbox) < count and time.monotonic() < deadline:
            self.sock.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                break
            except OSError:
                break
            if not chunk:
                break
            self.inbox.extend(json.loads(line) for line in self.buffer.feed(chunk))
        taken, self.inbox = self.inbox[:count], self.inbox[count:]
        return taken

    def is_closed_by_peer(self, timeout: float = 3.0) -> bool:
        self.sock.settimeout(timeout)
        try:
            return self.sock.recv(65536) == b""
        except ConnectionError:
            return True
        except socket.timeout:
            return False

    def close(self) -> None:
        self.sock.close()
