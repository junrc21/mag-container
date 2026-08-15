"""A recusa por crédito, exercitada contra o `registry.py` REAL do Hermes.

Este teste não olha o texto do patch: ele aplica o patch a uma cópia do
`tools/registry.py` que veio do container, importa o resultado, e chama
`dispatch()` de verdade. É a diferença entre "o patch produz o texto esperado" e
"a ferramenta não roda quando o saldo não cobre" — e só a segunda é a promessa.

Precisa de uma cópia do registry em `tests/fixtures/registry.py`. Ela é gerada
por `make fixtures` (ou `docker cp <container>:/opt/hermes/tools/registry.py`);
sem ela os testes são pulados, para o CI de quem não tem container não quebrar.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[1]
FIXTURE = RAIZ / "tests" / "fixtures" / "registry.py"

sys.path.insert(0, str(RAIZ / "bootstrap"))


def _carregar_registry_patcheado(tmp: Path):
    """Aplica o patch numa cópia do registry real e importa o módulo resultante."""
    alvo = tmp / "registry.py"
    shutil.copy(FIXTURE, alvo)

    spec = importlib.util.spec_from_file_location("patch_tcg", RAIZ / "bootstrap" / "patch_tool_credit_gate.py")
    patch_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch_mod)
    with patch.dict(os.environ, {"TOOLS_REGISTRY_PY": str(alvo)}):
        patch_mod.REGISTRY_PY = alvo
        patch_mod.main()

    spec2 = importlib.util.spec_from_file_location("registry_patcheado", alvo)
    mod = importlib.util.module_from_spec(spec2)
    # `registry.py` importa `tools.registry`? Não — ele é a raiz da cadeia e não
    # importa model_tools nem tool files (ver o docstring dele). Importa limpo.
    spec2.loader.exec_module(mod)
    return mod


class _Check:
    """O que `check_authoritative_credits()` devolveria."""

    def __init__(self, remaining, toolset_costs):
        self.remaining = remaining
        self.toolset_costs = toolset_costs


@unittest.skipUnless(FIXTURE.exists(), f"fixture ausente: {FIXTURE}")
class ToolCreditGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.mod = _carregar_registry_patcheado(Path(cls.tmpdir))

    def setUp(self):
        # O memo de saldo é global no módulo; zerar entre testes evita que um
        # contamine o outro pelo mesmo mecanismo que estamos testando.
        self.mod._MAG_SALDO_CACHE["check"] = None
        self.mod._MAG_SALDO_CACHE["ate"] = 0.0

        self.reg = self.mod.ToolRegistry()
        self.rodou = []

        def caro(args, **kwargs):
            self.rodou.append("image_gen")
            return "imagem gerada"

        def barato(args, **kwargs):
            self.rodou.append("web")
            return "resultado da busca"

        self.reg.register(
            name="image_gen", toolset="image_gen", schema={"name": "image_gen"},
            handler=caro, check_fn=lambda: True,
        )
        self.reg.register(
            name="web_search", toolset="web", schema={"name": "web_search"},
            handler=barato, check_fn=lambda: True,
        )

    def _com_saldo(self, restante, precos=None):
        precos = precos if precos is not None else {"image_gen": 10, "web": 1}
        return patch.object(
            self.mod, "_mag_saldo_e_precos", lambda: (restante, precos)
        )

    # ── a regra que o dono pediu, literal ───────────────────────────────────

    def test_saldo_5_nao_paga_ferramenta_de_10(self):
        with self._com_saldo(5):
            out = self.reg.dispatch("image_gen", {})
        self.assertNotIn("image_gen", self.rodou, "a ferramenta NAO podia ter rodado")
        corpo = json.loads(out)
        self.assertIn("Creditos insuficientes", corpo["error"])
        self.assertIn("custa 10", corpo["error"])
        self.assertIn("restam 5", corpo["error"])

    def test_saldo_10_paga_ferramenta_de_10(self):
        # Exatamente o preço passa: é ">=", não ">".
        with self._com_saldo(10):
            out = self.reg.dispatch("image_gen", {})
        self.assertIn("image_gen", self.rodou)
        self.assertEqual(out, "imagem gerada")

    def test_saldo_5_paga_ferramenta_de_1(self):
        with self._com_saldo(5):
            out = self.reg.dispatch("web_search", {})
        self.assertIn("web", self.rodou)
        self.assertEqual(out, "resultado da busca")

    # ── o laço de retry, que já custou 60s de resposta neste produto ────────

    def test_a_recusa_manda_o_modelo_nao_tentar_de_novo(self):
        with self._com_saldo(5):
            corpo = json.loads(self.reg.dispatch("image_gen", {}))
        self.assertIs(corpo["retry"], False)
        self.assertIn("Nao tente esta ferramenta de novo", corpo["instruction"])

    # ── quando não dá para saber o saldo ────────────────────────────────────

    def test_sem_saldo_conhecido_a_cara_e_recusada(self):
        """
        O preço vem da memória de disco; o saldo, não.

        A primeira versão devolvia `(None, None)` quando a API caía — e sem preço
        o gate não conseguia distinguir uma imagem de 10 de uma busca de 1, então
        deixava as duas passarem. Este teste pegou isso. Agora o preço sobrevive à
        queda e a ferramenta cara é recusada.
        """
        with patch.object(self.mod, "_mag_saldo_e_precos", lambda: (None, {"image_gen": 10})):
            out = self.reg.dispatch("image_gen", {})
        self.assertNotIn("image_gen", self.rodou)
        self.assertIn("Nao consegui confirmar o saldo", json.loads(out)["error"])

    def test_a_tabela_de_precos_sobrevive_a_queda_da_api(self):
        """Prova o mecanismo, e não o mock: grava, derruba, e o preço continua lá."""
        precos = {"image_gen": 10, "web": 1}
        with patch.object(self.mod, "_MAG_PRECOS_CACHE", str(Path(self.tmpdir) / "precos.json")):
            self.mod._mag_guardar_precos(precos)

            # A API cai: `check_authoritative_credits` levanta.
            def cai():
                raise OSError("control plane fora do ar")

            with patch.dict(sys.modules, {"mag_credit_guard": types.SimpleNamespace(check_authoritative_credits=cai)}):
                restante, lidos = self.mod._mag_saldo_e_precos()

        self.assertIsNone(restante, "o saldo NAO pode vir de memoria")
        self.assertEqual(lidos, precos, "o preco TEM que vir de memoria")

    def test_sem_saldo_conhecido_a_barata_passa(self):
        """Uma queda do control plane não pode matar a conversa básica."""
        with patch.object(self.mod, "_mag_saldo_e_precos", lambda: (None, None)):
            out = self.reg.dispatch("web_search", {})
        self.assertIn("web", self.rodou)
        self.assertEqual(out, "resultado da busca")

    def test_toolset_sem_preco_conhecido_vale_1_e_passa(self):
        # Ausente = 1, nunca 0. Um toolset lido como gratuito seria cobrado
        # depois e recusado nunca.
        with self._com_saldo(1, precos={}):
            out = self.reg.dispatch("web_search", {})
        self.assertIn("web", self.rodou)
        self.assertEqual(out, "resultado da busca")

    # ── o gate não pode ser o que quebra o turno ────────────────────────────

    def test_erro_dentro_do_gate_deixa_a_ferramenta_rodar(self):
        """
        Fail-open SÓ para bug nosso.

        Um defeito no gate não pode transformar toda ferramenta em erro — isso
        derrubaria o produto inteiro para todos os clientes. A recusa por saldo
        continua fail-closed; o que falha aberto é a exceção inesperada.
        """
        def explode():
            raise RuntimeError("bug no gate")

        with patch.object(self.mod, "_mag_saldo_e_precos", explode):
            out = self.reg.dispatch("image_gen", {})
        self.assertIn("image_gen", self.rodou)
        self.assertEqual(out, "imagem gerada")

    def test_ferramenta_desconhecida_continua_respondendo_como_antes(self):
        out = self.reg.dispatch("nao_existe", {})
        self.assertIn("Unknown tool", json.loads(out)["error"])


if __name__ == "__main__":
    unittest.main()
