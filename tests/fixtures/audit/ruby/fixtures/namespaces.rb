module Admin
  class Worker
    def perform(value)
      admin_sink(value)
    end
  end

  module Nested
    def helper(value)
      value.to_s
    end
  end
end

module Public
  class Worker
    def perform(value)
      public_sink(value)
    end
  end
end

class Outer
  class Inner
    def execute(value)
      value.to_s
    end
  end
end
