function Get-Default { Write-Output 'default' }
function Invoke-Header($Value = (Get-Default)) { Write-Output $Value }
function Invoke-Block {
 param($Value = (Get-Default))
 Write-Output $Value
}
function Invoke-HeaderCallback($Callback = { if ($true) { Get-Default } }) { & $Callback }
