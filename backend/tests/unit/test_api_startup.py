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
