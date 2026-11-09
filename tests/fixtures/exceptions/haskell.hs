module App where
import Control.Exception
f = catch (throwIO (userError "bad")) handler
handler error = observe
