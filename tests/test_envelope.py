"""Testes do envelope: montagem, validação e JSON inválido."""

import unittest

from p2p import envelope as env
from p2p.envelope import ProtocolError, parse_line
from p2p.ndjson import encode


def valid_message(**changes):
    message = env.make_message(env.HEARTBEAT, group="grupo-01", source="worker-01",
                               destination="master-01", payload={"seq": 1})
    message.update(changes)
    return message


class EnvelopeTest(unittest.TestCase):
    def test_make_message_tem_todos_os_campos_obrigatorios(self):
        message = valid_message()
        self.assertEqual(set(env.REQUIRED_FIELDS) - set(message), set())
        self.assertEqual(env.validate(message), message)

    def test_make_reply_mantem_request_id_e_inverte_destino(self):
        request = valid_message()
        reply = env.make_reply(request, env.HEARTBEAT_ACK, group="grupo-01", source="master-01", payload={})
        self.assertEqual(reply["request_id"], request["request_id"])
        self.assertEqual(reply["destination"], "worker-01")
        self.assertEqual(reply["source"], "master-01")

    def test_request_ids_sao_unicos(self):
        self.assertNotEqual(valid_message()["request_id"], valid_message()["request_id"])

    def test_parse_line_ida_e_volta(self):
        message = valid_message()
        self.assertEqual(parse_line(encode(message)[:-1]), message)

    def assertProtocolError(self, line, code):
        with self.assertRaises(ProtocolError) as ctx:
            parse_line(line)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_json_invalido(self):
        self.assertProtocolError(b"{ isso nao eh json", env.INVALID_JSON)

    def test_bytes_que_nao_sao_utf8(self):
        self.assertProtocolError(b"\xff\xfe\xfa", env.INVALID_JSON)

    def test_json_que_nao_e_objeto(self):
        self.assertProtocolError(b"[1, 2, 3]", env.INVALID_ENVELOPE)

    def test_campo_obrigatorio_ausente_preserva_request_id(self):
        message = valid_message()
        del message["source"]
        error = self.assertProtocolError(encode(message)[:-1], env.INVALID_ENVELOPE)
        self.assertEqual(error.request_id, message["request_id"])
        self.assertIn("source", error.detail)

    def test_tipo_errado_de_campo(self):
        self.assertProtocolError(encode(valid_message(payload="texto"))[:-1], env.INVALID_ENVELOPE)

    def test_campo_vazio(self):
        self.assertProtocolError(encode(valid_message(type="  "))[:-1], env.INVALID_ENVELOPE)

    def test_versao_incompativel(self):
        self.assertProtocolError(encode(valid_message(version="2.0"))[:-1], env.UNSUPPORTED_VERSION)

    def test_versao_menor_compativel(self):
        self.assertEqual(parse_line(encode(valid_message(version="1.3"))[:-1])["version"], "1.3")


if __name__ == "__main__":
    unittest.main()
