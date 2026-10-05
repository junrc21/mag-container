import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('mag_owner_alerts',ROOT/'bootstrap/mag_owner_alerts.py')
alerts=importlib.util.module_from_spec(spec)
with patch.dict(os.environ, {}, clear=True): spec.loader.exec_module(alerts)

class OwnerAlertsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ, {'HERMES_HOME':self.temp.name,'MAG_API_URL':'http://api','MAG_INTERNAL_KEY':'secret','MAG_TENANT_ID':'tenant'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.start=patch.object(alerts,'_start'); self.start.start(); self.addCleanup(self.start.stop)
        alerts._last.clear()
    def test_only_operational_metadata_is_persisted(self):
        alerts.report('provider','whatsapp_cloud'); alerts.report('provider','whatsapp_cloud')
        files=list(alerts._directory().glob('*.json'))
        self.assertEqual(len(files),1)
        self.assertEqual(json.loads(files[0].read_text()), {'category':'provider','platform':'whatsapp_cloud'})
    def test_outage_keeps_private_alert_and_retry_ack_removes_only_spool(self):
        alerts.report('generation','telegram')
        with patch('urllib.request.urlopen',side_effect=TimeoutError()): alerts._flush()
        self.assertEqual(len(list(alerts._directory().glob('*.json'))),1)
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b'{"accepted":true}'
        with patch('urllib.request.urlopen',return_value=Response()) as post:
            alerts._flush()
            body=json.loads(post.call_args.args[0].data)
            self.assertEqual(body,{'tenantId':'tenant','category':'generation','platform':'telegram'})
        self.assertFalse(list(alerts._directory().glob('*.json')))
    def test_invalid_ack_preserves_pending_alert(self):
        alerts.report('delivery','whatsapp_cloud')
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b'{"accepted":false}'
        with patch('urllib.request.urlopen',return_value=Response()): alerts._flush()
        self.assertTrue(list(alerts._directory().glob('*.json')))
    def test_internal_surfaces_do_not_create_customer_alert(self):
        for platform in ['local','cli','api_server']: alerts.report('generation',platform)
        self.assertFalse(alerts._directory().exists())
    def test_suppressed_error_marks_whatsapp_batch_failed(self):
        state={'failed':False}
        with patch.dict(sys.modules,{'mag_whatsapp_resume':SimpleNamespace(turn=SimpleNamespace(get=lambda:state))}):
            alerts.report('provider','whatsapp_cloud')
        self.assertTrue(state['failed'])
    def test_model_critical_copy_is_detected_but_normal_answer_is_not(self):
        self.assertTrue(alerts.operational_error('Tive um erro crítico de configuração.'))
        self.assertTrue(alerts.operational_error('Fale com o suporte da CyriusX.'))
        self.assertFalse(alerts.operational_error('Seu pedido está confirmado.'))

if __name__=='__main__': unittest.main()
