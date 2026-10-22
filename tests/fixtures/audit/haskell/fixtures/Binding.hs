module Binding where

danger :: Int -> Int
danger n = n + 1

direct :: Int -> Int
direct n = danger n

applyParam :: (Int -> Int) -> Int -> Int
applyParam danger n = danger n

lambdaShadow :: [Int -> Int] -> [Int]
lambdaShadow callbacks = map (\danger -> danger 1) callbacks

caseShadow :: Maybe (Int -> Int) -> Int
caseShadow m = case m of
  Just danger -> danger 1
  Nothing -> 0

whereOwner :: Int -> Int
whereOwner n = local n
  where
    local x
      | x > 0 = danger x
      | otherwise = 0

letOwner :: Int -> Int
letOwner n = let helper x = danger x in helper n

passedOnly :: [Int] -> [Int]
passedOnly xs = map danger xs
