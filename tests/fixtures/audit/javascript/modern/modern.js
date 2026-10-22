import { readFile } from 'node:fs/promises';
function sink(value) { return value; }
export class Worker {
  #secret(value) { return sink(value); }
  handle = (value) => sink(value);
  run(value) { this.#secret(value); this.handle?.(value); return new Worker(); }
}
export async function load(path = readFile('default')) { return (await readFile(path)); }
