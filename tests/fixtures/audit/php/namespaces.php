<?php
namespace Alpha {
    function sink(): string { return 'alpha'; }
    function alphaEntry(): string { return sink(); }
    class Service { public function run(): string { return 'alpha'; } }
}
namespace Beta {
    function sink(): string { return 'beta'; }
    function betaEntry(): string { return sink(); }
    class Service { public function run(): string { return 'beta'; } }
}
