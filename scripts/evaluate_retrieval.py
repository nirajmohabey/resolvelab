"""Compare lexical/dense/hybrid on the same frozen, small labeled policy queries."""
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import numpy as np
from sqlalchemy import text
from resolvelab.db import transaction
from resolvelab.retrieval import retrieve, EMBEDDING_MODEL, RRF_K


def main():
    seed=json.loads((ROOT/"artifacts/seed_manifest.json").read_text())
    principal=seed["actors"]["alpha-operator"]
    corpus=seed["corpora"]["alpha-published-v1"]
    dataset=ROOT/"fixtures/retrieval_queries.json"
    queries=json.loads(dataset.read_text())
    results={}
    for mode in ["lexical","dense","hybrid"]:
        with transaction(principal) as conn:
            retrieve(conn,principal["tenant_id"],corpus,"tracking",principal["role"],mode,4)
        cases=[]
        timings=[]
        for query in queries:
            tick=time.perf_counter()
            with transaction(principal) as conn:
                rows=retrieve(conn,principal["tenant_id"],corpus,query["query"],principal["role"],mode,4)
            timings.append((time.perf_counter()-tick)*1000)
            assert not any("BETA-CANARY-SECRET" in row["body"] for row in rows)
            assert all(row["corpus_id"]==corpus for row in rows)
            relevant=set(query["relevant_locators"])
            recall={str(k):len(relevant & {row["locator"] for row in rows[:k]})/len(relevant) for k in [1,2,4]}
            cases.append({"id":query["id"],"recall_at_k":recall,"ranked_locators":[row["locator"] for row in rows],
                          "retrieved_chunk_ids":[row["chunk_id"] for row in rows]})
        results[mode]={"N":len(cases),"mean_recall_at_k":{str(k):float(np.mean([r["recall_at_k"][str(k)] for r in cases])) for k in [1,2,4]},
                       "p50_ms":float(np.median(timings)),"p95_ms":float(np.percentile(timings,95)),"warm":True,"cases":cases}
    with transaction(principal) as conn:
        identity=conn.execute(text("SELECT current_user AS role, (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user) AS bypass")).mappings().one()
        metadata=conn.execute(text("SELECT content_sha256,embedding_model_revision FROM resolvelab.corpus_versions WHERE tenant_id=:t AND corpus_id=:c"),
                              {"t":principal["tenant_id"],"c":corpus}).mappings().one()
    report={"dataset_sha256":hashlib.sha256(dataset.read_bytes()).hexdigest(),"N":len(queries),
            "definition":"Mean fraction of labeled relevant policy-section locators found in top k, over all 16 answerable synthetic queries. Each query has one relevant section.",
            "labeling":"Coding-agent authored queries and section labels; no independent human adjudication. Tiny synthetic corpus, not general search accuracy.",
            "corpus_id":corpus,"corpus_sha256":metadata["content_sha256"],"corpus_sections":4,
            "embedding_model":EMBEDDING_MODEL,"rrf_constant":RRF_K,"runtime_role":identity["role"],"runtime_bypass_rls":identity["bypass"],
            "environment":{"python":platform.python_version(),"platform":platform.platform()},"modes":results,
            "latency_scope":"Warm retrieval plus one scoped local database transaction; excludes routing and generation. CPU local fixture, no provider cost.",
            "limitations":"Top4 equals entire four-section alpha corpus; recall@4 is a coverage sanity check. No real-world retrieval or attack-resistance estimate."}
    output=ROOT/"artifacts/retrieval_report.json"
    output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({mode:result["mean_recall_at_k"] for mode,result in results.items()}))


if __name__=="__main__":
    main()
