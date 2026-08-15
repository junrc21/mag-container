"""Build-time patch: sanitize technical cron job errors sent to client channels.

PROBLEM: When a cron job fails (e.g. "No Codex credentials stored. Run hermes auth..."),
Hermes sends the raw technical error directly to the client channel (Telegram/WhatsApp).

The error is included in deliver_content at line ~2103:
    deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"

This violates product secrecy and confuses the user.

SOLUTION: This patch adds a sanitize function and applies it BEFORE errors are used in:
- deliver_content (sent to channels)
- mark_job_run (saved to jobs.json)
- _mag_report_job_run (sent to control plane)
- logger.error (logs)

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib
import re

SCHEDULER_PY = pathlib.Path(
    os.getenv("CRON_SCHEDULER_PY", "/opt/hermes/cron/scheduler.py")
)

MARKER = "MAG: sanitize cron job errors"

# Helper function to sanitize error messages - inserted before tick()
SANITIZE_HELPER = '''# MAG: sanitize cron job errors sent to client channels
#
# ASCII com escapes unicode de proposito: este bloco e injetado dentro do source do
# Hermes, e nao vale arriscar mojibake na unica frase que o cliente le.
_MAG_GENERIC_CRON_ERROR = (
    "N\u00e3o consegui processar essa tarefa agora. "
    "Tente novamente em instantes ou entre em contato com o suporte da CyriusX."
)


def _mag_sanitize_cron_error(msg: str) -> str:
    """Sanitize technical error messages before they reach client channels."""
    if not msg:
        return msg
    import re
    technical_patterns = [
        r"no codex credentials stored",
        r"Run hermes auth",
        r"Run hermes model",
        r"authentication",
        r"credential",
        r"API key",
        r"token.*expired",
        r"RuntimeError",
        r"Traceback",
        r'File ".*"',
        r"\\.env",
        r"/opt/",
    ]
    msg_lower = msg.lower()
    for pattern in technical_patterns:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            return _MAG_GENERIC_CRON_ERROR
    return msg


# MAG: a frase que o cliente recebe quando uma rotina falha.
#
# O Hermes monta "\u26a0\ufe0f Cron job '<nome>' failed:\\n<erro>" -- em INGLES, com o nome
# interno do agendador. Higienizar so o erro (o que este patch fazia antes) deixava o
# involucro passar, e o cliente recebia no Telegram, num produto pt-BR:
#
#     Cron job 'Verificar tarefas atrasadas' failed:
#     Nao consegui processar essa tarefa agora.
#
# Tres coisas erradas numa mensagem so: jargao de engenharia num canal de cliente (a
# barreira de sigilo do produto proibe), em ingles, e "Nao" sem til. Reportado por um
# cliente real que recebeu isso as 7h da manha.
def _mag_cron_failure_message(job, error) -> str:
    """Frase de falha de rotina, na voz dela. Nunca cita agendador, stack ou provedor."""
    nome = str((job or {}).get("name") or "").strip()
    sanitizado = _mag_sanitize_cron_error(str(error or ""))

    # Erro que sobreviveu a higienizacao ja e legivel e nao-tecnico: entra como
    # complemento, pra pessoa nao ficar sem nenhuma pista. O generico, nao -- repetiria
    # "nao consegui" duas vezes na mesma frase.
    motivo = "" if (not sanitizado or sanitizado == _MAG_GENERIC_CRON_ERROR) else " " + sanitizado
    alvo = 'a rotina "%s"' % nome if nome else "uma rotina que voc\\u00ea agendou"

    return (
        "N\u00e3o consegui terminar %s agora.%s "
        "Vou tentar de novo no pr\u00f3ximo hor\u00e1rio. "
        "Se continuar falhando, fale com o suporte da CyriusX."
    ) % (alvo, motivo)


'''


def main() -> None:
    if not SCHEDULER_PY.exists():
        raise SystemExit(f"cron scheduler.py not found at {SCHEDULER_PY}")

    text = SCHEDULER_PY.read_text(encoding='utf-8')

    if MARKER in text:
        print("OK: cron error sanitization already patched (idempotent no-op)")
        return

    # Check anchors
    if "def tick(" not in text:
        raise SystemExit("patch_sanitize_cron_errors: `def tick(` anchor missing (Hermes changed).")
    if 'mark_job_run' not in text:
        raise SystemExit("patch_sanitize_cron_errors: `mark_job_run` anchor missing (Hermes changed).")
    if "deliver_content = final_response if success else" not in text:
        raise SystemExit("patch_sanitize_cron_errors: `deliver_content` anchor missing (Hermes changed).")

    edits = 0

    # 1) Add sanitize helper function before tick()
    if "_mag_sanitize_cron_error" not in text:
        # Find the last import line and insert before def tick(
        lines = text.split('\n')
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.strip().startswith(('import ', 'from ')):
                insert_idx = i + 1
            elif 'def tick(' in line:
                insert_idx = i
                break

        if insert_idx > 0:
            lines.insert(insert_idx, SANITIZE_HELPER.strip())
            text = '\n'.join(lines)
            edits += 1

    # 2) Sanitize error BEFORE it's used in deliver_content
    # OLD: deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"
    # NEW: error = _mag_sanitize_cron_error(error)
    #      deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"
    OLD_DELIVERY = (
        "                deliver_content = final_response if success else f\"⚠️ Cron job '{job.get('name', job['id'])}' failed:\\n{error}\""
    )
    NEW_DELIVERY = (
        "                # MAG: frase de falha na voz dela, sem o involucro em ingles do Hermes\n"
        "                deliver_content = final_response if success else _mag_cron_failure_message(job, error)"
    )
    if OLD_DELIVERY in text:
        text = text.replace(OLD_DELIVERY, NEW_DELIVERY, 1)
        edits += 1
    elif "_mag_cron_failure_message(job, error)" not in text:
        # A sentinela precisa apontar pro que a edição REALMENTE deixa no arquivo. Ela
        # ficou apontando pro comentário da versão anterior quando a linha de entrega
        # mudou, e o resultado foi um patch que falhava na segunda aplicação sobre um
        # arquivo já patcheado — silencioso até alguém rodar duas vezes.
        raise SystemExit("patch_sanitize_cron_errors: deliver_content anchor missing (Hermes changed).")

    # 3) Sanitize error BEFORE mark_job_run and _mag_report_job_run
    # OLD: mark_job_run(job["id"], success, error, delivery_error=delivery_error)
    # NEW: mark_job_run(job["id"], success, _mag_sanitize_cron_error(error), delivery_error=delivery_error)
    OLD_MARK_RUN = (
        "                mark_job_run(job[\"id\"], success, error, delivery_error=delivery_error)"
    )
    NEW_MARK_RUN = (
        "                mark_job_run(job[\"id\"], success, _mag_sanitize_cron_error(error), delivery_error=delivery_error)"
    )
    if OLD_MARK_RUN in text:
        text = text.replace(OLD_MARK_RUN, NEW_MARK_RUN, 1)
        edits += 1

    # 4) Sanitize error in _mag_report_job_run
    # OLD: _mag_report_job_run(job, success, error, delivery_error, _mag_run_started_at, final_response)
    # NEW: _mag_report_job_run(job, success, _mag_sanitize_cron_error(error), delivery_error, _mag_run_started_at, final_response)
    OLD_REPORT_RUN = (
        "                _mag_report_job_run(job, success, error, delivery_error, _mag_run_started_at, final_response)"
    )
    NEW_REPORT_RUN = (
        "                _mag_report_job_run(job, success, _mag_sanitize_cron_error(error), delivery_error, _mag_run_started_at, final_response)"
    )
    if OLD_REPORT_RUN in text:
        text = text.replace(OLD_REPORT_RUN, NEW_REPORT_RUN, 1)
        edits += 1

    # 5) Sanitize exception in except block
    # OLD: logger.error("Error processing job %s: %s", job['id'], e)
    #      mark_job_run(job["id"], False, str(e))
    #      _mag_report_job_run(job, False, str(e), None, _mag_run_started_at, None)
    # NEW: sanitized_e = _mag_sanitize_cron_error(str(e))
    #      logger.error("Error processing job %s: %s", job['id'], sanitized_e)
    #      mark_job_run(job["id"], False, sanitized_e)
    #      _mag_report_job_run(job, False, sanitized_e, None, _mag_run_started_at, None)
    OLD_EXCEPT_BLOCK = (
        "            except Exception as e:\n"
        "                logger.error(\"Error processing job %s: %s\", job['id'], e)\n"
        "                mark_job_run(job[\"id\"], False, str(e))\n"
        "                _mag_report_job_run(job, False, str(e), None, _mag_run_started_at, None)"
    )
    NEW_EXCEPT_BLOCK = (
        "            except Exception as e:\n"
        "                sanitized_e = _mag_sanitize_cron_error(str(e))\n"
        "                logger.error(\"Error processing job %s: %s\", job['id'], sanitized_e)\n"
        "                mark_job_run(job[\"id\"], False, sanitized_e)\n"
        "                _mag_report_job_run(job, False, sanitized_e, None, _mag_run_started_at, None)"
    )
    if OLD_EXCEPT_BLOCK in text:
        text = text.replace(OLD_EXCEPT_BLOCK, NEW_EXCEPT_BLOCK, 1)
        edits += 1

    # Pós-condição: chamar uma função que não foi definida produz um scheduler que só
    # quebra em RUNTIME, na primeira rotina que falha — e o patch teria impresso "OK".
    #
    # Isso não é hipotético: a injeção do helper é guardada por
    # `if "_mag_sanitize_cron_error" not in text`, e a checagem de MARKER usa outra
    # condição. Um arquivo em que as chamadas existem mas a definição não (upstream
    # reorganizado, patch parcial de uma versão anterior) passava pelas duas e saía
    # daqui quebrado, em silêncio. A disciplina fail-loud do repo existe justamente pra
    # isso não acontecer.
    for chamada, definicao in (
        ("_mag_sanitize_cron_error(", "def _mag_sanitize_cron_error"),
        ("_mag_cron_failure_message(", "def _mag_cron_failure_message"),
        ("_MAG_GENERIC_CRON_ERROR", "_MAG_GENERIC_CRON_ERROR = ("),
    ):
        if chamada in text and definicao not in text:
            raise SystemExit(
                f"patch_sanitize_cron_errors: {chamada!r} e chamado mas {definicao!r} nao "
                "foi injetado — o scheduler sairia quebrado."
            )

    if edits == 0:
        print("OK: cron error sanitization already applied (idempotent no-op)")
        return

    SCHEDULER_PY.write_text(text, encoding='utf-8')
    print(f"OK: patched {SCHEDULER_PY} with cron error sanitization ({edits} edit(s))")


if __name__ == "__main__":
    main()
