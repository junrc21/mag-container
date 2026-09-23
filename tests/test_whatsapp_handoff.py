import asyncio
import importlib.util
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mag_whatsapp_handoff', ROOT / 'bootstrap/mag_whatsapp_handoff.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

class HandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'WHATSAPP_CLOUD_HANDOFF_ENABLED': 'true', 'MAG_API_URL': 'http://api',
            'MAG_INTERNAL_KEY': 'test', 'MAG_TENANT_ID': 'tenant'})
        self.env.start()
        self.adapter = SimpleNamespace(_phone_number_id='123', _http_client=SimpleNamespace(post=AsyncMock(return_value='response')))
    def tearDown(self):
        self.env.stop()
    async def test_proxy_never_sends_graph_credentials_to_control_plane(self):
        await guard.post(self.adapter, 'https://graph.facebook.com/messages', {'Authorization': 'secret'}, {'to':'5511999999999','type':'text'})
        call = self.adapter._http_client.post.call_args
        self.assertEqual(call.args[0], 'http://api/internal/whatsapp-handoff/123/send')
        self.assertEqual(call.kwargs['headers'], {'x-internal-key':'test'})
    async def test_paused_or_failed_proxy_has_no_direct_fallback(self):
        self.adapter._http_client.post.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            await guard.post(self.adapter, 'https://graph.facebook.com/messages', {}, {'to':'5511999999999'})
        self.assertEqual(self.adapter._http_client.post.await_count, 1)
    async def test_revision_survives_background_task_and_is_scoped_to_contact(self):
        event = SimpleNamespace(source=SimpleNamespace(chat_id='5511999999999'), raw_message={'_mag_handoff_revision': 7})
        async def parent(event, key):
            await asyncio.create_task(guard.post(self.adapter, 'graph', {}, {'to':'5511999999999'}))
            self.assertEqual(self.adapter._http_client.post.call_args.kwargs['json']['revision'], 7)
            await guard.post(self.adapter, 'graph', {}, {'to':'5511888888888'})
            self.assertNotIn('revision', self.adapter._http_client.post.call_args.kwargs['json'])
        await guard.process(self.adapter, event, 'session', parent)
        self.assertIsNone(guard._turn.get())
    async def test_same_turn_delivery_retry_has_stable_request_id(self):
        event = SimpleNamespace(source=SimpleNamespace(chat_id='5511999999999'), message_id='wamid.in', raw_message={'_mag_handoff_revision': 7})
        async def parent(event, key):
            await guard.post(self.adapter, 'graph', {}, {'to':'5511999999999', 'text': {'body': 'hello'}})
            first = self.adapter._http_client.post.call_args.kwargs['json']['requestId']
            await guard.post(self.adapter, 'graph', {}, {'to':'5511999999999', 'text': {'body': 'hello'}})
            self.assertEqual(first, self.adapter._http_client.post.call_args.kwargs['json']['requestId'])
        await guard.process(self.adapter, event, 'session', parent)

    async def test_conventional_mode_unchanged(self):
        os.environ['WHATSAPP_CLOUD_HANDOFF_ENABLED'] = 'false'
        await guard.post(self.adapter, 'graph', {'Authorization':'token'}, {'to':'5511999999999'})
        self.assertEqual(self.adapter._http_client.post.call_args.args[0], 'graph')
    def test_context_is_bounded_and_original_message_retained(self):
        event = SimpleNamespace(text='Nova pergunta')
        guard.enrich(event, {'revision': 2, 'context': [{'text': 'x' * 50000}]})
        self.assertLess(len(event.text), 24500)
        self.assertTrue(event.text.endswith('Nova pergunta'))
        self.assertEqual(event._mag_handoff_revision, 2)

if __name__ == '__main__':
    unittest.main()
