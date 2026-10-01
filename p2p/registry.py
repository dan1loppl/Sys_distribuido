"""Registro de workers do master: quem existe, quem está ONLINE, quem caiu.

A chave do cadastro é o UUID (identidade estável), nunca o label nem o IP.

Regras do register_worker (o coração do "reconexão não duplica"):

    UUID nunca visto ................................ cria cadastro      -> "registered"
    UUID OFFLINE (já caiu antes) .................... reaproveita        -> "reconnected"
    UUID ONLINE na MESMA conexão (repetiu o pedido) . nada muda          -> "already_registered"
    UUID ONLINE em OUTRA conexão .................... recusa             -> "rejected" (identidade duplicada)

Ciclo de vida de um cadastro:

            register            queda / timeout
    (novo) ---------> ONLINE ------------------> OFFLINE
                        ^                           |
                        +------- register ----------+   (reconexão: mesmo cadastro)

Todas as operações usam um lock: várias threads leitoras e o monitor
mexem no registro ao mesmo tempo.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

ONLINE = "ONLINE"
OFFLINE = "OFFLINE"

REGISTERED = "registered"
RECONNECTED = "reconnected"
ALREADY_REGISTERED = "already_registered"
REJECTED = "rejected"


@dataclass
class WorkerRecord:
    worker_id: str
    label: str
    status: str
    address: str
    hostname: str = ""
    pid: int | None = None
    connection: Any = field(default=None, repr=False)
    registered_at: float = 0.0     # time.time() do primeiro cadastro
    last_seen: float = 0.0         # time.monotonic() do último sinal de vida
    heartbeats: int = 0
    reconnections: int = 0
    offline_reason: str = ""


class WorkerRegistry:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        # monotonic: relógio que nunca volta para trás (ajuste de horário não afeta)
        self._clock = clock
        self._lock = threading.Lock()
        self._workers: dict[str, WorkerRecord] = {}

    def register(self, worker_id: str, label: str, connection: Any, address: str,
                 hostname: str = "", pid: int | None = None) -> tuple[str, WorkerRecord]:
        now = self._clock()
        with self._lock:
            record = self._workers.get(worker_id)

            if record is None:
                record = WorkerRecord(
                    worker_id=worker_id, label=label, status=ONLINE, address=address,
                    hostname=hostname, pid=pid, connection=connection,
                    registered_at=time.time(), last_seen=now,
                )
                self._workers[worker_id] = record
                return REGISTERED, replace(record)

            if record.status == ONLINE:
                if record.connection is connection:
                    record.last_seen = now
                    return ALREADY_REGISTERED, replace(record)
                return REJECTED, replace(record)

            # Estava OFFLINE: é o mesmo worker voltando. Atualiza, não duplica.
            record.status = ONLINE
            record.label = label
            record.address = address
            record.hostname = hostname
            record.pid = pid
            record.connection = connection
            record.last_seen = now
            record.reconnections += 1
            record.offline_reason = ""
            return RECONNECTED, replace(record)

    def touch(self, worker_id: str, connection: Any) -> bool:
        """Registra um sinal de vida. False se o cadastro não pertence mais a essa conexão."""
        with self._lock:
            record = self._workers.get(worker_id)
            if record is None or record.status != ONLINE or record.connection is not connection:
                return False
            record.last_seen = self._clock()
            record.heartbeats += 1
            return True

    def mark_offline(self, worker_id: str, connection: Any, reason: str) -> WorkerRecord | None:
        """Marca OFFLINE — mas só se o cadastro ainda pertence a ESSA conexão.

        Sem essa conferência, o fechamento atrasado de uma conexão antiga
        derrubaria o cadastro que já foi reaproveitado por uma conexão nova.
        """
        with self._lock:
            record = self._workers.get(worker_id)
            if record is None or record.status != ONLINE or record.connection is not connection:
                return None
            self._set_offline(record, reason)
            return replace(record)

    def expire(self, timeout: float) -> list[WorkerRecord]:
        """Marca OFFLINE quem está sem sinal há mais de `timeout` segundos.
        Devolve os expirados (com a conexão, para o master fechá-la)."""
        now = self._clock()
        expired = []
        with self._lock:
            for record in self._workers.values():
                if record.status == ONLINE and now - record.last_seen > timeout:
                    silence = now - record.last_seen
                    connection = record.connection
                    self._set_offline(record, f"sem heartbeat há {silence:.1f}s")
                    expired.append(replace(record, connection=connection))
        return expired

    def get(self, worker_id: str) -> WorkerRecord | None:
        with self._lock:
            record = self._workers.get(worker_id)
            return replace(record) if record else None

    def find_by_label(self, label: str) -> list[WorkerRecord]:
        with self._lock:
            return [replace(r) for r in self._workers.values() if r.label == label]

    def snapshot(self) -> list[WorkerRecord]:
        """Cópias dos cadastros (seguras para ler fora do lock)."""
        with self._lock:
            return [replace(r) for r in sorted(self._workers.values(), key=lambda r: r.label)]

    def seconds_since_seen(self, record: WorkerRecord) -> float:
        return self._clock() - record.last_seen

    def __len__(self) -> int:
        with self._lock:
            return len(self._workers)

    @staticmethod
    def _set_offline(record: WorkerRecord, reason: str) -> None:
        record.status = OFFLINE
        record.connection = None
        record.offline_reason = reason
