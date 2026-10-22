function sink(event) { return event; }
exports.handler = async (event) => { return sink(event); };
