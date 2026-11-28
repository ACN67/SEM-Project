import os,json,csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from test_orchestration_mysql import StoreMySQLTests
from backend.app.main import create_app
from backend.app.config import Settings
from backend.app.contracts.models import Filters
from backend.app.knowledge.tabular import COLUMNS

class ApiMySQLTests(StoreMySQLTests):
    def test_real_upload_versions_and_filtered_sources(self):
        from fastapi.testclient import TestClient
        with TemporaryDirectory() as root,patch.dict(os.environ,{'INGEST_LABELS_JSON':json.dumps({'industries':['新能源汽车'],'regions':['中国']})}):
            settings=Settings.from_env({'DATABASE_URL':f'mysql://root@127.0.0.1:{os.environ["W1_MYSQL_PORT"]}/{self.db}','RUNTIME_DIR':root,'ADMIN_CREDENTIAL':'test'})
            app=create_app(settings,start_worker=False)
            try:
                with TestClient(app) as client:
                    self.assertEqual(client.post('/api/v1/admin/session',json={'credential':'test'}).status_code,204)
                    def upload(value):
                        import io
                        text=io.StringIO();writer=csv.writer(text);writer.writerow(COLUMNS)
                        writer.writerow(['sales','sales','','2024','year','中国','test',value,'辆','1','辆',''])
                        result=client.post('/api/v1/documents',files={'file':('same.csv',text.getvalue().encode(),'text/csv')},data={'metadata':json.dumps({'title':'test','publisher':'test','published_at':'2025-01-01'})})
                        self.assertEqual(result.status_code,202,result.text)
                        return result.json()
                    first=upload('10');again=upload('10');second=upload('20')
                    self.assertEqual(first['document_version_id'],again['document_version_id'])
                    self.assertEqual(first['document_id'],second['document_id'])
                    self.assertNotEqual(first['document_version_id'],second['document_version_id'])
                    services=app.state.services
                    services.orchestration._process_ingests()
                    ids=[second['document_version_id']]
                    filters=Filters(industries=['新能源汽车'],regions=['中国'],periods=['2024'])
                    metrics=services.knowledge.get_metrics(filters,ids)
                    self.assertEqual(len(metrics),1)
                    self.assertEqual(metrics[0].value,'20')
                    self.assertTrue(services.knowledge.retrieve('sales',filters,5,ids))
                    source_ids=metrics[0].source_evidence_ids
                    self.assertEqual(len(services.knowledge.get_evidences(source_ids,ids)),1)
                    self.assertEqual(services.knowledge.get_evidences(source_ids,[]),[])
                    self.assertEqual(client.get(second['status_url']).json()['ingest_status'],'READY')
            finally:app.state.services.knowledge.pipeline.index.client._system.stop()
