local worker = require "worker"
local function recurse(n)
  if n > 0 then return recurse(n - 1) end
end
local constant <const> = function(x) return x end
local function make()
  local function nested() return constant(1) end
  return nested
end
function main()
  worker:handle(1)
  recurse(1)
  register(function() if enabled then return make() end end)
end
