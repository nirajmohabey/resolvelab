"""Measure each frozen router in an isolated CPU process on the same messages."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"scripts"))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--artifact")
    args=parser.parse_args()
    if args.artifact:
        import numpy as np
        from resolvelab.ml import Router
        from train_challenger import peak_rss_mb
        rows=[json.loads(line) for line in (ROOT/"fixtures/independent_routing_cases.jsonl").read_text().splitlines()]
        tick=time.perf_counter();cpu=time.process_time()
        router=Router(args.artifact)
        router.predict(rows[0]["message"])
        cold=(time.perf_counter()-tick)*1000
        latencies=[]
        for row in rows:
            tick=time.perf_counter();router.predict(row["message"]);latencies.append((time.perf_counter()-tick)*1000)
        print(json.dumps({"version":router.version,"N":len(rows),"cold_load_and_first_query_ms":cold,
                          "warm_p50_ms":float(np.median(latencies)),"warm_p95_ms":float(np.percentile(latencies,95)),
                          "cpu_seconds_including_cold_load":time.process_time()-cpu,"peak_process_rss_mb":peak_rss_mb()}))
        return
    manifest=json.loads((ROOT/"artifacts/model_manifest.json").read_text())
    report={"scope":"Separate fresh Python process per artifact. Same original80 diagnostic messages, routing only; no DB or generator. Peak process RSS includes interpreter/libraries. CPU only; local monetary compute cost not estimated.","models":{}}
    for name,path in [("tfidf_v1",ROOT/"artifacts/router-tfidf-v1.joblib"),("selected",ROOT/manifest["artifact_uri"])]:
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),"--artifact",str(path)],cwd=ROOT,check=True,capture_output=True,text=True)
        report["models"][name]=json.loads(result.stdout)
    (ROOT/"artifacts/routing_latency.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":
    main()
