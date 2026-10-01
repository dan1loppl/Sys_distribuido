"""Dispatcher: olha o "type" da mensagem e chama a função certa (handler).

    mensagem chegou -> qual type? -> "register_worker" -> handle_register(...)
                                  -> "heartbeat"       -> handle_heartbeat(...)
                                  -> desconhecido      -> erro unknown_type

Vantagem: para suportar um novo tipo de mensagem (Sprint 2: task_request,
task_assignment...), basta registrar um novo handler, sem mexer no laço de rede.
"""

from __future__ import annotations

from typing import Any, Callable

from .envelope import UNKNOWN_TYPE, ProtocolError

Handler = Callable[[Any, dict], None]  # handler(conexao, mensagem)


class Dispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, type_: str, handler: Handler) -> None:
        self._handlers[type_] = handler

    def knows(self, type_: str) -> bool:
        return type_ in self._handlers

    def dispatch(self, connection: Any, message: dict) -> None:
        handler = self._handlers.get(message["type"])
        if handler is None:
            raise ProtocolError(
                UNKNOWN_TYPE,
                f"type '{message['type']}' não é tratado por este processo",
                message["request_id"],
            )
        handler(connection, message)
