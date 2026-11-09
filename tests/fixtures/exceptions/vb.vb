Module App
 Sub f()
  Try
   Throw New Bad()
  Catch error As Bad
   observe()
   Throw
  Finally
   cleanup()
  End Try
 End Sub
End Module
