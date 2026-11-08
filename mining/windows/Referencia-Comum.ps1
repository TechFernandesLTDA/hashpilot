# Funcoes comuns aos scripts historicos da RTX 3070 de referencia do autor.
# Carregado com: . (Join-Path $PSScriptRoot 'Referencia-Comum.ps1')
# Versoes permitidas e carteira padrao espelham miner_update.CATALOG e
# contribution.AUTHOR_WALLET; test_reference_scripts.py confere a coerencia.
$ReferenceAllowedMinerVersions=@('3.8.0','3.8.1','3.8.3','3.8.4')
$ReferenceDefaultMinerVersion='3.8.0'
$ReferenceAuthorWallet='prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t'

function Assert-ReferenceRtx3070 {
    $name=(& nvidia-smi -i 0 --query-gpu=name --format=csv,noheader)
    if($LASTEXITCODE -ne 0 -or -not $name -or $name.Trim() -notmatch 'RTX 3070$'){
        throw 'Script historico exclusivo da RTX 3070 de referencia. Use o iniciador generico (Iniciar-Rig.ps1) para outras GPUs.'
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

function Get-ReferenceWallet {
    # A carteira vem da configuracao do HashPilot; sem configuracao, a padrao do autor (como no aplicativo).
    $wallet=$ReferenceAuthorWallet
    $settings=Join-Path $env:LOCALAPPDATA 'PearlMonitor\settings.json'
    if(Test-Path -LiteralPath $settings){
        $value=(Get-Content -LiteralPath $settings -Raw -Encoding UTF8 | ConvertFrom-Json).wallet
        if($value){$wallet=([string]$value).Trim()}
    }
    if($wallet -notmatch '^prl1[ac-hj-np-z02-9]{30,100}$'){throw 'Endereco publico Pearl invalido em settings.json'}
    if($wallet -eq $ReferenceAuthorWallet){Write-Host 'Aviso: carteira do autor em uso; os pagamentos irao para o autor.'}
    return $wallet
}
