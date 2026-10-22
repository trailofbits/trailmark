function sink(value) { return value; }
function normalize(value) { return value; }
export const block = (value) => { return sink(value); };
export const concise = (value) => sink(value);
export const nestedArg = (value) => sink(normalize(value));
export const choose = (value) => value ? sink(value) : normalize(value);
export const shorthand = value => { return sink(value); };
