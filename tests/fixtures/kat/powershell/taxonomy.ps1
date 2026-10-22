param([string]$Name)
function Get-Value([int]$Count = 1) {
 if ($Count -gt 0) { Write-Output $Count }
}
filter Select-Thing { $_ }
function main {
 get-value -Count 2
 & $Name
}
main
