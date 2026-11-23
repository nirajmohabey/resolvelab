param([ValidateSet('start','stop','status')][string]$Task='start')
$ErrorActionPreference='Stop'
$projectRoot=(Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path
Set-Location -LiteralPath $projectRoot
$pythonPath=Join-Path $projectRoot '.venv\Scripts\python.exe'
$runnerPath=Join-Path $projectRoot 'scripts\serve.py'
New-Item -ItemType Directory -Force -Path '.runtime' | Out-Null

function Get-OwnedInterpreter($component, $recordPath, $legacyPath) {
  if(Test-Path -LiteralPath $recordPath) {
    $record=Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $detail=Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.pid)" -ErrorAction SilentlyContinue
    if($detail -and $record.root -eq $projectRoot -and $record.component -eq $component -and
       $detail.CommandLine.Contains($runnerPath) -and $detail.CommandLine.Contains([string]$record.instance_id) -and
       $detail.CommandLine.Contains("--component $component")) { return $detail }
  }
  if(Test-Path -LiteralPath $legacyPath) {
    $legacyId=[int](Get-Content -LiteralPath $legacyPath)
    $expected=if($component -eq 'api') {'resolvelab.api:app'} else {'resolvelab.worker'}
    $direct=Get-CimInstance Win32_Process -Filter "ProcessId = $legacyId" -ErrorAction SilentlyContinue
    if($direct -and $direct.CommandLine.Contains($pythonPath) -and $direct.CommandLine.Contains($expected)) { return $direct }
    $written=(Get-Item -LiteralPath $legacyPath).LastWriteTimeUtc
    $children=Get-CimInstance Win32_Process -Filter "ParentProcessId = $legacyId" -ErrorAction SilentlyContinue
    foreach($child in $children) {
      if($child.Name -eq 'python.exe' -and $child.CommandLine.Contains($expected) -and
         [Math]::Abs(($child.CreationDate.ToUniversalTime()-$written).TotalSeconds) -lt 15) { return $child }
    }
  }
  return $null
}

foreach($component in @('api','worker')) {
  $recordPath=Join-Path $projectRoot ".runtime\$component.process.json"
  $legacyPath=Join-Path $projectRoot ".runtime\$component.pid"
  $owned=Get-OwnedInterpreter $component $recordPath $legacyPath
  if($Task -eq 'status') { Write-Output "$component`: $(if($owned){'running '+$owned.ProcessId}else{'stopped'})"; continue }
  if($Task -eq 'stop') {
    if($owned) {
      & taskkill.exe /PID ([string]$owned.ProcessId) /T /F | Out-Null
      if($LASTEXITCODE -ne 0) { throw "Could not stop owned $component process tree" }
    }
    Remove-Item -LiteralPath $recordPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $legacyPath -Force -ErrorAction SilentlyContinue
    continue
  }
  if($owned) { Write-Output "$component already running: $($owned.ProcessId)"; continue }
  $instanceId=[guid]::NewGuid().ToString()
  $arguments=@(('"'+$runnerPath+'"'),'--component',$component,'--instance-id',$instanceId)
  $launcher=Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput ".runtime\$component.log" -RedirectStandardError ".runtime\$component.err" -PassThru
  $deadline=[DateTime]::UtcNow.AddSeconds(15)
  do {
    Start-Sleep -Milliseconds 100
    $owned=Get-OwnedInterpreter $component $recordPath $legacyPath
    if([DateTime]::UtcNow -gt $deadline) { throw "No interpreter ownership handshake for $component; inspect .runtime logs" }
  } while(-not $owned)
  Write-Output "$component started: real interpreter $($owned.ProcessId), launcher $($launcher.Id)"
}
