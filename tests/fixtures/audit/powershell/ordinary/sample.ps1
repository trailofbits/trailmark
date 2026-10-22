param([string]$Name)
function Get-Source([int]$Count = 1) { Write-Output $Count }
filter Select-Thing { if ($_ -gt 0) { $_ } }
function main {
 [CmdletBinding()]
 param([Parameter(ValueFromPipeline)][string]$Text = 'ok')
 begin { Get-Source }
 process { $Text | Select-Thing | Write-Output }
 end { $callback = { param([int]$Value) if ($Value) { Get-Source } }; & $callback 1 }
}
class Thing {
 [string]$Name
 Thing([string]$name) { $this.Name = $name }
 [string] Run([int]$x) { return $this.Name }
 static [int] Twice([int]$x) { return $x * 2 }
}
main
