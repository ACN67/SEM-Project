from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import unittest, os, json
import pymysql
from backend.app.database.mysql import MySqlStore
from backend.app.database.store import MemoryStore, ReportRecord, JobRecord
from backend.app.contracts.models import ReportDocument, ErrorInfo

@unittest.skipUnless(os.getenv('W1_MYSQL_PORT'), 'isolated MySQL required')
class StoreMySQLTests(unittest.TestCase):
    def setUp(self):
        self.db='review_'+uuid4().hex
        self.now=datetime.now(timezone.utc)
        self.connect=lambda: pymysql.connect(host='127.0.0.1',port=int(os.environ['W1_MYSQL_PORT']),user='root',database=self.db,autocommit=True,cursorclass=pymysql.cursors.DictCursor)
        with pymysql.connect(host='127.0.0.1',port=int(os.environ['W1_MYSQL_PORT']),user='root',autocommit=True) as c,c.cursor() as q:q.execute('CREATE DATABASE '+self.db)
        self.addCleanup(self.cleanup)
        with self.connect() as c,c.cursor() as q:
            for name in ['010*','011*','012*','020*']:
                for statement in next(Path('backend/migrations').glob(name)).read_text().split(';'):
                    if statement.strip():q.execute(statement)
        self.store=MySqlStore(self.connect)
    def cleanup(self):
        with self.connect() as c,c.cursor() as q:q.execute('DROP DATABASE '+self.db)
    def test_immutable_and_nullable_fields(self):
        for store in [self.store,MemoryStore()]:
            report=ReportRecord(report_id='r',title='test',report_type='industry_analysis',template_id='industry_analysis_v1',template_version=1)
            store.create_report(report,self.now)
            job=JobRecord(job_id='j',report_id='r',run_id='run')
            store.create_job(job,self.now)
            store.update_job('j',stage='GENERATING',error=ErrorInfo(code='TEST',message='old',retryable=False,details={}))
            store.update_job('j',status='COMPLETED',stage=None,error=None)
            actual=store.get_job('j');self.assertIsNone(actual.stage);self.assertIsNone(actual.error)
            d=ReportDocument.model_validate_json(Path('shared/contracts/fixtures/report_document.json').read_text(encoding='utf8')).model_copy(update={'report_id':'r','version_id':'v'})
            store.save_version(d,make_current=True,now=self.now)
            store.save_version(d,make_current=True,now=self.now)
            with self.assertRaises(ValueError):store.save_version(d.model_copy(update={'exportable':not d.exportable}),make_current=True,now=self.now)
            self.assertEqual(store.get_version('r','v'),d)

    def test_budget_survives_store_recreation(self):
        from decimal import Decimal
        self.assertTrue(self.store.reserve_budget(Decimal('60'),Decimal('100')))
        self.assertFalse(MySqlStore(self.connect).reserve_budget(Decimal('50'),Decimal('100')))

    def test_atomic_idempotency_and_claims(self):
        from concurrent.futures import ThreadPoolExecutor
        from backend.app.database.store import IdempotencyRequest
        from backend.app.contracts.services import ServiceError
        guard=IdempotencyRequest(operation='create',target_id='',key='same',hash='same')
        def submit(i):
            report=ReportRecord(report_id='r'+str(i),title='test',report_type='industry_analysis',template_id='industry_analysis_v1',template_version=1)
            job=JobRecord(job_id='j'+str(i),report_id=report.report_id,run_id='run'+str(i))
            return self.store.begin_report(guard,report,job,{'job_id':job.job_id},self.now)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(self.store.list_reports(1,20)[0],1)
        with self.assertRaises(ServiceError):
            self.store.begin_report(IdempotencyRequest(operation='create',target_id='',key='same',hash='other'),None,None,{},self.now)
        with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(lambda _:self.store.claim_next_queued(self.now),range(2)))
        self.assertEqual(sum(x is not None for x in claims),1)

    def test_report_insert_rolls_back_on_invalid_job(self):
        from backend.app.database.store import IdempotencyRequest
        report=ReportRecord(report_id='rollback',title='test',report_type='industry_analysis',template_id='industry_analysis_v1',template_version=1)
        job=JobRecord(job_id='bad',report_id='missing',run_id='run')
        with self.assertRaises(Exception):self.store.begin_report(IdempotencyRequest(operation='create',target_id='',key='bad',hash='h'),report,job,{},self.now)
        self.assertIsNone(self.store.get_report('rollback'))
