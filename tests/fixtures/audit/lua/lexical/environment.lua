local function stable() return "lexical" end
function helper() return "global" end
function main(_ENV)
  stable()
  helper()
  require "sandbox_only"
end
