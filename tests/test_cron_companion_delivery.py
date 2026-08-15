import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parent.parent / "bootstrap" / "patch_cron_companion_delivery.py"

# Recorte do `cron/scheduler.py` real (lido da imagem 1.0.5) com as duas âncoras que o
# patch usa e nada mais. Cópia fiel do indentamento — o patch casa string exata, então um
# espaço a menos aqui faria o teste passar contra algo que não existe.
FAKE_SCHEDULER = '''\
from typing import Optional


def _deliver_result(job: dict, content: str, adapters=None, loop=None) -> Optional[str]:
    delivery_errors = []
    for target in _resolve_delivery_targets(job):
        platform_name = target["platform"]
        chat_id = target["chat_id"]
        cleaned_delivery_content = content
        # Built-in names resolve to their enum member; plugin platform names
        # create dynamic members via Platform._missing_().
        try:
            platform = Platform(platform_name.lower())
        except (ValueError, KeyError):
            msg = f"unknown platform '{platform_name}'"
            logger.warning("Job '%s': %s", job["id"], msg)
            delivery_errors.append(msg)
            continue
    return "; ".join(delivery_errors) if delivery_errors else None
'''


class CronCompanionDeliveryPatchTests(unittest.TestCase):
    """O patch que faz uma rotina agendada entregar no MAG Companion.

    Este patch é a peça mais frágil da entrega proativa: ele reescreve um arquivo do
    Hermes que já quebrou uma vez num upgrade upstream. Os testes cobrem exatamente o que
    protege contra isso — aplicar certo, não aplicar duas vezes, e MORRER RUIDOSAMENTE se
    a âncora sumir (um patch que vira no-op em silêncio é o pior desfecho possível: o
    build passa e a rotina some).
    """

    def _apply(self, source: str):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scheduler.py"
            target.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(PATCH)],
                capture_output=True,
                text=True,
                # Só o PATH e a variável que o patch lê. Sem PATH, o subprocess não acha
                # o Python (mesmo cuidado dos outros testes deste diretório).
                env={"PATH": os.environ.get("PATH", ""), "CRON_SCHEDULER_PY": str(target)},
            )
            return completed, target.read_text(encoding="utf-8")

    def test_injects_helper_and_interception(self):
        completed, patched = self._apply(FAKE_SCHEDULER)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        # O helper entra ANTES de _deliver_result — e não antes de `def tick(`, que é onde
        # patch_cron_job_runs injeta o dele. Assim os dois nunca aninham.
        self.assertLess(
            patched.index("def _mag_deliver_companion"),
            patched.index("def _deliver_result"),
        )

        # A interceptação vem antes do Platform(...), que é onde "companion" falha hoje.
        self.assertLess(
            patched.index('if platform_name.lower() == "companion":'),
            patched.index("platform = Platform(platform_name.lower())"),
        )

        # E o caminho das plataformas de verdade continua intacto.
        self.assertIn('msg = f"unknown platform \'{platform_name}\'"', patched)

        compile(patched, "scheduler.py", "exec")

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scheduler.py"
            target.write_text(FAKE_SCHEDULER, encoding="utf-8")
            env = {"PATH": os.environ.get("PATH", ""), "CRON_SCHEDULER_PY": str(target)}

            first = subprocess.run([sys.executable, str(PATCH)], capture_output=True, text=True, env=env)
            once = target.read_text(encoding="utf-8")
            second = subprocess.run([sys.executable, str(PATCH)], capture_output=True, text=True, env=env)
            twice = target.read_text(encoding="utf-8")

        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertIn("already patched", second.stdout)
        self.assertEqual(once, twice)

    def test_fails_loud_when_platform_anchor_moves(self):
        broken = FAKE_SCHEDULER.replace(
            "platform = Platform(platform_name.lower())",
            "platform = Platform(platform_name.casefold())",
        )
        completed, unchanged = self._apply(broken)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Hermes changed", completed.stderr)
        self.assertNotIn("_mag_deliver_companion", unchanged)

    def test_fails_loud_when_deliver_result_anchor_moves(self):
        broken = FAKE_SCHEDULER.replace(
            "def _deliver_result(job: dict, content: str, adapters=None, loop=None) -> Optional[str]:",
            "def _deliver_result(job: dict, content: str, *, adapters=None, loop=None) -> Optional[str]:",
        )
        completed, unchanged = self._apply(broken)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Hermes changed", completed.stderr)
        self.assertNotIn("_mag_deliver_companion", unchanged)

    def test_refuses_ambiguous_anchor(self):
        # Duas ocorrências = o upstream duplicou o trecho e não dá pra saber qual é a certa.
        # Aplicar na primeira seria um chute; recusar é o comportamento honesto.
        ambiguous = FAKE_SCHEDULER + FAKE_SCHEDULER.split("from typing import Optional\n")[1]
        completed, unchanged = self._apply(ambiguous)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("exactly one", completed.stderr)
        self.assertNotIn("_mag_deliver_companion", unchanged)


if __name__ == "__main__":
    unittest.main()
