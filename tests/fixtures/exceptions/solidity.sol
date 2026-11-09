contract App {
    error Bad();
    function f() public {
        try this.helper() {} catch Error(string memory reason) { revert Bad(); }
    }
    function helper() external {}
}
