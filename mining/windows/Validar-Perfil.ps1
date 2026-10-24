$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$clock=1740;$samples=@();$thermalFallback=$false
& nvidia-smi -i 0 -lgc '1740,1740' | Out-File 'logs\validation-actions.txt'
if($LASTEXITCODE -ne 0){throw 'Falha ao aplicar clock'}
$started=Get-Date
for($i=0;$i -lt 45;$i++){
 Start-Sleep -Seconds 4
 if(-not (Get-Process -Name SRBMiner-MULTI -ErrorAction SilentlyContinue)){throw 'Minerador parou'}
 $s=(& nvidia-smi -i 0 --query-gpu=temperature.gpu,power.draw,clocks.gr --format=csv,noheader,nounits) -split ','
 $t=[double]::Parse($s[0].Trim(),[Globalization.CultureInfo]::InvariantCulture)
 $samples+=[pscustomobject]@{Time=(Get-Date).ToString('o');TempC=$t;Watts=[double]::Parse($s[1].Trim(),[Globalization.CultureInfo]::InvariantCulture);Clock=[double]$s[2].Trim()}
 if($t -ge 77){$clock=1710;$thermalFallback=$true;& nvidia-smi -i 0 -lgc '1710,1710' | Out-File 'logs\validation-actions.txt' -Append}
}
$log=Get-ChildItem 'logs\maximum-*.log'|Sort-Object LastWriteTime -Descending|Select-Object -First 1
$rates=@();$accepted=0;$rejected=0;$hw=0;$pauses=0
foreach($line in (Get-Content -LiteralPath $log.FullName -Tail 200)){
 if($line -match '^\[(?<time>[^\]]+)\]'){
  $ts=[datetime]::ParseExact($Matches.time,'yyyy-MM-dd HH:mm:ss',[Globalization.CultureInfo]::InvariantCulture)
  if($ts -lt $started.AddSeconds(20)){continue}
  if($line -like '*GPU0 turned off*'){$pauses++}
  if($line -match 'GPU0 RTX 3070: (?<rate>[0-9.]+) TH/s.* A:(?<a>\d+) R:(?<r>\d+) HW:(?<hw>\d+)'){$rates+=[double]::Parse($Matches.rate,[Globalization.CultureInfo]::InvariantCulture);$accepted=[int]$Matches.a;$rejected=[int]$Matches.r;$hw=[int]$Matches.hw}
 }
}
if($hw -gt 0 -or $pauses -gt 0){$clock=1710;& nvidia-smi -i 0 -lgc '1710,1710' | Out-File 'logs\validation-actions.txt' -Append}
$result=[pscustomobject]@{CoreClock=$clock;CoreOffset=150;MemoryClock=5001;PowerLimit=240;FanPercent=100;ThermalPauseC=78;ThermalFallback=$thermalFallback;Started=$started.ToString('o');Finished=(Get-Date).ToString('o');AverageHashrateTHs=if($rates.Count){[math]::Round(($rates|Measure-Object -Average).Average,2)}else{$null};AverageWatts=[math]::Round(($samples.Watts|Measure-Object -Average).Average,1);MaxTempC=($samples.TempC|Measure-Object -Maximum).Maximum;AcceptedTotal=$accepted;RejectedTotal=$rejected;HardwareErrors=$hw;ThermalPauses=$pauses}
$result|ConvertTo-Json|Set-Content 'logs\validated-profile.json' -Encoding UTF8
$samples|ConvertTo-Json|Set-Content 'logs\validation-sensors.json' -Encoding UTF8
'Validacao concluida'|Set-Content 'logs\validation-status.txt'
