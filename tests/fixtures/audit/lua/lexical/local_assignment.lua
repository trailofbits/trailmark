do
  local hidden
  hidden = function() return "local-only" end
end
function main()
  return hidden()
end
