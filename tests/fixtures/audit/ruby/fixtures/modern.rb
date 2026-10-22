def delegate(...)
  target(...)
end

def configured(first, *rest, required:, optional: 42, **options, &block)
  [first, rest, required, optional, options, block]
end

def double(value) = value * 2

def deferred
  ->(value) { if value; danger(value); end }
end

def iterate(items)
  items.each do |item|
    helper(item) if item
  end
end
