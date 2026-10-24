Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.ProcessId -ne $PID -and $_.CommandLine -like ('*C:\Users\renan\AppData\Local\Mining\Pearl\Iniciar-Pearl.ps1*')} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$miner=Join-Path $PSScriptRoot 'SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe'
$results=@();$minerProcess=$null
 Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.CommandLine -like ('*'+$PSScriptRoot+'\Desempenho-Pearl.ps1*')} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
try {
 Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.CommandLine -like ('*'+$PSScriptRoot+'\Ajustar-RTX3070.ps1*')} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
 Get-CimInstance Win32_Process -Filter "Name='SRBMiner-MULTI.exe'" | Where-Object {$_.ExecutablePath -eq $miner} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
 Start-Sleep -Seconds 4
 & nvidia-smi -i 0 -pl 240 | Out-File 'logs\turbo-actions.txt' -Append
 if($LASTEXITCODE -ne 0){throw 'Falha no power limit'}
 $log=Join-Path $PSScriptRoot ('logs\turbo-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
 $arguments=@('--disable-cpu','--disable-msr-tweaks','--algorithm-gpu','pearlhash','--gpu-id','0','--pool','prl.kryptex.network:7048','--wallet','prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t','--worker','RenanRTX3070','--gpu-off-temperature','78','--gpu-mclock0','5001','--gpu-coffset0','225','--gpu-fan0','100','--gpu-cclock0','1740','--log-file',$log)
 $minerProcess=Start-Process -FilePath $miner -ArgumentList $arguments -WorkingDirectory (Split-Path $miner) -WindowStyle Hidden -PassThru
 $minerProcess.Id | Set-Content 'logs\turbo-miner.pid'
 Start-Sleep -Seconds 25
 foreach($clock in @(1740,1800,1860)){
  $minerProcess.Refresh();if($minerProcess.HasExited){throw 'Minerador encerrou'}
  & nvidia-smi -i 0 -lgc "$clock,$clock" | Out-File 'logs\turbo-actions.txt' -Append
  if($LASTEXITCODE -ne 0){throw 'Falha no clock'}
  $started=Get-Date;$temps=@();$powers=@();$actualClocks=@();$hot=$false
  for($i=0;$i -lt 18;$i++){
   Start-Sleep -Seconds 5
   $s=(& nvidia-smi -i 0 --query-gpu=temperature.gpu,power.draw,clocks.gr --format=csv,noheader,nounits) -split ','
   $temp=[double]::Parse($s[0].Trim(),[Globalization.CultureInfo]::InvariantCulture)
   if($i -ge 6){$temps+=$temp;$powers+=[double]::Parse($s[1].Trim(),[Globalization.CultureInfo]::InvariantCulture);$actualClocks+=[double]$s[2].Trim()}
   if($temp -ge 78){$hot=$true;break}
   $minerProcess.Refresh();if($minerProcess.HasExited){throw 'Minerador encerrou durante teste'}
  }
  $rates=@();$hardwareErrors=0;$rejects=0
  foreach($line in (Get-Content -LiteralPath $log -Tail 150)){
   if($line -match '^\[(?<time>[^\]]+)\] GPU0 RTX 3070: (?<rate>[0-9.]+) TH/s.* R:(?<rejects>\d+) HW:(?<hw>\d+)'){
    $timestamp=[datetime]::ParseExact($Matches.time,'yyyy-MM-dd HH:mm:ss',[Globalization.CultureInfo]::InvariantCulture)
    if($timestamp -ge $started.AddSeconds(25)){$rates+=[double]::Parse($Matches.rate,[Globalization.CultureInfo]::InvariantCulture);$hardwareErrors=[int]$Matches.hw;$rejects=[int]$Matches.rejects}
   }
  }
  $row=[pscustomobject]@{ClockMHz=$clock;HashrateTHs=if($rates.Count){[math]::Round(($rates|Measure-Object -Average).Average,2)}else{0};AverageWatts=if($powers.Count){[math]::Round(($powers|Measure-Object -Average).Average,1)}else{0};MaxTempC=if($temps.Count){($temps|Measure-Object -Maximum).Maximum}else{78};ActualClockMHz=if($actualClocks.Count){[math]::Round(($actualClocks|Measure-Object -Average).Average)}else{0};TooHot=$hot;HardwareErrors=$hardwareErrors;Rejects=$rejects}
  $results+=$row;$results|ConvertTo-Json|Set-Content 'logs\turbo-results.json' -Encoding UTF8
  if($hot -or $hardwareErrors -gt 0){break}
 }
 $best=$results|Where-Object {$_.HashrateTHs -gt 0 -and -not $_.TooHot -and $_.MaxTempC -le 76 -and $_.HardwareErrors -eq 0}|Sort-Object HashrateTHs -Descending|Select-Object -First 1
 if(-not $best){throw 'Nenhum perfil aprovado'}
 & nvidia-smi -i 0 -lgc "$($best.ClockMHz),$($best.ClockMHz)" | Out-File 'logs\turbo-actions.txt' -Append
 $best | ConvertTo-Json | Set-Content 'logs\turbo-selected.json' -Encoding UTF8
 'Validando perfil vencedor'|Set-Content 'logs\turbo-status.txt'
 $minerProcess.WaitForExit()
} catch {
 $_.Exception.Message|Set-Content 'logs\turbo-error.txt'
 if($minerProcess -and -not $minerProcess.HasExited){Stop-Process -Id $minerProcess.Id -Force}
} finally {
 & nvidia-smi -i 0 -rgc | Out-File 'logs\turbo-actions.txt' -Append
 & nvidia-smi -i 0 -rmc | Out-File 'logs\turbo-actions.txt' -Append
 & nvidia-smi -i 0 -pl 240 | Out-File 'logs\turbo-actions.txt' -Append
 'Encerrado e clocks restaurados'|Set-Content 'logs\turbo-status.txt'
}
