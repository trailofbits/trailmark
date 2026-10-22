import { sink as importedSink } from './a.js';
export function caller(value) { return importedSink(value); }
