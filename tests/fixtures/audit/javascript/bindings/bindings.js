function sink(value) { return value; }
function byParameter(sink, value) { return sink(value); }
function byBlock(value) { const sink = () => 0; return sink(value); }
function byNested(value) { function sink() { return 0; } return sink(value); }
function reassigned(value) { sink = () => 0; return sink(value); }
