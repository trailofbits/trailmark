function f()
    try
        throw(Bad())
    catch error
        observe()
        rethrow()
    finally
        cleanup()
    end
end
