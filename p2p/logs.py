"""Logs padronizados.

Cada linha traz: data/hora | nível | grupo | processo | thread | evento

    2026-10-01 09:10:31.123 | INFO  | grupo-01 | master-01 | leitor-worker-01 | RECV heartbeat origem=worker-01 destino=master-01 request_id=1d31f0c2-...

- "thread" mostra QUAL thread escreveu (accept, monitor, heartbeat, leitor-...),
  o que deixa visível que as threads trabalham de forma independente.
- O mesmo request_id aparece no log de quem enviou e de quem recebeu:
  dá para cruzar os logs dos processos e reconstruir a conversa.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


def setup_logging(process_label: str, group: str, log_dir: str | Path | None = "logs") -> logging.Logger:
    """Cria o logger do processo: escreve no terminal e em logs/<label>.log."""
    # Acentos corretos em qualquer terminal, e nenhum caractere derruba o print.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass

    formatter = logging.Formatter(
        fmt=f"%(asctime)s.%(msecs)03d | %(levelname)-5s | {group} | {process_label} | %(threadName)-18s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger = logging.getLogger(f"p2p.{process_label}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(Path(log_dir) / f"{process_label}.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def describe(message: dict, max_payload: int = 160) -> str:
    """Resumo de uma linha de uma mensagem do protocolo, para o log."""
    payload = json.dumps(message.get("payload", {}), ensure_ascii=False, separators=(",", ":"))
    if len(payload) > max_payload:
        payload = payload[: max_payload - 3] + "..."
    return (
        f"{message.get('type')} "
        f"grupo={message.get('group')} "
        f"origem={message.get('source')} "
        f"destino={message.get('destination')} "
        f"request_id={message.get('request_id')} "
        f"payload={payload}"
    )
