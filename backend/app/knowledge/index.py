"""A revision-specific normalized embedding collection."""
from hashlib import sha256
import json
import re

MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
SCORE_THRESHOLD = 0.5142417848110199

class EvidenceIndex:
    def __init__(self, root, *, revision=MODEL_REVISION, threshold=SCORE_THRESHOLD, model=None):
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("model revision must be an immutable commit")
        import chromadb
        from sentence_transformers import SentenceTransformer
        config = {"model":"BAAI/bge-small-zh-v1.5", "revision":revision, "normalize":True, "dimension":512}
        self.fingerprint = sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
        self.model = model or SentenceTransformer(config["model"], revision=revision)
        self.client = chromadb.PersistentClient(path=str(root))
        self.collection = self.client.get_or_create_collection(
            name="evidence-"+self.fingerprint[:24],metadata={"hnsw:space":"cosine"})
        self.threshold = threshold

    def replace(self, version, evidences):
        self.collection.delete(where={"version":version})
        for offset in range(0,len(evidences),128):
            batch = evidences[offset:offset+128]
            vectors = self.model.encode([e.excerpt for e in batch],normalize_embeddings=True).tolist()
            if any(len(v)!=512 for v in vectors):
                raise ValueError("embedding dimension mismatch")
            self.collection.upsert(ids=[e.evidence_id for e in batch],embeddings=vectors,
                metadatas=[{"version":version,"evidence_id":e.evidence_id} for e in batch])
        found = self.collection.get(where={"version":version},include=[])
        if set(found["ids"])!={e.evidence_id for e in evidences}:
            raise ValueError("incomplete version index")

    def rank(self, query, ids):
        vector = self.model.encode(["为这个句子生成表示以用于检索相关文章："+query],normalize_embeddings=True).tolist()
        scores = []
        for offset in range(0,len(ids),128):
            batch = ids[offset:offset+128]
            result = self.collection.query(query_embeddings=vector,where={"evidence_id":{"$in":batch}},n_results=len(batch))
            scores.extend((id,1-distance) for id,distance in zip(result["ids"][0],result["distances"][0]))
        return sorted((item for item in scores if item[1]>=self.threshold),key=lambda item:(-item[1],item[0]))
