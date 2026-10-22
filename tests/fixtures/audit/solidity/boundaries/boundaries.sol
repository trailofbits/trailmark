pragma solidity ^0.8.24;
contract Base {
    uint inherited;
    function base() internal {}
    function overloaded(uint) internal {}
    function overloaded(address) internal {}
}
contract Child is Base {
    uint[] items;
    uint public ordinary;
    uint transient lock;
    constructor() { ordinary = 1; }
    modifier guard() { require(ordinary != 0); _; }
    function entry() external guard {
        base(); overloaded(1); inherited = 2;
        uint[] storage alias_ = items; alias_.push(1);
    }
    receive() external payable {}
    fallback() external payable {}
}
