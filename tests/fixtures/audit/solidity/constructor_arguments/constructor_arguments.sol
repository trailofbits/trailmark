pragma solidity ^0.8.24;
function compute() pure returns (uint) { return 1; }
contract Base { constructor(uint) {} }
contract Child is Base {
    constructor() Base(compute()) {}
}
