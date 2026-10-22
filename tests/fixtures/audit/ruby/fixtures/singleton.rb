class Processor
  def process(value)
    instance_sink(value)
  end

  def self.process(value)
    class_sink(value)
  end

  class << self
    def scheduled(value)
      class_sink(value)
    end
  end

  def instance_sink(value)
    "instance:#{value}"
  end

  def self.class_sink(value)
    "class:#{value}"
  end
end

module Utilities
  def self.format(value)
    value.to_s
  end
end
