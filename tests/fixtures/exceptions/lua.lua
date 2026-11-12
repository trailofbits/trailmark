function f()
    local ok = pcall(function() error("bad") end)
    observe()
end
