from contextlib import nullcontext
from unittest.mock import MagicMock
import unittest,json
from pathlib import Path
from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError
from backend.app.knowledge.retrieval import Retrieval

class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.snap=m.ReportDocument.model_validate_json(Path("shared/contracts/fixtures/report_document.json").read_text(encoding="utf8"))
        self.cursor=MagicMock()
        self.connection=MagicMock()
        self.connection.cursor.return_value=nullcontext(self.cursor)
        self.index=MagicMock()
        self.service=Retrieval(lambda:self.connection,self.index)
        e=self.snap.evidences[0]
        meta=m.Metadata(title=e.title,publisher=e.publisher,published_at=e.published_at,source_url=e.source_url)
        self.cursor.fetchall.return_value=[(e.model_dump_json(),"auto",meta.model_dump_json(),
            json.dumps({"entity_ids":["A"],"periods":["2024"]}),None)]
        self.index.rank.return_value=[(e.evidence_id,0.8)]

    def test_frozen_candidates(self):
        result=self.service.retrieve("销量",m.Filters(entity_ids=["A"],industries=["auto"]),1,["v-old"])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].score,0.8)
        self.assertIn("ingest_status='READY'",self.cursor.execute.call_args.args[0])
        self.assertEqual(self.cursor.execute.call_args.args[1],("v-old",))
        self.index.rank.assert_called_once_with("销量",[self.snap.evidences[0].evidence_id])

    def test_empty_missing_tags_and_asof(self):
        from datetime import date
        for filters,versions in [(m.Filters(),[]),(m.Filters(regions=["CN"]),["v"]),
            (m.Filters(as_of=date(2000,1,1)),["v"])]:
            self.assertEqual(self.service.retrieve("销量",filters,5,versions),[])
        self.index.rank.assert_not_called()

    def test_invalid_limit(self):
        for n in (0,51,True,1.5):
            with self.assertRaises(ServiceError):self.service.retrieve("test",m.Filters(),n,["v"])
