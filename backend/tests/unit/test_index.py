from unittest.mock import Mock
import unittest
from backend.app.knowledge.index import EvidenceIndex

class IndexRankingTests(unittest.TestCase):
    def test_candidate_batches_stable_ties_and_threshold(self):
        index=EvidenceIndex.__new__(EvidenceIndex)
        index.threshold=0.5
        index.model=Mock()
        index.model.encode.return_value.tolist.return_value=[[1.0]*512]
        index.collection=Mock()
        ids=[f"id-{i:03}" for i in range(130)]
        index.collection.query.side_effect=[
            {"ids":[["id-100","id-001"]],"distances":[[0.2,0.2]]},
            {"ids":[["id-129","id-128"]],"distances":[[0.8,0.1]]}]
        ranked=index.rank("问题",ids)
        self.assertEqual([id for id,score in ranked],["id-128","id-001","id-100"])
        self.assertEqual(index.collection.query.call_args_list[0].kwargs["where"]["evidence_id"]["$in"],ids[:128])
        self.assertEqual(index.collection.query.call_args_list[1].kwargs["where"]["evidence_id"]["$in"],ids[128:])
