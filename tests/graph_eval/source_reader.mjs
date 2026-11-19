// The model receives no shell: this MCP server is its only filesystem interface.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';

const root = fs.realpathSync(process.env.SOURCE_ROOT || '/source');
const audit = process.env.SOURCE_AUDIT;
const blocked = new Set(['.git', '.codex', '.agents', 'AGENTS.md', 'AGENTS.override.md', 'node_modules', '__pycache__']);
function event(value) {
  if (audit) fs.appendFileSync(audit, JSON.stringify(value) + '\n');
}
function checked(raw) {
  if (typeof raw !== 'string' || path.isAbsolute(raw) || raw.split(/[\\/]/).some(p => p === '..' || blocked.has(p))) throw new Error('Path is outside the source view');
  const resolved = fs.realpathSync(path.join(root, raw));
  if (resolved !== root && !resolved.startsWith(root + path.sep)) throw new Error('Path is outside the source view');
  return resolved;
}
function inventory(directory = root, prefix = '') {
  const found = [];
  for (const entry of fs.readdirSync(directory, {withFileTypes: true}).sort((a,b) => a.name.localeCompare(b.name))) {
    if (blocked.has(entry.name) || entry.isSymbolicLink()) continue;
    const relative = prefix ? prefix + '/' + entry.name : entry.name;
    if (entry.isDirectory()) found.push(...inventory(path.join(directory, entry.name), relative));
    else if (entry.isFile()) found.push(relative);
  }
  return found;
}
function contents(relative) {
  const file = checked(relative);
  const size = fs.statSync(file).size;
  if (size > 32 * 1024 * 1024) throw new Error('File exceeds 32 MiB source limit');
  return new TextDecoder('utf-8', {fatal: true}).decode(fs.readFileSync(file)).split('\n');
}
const tools = [
  {name: 'source_list', description: 'List source files. Only the frozen source view is available.', inputSchema: {type:'object',additionalProperties:false,properties:{prefix:{type:'string'}},required:['prefix']}, annotations:{readOnlyHint:true}},
  {name: 'source_read', description: 'Read numbered UTF-8 source lines; at most 1000 lines per call. Paths are relative to the source view.', inputSchema:{type:'object',additionalProperties:false,properties:{path:{type:'string'},start_line:{type:'integer',minimum:1},end_line:{type:'integer',minimum:1}},required:['path','start_line','end_line']},annotations:{readOnlyHint:true}},
  {name: 'source_search', description:'Find a literal substring in source files. Up to 100 matching lines are returned.',inputSchema:{type:'object',additionalProperties:false,properties:{query:{type:'string',minLength:1},prefix:{type:'string'}},required:['query','prefix']},annotations:{readOnlyHint:true}},
];
function call(name, args) {
  if (name === 'source_list') {
    if (typeof args.prefix !== 'string' || args.prefix.includes('..') || args.prefix.startsWith('/')) throw new Error('Invalid source prefix');
    const files = inventory().filter(p => p.startsWith(args.prefix));
    return {files:files.slice(0,500),truncated:files.length > 500,total:files.length};
  }
  if (name === 'source_read') {
    if (!Number.isInteger(args.start_line) || !Number.isInteger(args.end_line) || args.start_line < 1 || args.end_line < args.start_line || args.end_line - args.start_line >= 1000) throw new Error('Specify 1–1000 source lines');
    const lines = contents(args.path);
    return {path:args.path,total_lines:lines.length,lines:lines.slice(args.start_line-1,args.end_line).map((text,index)=>({line:args.start_line+index,text}))};
  }
  if (name === 'source_search') {
    if (typeof args.query !== 'string' || !args.query || args.query.length > 200 || typeof args.prefix !== 'string' || args.prefix.includes('..') || args.prefix.startsWith('/')) throw new Error('Invalid literal source search');
    const hits = [];
    for (const file of inventory().filter(p => p.startsWith(args.prefix))) {
      let lines; try {lines=contents(file);} catch {continue;}
      for (let index=0;index<lines.length;index++) if (lines[index].includes(args.query)) {hits.push({path:file,line:index+1,text:lines[index]});if(hits.length===100)return {hits,truncated:true};}
    }
    return {hits,truncated:false};
  }
  throw new Error('Unknown source tool');
}
async function handle(message) {
  if (!Object.hasOwn(message,'id')) return;
  if (message.method === 'initialize') return {protocolVersion:message.params?.protocolVersion || '2025-03-26',capabilities:{tools:{}},serverInfo:{name:'graph-eval-source',version:'1'}};
  if (message.method === 'ping') return {};
  if (message.method === 'tools/list') return {tools};
  if (message.method === 'tools/call') {
    const {name,arguments:args={}}=message.params || {};
    try {const result=call(name,args);event({tool:name,arguments:args,status:'ok'});return {content:[{type:'text',text:JSON.stringify(result)}]};}
    catch (error) {event({tool:name,arguments:args,status:'denied',message:error.message});return {isError:true,content:[{type:'text',text:error.message}]};}
  }
  throw new Error('Unknown MCP method');
}
for await (const line of readline.createInterface({input:process.stdin})) {
  let message;
  try {message=JSON.parse(line);const result=await handle(message);if(Object.hasOwn(message,'id'))process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:message.id,result})+'\n');}
  catch(error) {if(message && Object.hasOwn(message,'id'))process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:message.id,error:{code:-32602,message:error.message}})+'\n');}
}
