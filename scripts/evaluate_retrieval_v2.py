"""Actual-PostgreSQL benchmark in the dedicated evaluation DB only.

Appends isolated benchmark tenants/corpora; never resets main demo data. Do not
run concurrently with another evaluation that truncates this disposable DB.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid5,NAMESPACE_URL

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from dotenv import load_dotenv
from sqlalchemy.engine import make_url
load_dotenv(ROOT/".env")
for key,evaluation in [("DATABASE_URL","EVALUATION_DATABASE_URL"),("ADMIN_DATABASE_URL","ADMIN_EVALUATION_DATABASE_URL")]:
    value=os.getenv(evaluation)
    if not value or make_url(value).database!="resolvelab_evaluation":raise SystemExit("Requires dedicated resolvelab_evaluation database")
    os.environ[key]=value
import numpy as np
from sqlalchemy import text
from resolvelab.db import transaction,admin_engine
from resolvelab.retrieval import retrieve,embed_consistent,EMBEDDING_MODEL,EMBEDDING_DIMENSION

OUT=ROOT/"artifacts/retrieval-v2"


def identity(alias):return str(uuid5(NAMESPACE_URL,"resolvelab/retrieval-benchmark-v2/"+alias))
def sha(value):return hashlib.sha256(value).hexdigest()


def seed(fixture,fixture_hash):
    tenant_a,tenant_b=identity("alpha"),identity("beta")
    actor_a,actor_b=identity("alpha-operator"),identity("beta-operator")
    corpora={"published":identity("alpha-published-"+fixture_hash),"retired":identity("alpha-retired-"+fixture_hash),"beta":identity("beta-published-"+fixture_hash)}
    vectors=embed_consistent([section["body"] for section in fixture["sections"]])
    with admin_engine.begin() as conn:
        for tid,aid,name in [(tenant_a,actor_a,"Retrieval benchmark alpha"),(tenant_b,actor_b,"Retrieval benchmark beta")]:
            conn.execute(text("INSERT INTO resolvelab.tenants(tenant_id,display_name) VALUES(:t,:name) ON CONFLICT DO NOTHING"),{"t":tid,"name":name})
            conn.execute(text("INSERT INTO resolvelab.memberships(tenant_id,actor_id,role) VALUES(:t,:a,'operator') ON CONFLICT DO NOTHING"),{"t":tid,"a":aid})
        for key,corpus in corpora.items():
            tenant=tenant_b if key=="beta" else tenant_a
            exists=conn.execute(text("SELECT state FROM resolvelab.corpus_versions WHERE tenant_id=:t AND corpus_id=:c"),{"t":tenant,"c":corpus}).scalar_one_or_none()
            if exists:continue
            conn.execute(text("INSERT INTO resolvelab.corpus_versions(tenant_id,corpus_id,version_label,content_sha256,embedding_model_revision,embedding_dimension,state) VALUES(:t,:c,:v,:h,:model,:dim,'draft')"),
                         {"t":tenant,"c":corpus,"v":"retrieval-v2-"+key+"-"+fixture_hash[:12],"h":fixture_hash,"model":EMBEDDING_MODEL,"dim":EMBEDDING_DIMENSION})
            for section,vector in zip(fixture["sections"],vectors):
                target="beta" if section["kind"]=="other_tenant" else "retired" if section["kind"]=="retired" else "published"
                if target!=key:continue
                document=identity(key+"/doc/"+section["id"]);chunk=identity(key+"/chunk/"+section["id"])
                conn.execute(text("INSERT INTO resolvelab.documents(tenant_id,corpus_id,document_id,source_label,title,content_sha256,effective_at,access_scope) VALUES(:t,:c,:d,:source,:title,:hash,'2026-11-01T00:00:00Z',:access)"),
                             {"t":tenant,"c":corpus,"d":document,"source":section["id"],"title":section["title"],"hash":sha(section["body"].encode()),"access":"admin" if section["kind"]=="admin" else "support"})
                conn.execute(text("INSERT INTO resolvelab.chunks(tenant_id,corpus_id,document_id,chunk_id,locator,body,embedding,embedding_dimension) VALUES(:t,:c,:d,:ch,:loc,:body,:embedding,:dim)"),
                             {"t":tenant,"c":corpus,"d":document,"ch":chunk,"loc":section["id"],"body":section["body"],"embedding":vector.tolist(),"dim":EMBEDDING_DIMENSION})
            conn.execute(text("UPDATE resolvelab.corpus_versions SET state='published' WHERE tenant_id=:t AND corpus_id=:c"),{"t":tenant,"c":corpus})
            if key=="retired":conn.execute(text("UPDATE resolvelab.corpus_versions SET state='retired' WHERE tenant_id=:t AND corpus_id=:c"),{"t":tenant,"c":corpus})
    return {"tenant_id":tenant_a,"actor_id":actor_a,"role":"operator"},{"tenant_id":tenant_b,"actor_id":actor_b,"role":"operator"},corpora


def main():
    OUT.mkdir(exist_ok=True);path=ROOT/"fixtures/retrieval_benchmark_v2.json"
    fixture=json.loads(path.read_text());fixture_hash=sha(path.read_bytes())
    alpha,beta,corpora=seed(fixture,fixture_hash)
    configurations={"lexical_all_v1":("lexical","all"),"lexical_any_v2":("lexical","any"),"dense_v1":("dense","all"),"hybrid_all_v1":("hybrid","all"),"hybrid_any_v2":("hybrid","any")}
    report={"fixture_sha256":fixture_hash,"physical_sections":fixture["physical_sections"],"permitted_operator_sections":fixture["allowed_operator_sections"],
            "labeling":fixture["labeling"],"configurations":{},"isolation_checks":{},"claim_support_human_review":None,
            "lexical_semantics":"Legacy all uses AND plainto_tsquery and retains zero-score ranks. New any uses parameterized OR websearch terms and excludes zero-score lexical entries from ranking/fusion. No main pipeline configuration is changed.",
            "latency_scope":"Warm retrieval with a new scoped runtime-role transaction per query; local CPU/PostgreSQL. Excludes fixture seed, routing, generation and provider calls."}
    with transaction(alpha) as conn:
        report["runtime_role"]=conn.execute(text("SELECT current_user")).scalar_one()
        report["runtime_bypass_rls"]=conn.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one()
        assert not report["runtime_bypass_rls"]
        assert not retrieve(conn,alpha["tenant_id"],corpora["retired"],"cancellation tracking approval",alpha["role"])
        assert not retrieve(conn,alpha["tenant_id"],corpora["beta"],"BETA-CANARY-SECRET",alpha["role"])
    with transaction(beta) as conn:
        actual_beta=retrieve(conn,beta["tenant_id"],corpora["beta"],"BETA-CANARY-SECRET",beta["role"])
        assert actual_beta and all("BETA-CANARY-SECRET" in row["body"] for row in actual_beta)
    report["isolation_checks"]={"actual_beta_corpus_exists_and_retrievable_as_beta":True,"same_beta_corpus_empty_as_alpha":True,"retired_corpus_empty":True,"admin_beta_retired_canaries_absent_from_operator_rankings":True}
    for name,(mode,lexical) in configurations.items():
        with transaction(alpha) as conn:retrieve(conn,alpha["tenant_id"],corpora["published"],"cancellation approval",alpha["role"],mode,10,lexical)
        rows=[]
        for query in fixture["queries"]:
            tick=time.perf_counter()
            with transaction(alpha) as conn:found=retrieve(conn,alpha["tenant_id"],corpora["published"],query["query"],alpha["role"],mode,10,lexical)
            elapsed=(time.perf_counter()-tick)*1000
            assert all(not any(marker in row["body"] for marker in ["BETA-CANARY-SECRET","ADMIN-CANARY-SECRET","RETIRED-CANARY-SECRET"]) for row in found)
            rank=[row["locator"] for row in found];relevant=set(query["relevant_sections"])
            recall={str(k):len(relevant&set(rank[:k]))/len(relevant) for k in [1,3,5,10]}
            positions=[i+1 for i,item in enumerate(rank) if item in relevant]
            dcg=sum(1/np.log2(i+2) for i,item in enumerate(rank) if item in relevant)
            ideal=sum(1/np.log2(i+2) for i in range(min(len(relevant),10)))
            rows.append({"id":query["id"],"split":query["split"],"recall_at_k":recall,"mrr_at_10":1/min(positions) if positions else 0.,
                         "ndcg_at_10":float(dcg/ideal),"latency_ms":elapsed,"ranked_locators":rank,"chunk_ids":[row["chunk_id"] for row in found]})
        summary={}
        for split in ["dev","test"]:
            chosen=[row for row in rows if row["split"]==split]
            summary[split]={"N":len(chosen),"mean_recall_at_k":{str(k):float(np.mean([row["recall_at_k"][str(k)] for row in chosen])) for k in [1,3,5,10]},
                            "mrr_at_10":float(np.mean([row["mrr_at_10"] for row in chosen])),"ndcg_at_10":float(np.mean([row["ndcg_at_10"] for row in chosen])),
                            "p50_ms":float(np.median([row["latency_ms"] for row in chosen])),"p95_ms":float(np.percentile([row["latency_ms"] for row in chosen],95))}
        report["configurations"][name]={"summary":summary,"queries":rows}
    report["selected_by_dev_only"]=max(configurations,key=lambda name:(report["configurations"][name]["summary"]["dev"]["mean_recall_at_k"]["3"],report["configurations"][name]["summary"]["dev"]["mrr_at_10"],-list(configurations).index(name)))
    report["limitations"]="32 agent-authored synthetic queries,16 per split; no independent human relevance adjudication or representative traffic sample. Ranking comparison does not establish answer factuality or retrieval abstention on unknown topics. Main demo still retains its previous corpus/retrieval release."
    (OUT/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"selected_by_dev":report["selected_by_dev_only"],"test":{name:result["summary"]["test"] for name,result in report["configurations"].items()}}))


if __name__=="__main__":main()
