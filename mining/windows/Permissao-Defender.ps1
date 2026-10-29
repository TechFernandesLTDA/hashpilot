# Optional, administrator-confirmed exclusion for one pinned miner executable.
[CmdletBinding()]
param([ValidateSet('Add','Remove')][string]$Action='Add',[string]$UserLocalAppData=$env:LOCALAPPDATA,[ValidateSet('3.8.0','3.8.1')][string]$Version='3.8.0')
$ErrorActionPreference='Stop'
$UserLocalAppData=[IO.Path]::GetFullPath($UserLocalAppData)
if($UserLocalAppData -notmatch '^[A-Za-z]:\\' -or $UserLocalAppData.Contains('"')){throw 'AppData local invalido.'}

function Assert-NoReparsePoint([string]$Path) {
    $current=[IO.Path]::GetFullPath($Path)
    while($current){
        if(Test-Path -LiteralPath $current){
            if((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Caminho redirecionado (link/junction) recusado.'}
        }
        $parent=Split-Path -Parent $current
        if($parent -eq $current){break};$current=$parent
    }
}

function Get-MinerVersion {
    if(-not $Version){return '3.8.0'}
    if($Version -notin @('3.8.0','3.8.1')){throw 'Versao nao permitida'}
    return $Version
}

function Get-VerifiedMinerTarget {
    $Version=Get-MinerVersion
    $exeHash=if($Version -eq '3.8.1'){'2f7e60a17fcc65ddd3c7f5821666a6ae11ef6c1f2c95331bb7e46793c032b688'}else{'6eb4e0bc37d65fdf7644ab620737e14f4c7dfa9666acde43d5f38ab1e9094ac1'}
    $archiveHash=if($Version -eq '3.8.1'){'16bd455557bd3698e004d1f6b5d19290b9b59e9f9c8ef9245acafdaeb2413e79'}else{'33eef5f8946259290013ce370a746a85f91c2bbcd81fcefa1aad904dd9a912f8'}
    $folder='SRBMiner-Multi-'+(Get-MinerVersion).Replace('.','-')
    $base=[IO.Path]::GetFullPath((Join-Path $UserLocalAppData 'Mining\Pearl'))
    $target=Join-Path $base ($folder+'\SRBMiner-MULTI.exe')
    Assert-NoReparsePoint $target
    if((Test-Path -LiteralPath $target) -and -not (Test-Path -LiteralPath $target -PathType Leaf)){throw 'O destino deve ser um arquivo, nunca uma pasta.'}
    if(Test-Path -LiteralPath $target -PathType Leaf){
        if((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $exeHash){throw 'Executavel diferente do hash fixado. Excecao recusada.'}
    }else{
        $archive=Join-Path $base ($folder+'-win64.zip')
        Assert-NoReparsePoint $archive
        if(-not (Test-Path -LiteralPath $archive -PathType Leaf) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $archiveHash){throw 'Arquivo ausente e pacote oficial verificado indisponivel. Excecao recusada.'}
    }
    return $target
}

function Set-MinerException([string]$Operation) {
    # Removal must remain possible even after the binary/package is removed.
    $target=Join-Path $UserLocalAppData ('Mining\Pearl\SRBMiner-Multi-'+(Get-MinerVersion).Replace('.','-')+'\SRBMiner-MULTI.exe')
    if($Operation -eq 'Add'){
        $target=Get-VerifiedMinerTarget
        Add-MpPreference -ExclusionPath $target -ErrorAction Stop
        if(@((Get-MpPreference).ExclusionPath) -notcontains $target){throw 'O Defender nao confirmou a excecao; uma politica pode ter impedido a alteracao.'}
    }elseif($Operation -eq 'Remove'){
        Remove-MpPreference -ExclusionPath $target -ErrorAction Stop
        if(@((Get-MpPreference).ExclusionPath) -contains $target){throw 'O Defender ainda informa a excecao; remocao nao confirmada.'}
    }else{throw 'Operacao invalida.'}
}

try {
    $principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
        $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Action '+$Action+' -Version '+$Version+' -UserLocalAppData "'+$UserLocalAppData+'"'
        $child=Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        exit $child.ExitCode
    }
    Add-Type -AssemblyName System.Windows.Forms
    $verb=if($Action -eq 'Add'){'ADICIONAR'}else{'REMOVER'}
    $target=Join-Path $UserLocalAppData ('Mining\Pearl\SRBMiner-Multi-'+(Get-MinerVersion).Replace('.','-')+'\SRBMiner-MULTI.exe')
    $warning="$verb excecao local para este caminho?`n`n$target`n`nUma deteccao nao confirma falso positivo. O hash oficial nao garante seguranca. A excecao dispensa verificacao antivirus neste caminho, inclusive de substituicoes futuras. Nao desliga o Defender, nao restaura quarentena e nao inicia mineracao."
    $choice=[System.Windows.Forms.MessageBox]::Show($warning,'HashPilot - permissao do administrador',[System.Windows.Forms.MessageBoxButtons]::YesNo,[System.Windows.Forms.MessageBoxIcon]::Warning,[System.Windows.Forms.MessageBoxDefaultButton]::Button2)
    if($choice -ne [System.Windows.Forms.DialogResult]::Yes){exit 2}
    Set-MinerException $Action
    [System.Windows.Forms.MessageBox]::Show('Alteracao confirmada pelo Defender. Nenhum arquivo foi restaurado e nenhum minerador foi iniciado.','HashPilot')|Out-Null
}catch{
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(('Permissao nao aplicada/confirmada: '+$_.Exception.Message),'HashPilot')|Out-Null
    exit 1
}
