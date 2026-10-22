function sink(event) { return event; }
export const handler = async (event) => { return sink(event); };
