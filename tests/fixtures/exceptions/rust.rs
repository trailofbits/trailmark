fn f() -> Result<(), Error> {
    helper()?;
    panic!("bad");
}
