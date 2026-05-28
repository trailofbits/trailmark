# Comprehensive Ruby feature taxonomy fixture.

module Taxonomy
  CONSTANT = 42

  module Greeter
    def greet(name)
      raise NotImplementedError
    end
  end

  def self.add(a, b)
    a + b
  end

  def self.branchy(value, mode)
    total = 0
    if value > 0
      total += value
    elsif value < 0
      total -= value
    end
    (0...value).each do |i|
      total += i if i.even?
    end
    while total > 100
      total /= 2
    end
    begin
      Integer(mode) + total
    rescue ArgumentError
      raise "bad mode"
    end
  end

  class Animal
    attr_accessor :name, :species

    def initialize(name)
      @name = name
      @species = "unknown"
    end

    def describe
      "#{@name} the #{@species}"
    end
  end

  class Dog < Animal
    include Greeter

    attr_accessor :breed

    def initialize(name, breed = nil)
      super(name)
      @species = "dog"
      @breed = breed
    end

    def bark(loud = false)
      raise "too loud" if loud
      "#{@name}: woof"
    end

    def greet(name)
      "#{@name} greets #{name}"
    end
  end

  def self.use_animal(d)
    d.bark(false)
  end
end
