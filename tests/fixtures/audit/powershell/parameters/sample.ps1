function Invoke-Work {
 param($Callback = { param([string]$Inner) Write-Output $Inner })
 & $Callback
}
function Invoke-Header($Callback = { param([int]$Inner) Write-Output $Inner }) { & $Callback }
