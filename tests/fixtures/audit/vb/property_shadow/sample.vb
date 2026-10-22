Delegate Function Converter() As String
Class App
 Private Function Helper() As String
  Return "method"
 End Function
 Default Public ReadOnly Property Item(HELPER As Converter) As String
  Get
   Return helper()
  End Get
 End Property
End Class
