local M = {}
local function helper(x, ...)
  if x then return x end
end
function M:handle(x)
  return helper(x)
end
M.callback = function(x) return helper(x) end
M.options = { run = function() return helper(1) end }
return M
