"""Executável do WORKER.

Exemplos:
    python worker.py --config config/worker1.json
    python worker.py --config config/worker2.json --master-host 192.168.0.10
    python worker.py --config config/worker3.json --label worker-03      # cria um worker novo

Opções de demonstração:
    --simular-trabalho          a thread principal fica ocupada calculando; os
                                heartbeats continuam saindo (threads independentes)
    --parar-heartbeat-apos 20   após 20 s, para de mandar heartbeat SEM fechar a
                                conexão (simula travamento; o master detecta por timeout)
"""

import argparse
import sys
import time

from p2p.config import WORKER_DEFAULTS, ConfigError, load_config
from p2p.logs import setup_logging
from p2p.worker import Worker


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker do sistema P2P (Sprint 1)")
    parser.add_argument("--config", default="config/worker1.json", help="arquivo de configuração persistente")
    parser.add_argument("--label", help="nome amigável do worker")
    parser.add_argument("--group", help="nome do grupo (aparece em todos os logs)")
    parser.add_argument("--master-host", help="IP do computador onde o master está rodando")
    parser.add_argument("--master-port", type=int, help="porta do master")
    parser.add_argument("--simular-trabalho", action="store_true",
                        help="ocupa a thread principal com cálculo para mostrar que o heartbeat não trava")
    parser.add_argument("--parar-heartbeat-apos", type=float, metavar="SEGUNDOS",
                        help="simula um worker travado: para os heartbeats mas mantém a conexão")
    args = parser.parse_args()

    try:
        config = load_config(args.config, WORKER_DEFAULTS, {
            "label": args.label, "group": args.group,
            "master_host": args.master_host, "master_port": args.master_port,
        })
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2

    logger = setup_logging(config["label"], config["group"], config.get("log_dir"))
    logger.info("Configuração carregada de %s", args.config)

    worker = Worker(config, logger, stop_heartbeat_after=args.parar_heartbeat_apos)
    worker.start()
    try:
        if args.simular_trabalho:
            simulate_work(logger)
        while True:
            time.sleep(0.5)  # a thread principal só espera o Ctrl+C
    except KeyboardInterrupt:
        logger.info("Ctrl+C recebido, encerrando o worker...")
    finally:
        worker.stop()
    return 0


def simulate_work(logger) -> None:
    """Trabalho de CPU na thread principal (prévia da Sprint 2).
    Enquanto isto roda, as threads de rede continuam funcionando."""
    logger.info("Simulando trabalho pesado na thread principal...")
    block = 0
    while True:
        block += 1
        started = time.monotonic()
        total = 0
        while time.monotonic() - started < 4:  # ~4 s de CPU ocupada
            total += sum(i * i for i in range(10_000))
        logger.info("bloco de trabalho %d concluído (resultado parcial %d)", block, total % 1_000_000)


if __name__ == "__main__":
    sys.exit(main())
