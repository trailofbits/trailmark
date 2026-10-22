function helper { Write-Output 'original' }
si Function:helper { Write-Output 'replacement' }
function main { helper }
main
