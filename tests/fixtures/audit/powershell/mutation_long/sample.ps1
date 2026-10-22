function helper { Write-Output 'original' }
Set-Item Function:helper { Write-Output 'replacement' }
function main { helper }
main
