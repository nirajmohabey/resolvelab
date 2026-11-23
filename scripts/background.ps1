param([ValidateSet('start','stop')][string]$Task='start')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$pythonPath=Join-Path $projectRoot '.venv\Scripts\python.exe'
New-Item -ItemType Directory -Force -Path '.runtime' | Out-Null
foreach($name in @('api','worker')) {
  $pidFile=Join-Path $projectRoot ".runtime\$name.pid"
  $ownedProcess=$null
  if(Test-Path -LiteralPath $pidFile) {
    $taskProcessId=[int](Get-Content -LiteralPath $pidFile)
    $details=Get-CimInstance Win32_Process -Filter "ProcessId = $taskProcessId" -ErrorAction SilentlyContinue
    if($details -and $details.CommandLine.Contains($projectRoot) -and $details.CommandLine.Contains('resolvelab.')) { $ownedProcess=Get-Process -Id $taskProcessId -ErrorAction SilentlyContinue }
  }
  if($Task -eq 'stop') {
    if($ownedProcess) { Stop-Process -Id $ownedProcess.Id }
    continue
  }
  if($ownedProcess) { continue }
  $arguments=if($name -eq 'api') { @('-m','uvicorn','resolvelab.api:app','--host','127.0.0.1','--port','8000') } else { @('-m','resolvelab.worker') }
  $started=Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput ".runtime\$name.log" -RedirectStandardError ".runtime\$name.err" -PassThru
  $started.Id | Set-Content -LiteralPath $pidFile
}
Write-Output "Local API and worker: $Task. PostgreSQL is managed separately by scripts/local.ps1."
