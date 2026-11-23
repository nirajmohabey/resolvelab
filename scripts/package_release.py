"""Archive tracked source/artifacts plus the actual browser recording; never .env."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import zipfile

root=Path(__file__).resolve().parents[1]
output=root.parent
videos=sorted((root/'artifacts/browser').glob('*.webm'),key=lambda p:p.stat().st_mtime)
if not videos:raise SystemExit('Run the browser acceptance suite first.')
demo=output/'ResolveLab_Demo.webm';shutil.copy2(videos[-1],demo)
preview=output/'ResolveLab_Preview.png';shutil.copy2(root/'artifacts/browser/03-proposal.png',preview)
archive=output/'ResolveLab_Complete.zip'
subprocess.run(['git','archive','--format=zip','--prefix=resolvelab/','--output='+str(archive),'HEAD'],cwd=root,check=True)
with zipfile.ZipFile(archive,'a',compression=zipfile.ZIP_DEFLATED) as pack:
    pack.write(demo,'resolvelab/artifacts/browser/ResolveLab_Demo.webm')
    mp4=output/'ResolveLab_Demo.mp4'
    if mp4.exists():pack.write(mp4,'resolvelab/artifacts/browser/ResolveLab_Demo.mp4')
    for name in pack.namelist():
        parts=Path(name).parts
        assert not any(p in {'.git','.runtime','.venv','node_modules','work'} for p in parts),name
        assert not any(p.startswith('.env') and p!='.env.example' for p in parts),name
deliverables=[archive,demo,preview,*[output/name for name in ['ResolveLab_Demo.mp4','ResolveLab_Resume.pdf','ResolveLab_Resume_Source.txt','ResolveLab_Sharing_Pack.txt'] if (output/name).exists()]]
checks={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in deliverables}
(output/'ResolveLab_Checksums.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
print(json.dumps(checks,indent=2))
