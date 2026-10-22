def sink(value = nil)
  "top-level:#{value}"
end

class Handler
  def sink(value = nil)
    "instance:#{value}"
  end

  def run(other)
    sink
    sink()
    self.sink
    self.sink()
    sink "payload"
    other.sink
    [1].each { sink() }
    callback = -> { if true; sink(); end }
    callback
  end
end
