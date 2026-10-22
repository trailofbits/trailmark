use v5.38;
use feature 'class';
no warnings 'experimental::class';
class Example {
    field $x :param = 1;
    method value { return $x; }
}
