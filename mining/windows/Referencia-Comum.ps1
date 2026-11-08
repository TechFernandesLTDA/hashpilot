# Funcoes comuns aos scripts historicos da RTX 3070 de referencia do autor.
# Carregado com: . (Join-Path $PSScriptRoot 'Referencia-Comum.ps1')
# Versoes permitidas, carteira padrao e UUID espelham miner_update.CATALOG,
# contribution.AUTHOR_WALLET e reference_profile.REFERENCE_RTX3070_UUID;
# test_reference_scripts.py confere a coerencia.
$ReferenceAllowedMinerVersions=@('3.8.0','3.8.1','3.8.3','3.8.4')
$ReferenceDefaultMinerVersion='3.8.0'
$ReferenceAuthorWallet='prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t'
$ReferenceRtx3070Uuid='GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75'
$ReferenceMinerRecord='logs\reference-miners.txt'

function Assert-ReferenceRtx3070 {
    # Mesmo criterio de reference_profile.reference_device_confirmed: a placa fisica do autor
    # (UUID exato) com limite de fabrica de 240 W. Outra RTX 3070 nao recebe os clocks medidos.
    $row=[string](& nvidia-smi -i 0 --query-gpu=name,uuid,power.default_limit --format=csv,noheader,nounits)
    if($LASTEXITCODE -ne 0 -or -not $row){throw 'nvidia-smi indisponivel; a GPU de referencia nao foi confirmada.'}
    $fields=@($row.Split(',') | ForEach-Object {$_.Trim()})
    $power=0.0
    $parsed=($fields.Count -eq 3) -and [double]::TryParse($fields[2],[Globalization.NumberStyles]::Float,[Globalization.CultureInfo]::InvariantCulture,[ref]$power)
    if(-not $parsed -or $fields[0] -notmatch 'RTX 3070$' -or $fields[1] -ne $ReferenceRtx3070Uuid -or [math]::Abs($power-240) -gt 0.5){
        throw 'Script historico exclusivo da RTX 3070 de referencia do autor (UUID e limite de 240 W). Use o iniciador generico (Iniciar-Rig.ps1) para outras GPUs.'
    }
}

function Get-ReferenceMinerPath([string]$Base=$PSScriptRoot) {
    # Mesma selecao do aplicativo: miner-install.json, ou a versao base instalada.
    $version=$ReferenceDefaultMinerVersion
    $record=Join-Path $Base 'miner-install.json'
    if(Test-Path -LiteralPath $record){
        $saved=(Get-Content -LiteralPath $record -Raw -Encoding UTF8 | ConvertFrom-Json).version
        if($saved){$version=[string]$saved}
    }
    if($version -notin $ReferenceAllowedMinerVersions){throw ('Versao do minerador nao revisada pelo HashPilot: '+$version)}
    $folder='SRBMiner-Multi-'+$version.Replace('.','-')
    return (Join-Path (Join-Path $Base $folder) 'SRBMiner-MULTI.exe')
}

function Register-ReferenceMiner([string]$Path,[string]$Base=$PSScriptRoot) {
    # Anota o executavel iniciado ANTES de inicia-lo: o Parar encerra este caminho mesmo
    # que a versao selecionada no aplicativo mude enquanto o minerador roda.
    $record=Join-Path $Base $ReferenceMinerRecord
    New-Item -ItemType Directory -Path (Split-Path $record) -Force | Out-Null
    $known=@()
    if(Test-Path -LiteralPath $record){$known=@(Get-Content -LiteralPath $record -Encoding UTF8)}
    if($known -notcontains $Path){Add-Content -LiteralPath $record -Value $Path -Encoding UTF8}
}

function Get-ReferenceMinerPaths([string]$Base=$PSScriptRoot) {
    # Versao selecionada agora + executaveis anotados por Register-ReferenceMiner, so dentro de $Base.
    $paths=@()
    try{$paths+=Get-ReferenceMinerPath $Base}catch{Write-Host $_.Exception.Message}
    $record=Join-Path $Base $ReferenceMinerRecord
    if(Test-Path -LiteralPath $record){
        $root=[IO.Path]::GetFullPath($Base).TrimEnd('\')+'\'
        foreach($line in @(Get-Content -LiteralPath $record -Encoding UTF8)){
            $candidate=([string]$line).Trim()
            if($candidate -match '\\SRBMiner-Multi-\d+-\d+-\d+\\SRBMiner-MULTI\.exe$' -and $candidate.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){$paths+=$candidate}
        }
    }
    return @($paths | Select-Object -Unique)
}

function Get-ReferenceSettings {
    $path=Join-Path $env:LOCALAPPDATA 'PearlMonitor\settings.json'
    if(Test-Path -LiteralPath $path){return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)}
    return $null
}

function Assert-ReferenceContribution([string]$Wallet) {
    # A contribuicao opcional (57 min usuario / 3 min autor) so existe pelo proxy do servico do rig.
    # O fluxo historico minera direto na pool; com a opcao ativa, recusa em vez de ignora-la.
    $settings=Get-ReferenceSettings
    if($settings -and $settings.support_enabled -eq $true -and $Wallet -ne $ReferenceAuthorWallet){
        throw 'Contribuicao opcional ativada: o fluxo historico nao passa pelo proxy de contribuicao. Use o servico do rig (Iniciar-Rig.ps1) ou desative a contribuicao nas Configuracoes.'
    }
}

function Get-ReferenceWallet {
    # Mesma regra de rig_config.resolve_devices: carteira propria da GPU de referencia,
    # senao a global; sem configuracao, a padrao do autor (como no aplicativo).
    $wallet=$ReferenceAuthorWallet
    $settings=Get-ReferenceSettings
    if($settings){
        if($settings.wallet){$wallet=([string]$settings.wallet).Trim()}
        $gpuSettings=$settings.gpu_settings
        if($gpuSettings -and $gpuSettings.PSObject.Properties[$ReferenceRtx3070Uuid]){
            $override=$gpuSettings.PSObject.Properties[$ReferenceRtx3070Uuid].Value.wallet
            if($override){$wallet=([string]$override).Trim()}
        }
    }
    if($wallet -notmatch '^prl1[ac-hj-np-z02-9]{30,100}$'){throw 'Endereco publico Pearl invalido em settings.json'}
    Assert-ReferenceContribution $wallet
    if($wallet -eq $ReferenceAuthorWallet){Write-Host 'Aviso: carteira do autor em uso; os pagamentos irao para o autor.'}
    return $wallet
}
