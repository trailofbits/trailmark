local dependency = require("dependency")
local function helper(x)
    if x > 0 then return x else return 0 end
end
local M = {}
function M:handle(x)
    return helper(x), self:other(x)
end
local callback = function(x) return helper(x) end
function main()
    return helper(1), callback(2), dependency.run(3)
end
main()
return M
