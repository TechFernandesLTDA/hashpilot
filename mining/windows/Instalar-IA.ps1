$ErrorActionPreference='Stop'
try {
    $ollama=Get-Command ollama.exe -ErrorAction SilentlyContinue
    $localExecutable=Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if(-not $ollama -and -not (Test-Path -LiteralPath $localExecutable)){
        if(-not (Get-Command winget.exe -ErrorAction SilentlyContinue)){throw 'Instale o App Installer (winget) pela Microsoft Store.'}
        & winget.exe install --id Ollama.Ollama --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
        if($LASTEXITCODE -ne 0){throw 'A instalacao do Ollama falhou.'}
    }
    $executable=if($ollama){$ollama.Source}else{$localExecutable}
    if(-not (Test-Path -LiteralPath $executable)){throw 'Ollama indisponivel. Reabra o aplicativo e tente novamente.'}
    try {$null=Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3} catch {
        Start-Process -FilePath $executable -ArgumentList 'serve' -WindowStyle Hidden
        Start-Sleep -Seconds 4
    }
    & $executable pull qwen2.5:0.5b
    if($LASTEXITCODE -ne 0){throw 'Falha no download do modelo local gratuito.'}
    Write-Host 'IA local pronta. O HashPilot solicita inferencia na CPU (num_gpu=0).' -ForegroundColor Green
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Pressione Enter para fechar'
    exit 1
}
