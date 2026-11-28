from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from backend.app.main import create_app
from backend.app.config import Settings
class StartupTests(unittest.TestCase):
    def test_default_wiring_starts(self):
        from fastapi.testclient import TestClient
        with TemporaryDirectory() as root:
            app=create_app(Settings.from_env({'RUNTIME_DIR':root}),start_worker=False)
            with TestClient(app) as client:self.assertEqual(client.get('/api/v1/templates').status_code,200)

    def test_budget_configuration_rejects_invalid_values(self):
        for key,values in [('PER_CALL_RESERVE_CNY',['-1','0','NaN','Infinity','oops']),('BUDGET_CNY',['-1','NaN','Infinity','oops'])]:
            for value in values:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):Settings.from_env({key:value})
        self.assertEqual(Settings.from_env({'BUDGET_CNY':'0','PER_CALL_RESERVE_CNY':'0.01'}).budget_cny,0)
