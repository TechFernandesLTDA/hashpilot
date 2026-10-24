Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.CommandLine -like ('*C:\Users\renan\AppData\Local\Mining\Pearl\Maximo-Pearl.ps1*')} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$results=@()
$selected=1650
$completed=$false
try {
 foreach($clock in @(1710,1740,1770)) {
  if(-not (Get-Process -Name SRBMiner-MULTI -ErrorAction SilentlyContinue)){throw 'Minerador parou'}
  & nvidia-smi -i 0 -lgc "$clock,$clock" | Out-File -LiteralPath 'logs\fine-actions.txt' -Append
  if($LASTEXITCODE -ne 0){throw 'Falha ao alterar clock'}
  $started=Get-Date
  $temps=@(); $powers=@(); $hot=$false
  for($i=0;$i -lt 15;$i++){
   Start-Sleep -Seconds 5
   $s=(& nvidia-smi -i 0 --query-gpu=temperature.gpu,power.draw --format=csv,noheader,nounits) -split ','
   $temp=[double]::Parse($s[0].Trim(),[Globalization.CultureInfo]::InvariantCulture)
   $power=[double]::Parse($s[1].Trim(),[Globalization.CultureInfo]::InvariantCulture)
   if($i -ge 6){$temps+=$temp; $powers+=$power}
   if($temp -ge 77){$hot=$true;break}
  }
  $file=Get-ChildItem -LiteralPath 'logs' -Filter 'maximum-*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  $rates=@()
  foreach($line in (Get-Content -LiteralPath $file.FullName -Tail 100)){
   if($line -match '^\[(?<time>[^\]]+)\] GPU0 RTX 3070: (?<rate>[0-9.]+) TH/s'){
    $timestamp=[datetime]::ParseExact($Matches.time,'yyyy-MM-dd HH:mm:ss',[Globalization.CultureInfo]::InvariantCulture)
    $rate=[double]::Parse($Matches.rate,[Globalization.CultureInfo]::InvariantCulture)
    if($timestamp -ge $started.AddSeconds(25)){$rates+=$rate}
   }
  }
  $row=[pscustomobject]@{ClockMHz=$clock;HashrateTHs=if($rates.Count){[math]::Round(($rates|Measure-Object -Average).Average,2)}else{0};AverageWatts=if($powers.Count){[math]::Round(($powers|Measure-Object -Average).Average,1)}else{0};MaxTempC=if($temps.Count){($temps|Measure-Object -Maximum).Maximum}else{77};TooHot=$hot}
  $results+=$row
  $results | ConvertTo-Json | Set-Content -LiteralPath 'logs\fine-results.json' -Encoding UTF8
  if($hot){& nvidia-smi -i 0 -lgc '1650,1650' | Out-Null}
 }
 $best=$results | Where-Object {$_.HashrateTHs -gt 0 -and -not $_.TooHot -and $_.MaxTempC -le 75} | Sort-Object HashrateTHs -Descending | Select-Object -First 1
 if($best){$selected=[int]$best.ClockMHz}
 $selected | Set-Content -LiteralPath 'logs\selected-clock.txt'
 $completed=$true
} catch {
 $_.Exception.Message | Set-Content -LiteralPath 'logs\fine-error.txt'
} finally {
 & nvidia-smi -i 0 -lgc "$selected,$selected" | Out-File -LiteralPath 'logs\fine-actions.txt' -Append
 'Concluido: '+$completed+' Clock final: '+$selected | Set-Content -LiteralPath 'logs\fine-status.txt'
}
