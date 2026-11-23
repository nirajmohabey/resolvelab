param([ValidateSet('setup','start','stop','train','seed','reset','test','evaluate','run','worker','backup-check')][string]$Task='run')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
if ($Task -eq 'setup') { uv sync --frozen; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
$pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run uv sync --frozen first.' }
switch ($Task) {
  'setup' { & $pythonPath scripts/bootstrap.py; if ($LASTEXITCODE) { exit $LASTEXITCODE }; & $pythonPath scripts/train_all.py; if ($LASTEXITCODE) { exit $LASTEXITCODE }; & $pythonPath -m resolvelab.seed }
  'start' { & $pythonPath scripts/bootstrap.py }
  'stop' { & $pythonPath scripts/bootstrap.py --stop }
  'train' { & $pythonPath scripts/train_all.py }
  'seed' { & $pythonPath -m resolvelab.seed }
  'reset' { & $pythonPath -m resolvelab.seed --reset }
  'test' { & $pythonPath scripts/run_tests.py }
  'evaluate' { & $pythonPath scripts/evaluate.py }
  'run' { & $pythonPath -m uvicorn resolvelab.api:app --host 127.0.0.1 --port 8000 }
  'worker' { & $pythonPath -m resolvelab.worker }
  'backup-check' { & $pythonPath scripts/backup_check.py }
}
exit $LASTEXITCODE
