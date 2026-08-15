import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bootstrap"))

import mag_block_guard
from mag_block_guard import AccessStatus, check_tenant_access


class _BlockedHandler(BaseHTTPRequestHandler):
    payload = {"blocked": False, "reason": None}
    status = 200

    def do_GET(self):
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        return


class BlockGuardTests(unittest.TestCase):
    """
    O bloqueio é a única coisa da plataforma que decide se um cliente pagante
    consegue usar o produto. Estes testes existem porque a versão anterior tinha
    um fail-open escondido e ninguém percebeu por meses.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _BlockedHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_env = {
            "MAG_API_URL": f"http://127.0.0.1:{cls.server.server_port}",
            "MAG_INTERNAL_KEY": "test-key",
            "MAG_TENANT_SLUG": "tenant test",
        }

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        # Cada teste começa sem memória de disco, senão um teste contamina o outro
        # exatamente pelo mecanismo que estamos testando.
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, ".mag_block.json")
        self._cache_patch = patch.object(mag_block_guard, "CACHE_PATH", self.cache)
        self._cache_patch.start()
        _BlockedHandler.payload = {"blocked": False, "reason": None}
        _BlockedHandler.status = 200

    def tearDown(self):
        self._cache_patch.stop()

    # ── o caminho normal ────────────────────────────────────────────────────

    def test_api_diz_liberado(self):
        with patch.dict(os.environ, self.base_env, clear=True):
            self.assertIs(check_tenant_access().status, AccessStatus.ALLOWED)

    def test_api_diz_bloqueado(self):
        _BlockedHandler.payload = {"blocked": True, "reason": "inadimplência"}
        with patch.dict(os.environ, self.base_env, clear=True):
            self.assertIs(check_tenant_access().status, AccessStatus.BLOCKED)

    def test_o_motivo_nunca_sai_do_guard(self):
        # O `reason` é anotação interna da equipe. O tipo de retorno não tem onde
        # colocá-lo, e é assim de propósito: nada downstream pode vazar o que
        # nunca recebe.
        _BlockedHandler.payload = {"blocked": True, "reason": "não pagou desde março"}
        with patch.dict(os.environ, self.base_env, clear=True):
            check = check_tenant_access()
        self.assertNotIn("não pagou", repr(check))
        self.assertFalse(hasattr(check, "reason"))

    # ── configuração ausente ────────────────────────────────────────────────

    def test_sem_configuracao_bloqueia(self):
        # Não é uma queda: é um container que não sabe quem é. Ele não tem como
        # provar que pode servir.
        for faltando in ("MAG_API_URL", "MAG_INTERNAL_KEY", "MAG_TENANT_SLUG"):
            env = dict(self.base_env)
            env.pop(faltando)
            with self.subTest(faltando=faltando), patch.dict(os.environ, env, clear=True):
                self.assertIs(check_tenant_access().status, AccessStatus.BLOCKED)

    # ── a queda do control plane, que é o ponto todo ────────────────────────

    def test_api_fora_do_ar_sem_memoria_bloqueia(self):
        # Nunca conseguimos verificar este tenant. Servir sem nunca ter podido
        # confirmar é o único erro cujo tamanho não tem teto.
        env = dict(self.base_env, MAG_API_URL="http://127.0.0.1:9")
        with patch.dict(os.environ, env, clear=True):
            self.assertIs(check_tenant_access().status, AccessStatus.BLOCKED)

    def test_api_fora_do_ar_com_memoria_de_liberado_libera(self):
        """Uma queda do control plane NÃO pode derrubar a frota inteira."""
        with patch.dict(os.environ, self.base_env, clear=True):
            check_tenant_access()  # aquece a memória com "liberado"
        env = dict(self.base_env, MAG_API_URL="http://127.0.0.1:9")
        with patch.dict(os.environ, env, clear=True):
            check = check_tenant_access()
        self.assertIs(check.status, AccessStatus.ALLOWED)
        self.assertTrue(check.from_cache)

    def test_api_fora_do_ar_com_memoria_de_bloqueado_continua_bloqueado(self):
        """E também NÃO pode desbloquear quem a equipe cortou."""
        _BlockedHandler.payload = {"blocked": True, "reason": None}
        with patch.dict(os.environ, self.base_env, clear=True):
            check_tenant_access()  # aquece a memória com "bloqueado"
        env = dict(self.base_env, MAG_API_URL="http://127.0.0.1:9")
        with patch.dict(os.environ, env, clear=True):
            check = check_tenant_access()
        self.assertIs(check.status, AccessStatus.BLOCKED)
        self.assertTrue(check.from_cache)

    def test_memoria_velha_demais_nao_vale(self):
        # Um container offline há dias não pode servir para sempre com base numa
        # resposta antiga. O limite é generoso, mas existe.
        with patch.dict(os.environ, self.base_env, clear=True):
            check_tenant_access()
        antigo = json.loads(Path(self.cache).read_text())
        antigo["at"] = time.time() - (mag_block_guard.CACHE_MAX_AGE_S + 60)
        Path(self.cache).write_text(json.dumps(antigo))

        env = dict(self.base_env, MAG_API_URL="http://127.0.0.1:9")
        with patch.dict(os.environ, env, clear=True):
            self.assertIs(check_tenant_access().status, AccessStatus.BLOCKED)

    def test_resposta_malformada_nao_e_lida_como_liberado(self):
        # `blocked` ausente virava `None`, que é falsy — e um payload quebrado
        # liberaria o turno sem ninguém notar.
        _BlockedHandler.payload = {"reason": "sem o campo blocked"}
        with patch.dict(os.environ, self.base_env, clear=True):
            self.assertIs(check_tenant_access().status, AccessStatus.BLOCKED)

    def test_resposta_malformada_nao_suja_a_memoria(self):
        _BlockedHandler.payload = {"blocked": True}
        with patch.dict(os.environ, self.base_env, clear=True):
            check_tenant_access()
        _BlockedHandler.payload = {"lixo": True}
        with patch.dict(os.environ, self.base_env, clear=True):
            check_tenant_access()
        # A memória continua com a última resposta VÁLIDA, não com o lixo.
        self.assertTrue(json.loads(Path(self.cache).read_text())["blocked"])


if __name__ == "__main__":
    unittest.main()
