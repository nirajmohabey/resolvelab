"""Publish only synthetic fixture measurement summaries to the local dashboard."""
import json
from pathlib import Path
import shutil

root=Path(__file__).resolve().parents[1]
target=root/'web/reports';target.mkdir(exist_ok=True)
workflow=json.loads((root/'artifacts/evaluation/workflow-evaluation.json').read_text())
blind=json.loads((root/'artifacts/v3-blind-final/workflow-evaluation.json').read_text())
manifest=json.loads((root/'artifacts/model_manifest.json').read_text())
summary={'workflow':{'metrics':workflow['metrics'],'generated_at':workflow['generated_at']},'model':{'version':manifest['version'],'source_macro_f1':manifest['metrics']['source_test']['macro_f1'],'independent_macro_f1':blind['routing_metrics']['macro_f1'],'independent_count':blind['routing_metrics']['N'],'independent_workflow_success':blind['metrics']['workflow_success']}}
(target/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
report={'active_model':manifest,'independent_final':{'dataset_sha256':blind['dataset_sha256'],'routing_metrics':blind['routing_metrics'],'workflow_metrics':blind['metrics'],'limitations':blind['limitations']}}
(target/'model_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
for source,name in [(root/'artifacts/evaluation/workflow-evaluation.html','workflow-evaluation.html'),(root/'artifacts/v3-blind-final/workflow-evaluation.html','independent-evaluation.html')]:
    shutil.copyfile(source,target/name)
print('Packaged synthetic regression and independent evaluation evidence for the dashboard.')
