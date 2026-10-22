use strict;
package Example;
sub helper($x, $y = 1) { if ($x) { return $y; } }
sub legacy ($$) { return shift; }
sub main { helper(1); Example::helper(2); $obj->helper(); }
my $cb = sub { helper(3); };
Example::main();
