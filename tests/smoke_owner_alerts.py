"""Executar no Hermes fixado, sem rede, montando o repositório somente leitura em /test."""
import asyncio
import ast
from types import SimpleNamespace
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import AsyncMock, Mock
# Mount the runtime repository read-only at /test; only the disposable container is changed.
import runpy
patch_root=Path('/test/bootstrap')
if os.environ.get('MAG_SMOKE_USE_BUILT_IMAGE') != '1':
    shutil.copy(patch_root/'mag_owner_alerts.py', '/opt/hermes/mag_owner_alerts.py')
    for patch_name in ['patch_gateway_output.py','patch_sanitize_cron_errors.py','patch_owner_error_alerts.py','patch_owner_error_alerts.py']:
        runpy.run_path(str(patch_root/patch_name),run_name='__main__')
sys.path.insert(0,'/opt/hermes')
import mag_owner_alerts as alerts
alerts.report=Mock()
from gateway.run import _sanitize_gateway_final_response, _prepare_gateway_status_message
from gateway.config import PlatformConfig
from gateway.platforms.whatsapp_cloud import WhatsAppCloudAdapter
from gateway.platforms.base import SendResult
from cron.scheduler import _mag_cron_failure_message

async def main():
    failures=['Provider authentication failed: invalid token', 'API call failed: service unavailable', 'Tive um erro crítico de configuração aqui.', 'ModuleNotFoundError: missing package']
    for platform in ['whatsapp_cloud','whatsapp','telegram']:
        for content in failures:
            assert not _sanitize_gateway_final_response(platform,content), (platform,content)
        assert _prepare_gateway_status_message(platform,'error',failures[0]) is None
        assert _sanitize_gateway_final_response(platform,'Seu pedido está confirmado.')=='Seu pedido está confirmado.'
    assert _sanitize_gateway_final_response('cli',failures[0])==failures[0]
    tree=ast.parse(Path('/opt/hermes/gateway/run.py').read_text())
    callback=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_notice_callback_sync')
    schedule=Mock()
    ns={'_status_adapter':object(), '_run_still_current':lambda:True, '_mag_owner_alerts':alerts,
        'source':SimpleNamespace(platform='whatsapp_cloud'),'safe_schedule_threadsafe':schedule}
    exec(compile(ast.Module(body=[callback],type_ignores=[]),'notice-callback','exec'),ns)
    ns['_notice_callback_sync'](SimpleNamespace(level='critical',text='private failure'))
    schedule.assert_not_called()
    assert _mag_cron_failure_message({'name':'rotina'},'token expired')==''
    adapter=WhatsAppCloudAdapter(PlatformConfig(extra={'phone_number_id':'123','dm_policy':'open'}))
    adapter.send=AsyncMock(return_value=SendResult(success=False,error='timeout',retryable=True))
    result=await adapter._send_with_retry('5511999999999','Resposta normal')
    assert not result.success
    assert adapter.send.await_count==1, 'Delivery diagnostic leaked to customer'
    adapter.send.reset_mock()
    adapter.send_typing=AsyncMock(); adapter.stop_typing=AsyncMock()
    adapter.set_message_handler(AsyncMock(side_effect=RuntimeError('private-stack-token')))
    event=await adapter._build_message_event_from_cloud({'id':'wamid.a','from':'5511999999999','timestamp':'1790340000','type':'text','text':{'body':'Olá'}},{},{})
    await adapter._process_message_background(event,'test-session')
    adapter.send.assert_not_awaited()
    assert alerts.report.call_count >= 10
    print('PASS: provider/status/model errors, private CLI preserved, cron silence, native delivery failure and native handler exception')
asyncio.run(main())
