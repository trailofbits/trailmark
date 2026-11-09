def f
  begin
    raise Bad, "bad"
  rescue Bad => error
    observe()
    raise
  ensure
    cleanup()
  end
end
