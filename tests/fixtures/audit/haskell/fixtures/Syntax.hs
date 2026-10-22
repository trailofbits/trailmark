{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE GADTs #-}
module Syntax where

import qualified Data.List as List

target :: Int -> Int
target n = n + 1

add :: Int -> Int -> Int
add x y = target x + y

(<+>) :: Int -> Int -> Int
x <+> y = target x + y

viaPrefix :: Int -> Int
viaPrefix x = target x

viaParens :: Int -> Int
viaParens x = (target) x

viaDollar :: Int -> Int
viaDollar x = target $ x

viaBacktick :: Int -> Int -> Int
viaBacktick x y = x `add` y

viaOperator :: Int -> Int -> Int
viaOperator x y = x <+> y

viaQualified :: [Int] -> [Int]
viaQualified xs = List.sort xs

lambdaCase :: Maybe Int -> Int
lambdaCase = \case
  Just x -> target x
  Nothing -> 0

data Box a where
  Box :: a -> Box a

unbox :: Box Int -> Int
unbox (Box x) = target x
