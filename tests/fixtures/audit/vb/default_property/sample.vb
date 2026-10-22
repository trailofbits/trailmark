Class App
 Private Shared Function Helper() As String
  Return "ok"
 End Function
 Default Public Property Item(index As Integer) As String
  Get
   Return Helper()
  End Get
  Set(value As String)
   Helper()
  End Set
 End Property
 Public Shared Property Value As Integer
  Get
   Return 1
  End Get
  Set(value As Integer)
  End Set
 End Property
End Class
