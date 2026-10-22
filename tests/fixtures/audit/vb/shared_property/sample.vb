Class App
 Private Sub InstanceOnly()
 End Sub
 Public Shared Sub SharedOnly()
 End Sub
 Public Shared Property Value As Integer
  Get
   SharedOnly()
   Return 1
  End Get
  Set(value As Integer)
   SharedOnly()
  End Set
 End Property
 Public Sub Run()
  Dim x = App.Value
 End Sub
End Class
