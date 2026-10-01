"""Testes de integração: master e workers reais conversando por TCP (localhost).

Cada teste sobe um master em uma porta livre e usa workers de verdade
(classe Worker) ou um cliente "cru" que envia bytes exatos.
"""

import time
import unittest

from p2p import envelope as env
from p2p.master import Master
from p2p.ndjson import encode
from p2p.registry import OFFLINE, ONLINE
from p2p.worker import Worker

from .helpers import RawClient, master_config, quiet_logger, wait_until, worker_config


class IntegrationBase(unittest.TestCase):
    def setUp(self):
        self.master = Master(master_config(), quiet_logger("master"))
        self.master.start()
        self.port = self.master.address[1]
        self.workers: list[Worker] = []
        self.clients: list[RawClient] = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        for worker in self.workers:
            worker.stop()
        self.master.stop()

    def start_worker(self, label: str, worker_id: str | None = None, **changes) -> Worker:
        worker = Worker(worker_config(self.port, label, worker_id, **changes), quiet_logger(label))
        worker.start()
        self.workers.append(worker)
        return worker

    def client(self, **kwargs) -> RawClient:
        client = RawClient(self.port, **kwargs)
        self.clients.append(client)
        return client

    def status_of(self, worker_id: str) -> str | None:
        record = self.master.registry.get(worker_id)
        return record.status if record else None


class RegistroEHeartbeatTest(IntegrationBase):
    def test_dois_workers_registram_e_mandam_heartbeats_ao_mesmo_tempo(self):
        w1 = self.start_worker("worker-01")
        w2 = self.start_worker("worker-02")
        self.assertTrue(w1.registered_event.wait(3) and w2.registered_event.wait(3))
        self.assertEqual(w1.registration_history, ["registered"])

        # Ambos acumulam heartbeats confirmados em paralelo.
        self.assertTrue(wait_until(lambda: w1.heartbeats_acked >= 3 and w2.heartbeats_acked >= 3))
        labels = {r.label: r.status for r in self.master.registry.snapshot()}
        self.assertEqual(labels, {"worker-01": ONLINE, "worker-02": ONLINE})
        self.assertGreaterEqual(self.master.registry.get(w1.id).heartbeats, 3)

    def test_heartbeat_continua_com_outro_worker_lento(self):
        # Um cliente que se registra e fica mandando lixo não atrapalha o worker normal.
        noisy = self.client(label="barulhento")
        noisy.send(noisy.register_message())
        noisy.read(1)
        worker = self.start_worker("worker-01")
        self.assertTrue(worker.registered_event.wait(3))
        before = worker.heartbeats_acked
        for _ in range(20):
            noisy.send(b"lixo\n")
        self.assertTrue(wait_until(lambda: worker.heartbeats_acked >= before + 3))


class QuedaEReconexaoTest(IntegrationBase):
    def test_queda_detectada_e_reconexao_sem_duplicar(self):
        w1 = self.start_worker("worker-01")
        w2 = self.start_worker("worker-02")
        self.assertTrue(w1.registered_event.wait(3) and w2.registered_event.wait(3))
        identity = w2.id

        w2.stop()  # "Ctrl+C" no worker-02
        self.assertTrue(wait_until(lambda: self.status_of(identity) == OFFLINE), "queda não detectada")
        self.assertEqual(self.status_of(w1.id), ONLINE)

        # Mesmo arquivo de config => mesmo UUID
        w2b = self.start_worker("worker-02", worker_id=identity)
        self.assertTrue(w2b.registered_event.wait(3))
        self.assertEqual(w2b.registration_history, ["reconnected"])
        self.assertEqual(len(self.master.registry), 2, "reconexão não pode duplicar o cadastro")
        self.assertEqual(self.master.registry.get(identity).reconnections, 1)

    def test_worker_travado_detectado_por_timeout_de_heartbeat(self):
        client = self.client(label="travado")
        client.send(client.register_message())
        self.assertEqual(client.read(1)[0]["payload"]["status"], "registered")
        # Não manda mais nada: conexão aberta, mas sem sinal de vida.
        self.assertTrue(wait_until(lambda: self.status_of(client.worker_id) == OFFLINE, timeout=3))
        self.assertIn("sem heartbeat", self.master.registry.get(client.worker_id).offline_reason)
        self.assertTrue(client.is_closed_by_peer(), "o master deve fechar a conexão do worker travado")

    def test_worker_que_para_heartbeat_volta_sozinho(self):
        worker = self.start_worker("worker-01")
        worker.stop_heartbeat_after = 0.3
        self.assertTrue(wait_until(lambda: worker.registration_history == ["registered", "reconnected"],
                                   timeout=5))
        self.assertEqual(len(self.master.registry), 1)

    def test_worker_reconecta_quando_o_master_reinicia(self):
        worker = self.start_worker("worker-01")
        self.assertTrue(worker.registered_event.wait(3))
        self.master.stop()
        self.assertTrue(wait_until(lambda: not worker.registered_event.is_set()))

        self.master = Master(master_config(port=self.port), quiet_logger("master2"))
        self.master.start()
        self.assertTrue(worker.registered_event.wait(5), "worker deveria reconectar sozinho")
        self.assertEqual(self.status_of(worker.id), ONLINE)

    def test_identidade_duplicada_recusada(self):
        original = self.start_worker("worker-01")
        self.assertTrue(original.registered_event.wait(3))

        impostor = self.client(label="impostor", worker_id=original.id)
        impostor.send(impostor.register_message())
        reply = impostor.read(1)[0]
        self.assertEqual(reply["payload"]["status"], "rejected")
        self.assertEqual(reply["payload"]["reason"], "duplicate_identity")
        self.assertEqual(len(self.master.registry), 1)
        self.assertEqual(self.master.registry.get(original.id).label, "worker-01")

        # O original segue saudável.
        before = original.heartbeats_acked
        self.assertTrue(wait_until(lambda: original.heartbeats_acked > before))


