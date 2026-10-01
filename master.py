"""Executável do MASTER.

Exemplos:
    python master.py                                  # usa config/master.json
    python master.py --port 6000                      # muda a porta (e salva no arquivo)
    python master.py --config config/master.json --group grupo-07
"""

import argparse
import sys

from p2p.config import MASTER_DEFAULTS, ConfigError, load_config
from p2p.logs import setup_logging
from p2p.master import Master


def main() -> int:
    parser = argparse.ArgumentParser(description="Master do sistema P2P (Sprint 1)")
    parser.add_argument("--config", default="config/master.json", help="arquivo de configuração persistente")
    parser.add_argument("--label", help="nome amigável do master")
    parser.add_argument("--group", help="nome do grupo (aparece em todos os logs)")
    parser.add_argument("--host", help="IP onde escutar (0.0.0.0 = todas as placas de rede)")
    parser.add_argument("--port", type=int, help="porta TCP onde escutar")
    args = parser.parse_args()

    try:
        config = load_config(args.config, MASTER_DEFAULTS, {
            "label": args.label, "group": args.group, "host": args.host, "port": args.port,
        })
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2

    logger = setup_logging(config["label"], config["group"], config.get("log_dir"))
    logger.info("Configuração carregada de %s", args.config)
    try:
        Master(config, logger).serve_forever()
    except OSError as exc:
        logger.error("Não foi possível abrir %s:%s (%s). A porta já está em uso?",
                     config["host"], config["port"], exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
