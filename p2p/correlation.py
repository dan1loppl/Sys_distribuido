"""Correlação de respostas: descobrir a qual solicitação cada resposta pertence.

O worker envia     heartbeat         request_id=ABC
e fica esperando.  ...
O master responde  heartbeat_ack     request_id=ABC   <- mesmo id!

Quem envia e quem recebe são threads diferentes:

    thread heartbeat:  expect("ABC") -> send(heartbeat) -> wait(3s) ...dorme...
    thread leitora:    recv() -> heartbeat_ack ABC -> resolve(msg) ----> acorda!

Se a resposta não chega no prazo, wait() devolve None (timeout). Se ela
chegar depois, resolve() devolve False: é uma resposta "órfã", só registrada no log.
"""

from __future__ import annotations

import threading


class Waiter:
    """Um "lugar reservado" para a resposta de UMA solicitação."""

    def __init__(self, owner: PendingRequests, request_id: str) -> None:
        self._owner = owner
        self._event = threading.Event()
        self.request_id = request_id
        self.response: dict | None = None

    def wait(self, timeout: float) -> dict | None:
        """Bloqueia até a resposta chegar ou o tempo acabar (devolve None)."""
        if not self._event.wait(timeout):
            self._owner._discard(self.request_id)
            # A resposta pode ter chegado entre o timeout e o _discard.
            if not self._event.is_set():
                return None
        return self.response

    def _deliver(self, response: dict | None) -> None:
        self.response = response
        self._event.set()


class PendingRequests:
    """Tabela thread-safe: request_id -> Waiter ainda sem resposta."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, Waiter] = {}

    def expect(self, request_id: str) -> Waiter:
        """Reserva o lugar ANTES de enviar: a resposta pode chegar muito rápido."""
        waiter = Waiter(self, request_id)
        with self._lock:
            self._pending[request_id] = waiter
        return waiter

    def resolve(self, response: dict) -> bool:
        """Entrega a resposta a quem espera. False = ninguém esperava (órfã)."""
        with self._lock:
            waiter = self._pending.pop(response["request_id"], None)
        if waiter is None:
            return False
        waiter._deliver(response)
        return True

    def cancel(self, request_id: str) -> None:
        """Desiste de esperar (ex.: o envio falhou)."""
        self._discard(request_id)

    def cancel_all(self) -> None:
        """Conexão caiu: acorda todos que esperam, com resposta None."""
        with self._lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for waiter in waiters:
            waiter._deliver(None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    def _discard(self, request_id: str) -> None:
        with self._lock:
            self._pending.pop(request_id, None)
