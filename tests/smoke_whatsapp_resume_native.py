import os, shutil, runpy, sys, asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace
for file in ['mag_whatsapp_handoff.py','mag_whatsapp_resume.py']:
    shutil.copy('/test/'+file, '/opt/hermes/'+file)
for file in ['patch_whatsapp_handoff.py','patch_whatsapp_resume.py','patch_whatsapp_resume.py']:
    runpy.run_path('/test/'+file, run_name='__main__')
sys.path.insert(0, '/opt/hermes')
os.environ.update(WHATSAPP_CLOUD_HANDOFF_ENABLED='true', MAG_API_URL='http://fake', MAG_INTERNAL_KEY='test', MAG_TENANT_ID='tenant')
from gateway.config import PlatformConfig
from gateway.platforms.whatsapp_cloud import WhatsAppCloudAdapter
import mag_whatsapp_resume as resume

async def main():
    for response, audio in [('O prazo é de dois dias.', False), (resume.NO_REPLY, False), (resume.NO_REPLY, True)]:
        adapter=WhatsAppCloudAdapter(PlatformConfig(extra={'phone_number_id':'123','access_token':'test','dm_policy':'open'}))
        adapter._phone_number_id='123'; adapter._access_token='test'
        adapter._mag_reply_controls={}
        adapter._http_client=SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda:{'messages':[{'id':'wamid.out'}]})))
        adapter.send_typing=AsyncMock()
        adapter.stop_typing=AsyncMock()
        adapter.set_message_handler(AsyncMock(return_value=response))
        update=AsyncMock(return_value={'status':'running'})
        resume.update=update
        task={'id':'job','leaseToken':'lease','revision':2,'phone':'5511999999999', 'messages':[{'id':'a','raw':{'id':'wamid.a','from':'5511999999999','timestamp':'1790340000','type':'text','text':{'body':'E o prazo?'}}}], 'context':[{'role':'human','text':'O preço é R$100'}]}
        if audio:
            task['messages'][0]['raw'].update(type='audio', audio={'id':'audio-id','mime_type':'audio/ogg'})
            adapter._download_media_to_cache=AsyncMock(return_value=('/tmp/audio.ogg','audio/ogg'))
        await asyncio.wait_for(resume.run_claimed(adapter,task), 15)
        expected='no_reply' if response==resume.NO_REPLY else 'complete'
        assert update.call_args.args[2]==expected, update.call_args_list
        assert adapter._message_handler.await_count==1
        assert adapter._http_client.post.await_count==(0 if expected=='no_reply' else 1)
        print('PASS native gateway pipeline:', expected)
    print('Native adapter import, patch idempotency, handoff, send proxy and sentinel suppression passed')
asyncio.run(main())
