"""Testes do enquadramento NDJSON (sem rede): fragmentação e múltiplas mensagens."""

import json
import unittest

from p2p.ndjson import NdjsonBuffer, encode


class EncodeTest(unittest.TestCase):
    def test_uma_mensagem_vira_uma_linha_terminada_em_newline(self):
        data = encode({"type": "heartbeat", "texto": "linha1\nlinha2"})
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(data.count(b"\n"), 1, "o \\n dentro do texto deve ser escapado")
        self.assertEqual(json.loads(data)["texto"], "linha1\nlinha2")


class BufferTest(unittest.TestCase):
    def setUp(self):
        self.buffer = NdjsonBuffer()

    def test_mensagem_fragmentada_byte_a_byte(self):
        data = encode({"type": "heartbeat", "seq": 1})
        for byte in data[:-1]:
            self.assertEqual(self.buffer.feed(bytes([byte])), [], "nada sai antes do \\n")
        self.assertEqual(self.buffer.feed(data[-1:]), [data[:-1]])
        self.assertEqual(self.buffer.pending_bytes, 0)

    def test_varias_mensagens_em_um_recv(self):
        data = encode({"n": 1}) + encode({"n": 2}) + encode({"n": 3})
        lines = self.buffer.feed(data)
        self.assertEqual([json.loads(line)["n"] for line in lines], [1, 2, 3])

    def test_mensagem_completa_mais_inicio_da_proxima(self):
        first, second = encode({"n": 1}), encode({"n": 2})
        lines = self.buffer.feed(first + second[:5])
        self.assertEqual(len(lines), 1)
        self.assertEqual(self.buffer.pending_bytes, 5)
        self.assertEqual(self.buffer.feed(second[5:]), [second[:-1]])

    def test_caractere_utf8_cortado_ao_meio(self):
        data = encode({"label": "worker-ação"})
        cut = data.index("ç".encode()) + 1  # corta no meio dos 2 bytes do "ç"
        self.assertEqual(self.buffer.feed(data[:cut]), [])
        line = self.buffer.feed(data[cut:])[0]
        self.assertEqual(json.loads(line)["label"], "worker-ação")

    def test_linhas_vazias_e_crlf(self):
        lines = self.buffer.feed(b'\n\r\n{"a":1}\r\n  \n')
        self.assertEqual(lines, [b'{"a":1}'])

    def test_linha_gigante_e_descartada_e_o_fluxo_se_recupera(self):
        buffer = NdjsonBuffer(max_line_bytes=100)
        self.assertEqual(buffer.feed(b"x" * 150), [])
        self.assertEqual(buffer.dropped_frames, 1)
        self.assertEqual(buffer.feed(b"y" * 150), [], "continua descartando até o \\n")
        self.assertEqual(buffer.dropped_frames, 1, "conta uma vez por linha gigante")
        self.assertEqual(buffer.feed(b'zzz\n{"ok":true}\n'), [b'{"ok":true}'])


if __name__ == "__main__":
    unittest.main()
