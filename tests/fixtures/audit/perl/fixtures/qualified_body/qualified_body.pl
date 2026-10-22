package Alpha;
sub helper { return "Alpha"; }
package Beta;
sub helper { return "Beta"; }
package Alpha;
sub Beta::main { return helper(); }
print Beta::main(), "\n";
