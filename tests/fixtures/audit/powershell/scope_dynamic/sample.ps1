function helper {}
function Invoke-Local {
 helper
 function nested {}
 nested
 & $Name
 & 'helper'
 $obj.Run()
 [Thing]::Run()
 $callback = { if ($true) { helper } }
}
function script:Scoped {}
script:Scoped
