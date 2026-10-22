function Get-Value { 'x' }
function main {
 $x = $null ?? (Get-Value)
 $y = $true ? (Get-Value) : 'fallback'
 Get-Value && Get-Value || Get-Value
 ${obj}?.Run()
}
main
