import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bootstrap'))
import mag_whatsapp_resume as resume
import mag_whatsapp_handoff as handoff

class ResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.task = dict(id='task', leaseToken='token', revision=4, phone='5511999999999',
            messages=[{'id':'a', 'raw': {'id':'wamid.a', 'type':'text'}}, {'id':'b', 'raw': {'id':'wamid.b', 'type':'text'}}],
            context=[{'role':'human', 'text':'O preço é 100'}])
        self.adapter = SimpleNamespace(_phone_number_id='123', _mag_reply_controls={})
        self.events = [SimpleNamespace(text=t, media_urls=[], media_types=[], raw_message={}, source=SimpleNamespace(chat_id=self.task['phone']), message_id=t) for t in ['Qual preço?', 'E prazo?']]
        self.adapter._build_message_event_from_cloud = AsyncMock(side_effect=self.events)
        self.update = AsyncMock(return_value={'status':'running'})
        self.patcher = patch.object(resume, 'update', self.update); self.patcher.start()
        self.addCleanup(self.patcher.stop)
    async def run_parent(self, parent):
        async def handle(event):
            await resume.process(self.adapter, event, 'session', parent)
        self.adapter.handle_message = handle
        await resume.run_claimed(self.adapter, self.task)
    async def test_consolidates_pending_and_team_context_then_completes(self):
        async def parent(event, key):
            self.assertIn('Qual preço?', event.text)
            self.assertIn('E prazo?', event.text)
            self.assertIn('O preço é 100', event.text)
            self.assertEqual(event._mag_handoff_revision, 4)
            resume.turn.get()['sent'] = True
        await self.run_parent(parent)
        self.assertEqual([c.args[2] for c in self.update.call_args_list], ['started','complete'])
        self.assertIsNone(resume.turn.get())
        self.assertEqual(self.adapter._mag_reply_controls, {})
    async def test_review_with_nothing_left_does_not_send(self):
        async def parent(event, key):
            self.assertTrue(resume.suppress_reply(resume.NO_REPLY))
        await self.run_parent(parent)
        self.assertEqual(self.update.call_args.args[2], 'no_reply')
    async def test_empty_or_swallowed_handler_failure_is_not_success(self):
        async def parent(event, key):
            resume.turn.get()['sent'] = True
            resume.processing_hook('on_processing_complete', (event, SimpleNamespace(value='failure')))
        await self.run_parent(parent)
        self.assertEqual(self.update.call_args.args[2], 'failed')
    async def test_sentinel_mixed_with_customer_text_never_leaks(self):
        async def parent(event, key):
            resume.suppress_reply('Resposta ' + resume.NO_REPLY)
        await self.run_parent(parent)
        self.assertEqual(self.update.call_args.args[2], 'failed')
    async def test_unavailable_media_never_becomes_a_text_question(self):
        self.task['messages'][0]['raw']['type'] = 'audio'
        parent = AsyncMock()
        await self.run_parent(parent)
        parent.assert_not_awaited()
        self.assertEqual(self.update.call_args.args[2], 'failed')
    async def test_preserves_all_native_attachments(self):
        from enum import Enum
        Kind = Enum('Kind', ['TEXT', 'VOICE', 'PHOTO'])
        self.events[0].message_type = Kind.VOICE
        self.events[1].message_type = Kind.PHOTO
        self.events[0].media_urls=['/tmp/voice.ogg']; self.events[0].media_types=['audio/ogg']
        self.events[1].media_urls=['/tmp/image.png']; self.events[1].media_types=['image/png']
        async def parent(event, key):
            self.assertEqual(event.message_type, Kind.TEXT)
            self.assertEqual(event.media_urls, ['/tmp/voice.ogg','/tmp/image.png'])
            self.assertEqual(event.media_types, ['audio/ogg','image/png'])
            resume.turn.get()['sent']=True
        await self.run_parent(parent)
    async def test_lost_start_lease_never_invokes_agent(self):
        self.update.side_effect = RuntimeError('stale')
        parent = AsyncMock()
        await self.run_parent(parent)
        parent.assert_not_awaited()
    async def test_interactive_answer_resolves_before_queueing(self):
        self.adapter._dispatch_interactive_reply = AsyncMock(return_value=True)
        with patch.object(resume, 'request', AsyncMock()) as request:
            raw = {'type':'interactive', 'id':'wamid.button'}
            await resume.ingress(self.adapter, raw)
            self.adapter._dispatch_interactive_reply.assert_awaited_once_with(raw, {})
            self.assertTrue(request.call_args.args[2]['handled'])
    async def test_proxy_carries_task_fence_and_records_transport_failure(self):
        import os
        state={'task':self.task, 'sent':False, 'failed':False, 'no_reply':False}
        token=resume.turn.set(state)
        self.adapter._http_client=SimpleNamespace(post=AsyncMock(side_effect=TimeoutError()))
        try:
            with patch.dict(os.environ, {'WHATSAPP_CLOUD_HANDOFF_ENABLED':'true', 'MAG_API_URL':'http://api', 'MAG_INTERNAL_KEY':'key', 'MAG_TENANT_ID':'tenant'}):
                with self.assertRaises(TimeoutError):
                    await handoff.post(self.adapter, 'graph', {}, {'to':self.task['phone']})
                body=self.adapter._http_client.post.call_args.kwargs['json']
                self.assertEqual(body['replyTaskId'], 'task')
                self.assertEqual(body['leaseToken'], 'token')
                self.assertTrue(state['failed'])
                self.assertFalse(state['sent'])
        finally:
            resume.turn.reset(token)

if __name__ == '__main__': unittest.main()
