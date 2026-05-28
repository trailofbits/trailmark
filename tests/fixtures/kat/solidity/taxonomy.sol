// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Comprehensive Solidity feature taxonomy fixture.

interface IGreeter {
    function greet(string memory name) external view returns (string memory);
}

abstract contract Animal {
    string public name;
    string public species;

    constructor(string memory _name) {
        name = _name;
        species = "unknown";
    }

    function describe() public view virtual returns (string memory) {
        return string(abi.encodePacked(name, " the ", species));
    }
}

contract Dog is Animal, IGreeter {
    uint256 public constant CONSTANT = 42;

    string public breed;

    event Barked(string indexed who, bool loud);

    error TooLoud();

    constructor(string memory _name, string memory _breed) Animal(_name) {
        species = "dog";
        breed = _breed;
    }

    function add(uint256 a, uint256 b) public pure returns (uint256) {
        return a + b;
    }

    function branchy(uint256 value, uint256 mode) external pure returns (uint256) {
        uint256 total = 0;
        if (value > 100) {
            total += value;
        } else if (value > 0) {
            total += 1;
        }
        for (uint256 i = 0; i < value; i++) {
            if (i % 2 == 0) {
                total += i;
            }
        }
        while (total > 100) {
            total /= 2;
        }
        return total + mode;
    }

    function bark(bool loud) public returns (string memory) {
        if (loud) {
            revert TooLoud();
        }
        emit Barked(name, loud);
        return string(abi.encodePacked(name, ": woof"));
    }

    function greet(string memory other) external view override returns (string memory) {
        return string(abi.encodePacked(name, " greets ", other));
    }

    function useAnimal() external returns (string memory) {
        return bark(false);
    }
}
