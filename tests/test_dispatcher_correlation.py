"""Testes do dispatcher (roteamento por type) e da correlação de respostas."""

import threading
import time
import unittest

from p2p import envelope as env
from p2p.correlation import PendingRequests
from p2p.dispatcher import Dispatcher
from p2p.envelope import ProtocolError


class DispatcherTest(unittest.TestCase):
    def test_chama_o_handler_do_type(self):
        calls = []
        dispatcher = Dispatcher()
        dispatcher.register("heartbeat", lambda conn, msg: calls.append(("hb", conn)))
        dispatcher.register("register_worker", lambda conn, msg: calls.append(("reg", conn)))
        dispatcher.dispatch("conexao-A", {"type": "heartbeat", "request_id": "1"})
        self.assertEqual(calls, [("hb", "conexao-A")])

    def test_type_desconhecido_gera_erro_com_request_id(self):
        with self.assertRaises(ProtocolError) as ctx:
            Dispatcher().dispatch(None, {"type": "xyz", "request_id": "abc"})
        self.assertEqual(ctx.exception.code, env.UNKNOWN_TYPE)
        self.assertEqual(ctx.exception.request_id, "abc")


class CorrelationTest(unittest.TestCase):
    def setUp(self):
        self.pending = PendingRequests()

    def test_resposta_acorda_quem_espera_em_outra_thread(self):
        waiter = self.pending.expect("ABC")
        threading.Timer(0.05, self.pending.resolve, args=({"request_id": "ABC", "type": "ok"},)).start()
        self.assertEqual(waiter.wait(2)["type"], "ok")
        self.assertEqual(len(self.pending), 0)

    def test_respostas_fora_de_ordem_vao_para_o_dono_certo(self):
        first, second = self.pending.expect("1"), self.pending.expect("2")
        self.pending.resolve({"request_id": "2", "n": 2})
        self.pending.resolve({"request_id": "1", "n": 1})
        self.assertEqual(first.wait(1)["n"], 1)
        self.assertEqual(second.wait(1)["n"], 2)

    def test_timeout_devolve_none_e_libera_a_reserva(self):
        waiter = self.pending.expect("X")
        started = time.monotonic()
        self.assertIsNone(waiter.wait(0.1))
        self.assertGreaterEqual(time.monotonic() - started, 0.09)
        self.assertEqual(len(self.pending), 0)

    def test_resposta_atrasada_e_orfa(self):
        self.pending.expect("X").wait(0.01)
        self.assertFalse(self.pending.resolve({"request_id": "X"}))

    def test_resposta_desconhecida_e_orfa(self):
        self.assertFalse(self.pending.resolve({"request_id": "nunca-pedido"}))

    def test_cancel_all_acorda_todos_com_none(self):
        waiters = [self.pending.expect(str(i)) for i in range(3)]
        self.pending.cancel_all()
        self.assertEqual([w.wait(1) for w in waiters], [None, None, None])


if __name__ == "__main__":
    unittest.main()