class EnquadramentoTest(IntegrationBase):
    """Os testes de enquadramento não podem derrubar o master."""

    def registered_client(self) -> RawClient:
        client = self.client()
        client.send(client.register_message())
        self.assertEqual(client.read(1)[0]["type"], env.REGISTRATION_ACK)
        return client

    def assertMasterAlive(self):
        probe = self.client(label="prova-de-vida")
        probe.send(probe.register_message())
        self.assertEqual(probe.read(1)[0]["payload"]["status"], "registered")

    def test_mensagem_fragmentada_pela_rede(self):
        client = self.client()
        data = encode(client.register_message())
        for i in range(0, len(data), 7):  # pedaços de 7 bytes
            client.send(data[i:i + 7])
            time.sleep(0.01)
        reply = client.read(1)
        self.assertEqual(reply[0]["type"], env.REGISTRATION_ACK)

    def test_mensagem_maior_que_o_recv(self):
        # 10 000 bytes > RECV_SIZE (4096): o master precisa de vários recv() para montá-la.
        client = self.client()
        message = client.register_message()
        message["payload"]["hostname"] = "x" * 10_000
        client.send(message)
        reply = client.read(1)[0]
        self.assertEqual((reply["type"], reply["request_id"]), (env.REGISTRATION_ACK, message["request_id"]))

    def test_varias_mensagens_em_um_envio(self):
        client = self.registered_client()
        heartbeats = [client.heartbeat_message(seq) for seq in range(1, 6)]
        client.send(b"".join(encode(h) for h in heartbeats))
        replies = client.read(5)
        self.assertEqual([r["request_id"] for r in replies], [h["request_id"] for h in heartbeats])
        self.assertEqual([r["payload"]["seq"] for r in replies], [1, 2, 3, 4, 5])

    def test_json_invalido_nao_derruba_e_conexao_continua(self):
        client = self.registered_client()
        client.send(b"{ isso nao eh json\n")
        self.assertEqual(client.read(1)[0]["payload"]["code"], env.INVALID_JSON)
        heartbeat = client.heartbeat_message()
        client.send(heartbeat)
        reply = client.read(1)[0]
        self.assertEqual((reply["type"], reply["request_id"]), (env.HEARTBEAT_ACK, heartbeat["request_id"]))
        self.assertMasterAlive()

    def test_bytes_aleatorios_e_linha_gigante(self):
        client = self.registered_client()
        client.send(b"\x00\xff\xfe\n")
        self.assertEqual(client.read(1)[0]["payload"]["code"], env.INVALID_JSON)
        client.send(b"a" * (1024 * 1024 + 10))  # > 1 MiB sem "\n"
        self.assertEqual(client.read(1)[0]["payload"]["code"], env.FRAME_TOO_LARGE)
        client.send(b"\n")
        client.send(client.heartbeat_message())
        self.assertEqual(client.read(1)[0]["type"], env.HEARTBEAT_ACK)
        self.assertMasterAlive()

    def test_envelope_invalido_tipo_desconhecido_e_payload_invalido(self):
        client = self.client()
        client.send(b'{"type":"heartbeat"}\n')
        self.assertEqual(client.read(1)[0]["payload"]["code"], env.INVALID_ENVELOPE)
        client.send(client.message("dancar", {}))
        self.assertEqual(client.read(1)[0]["payload"]["code"], env.UNKNOWN_TYPE)
        client.send(client.message(env.REGISTER_WORKER, {"worker_id": "nao-e-uuid", "label": "x"}))
        self.assertEqual(client.read(1)[0]["payload"]["code"], env.INVALID_PAYLOAD)
        self.assertMasterAlive()

    def test_heartbeat_sem_registro_e_com_identidade_trocada(self):
        client = self.client()
        client.send(client.heartbeat_message())
        self.assertEqual(client.read(1)[0]["payload"]["code"], env.NOT_REGISTERED)

        client.send(client.register_message())
        client.read(1)
        client.send(client.message(env.HEARTBEAT, {"worker_id": "outro", "seq": 1}))
        self.assertEqual(client.read(1)[0]["payload"]["code"], env.IDENTITY_MISMATCH)

    def test_cliente_que_desconecta_no_meio_da_mensagem(self):
        client = self.client()
        client.send(encode(client.register_message())[:20])
        client.close()
        self.assertMasterAlive()


if __name__ == "__main__":
    unittest.main()
