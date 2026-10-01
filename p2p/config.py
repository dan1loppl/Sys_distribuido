"""Configuração persistente: cada processo tem um arquivo JSON com sua identidade.

Na primeira execução, se o arquivo não tiver "uuid", um UUID novo é gerado
e SALVO no arquivo. Nas próximas execuções o mesmo UUID é reutilizado:
é assim que o master reconhece que "o worker que voltou é o mesmo que caiu".

    uuid   identidade única e estável (a máquina usa)
    label  nome amigável (as pessoas leem nos logs)
    host / port  onde escutar (master) ou onde conectar (worker)
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

MASTER_DEFAULTS: dict = {
    "group": "grupo-01",
    "uuid": "",
    "label": "master-01",
    "host": "0.0.0.0",            # 0.0.0.0 = aceita conexões de qualquer placa de rede
    "port": 5000,
    "heartbeat_timeout": 15.0,    # sem sinal por esse tempo => worker OFFLINE
    "monitor_interval": 1.0,      # de quanto em quanto tempo o monitor verifica
    "status_interval": 20.0,      # tabela de workers periódica (0 = desliga)
    "log_dir": "logs",
}

WORKER_DEFAULTS: dict = {
    "group": "grupo-01",
    "uuid": "",
    "label": "worker-01",
    "master_host": "127.0.0.1",   # IP do computador do master
    "master_port": 5000,
    "heartbeat_interval": 5.0,    # envia "estou vivo" a cada 5 s
    "ack_timeout": 3.0,           # quanto espera por uma resposta
    "max_missed_acks": 3,         # acks perdidos seguidos => master considerado morto
    "reconnect_initial_delay": 1.0,
    "reconnect_max_delay": 10.0,  # backoff: 1, 2, 4, 8, 10, 10... segundos
    "log_dir": "logs",
}


class ConfigError(ValueError):
    pass


def load_config(path: str | Path, defaults: dict, overrides: dict | None = None) -> dict:
    """Lê o arquivo (ou cria), aplica sobrescritas da linha de comando,
    garante um UUID e salva de volta se algo mudou."""
    path = Path(path)
    stored: dict = {}
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: JSON inválido ({exc})") from None
        if not isinstance(stored, dict):
            raise ConfigError(f"{path}: o arquivo deve conter um objeto JSON")

    config = {**defaults, **stored}
    for key, value in (overrides or {}).items():
        if value is not None:
            config[key] = value

    if not config.get("uuid"):
        config["uuid"] = str(uuid.uuid4())

    _validate(config, path)

    if config != stored:
        save_config(path, config)
    return config


def save_config(path: str | Path, config: dict) -> None:
    """Escrita atômica: grava em um temporário e troca de uma vez.
    Se o processo cair no meio, o arquivo antigo continua inteiro."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _validate(config: dict, path: Path) -> None:
    try:
        uuid.UUID(str(config["uuid"]))
    except ValueError:
        raise ConfigError(f"{path}: 'uuid' inválido: {config['uuid']!r}") from None

    if not str(config.get("label", "")).strip():
        raise ConfigError(f"{path}: 'label' não pode ser vazio")

    for key in ("port", "master_port"):
        if key in config:
            port = config[key]
            if not isinstance(port, int) or not 0 <= port <= 65535:
                raise ConfigError(f"{path}: '{key}' deve ser um inteiro entre 0 e 65535")
