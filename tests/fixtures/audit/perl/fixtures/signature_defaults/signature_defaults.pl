use feature 'signatures';
no warnings 'experimental::signatures';
sub side_effect { return "default"; }
sub handler ($x = side_effect(), $cb = sub { side_effect() }) {
    return $x . ":" . $cb->();
}
sub main { return handler(); }
print main(), "\n";
