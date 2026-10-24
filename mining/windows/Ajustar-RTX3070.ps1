$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$results=@()
$selected=1500
$completed=$false
try {
 foreach($clock in @(1500,1650,1800)) {
  if(-not (Get-Process -Name SRBMiner-MULTI -ErrorAction SilentlyContinue)){throw 'Minerador parou'}
  & nvidia-smi -i 0 -lgc "$clock,$clock" | Out-File -LiteralPath 'logs\tuning-actions.txt' -Append
  if($LASTEXITCODE -ne 0){throw 'Falha ao alterar clock'}
  $started=Get-Date
  $temps=@(); $powers=@(); $hot=$false
  for($i=0;$i -lt 18;$i++){
   Start-Sleep -Seconds 5
   $s=(& nvidia-smi -i 0 --query-gpu=temperature.gpu,power.draw --format=csv,noheader,nounits) -split ','
   $temp=[double]::Parse($s[0].Trim(),[Globalization.CultureInfo]::InvariantCulture)
   $power=[double]::Parse($s[1].Trim(),[Globalization.CultureInfo]::InvariantCulture)
   if($i -ge 6){$temps+=$temp; $powers+=$power}
   if($temp -ge 73){$hot=$true;break}
  }
  $file=Get-ChildItem -LiteralPath 'logs' -Filter 'mining-*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  $rates=@()
  foreach($line in (Get-Content -LiteralPath $file.FullName -Tail 100)){
   if($line -match '^\[(?<time>[^\]]+)\] GPU0 RTX 3070: (?<rate>[0-9.]+) TH/s'){
    $timestamp=[datetime]::ParseExact($Matches.time,'yyyy-MM-dd HH:mm:ss',[Globalization.CultureInfo]::InvariantCulture)
    $rate=[double]::Parse($Matches.rate,[Globalization.CultureInfo]::InvariantCulture)
    if($timestamp -ge $started.AddSeconds(25)){$rates+=$rate}
   }
  }
  $row=[pscustomobject]@{ClockMHz=$clock;HashrateTHs=if($rates.Count){[math]::Round(($rates|Measure-Object -Average).Average,2)}else{0};AverageWatts=if($powers.Count){[math]::Round(($powers|Measure-Object -Average).Average,1)}else{0};MaxTempC=if($temps.Count){($temps|Measure-Object -Maximum).Maximum}else{73};TooHot=$hot}
  $results+=$row
  $results | ConvertTo-Json | Set-Content -LiteralPath 'logs\tuning-results.json' -Encoding UTF8
  if($hot){& nvidia-smi -i 0 -lgc '1500,1500' | Out-Null}
 }
 $best=$results | Where-Object {$_.HashrateTHs -gt 0 -and -not $_.TooHot -and $_.MaxTempC -le 70} | Sort-Object HashrateTHs -Descending | Select-Object -First 1
 if($best){$selected=[int]$best.ClockMHz}
 $selected | Set-Content -LiteralPath 'logs\selected-clock.txt'
 $completed=$true
} catch {
 $_.Exception.Message | Set-Content -LiteralPath 'logs\tuning-error.txt'
} finally {
 & nvidia-smi -i 0 -lgc "$selected,$selected" | Out-File -LiteralPath 'logs\tuning-actions.txt' -Append
 'Concluido: '+$completed+' Clock final: '+$selected | Set-Content -LiteralPath 'logs\tuning-status.txt'
}
