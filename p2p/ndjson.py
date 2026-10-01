"""Enquadramento (framing) NDJSON — Newline Delimited JSON.

Problema: o TCP entrega um FLUXO de bytes, não mensagens.
Um send() de 100 bytes pode chegar em vários recv() (fragmentação),
e vários send() podem chegar juntos em um único recv().

Solução: cada mensagem é um JSON em uma única linha, terminada por "\\n".
O NdjsonBuffer acumula os bytes recebidos e só entrega uma mensagem
quando encontra o "\\n" que marca o fim dela.

    recv() -> b'{"type":"hea'          buffer: {"type":"hea          -> nada pronto
    recv() -> b'rtbeat"}\\n{"ty'        buffer: {"ty                  -> entrega {"type":"heartbeat"}
    recv() -> b'pe":"x"}\\n'            buffer: (vazio)               -> entrega {"type":"x"}
"""

from __future__ import annotations

import json

DELIMITER = b"\n"

# Limite de segurança: uma "linha" sem "\n" maior que isso é descartada,
# para que um par defeituoso não faça o buffer crescer sem fim.
MAX_LINE_BYTES = 1024 * 1024  # 1 MiB


def encode(message: dict) -> bytes:
    """Converte um dicionário em uma linha NDJSON (bytes prontos para o socket).

    json.dumps nunca gera uma quebra de linha "crua": um "\\n" dentro de um
    texto vira os dois caracteres '\\' e 'n'. Por isso o "\\n" final é
    sempre, e somente, o delimitador da mensagem.
    """
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8") + DELIMITER


class NdjsonBuffer:
    """Acumula bytes recebidos e devolve as linhas completas.

    Trabalha com bytes (e não com texto) de propósito: um caractere UTF-8
    como "ç" ocupa 2 bytes e pode ser cortado ao meio entre dois recv().
    Só decodificamos depois que a linha inteira chegou.
    """

    def __init__(self, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self._buffer = bytearray()
        self._discarding = False  # True enquanto ignoramos o resto de uma linha gigante
        self.max_line_bytes = max_line_bytes
        self.dropped_frames = 0  # quantas linhas gigantes já foram descartadas

    @property
    def pending_bytes(self) -> int:
        """Bytes guardados esperando o "\\n" (mensagem ainda incompleta)."""
        return len(self._buffer)

    def feed(self, data: bytes) -> list[bytes]:
        """Adiciona bytes vindos do recv() e devolve as linhas completas (sem o "\\n")."""
        self._buffer.extend(data)
        lines: list[bytes] = []

        while True:
            index = self._buffer.find(DELIMITER)
            if index < 0:
                break  # não há fim de mensagem: o resto fica guardado

            line = bytes(self._buffer[:index])
            del self._buffer[: index + 1]  # remove a linha e o "\n"

            if self._discarding:
                # Este "\n" encerra a linha gigante que já descartamos.
                self._discarding = False
                continue

            line = line.rstrip(b"\r")  # tolera "\r\n" (ex.: telnet no Windows)
            if line.strip():  # linhas vazias são ignoradas
                lines.append(line)

        if len(self._buffer) > self.max_line_bytes:
            # Linha grande demais sem "\n": descarta o que chegou e ignora o
            # restante até o próximo "\n" (ressincroniza o fluxo).
            self._buffer.clear()
            if not self._discarding:
                self._discarding = True
                self.dropped_frames += 1

        return lines
