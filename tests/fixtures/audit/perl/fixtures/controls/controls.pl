use feature 'lexical_subs';
package Demo;
sub emit { return "emit"; }
sub legacy ($$) { return shift; }
sub main {
    my sub emit { return "lexical"; }
    emit();
    my $cb = sub { if (1) { Demo::emit(); } };
    $cb->();
    $obj->emit();
}
my $code = <<'ASM';
sub phantom { attack(); }
call emit
ASM
