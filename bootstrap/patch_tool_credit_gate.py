"""Build-time patch: recusa uma ferramenta cara quando o saldo não a cobre.

O gate de turno que já existe (`patch_credit_hardcap.py`) pergunta "sobrou alguma
coisa?" — nunca "dá para pagar ISTO?". Ele não teria como: no início do turno
ninguém sabe quais ferramentas o modelo vai chamar. O custo de um turno é a soma
das ferramentas que ele acabou usando, e essa lista só existe depois.

Este patch fecha a diferença no único instante em que o preço é conhecido e a
ação ainda não aconteceu: a chamada da ferramenta.

    saldo 5, o agente tenta image_gen (10)  ->  recusado, o turno segue sem ela
    saldo 5, o agente usa web(1) tres vezes ->  roda; a cobranca vem depois

## Por que em `registry.dispatch`, e nao envolvendo `registry.register`

`dispatch` e o ponto UNICO por onde toda ferramenta do registry passa. Ele ja
resolve a entrada (portanto ja conhece o `toolset`, que e a chave do preco), ja
devolve `str`, e ja normaliza sync/async — o gate nao precisa saber nada disso.
Envolver `register` exigiria embrulhar N handlers e lidar com corotina, para
chegar no mesmo lugar por um caminho mais longo.

Cobre canal E rotinas de graca: os dois rodam o mesmo agente no mesmo processo.
Um gate no `gateway/run.py` deixaria o cron de fora — que foi exatamente o erro
que o bloqueio administrativo cometeu.

## Por que patch, e nao um plugin de `tool_execution` middleware

O Hermes tem um sistema de plugins com middleware de execucao de ferramenta, que
e o ponto de extensao DESENHADO para isto, e a escolha parecia obvia. Nao serve
aqui, por tres motivos:

1. **Plugin de usuario e opt-in por `plugins.enabled` no config.yaml.** Um tenant
   cujo config nao foi regenerado ficaria sem gate nenhum — silenciosamente. E o
   mesmo tipo de buraco que este trabalho existe para fechar.
2. **Uma chave de configuracao poderia desligar a cobranca.** Controle de
   faturamento que um `plugins.disabled` derruba nao e controle, e recurso.
3. **O host trata middleware que levanta excecao como fail-OPEN**: a cadeia segue
   para o proximo e a ferramenta roda. A defesa do host trabalharia contra a
   nossa.

O patch e incondicional e falha alto no build se a ancora sumir. A fragilidade a
refactor do Hermes e real, e e o preco — pago de olhos abertos.

## O que este gate NAO cobre

As ferramentas de agente despachadas inline (`todo`, `session_search`, `memory`,
`context_engine`, `clarify`, `delegate_task`) nao passam por `dispatch`. Todas
custam 1 credito. A regra "5 nao paga 10" so tem sentido para as caras, e todas
as caras — image_gen(10), video_gen(40), moa(5), computer_use(5), browser(3) —
sao ferramentas de registry.

Idempotente + fail-loud (espelha os outros patches do bootstrap).
"""

import os
import pathlib

REGISTRY_PY = pathlib.Path(os.getenv("TOOLS_REGISTRY_PY", "/opt/hermes/tools/registry.py"))

MARKER = "_mag_tool_credit_refusal"

