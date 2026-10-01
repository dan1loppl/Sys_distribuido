"""Envelope: o formato padrão de TODAS as mensagens do protocolo.

Exemplo de mensagem (uma linha NDJSON, aqui formatada para leitura):

    {
      "version": "1.0",
      "type": "heartbeat",
      "request_id": "1d31f0c2-...",
      "group": "grupo-01",
      "source": "worker-01",
      "destination": "master-01",
      "timestamp": "2026-10-01T09:10:31.123+00:00",
      "payload": {"worker_id": "550e8400-...", "seq": 7}
    }

- O envelope (campos de fora) é igual para todas as mensagens.
- O payload (conteúdo) muda conforme o "type".
- Uma resposta repete o request_id da solicitação (correlação).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

PROTOCOL_VERSION = "1.0"

# ---- Tipos de mensagem da Sprint 1 ----------------------------------------
REGISTER_WORKER = "register_worker"    # worker -> master: "quero me cadastrar"
REGISTRATION_ACK = "registration_ack"  # master -> worker: resultado do cadastro
HEARTBEAT = "heartbeat"                # worker -> master: "estou vivo"
HEARTBEAT_ACK = "heartbeat_ack"        # master -> worker: "recebi"
ERROR = "error"                        # qualquer lado: "sua mensagem tem problema"

REQUEST_TYPES = frozenset({REGISTER_WORKER, HEARTBEAT})
RESPONSE_TYPES = frozenset({REGISTRATION_ACK, HEARTBEAT_ACK, ERROR})

# Campos obrigatórios do envelope e o tipo Python esperado de cada um.
REQUIRED_FIELDS: dict[str, type] = {
    "version": str,
    "type": str,
    "request_id": str,
    "group": str,
    "source": str,
    "destination": str,
    "timestamp": str,
    "payload": dict,
}

MAX_ID_LENGTH = 128

# ---- Códigos de erro ------------------------------------------------------
INVALID_JSON = "invalid_json"                # a linha não é JSON
INVALID_ENVELOPE = "invalid_envelope"        # é JSON, mas fora do formato
UNSUPPORTED_VERSION = "unsupported_version"  # versão de protocolo diferente
UNKNOWN_TYPE = "unknown_type"                # ninguém trata esse "type"
INVALID_PAYLOAD = "invalid_payload"          # payload sem os campos exigidos
NOT_REGISTERED = "not_registered"            # mensagem antes do register_worker
IDENTITY_MISMATCH = "identity_mismatch"      # heartbeat com UUID de outro worker
FRAME_TOO_LARGE = "frame_too_large"          # linha maior que o limite do buffer


class ProtocolError(Exception):
    """Mensagem recebida que viola o protocolo. Nunca derruba o processo:
    é registrada no log e (no master) devolvida ao remetente como "error"."""

    def __init__(self, code: str, detail: str, request_id: str | None = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.request_id = request_id
        self.raw: bytes | None = None  # trecho da linha original, para o log


def new_request_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_message(
    type_: str,
    *,
    group: str,
    source: str,
    destination: str,
    payload: dict | None = None,
    request_id: str | None = None,
) -> dict:
    """Monta uma mensagem completa. Sem request_id, gera um novo (nova solicitação)."""
    return {
        "version": PROTOCOL_VERSION,
        "type": type_,
        "request_id": request_id or new_request_id(),
        "group": group,
        "source": source,
        "destination": destination,
        "timestamp": now_iso(),
        "payload": payload or {},
    }


def make_reply(request: dict, type_: str, *, group: str, source: str, payload: dict) -> dict:
    """Monta a resposta de uma solicitação: mesmo request_id, destino = quem perguntou."""
    return make_message(
        type_,
        group=group,
        source=source,
        destination=request["source"],
        payload=payload,
        request_id=request["request_id"],
    )


def validate(message: object) -> dict:
    """Confere se a mensagem segue o envelope. Lança ProtocolError se não seguir."""
    if not isinstance(message, dict):
        raise ProtocolError(INVALID_ENVELOPE, "a mensagem deve ser um objeto JSON {...}")

    request_id = message.get("request_id") if isinstance(message.get("request_id"), str) else None

    for field, expected in REQUIRED_FIELDS.items():
        if field not in message:
            raise ProtocolError(INVALID_ENVELOPE, f"campo obrigatório ausente: '{field}'", request_id)
        if not isinstance(message[field], expected):
            raise ProtocolError(
                INVALID_ENVELOPE,
                f"campo '{field}' deve ser {expected.__name__}",
                request_id,
            )

    for field in ("type", "request_id", "group", "source", "destination"):
        value = message[field].strip()
        if not value:
            raise ProtocolError(INVALID_ENVELOPE, f"campo '{field}' não pode ser vazio", request_id)
        if len(value) > MAX_ID_LENGTH:
            raise ProtocolError(INVALID_ENVELOPE, f"campo '{field}' longo demais", request_id)

    # Aceita qualquer 1.x: versões "menores" devem ser compatíveis entre si.
    if message["version"].split(".")[0] != PROTOCOL_VERSION.split(".")[0]:
        raise ProtocolError(
            UNSUPPORTED_VERSION,
            f"versão '{message['version']}' não suportada (esperado {PROTOCOL_VERSION})",
            request_id,
        )

    return message


def parse_line(line: bytes) -> dict:
    """Linha NDJSON (bytes) -> mensagem validada. Lança ProtocolError se inválida."""
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError(INVALID_JSON, "a linha não é texto UTF-8 válido") from None

    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(INVALID_JSON, f"JSON inválido ({exc.msg}, coluna {exc.colno})") from None

    return validate(message)
