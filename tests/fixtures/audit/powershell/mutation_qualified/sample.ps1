function helper { Write-Output 'original' }
Microsoft.PowerShell.Management\Set-Item Function:helper { Write-Output 'replacement' }
function main { helper }
main
