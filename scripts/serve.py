"""Ownership handshake exposes the real interpreter PID behind Windows venv launchers."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import UUID

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument("--component",choices=["api","worker"],required=True)
parser.add_argument("--instance-id",type=UUID,required=True)
args=parser.parse_args()
runtime=root/".runtime";runtime.mkdir(exist_ok=True)
record={"pid":os.getpid(),"component":args.component,"instance_id":str(args.instance_id),"root":str(root),
        "script":str(Path(__file__).resolve()),"started_at":datetime.now(timezone.utc).isoformat(),"executable":sys.executable}
destination=runtime/(args.component+".process.json")
temporary=runtime/(args.component+"."+str(args.instance_id)+".tmp")
temporary.write_text(json.dumps(record),encoding="utf-8");temporary.replace(destination)
if args.component=="api":
    import uvicorn
    uvicorn.run("resolvelab.api:app",host="127.0.0.1",port=8000,proxy_headers=False)
else:
    from resolvelab.worker import main
    main([])
