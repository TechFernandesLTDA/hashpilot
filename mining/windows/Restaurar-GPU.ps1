$ErrorActionPreference='Continue'
$miner=Join-Path $PSScriptRoot 'SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe'
$argsList=@('--gpu-reset-oc','--disable-cpu','--disable-msr-tweaks','--algorithm-gpu','pearlhash','--gpu-id','0','--pool','127.0.0.1:1','--wallet','prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t','--worker','RenanRTX3070Reset','--gpu-off-temperature','78','--log-file',(Join-Path $PSScriptRoot 'logs\reset.log'))
$p=Start-Process -FilePath $miner -ArgumentList $argsList -WorkingDirectory (Split-Path $miner) -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 8
if(-not $p.HasExited){Stop-Process -Id $p.Id -Force}
& nvidia-smi -i 0 -rgc
& nvidia-smi -i 0 -rmc
& nvidia-smi -i 0 -pl 240
Start-Sleep -Seconds 2
