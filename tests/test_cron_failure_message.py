import importlib.util
from pathlib import Path
import re
import unittest

PATCH = Path(__file__).resolve().parent.parent / "bootstrap" / "patch_sanitize_cron_errors.py"


def _load_injected_helpers():
    """Executa exatamente o código que o patch injeta dentro do Hermes.

    Testar o `SANITIZE_HELPER` em si, e não uma cópia, é o que garante que o teste
    continua valendo quando alguém mexer no patch.
    """
    spec = importlib.util.spec_from_file_location("patch", PATCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ns = {"re": re}
    exec(module.SANITIZE_HELPER, ns)
    return ns


class CronFailureMessageTests(unittest.TestCase):
    """A frase que o cliente recebe quando uma rotina falha.

    Isto tem teste porque já falhou em produção, e do pior jeito: um cliente recebeu no
    Telegram, às 7h da manhã, num produto pt-BR:

        ⚠️ Cron job 'Verificar tarefas atrasadas' failed:
        Nao consegui processar essa tarefa agora.

    Jargão de engenharia num canal de cliente, em inglês, e "Nao" sem til. O patch
    higienizava o ERRO mas deixava passar o invólucro que o Hermes monta em volta.
    """

    def setUp(self):
        ns = _load_injected_helpers()
        self.mensagem = ns["_mag_cron_failure_message"]
        self.generico = ns["_MAG_GENERIC_CRON_ERROR"]

    # O erro real que derrubou a rotina do cliente.
    ERRO_TECNICO = (
        "RuntimeError: Error code: 401 - {'error': {'message': 'Provided authentication "
        "token is expired. Please try signing in again.', 'code': 'token_expired'}}"
    )

    def test_nao_vaza_jargao_de_engenharia(self):
        texto = self.mensagem({"name": "Verificar tarefas atrasadas"}, self.ERRO_TECNICO)
        for proibido in ("cron", "job", "failed", "token", "401", "runtime", "hermes", "codex", "traceback"):
            self.assertNotIn(proibido, texto.lower(), f"vazou {proibido!r} pro cliente: {texto}")

    def test_e_portugues_de_verdade(self):
        texto = self.mensagem({"name": "Briefing"}, self.ERRO_TECNICO)
        self.assertIn("Não consegui", texto)
        self.assertNotIn("Nao ", texto)
        # Nenhuma palavra do invólucro original do Hermes.
        self.assertNotIn("⚠️", texto)

    def test_diz_qual_rotina_falhou(self):
        texto = self.mensagem({"name": "Verificar tarefas atrasadas"}, self.ERRO_TECNICO)
        self.assertIn('"Verificar tarefas atrasadas"', texto)

    def test_sem_nome_nao_deixa_buraco(self):
        for job in ({}, {"name": ""}, {"name": "   "}, None):
            texto = self.mensagem(job, self.ERRO_TECNICO)
            self.assertIn("uma rotina que você agendou", texto)
            self.assertNotIn('""', texto)

    def test_erro_ja_legivel_vira_pista(self):
        # Erro que não bate em nenhum padrão técnico já é apresentável: some seria pior,
        # porque a pessoa ficaria sem nenhuma ideia do que houve.
        texto = self.mensagem({"name": "Briefing"}, "A planilha de origem estava vazia.")
        self.assertIn("A planilha de origem estava vazia.", texto)

    def test_nao_repete_a_frase_generica(self):
        # O genérico já diz "não consegui"; repetir dentro da moldura sairia como
        # "Não consegui terminar X agora. Não consegui processar essa tarefa agora."
        texto = self.mensagem({"name": "Briefing"}, self.ERRO_TECNICO)
        self.assertEqual(texto.lower().count("não consegui"), 1, texto)

    def test_generico_tambem_tem_acento(self):
        # Esta constante vai pro histórico de execuções que o cliente vê no painel — o
        # "Nao" sem til aparecia lá também, não só no Telegram.
        self.assertIn("Não consegui", self.generico)

    def test_diz_o_que_acontece_depois(self):
        texto = self.mensagem({"name": "Briefing"}, self.ERRO_TECNICO)
        self.assertIn("próximo horário", texto)
        self.assertIn("suporte", texto)


if __name__ == "__main__":
    unittest.main()
