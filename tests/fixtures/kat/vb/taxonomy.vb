Imports System
Namespace Demo
Module App
 Function Helper(x As Integer, Optional y As Integer = 1) As Integer
  If x > 0 Then
   Return y
  End If
  Return 0
 End Function
 Sub Main()
  helper(1)
 End Sub
End Module
End Namespace
