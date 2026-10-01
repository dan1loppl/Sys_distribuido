"""Testes do registro de workers (identidade/reconexão) e da configuração persistente."""

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from p2p.config import WORKER_DEFAULTS, ConfigError, load_config
from p2p.registry import (ALREADY_REGISTERED, OFFLINE, ONLINE, RECONNECTED, REGISTERED, REJECTED,
                          WorkerRegistry)


class FakeClock:
    """Relógio controlado pelo teste (evita sleep)."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.registry = WorkerRegistry(clock=self.clock)
        self.uuid_a = str(uuid.uuid4())
        self.conn1, self.conn2 = object(), object()  # qualquer objeto serve como "conexão"

    def register(self, conn, worker_id=None, label="worker-01"):
        return self.registry.register(worker_id or self.uuid_a, label, conn, "127.0.0.1:1")

    def test_primeiro_registro(self):
        status, record = self.register(self.conn1)
        self.assertEqual(status, REGISTERED)
        self.assertEqual(record.status, ONLINE)
        self.assertEqual(len(self.registry), 1)

    def test_identidade_duplicada_e_recusada(self):
        self.register(self.conn1)
        status, record = self.register(self.conn2)
        self.assertEqual(status, REJECTED)
        self.assertIs(self.registry.get(self.uuid_a).connection, self.conn1, "o dono original continua")
        self.assertEqual(len(self.registry), 1)

    def test_registro_repetido_na_mesma_conexao(self):
        self.register(self.conn1)
        self.assertEqual(self.register(self.conn1)[0], ALREADY_REGISTERED)

    def test_reconexao_nao_duplica(self):
        self.register(self.conn1)
        self.registry.mark_offline(self.uuid_a, self.conn1, "caiu")
        self.assertEqual(self.registry.get(self.uuid_a).status, OFFLINE)

        status, record = self.register(self.conn2)
        self.assertEqual(status, RECONNECTED)
        self.assertEqual(record.reconnections, 1)
        self.assertEqual(len(self.registry), 1)

    def test_fechamento_atrasado_de_conexao_antiga_nao_derruba_a_nova(self):
        self.register(self.conn1)
        self.registry.mark_offline(self.uuid_a, self.conn1, "caiu")
        self.register(self.conn2)
        self.assertIsNone(self.registry.mark_offline(self.uuid_a, self.conn1, "atrasado"))
        self.assertEqual(self.registry.get(self.uuid_a).status, ONLINE)

    def test_heartbeat_atualiza_ultimo_sinal(self):
        self.register(self.conn1)
        self.clock.now += 4
        self.assertTrue(self.registry.touch(self.uuid_a, self.conn1))
        self.assertEqual(self.registry.get(self.uuid_a).heartbeats, 1)
        self.assertFalse(self.registry.touch(self.uuid_a, self.conn2), "conexão errada")

    def test_timeout_de_heartbeat(self):
        self.register(self.conn1)
        uuid_b = str(uuid.uuid4())
        self.register(self.conn2, uuid_b, "worker-02")

        self.clock.now += 8
        self.registry.touch(uuid_b, self.conn2)  # só o B dá sinal de vida
        self.clock.now += 8                      # A está há 16 s calado, B há 8 s

        expired = self.registry.expire(timeout=15)
        self.assertEqual([r.label for r in expired], ["worker-01"])
        self.assertIs(expired[0].connection, self.conn1, "devolve a conexão para o master fechar")
        self.assertEqual(self.registry.get(self.uuid_a).status, OFFLINE)
        self.assertEqual(self.registry.get(uuid_b).status, ONLINE)
        self.assertEqual(self.registry.expire(timeout=15), [], "não expira duas vezes")


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "worker.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_uuid_e_gerado_e_persistido(self):
        first = load_config(self.path, WORKER_DEFAULTS)
        uuid.UUID(first["uuid"])  # é um UUID válido
        second = load_config(self.path, WORKER_DEFAULTS)
        self.assertEqual(first["uuid"], second["uuid"], "reiniciar não pode trocar a identidade")

    def test_sobrescritas_da_linha_de_comando_sao_salvas(self):
        load_config(self.path, WORKER_DEFAULTS, {"label": "worker-09", "master_host": "192.168.0.10"})
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((stored["label"], stored["master_host"]), ("worker-09", "192.168.0.10"))

    def test_none_nao_sobrescreve(self):
        load_config(self.path, WORKER_DEFAULTS, {"label": "worker-09"})
        self.assertEqual(load_config(self.path, WORKER_DEFAULTS, {"label": None})["label"], "worker-09")

    def test_porta_invalida(self):
        with self.assertRaises(ConfigError):
            load_config(self.path, WORKER_DEFAULTS, {"master_port": 70000})

    def test_uuid_invalido(self):
        self.path.write_text('{"uuid": "abc"}', encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(self.path, WORKER_DEFAULTS)


if __name__ == "__main__":
    unittest.main()
