param([string]$Wallet,[string]$Python)
$ErrorActionPreference='Stop'
if($Wallet){
 if($Wallet -notmatch '^prl1[ac-hj-np-z02-9]{30,100}$'){throw 'Endereco publico Pearl invalido.'}
 $state=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
 New-Item -ItemType Directory -Path $state -Force | Out-Null
 $path=Join-Path $state 'settings.json'
 $settings=if(Test-Path -LiteralPath $path){Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json}else{[pscustomobject]@{}}
 $settings | Add-Member -NotePropertyName wallet -NotePropertyValue $Wallet -Force
 $temporary=$path+'.rig-launch.tmp'
 [IO.File]::WriteAllText($temporary,($settings|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
 Move-Item -LiteralPath $temporary -Destination $path -Force
}
& (Join-Path $PSScriptRoot 'Iniciar-Rig.ps1') -Python $Python