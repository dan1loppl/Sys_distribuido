"""Teste de ponta a ponta com PROCESSOS SEPARADOS (como na apresentação).

Sobe `python master.py` e dois `python worker.py` como processos distintos,
derruba um worker, religa e confere pelos arquivos de log.
"""

import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .helpers import wait_until

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ProcessosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.logs = self.dir / "logs"
        self.port = free_port()
        self.processes: list[subprocess.Popen] = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        self.tmp.cleanup()

    def write_config(self, name: str, data: dict) -> Path:
        path = self.dir / name
        path.write_text(json.dumps({**data, "group": "grupo-teste", "log_dir": str(self.logs)}), encoding="utf-8")
        return path

    def spawn(self, script: str, config: Path) -> subprocess.Popen:
        process = subprocess.Popen([sys.executable, script, "--config", str(config)], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        return process

    def master_log(self) -> str:
        path = self.logs / "master-teste.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_master_e_dois_workers_em_processos_distintos(self):
        master_cfg = self.write_config("master.json", {
            "label": "master-teste", "host": "127.0.0.1", "port": self.port,
            "heartbeat_timeout": 2, "status_interval": 0})
        w1_cfg = self.write_config("w1.json", {"label": "proc-worker-01", "master_port": self.port,
                                               "heartbeat_interval": 0.5, "reconnect_initial_delay": 0.2})
        w2_cfg = self.write_config("w2.json", {"label": "proc-worker-02", "master_port": self.port,
                                               "heartbeat_interval": 0.5, "reconnect_initial_delay": 0.2})

        self.spawn("master.py", master_cfg)
        self.assertTrue(wait_until(lambda: "iniciado" in self.master_log(), timeout=10))
        self.spawn("worker.py", w1_cfg)
        w2 = self.spawn("worker.py", w2_cfg)

        self.assertTrue(wait_until(lambda: self.master_log().count("NOVO WORKER REGISTRADO") == 2, timeout=10))
        uuid_w2 = json.loads(w2_cfg.read_text(encoding="utf-8"))["uuid"]  # gerado e salvo pelo worker
        self.assertTrue(wait_until(lambda: "RECV heartbeat grupo=grupo-teste origem=proc-worker-02" in self.master_log()))

        w2.kill()  # queda abrupta do processo
        self.assertTrue(wait_until(lambda: "WORKER OFFLINE: proc-worker-02" in self.master_log(), timeout=10))

        self.spawn("worker.py", w2_cfg)  # mesmo arquivo => mesmo UUID
        self.assertTrue(wait_until(lambda: "WORKER RECONECTADO: proc-worker-02" in self.master_log(), timeout=10))
        self.assertEqual(json.loads(w2_cfg.read_text(encoding="utf-8"))["uuid"], uuid_w2, "UUID persistente")
        self.assertEqual(self.master_log().count("NOVO WORKER REGISTRADO"), 2, "sem cadastro duplicado")


if __name__ == "__main__":
    unittest.main()
