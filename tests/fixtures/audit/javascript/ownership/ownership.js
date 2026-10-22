function sink(value) { return value; }
function dormant() {
  function unused(value) { if (value) sink(value); }
  return 0;
}
function schedule(value) {
  queueMicrotask(() => { if (value) sink(value); });
}
class Controller {
  field = value => sink(value);
  method(value) { return sink(value); }
}
const object = { handler(value) { return sink(value); } };
setTimeout(() => sink('top-level callback'), 0);