# --- Edit 1: helpers de modulo -------------------------------------------------
# Ancorados na definicao da classe, que e o ponto mais estavel do arquivo.
HELPERS_ANCHOR = "class ToolRegistry:"
HELPERS = '''# MAG: recusa por credito, por chamada de ferramenta. Ver o docstring de
# bootstrap/patch_tool_credit_gate.py para por que aqui e nao num plugin.
_MAG_SALDO_CACHE = {"ate": 0.0, "check": None}

# O saldo e relido a cada N segundos. Um turno faz varias chamadas em poucos
# segundos, entao na pratica isto e uma leitura por turno — a mesma ordem de
# grandeza do gate de turno que ja existe.
_MAG_SALDO_TTL_S = 10.0

# Preco quando nao se sabe. Espelha o padrao do servidor
# (`TOOLSET_CREDIT_DEFAULTS`): ausente vale 1, nunca 0. Um toolset lido como
# gratuito seria cobrado depois e recusado nunca.
_MAG_PRECO_PADRAO = 1

# A tabela de precos e guardada em disco, o SALDO nunca.
#
# Sao dois dados com prazos de validade opostos: o saldo muda a cada turno e
# so serve fresco; o preco de uma ferramenta muda quando alguem edita a tela de
# Precos, o que acontece uma vez por mes. Sem esta memoria, uma queda do control
# plane no meio de um turno apagava o preco junto com o saldo — e sem preco o
# gate nao consegue distinguir uma imagem de 10 creditos de uma busca de 1, entao
# deixava as duas passarem. Foi um teste que pegou isso.
_MAG_PRECOS_CACHE = "/opt/data/.mag_toolset_costs.json"


def _mag_precos_do_disco():
    try:
        import json as _j
        with open(_MAG_PRECOS_CACHE) as fh:
            d = _j.load(fh)
        return d if isinstance(d, dict) and d else None
    except Exception:
        return None


def _mag_guardar_precos(precos):
    try:
        import json as _j
        with open(_MAG_PRECOS_CACHE, "w") as fh:
            _j.dump(precos, fh)
    except Exception:
        pass  # memoria e otimizacao; nunca quebrar um turno por ela


def _mag_saldo_e_precos():
    """(restante, precos) — o saldo sempre fresco, o preco com memoria de disco.

    `restante` e None quando nao deu para falar com o control plane agora.
    `precos` cai na ultima tabela conhecida nesse caso."""
    import time as _t
    agora = _t.monotonic()
    if _MAG_SALDO_CACHE["check"] is not None and _MAG_SALDO_CACHE["ate"] > agora:
        c = _MAG_SALDO_CACHE["check"]
        return c.remaining, c.toolset_costs or _mag_precos_do_disco()
    try:
        from mag_credit_guard import check_authoritative_credits as _mag_check
        c = _mag_check()
    except Exception:
        return None, _mag_precos_do_disco()
    if c.toolset_costs:
        _mag_guardar_precos(c.toolset_costs)
    _MAG_SALDO_CACHE["check"] = c
    _MAG_SALDO_CACHE["ate"] = agora + _MAG_SALDO_TTL_S
    return c.remaining, c.toolset_costs or _mag_precos_do_disco()


def _mag_tool_credit_refusal(entry):
    """Texto de recusa se esta chamada nao cabe no saldo, senao None.

    Nunca levanta: uma excecao aqui apareceria para o modelo como falha da
    ferramenta, e o remedio seria pior que a doenca."""
    try:
        toolset = getattr(entry, "toolset", None)
        if not toolset:
            return None

        restante, precos = _mag_saldo_e_precos()
        preco = int((precos or {}).get(toolset, _MAG_PRECO_PADRAO))

        # Ferramenta de 1 credito nunca e recusada aqui. Quem nao tem 1 credito ja
        # foi barrado no gate de turno; recusar de novo so trocaria a mensagem
        # humana daquele gate por um erro de ferramenta.
        if preco <= _MAG_PRECO_PADRAO:
            return None

        if restante is None:
            # Nao deu para confirmar o saldo. Conversa basica segue (o caso acima),
            # gasto caro nao — nao autorizar o que nao se pode verificar e o unico
            # lado do erro que tem teto.
            return _mag_recusa_texto(entry, preco, None)

        if restante >= preco:
            return None
        return _mag_recusa_texto(entry, preco, restante)
    except Exception:
        return None


def _mag_recusa_texto(entry, preco, restante):
    import json as _json
    nome = getattr(entry, "name", "esta ferramenta")
    if restante is None:
        motivo = (
            "Nao consegui confirmar o saldo de creditos agora, entao nao posso usar "
            "%s (custa %d creditos)." % (nome, preco)
        )
    else:
        motivo = (
            "Creditos insuficientes para %s: custa %d e restam %d."
            % (nome, preco, restante)
        )
    # "Nao tente de novo" nao e educacao — e o que evita o laco de retry que ja
    # travou uma resposta por mais de 60s neste produto (ver
    # patch_disable_channel_code_exec.py). O modelo precisa ser instruido a
    # seguir sem a ferramenta, nao a insistir.
    return _json.dumps(
        {
            "error": motivo,
            "retry": False,
            "instruction": (
                "Nao tente esta ferramenta de novo neste turno. Responda com o que "
                "voce ja tem e, se fizer falta, diga ao usuario que a acao precisa "
                "de mais creditos."
            ),
        },
        ensure_ascii=False,
    )


'''

# --- Edit 2: o gate, dentro de dispatch ---------------------------------------
# Depois da resolucao da entrada (precisamos do `toolset`) e ANTES do try que
# executa o handler.
GATE_ANCHOR = (
    '        entry = self.get_entry(name)\n'
    '        if not entry:\n'
    '            return json.dumps({"error": f"Unknown tool: {name}"})\n'
)
GATE_BLOCK = (
    "        # MAG: recusa por credito antes de a ferramenta rodar. Ver\n"
    "        # bootstrap/patch_tool_credit_gate.py.\n"
    "        _mag_recusa = _mag_tool_credit_refusal(entry)\n"
    "        if _mag_recusa is not None:\n"
    "            return _mag_recusa\n"
)


def main() -> None:
    if not REGISTRY_PY.exists():
        raise SystemExit(f"tools/registry.py not found at {REGISTRY_PY}")
    text = REGISTRY_PY.read_text()

    if MARKER in text:
        print("OK: tool credit gate already patched (idempotent no-op)")
        return

    if HELPERS_ANCHOR not in text:
        raise SystemExit("patch_tool_credit_gate: helpers anchor missing (Hermes changed).")
    text = text.replace(HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR, 1)

    if GATE_ANCHOR not in text:
        raise SystemExit("patch_tool_credit_gate: dispatch anchor missing (Hermes changed).")
    text = text.replace(GATE_ANCHOR, GATE_ANCHOR + GATE_BLOCK, 1)

    REGISTRY_PY.write_text(text)
    print("OK: patched tool credit gate (helpers + dispatch gate)")


if __name__ == "__main__":
    main()
