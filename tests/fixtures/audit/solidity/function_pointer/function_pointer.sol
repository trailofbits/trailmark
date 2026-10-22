pragma solidity ^0.8.24;
contract C {
    uint counter;
    function work() internal { counter++; }
    function alternative() internal {}
    function dispatch(function() internal work) internal { work(); }
    function entry() external { dispatch(alternative); }
}
