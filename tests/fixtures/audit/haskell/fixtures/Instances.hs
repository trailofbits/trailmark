{-# LANGUAGE FlexibleInstances #-}
module Instances where

data Plain = Plain
data Box a = Box a

class Check a where
  check :: a -> Bool
  check _ = defaultHelper 0

defaultHelper :: Int -> Bool
defaultHelper n = n >= 0

instance Check Plain where
  check _ = plainHelper 0

plainHelper :: Int -> Bool
plainHelper n = n >= 0

instance Check (Box Int) where
  check (Box n) = intHelper n

intHelper :: Int -> Bool
intHelper n = n >= 0

instance Check (Box Bool) where
  check (Box n) = boolHelper n

boolHelper :: Bool -> Bool
boolHelper n = n
