"""Connection: um socket TCP já conectado, com envio seguro e laço de leitura.

Envio (send): várias threads podem enviar pelo mesmo socket (ex.: no worker,
a thread de heartbeat; no master, a thread leitora e o monitor). Um lock
garante que uma mensagem não seja intercalada com outra no meio do caminho.

Leitura (serve): roda em uma thread própria e repete:

    recv() -> buffer NDJSON -> linhas completas -> JSON -> envelope -> on_message
                                                     \\-> erro? -> on_invalid (o processo continua)

O laço só termina quando a conexão acaba (o outro lado fechou, caiu, ou
alguém chamou close()). Nenhuma mensagem ruim derruba a thread.
"""

from __future__ import annotations

import itertools
import logging
import socket
import threading
from typing import Callable

from . import ndjson
from .envelope import FRAME_TOO_LARGE, ProtocolError, parse_line
from .logs import describe

RECV_SIZE = 4096
_ids = itertools.count(1)


class Connection:
    def __init__(self, sock: socket.socket, address: tuple, logger: logging.Logger) -> None:
        self.sock = sock
        self.address = address
        self.logger = logger
        self.id = next(_ids)
        self._buffer = ndjson.NdjsonBuffer()
        self._send_lock = threading.Lock()
        self._closed = threading.Event()
        # Preenchidos pelo master quando o worker se registra nesta conexão:
        self.worker_id: str | None = None
        self.peer_label: str | None = None

    # ---- informações ------------------------------------------------------
    @property
    def name(self) -> str:
        return self.peer_label or f"conexao#{self.id}"

    @property
    def address_text(self) -> str:
        return f"{self.address[0]}:{self.address[1]}"

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    # ---- envio --------------------------------------------------------------
    def send(self, message: dict) -> bool:
        """Envia uma mensagem do protocolo. Devolve False se a conexão caiu."""
        if self.closed:
            return False
        data = ndjson.encode(message)
        try:
            with self._send_lock:
                self.sock.sendall(data)  # sendall: repete send() até mandar tudo
        except OSError as exc:
            self.logger.warning("Falha ao enviar para %s (%s); conexão encerrada", self.name, exc)
            self.close()
            return False
        self.logger.info("SEND %s", describe(message))
        return True

    # ---- leitura ------------------------------------------------------------
    def serve(
        self,
        on_message: Callable[[dict], None],
        on_invalid: Callable[[ProtocolError], None],
    ) -> None:
        """Laço de leitura. Bloqueia até a conexão terminar."""
        try:
            while not self.closed:
                try:
                    chunk = self.sock.recv(RECV_SIZE)
                except OSError:
                    break  # conexão resetada/fechada
                if not chunk:
                    break  # b"" = o outro lado fechou a conexão (EOF)

                dropped_before = self._buffer.dropped_frames
                lines = self._buffer.feed(chunk)
                if self._buffer.dropped_frames > dropped_before:
                    on_invalid(ProtocolError(FRAME_TOO_LARGE, f"linha maior que {ndjson.MAX_LINE_BYTES} bytes descartada"))

                for line in lines:
                    self._process_line(line, on_message, on_invalid)
        finally:
            self.close()

    def _process_line(self, line: bytes, on_message, on_invalid) -> None:
        try:
            message = parse_line(line)
        except ProtocolError as err:
            err.raw = line[:120]
            on_invalid(err)
            return

        self.logger.info("RECV %s", describe(message))
        try:
            on_message(message)
        except ProtocolError as err:
            err.request_id = err.request_id or message["request_id"]
            on_invalid(err)
        except Exception:
            # Bug em um handler: registra e segue. A conexão continua viva.
            self.logger.exception("Erro inesperado tratando %s; mensagem descartada", message["type"])

    # ---- encerramento -------------------------------------------------------
    def close(self) -> None:
        """Fecha a conexão (pode ser chamado várias vezes, de qualquer thread).
        O shutdown acorda a thread que está parada no recv()."""
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
