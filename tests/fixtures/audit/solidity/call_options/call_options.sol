pragma solidity ^0.8.24;
contract C {
    function f(address payable target, bytes memory payload) external payable {
        target.call{value: msg.value}(payload);
        target.delegatecall(payload);
        target.staticcall(payload);
        this.ping{gas: 50000}();
    }
    function ping() external {}
}
