pragma solidity ^0.8.24;
contract C {
    uint checks;
    modifier gate(bool ok) { require(ok); _; }
    function authorize() internal returns (bool) { checks++; return true; }
    function f() external gate(authorize()) {}
}
