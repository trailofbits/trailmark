pragma solidity ^0.8.29;
interface ICallback {
    function callback(bytes calldata data) external returns (bytes4);
}
contract Modern layout at 42 is ICallback {
    uint counter;
    uint transient lock;
    error Reentrant();
    function callback(bytes calldata) external override returns (bytes4) {
        require(lock == 0, Reentrant());
        lock = 1;
        counter++;
        lock = 0;
        return this.callback.selector;
    }
}
