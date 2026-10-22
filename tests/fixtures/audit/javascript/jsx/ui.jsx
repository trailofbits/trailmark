function sink(value) { return value; }
export function View({value}) { return <button onClick={() => sink(value)}>go</button>; }
