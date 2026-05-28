-- Comprehensive Haskell feature taxonomy fixture.

module Taxonomy where

import Data.Maybe (fromMaybe)
import Text.Read (readMaybe)

constant :: Int
constant = 42

data Status = Pending | Done String
  deriving (Show, Eq)

class Greeter a where
  greet :: a -> String -> String

add :: Int -> Int -> Int
add a b = a + b

branchy :: Int -> String -> Either String Int
branchy value mode =
  let positive = if value > 0 then value else 0
      negative = if value < 0 then negate value else 0
      total = positive + negative + sum [i | i <- [0 .. value - 1], even i]
      reduced = reduce total
   in case readMaybe mode of
        Just parsed -> Right (parsed + reduced)
        Nothing -> Left "bad mode"
  where
    reduce n
      | n > 100 = reduce (n `div` 2)
      | otherwise = n

data Animal = Animal
  { animalName :: String,
    animalSpecies :: String
  }
  deriving (Show)

mkAnimal :: String -> Animal
mkAnimal name = Animal {animalName = name, animalSpecies = "unknown"}

describe :: Animal -> String
describe a = animalName a ++ " the " ++ animalSpecies a

data Dog = Dog
  { dogBase :: Animal,
    dogBreed :: Maybe String
  }

mkDog :: String -> Maybe String -> Dog
mkDog name breed =
  Dog
    { dogBase = (mkAnimal name) {animalSpecies = "dog"},
      dogBreed = breed
    }

bark :: Dog -> Bool -> Either String String
bark d loud
  | loud = Left "too loud"
  | otherwise = Right (animalName (dogBase d) ++ ": woof")

instance Greeter Dog where
  greet d name = animalName (dogBase d) ++ " greets " ++ name

useAnimal :: Dog -> Either String String
useAnimal d = bark d False
