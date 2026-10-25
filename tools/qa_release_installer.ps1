# Test the exact verification function used by install.ps1, without installing anything.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Archive,[Parameter(Mandatory=$true)][string]$Sums,
      [Parameter(Mandatory=$true)][string]$Destination,[string]$Release='v1.2.3',
      [string]$Installer=(Join-Path (Get-Location) 'install.ps1'))
$ErrorActionPreference='Stop'
$tokens=$null
$errors=$null
$tree=[System.Management.Automation.Language.Parser]::ParseFile($Installer,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Installer syntax errors'}
$functions=@($tree.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Confirm-HashPilotRelease'},$true))
if($functions.Count -ne 1){throw 'Verification function not found'}
. ([scriptblock]::Create($functions[0].Extent.Text))
$manifest=Confirm-HashPilotRelease -Archive $Archive -Sums $Sums -Release $Release -Destination $Destination
Write-Output ('Verified commit: '+$manifest.commit)
