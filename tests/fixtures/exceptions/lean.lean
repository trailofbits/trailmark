def fail (message : String) : IO Unit := do
  throw (IO.userError message)

def crash (message : String) : Nat := panic! message
