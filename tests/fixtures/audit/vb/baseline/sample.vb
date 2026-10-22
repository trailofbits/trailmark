Imports System
Namespace Demo
Module App
 Delegate Function Converter(value As Integer) As Integer
 Function Helper(Optional x As Integer = 1) As Integer
  Return x
 End Function
 Sub Helper(x As String)
 End Sub
 Sub Main(args As String())
  Dim cb As Converter = Function(x As Integer) Helper(x)
  If args.Length > 0 Then
   cb(1)
  End If
  App.HELPER(1)
 End Sub
End Module
End Namespace
