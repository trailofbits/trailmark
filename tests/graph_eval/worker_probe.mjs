// A benign preflight tests actual container boundaries before any evaluation.
import fs from 'node:fs';
import net from 'node:net';
import http from 'node:http';
import {spawnSync} from 'node:child_process';

const result = {};
result.source_read = fs.readFileSync('/source/control.txt','utf8').trim() === 'source-view-control';
try {
  fs.writeFileSync('/source/write-probe','bad');
  result.source_read_only = false;
} catch {
  result.source_read_only = true;
}
result.host_hidden = !fs.existsSync('/Users') &&
  !fs.existsSync('/var/run/docker.sock') && !fs.existsSync('/root/.codex');

result.credentials_not_mounted = !fs.existsSync('/secrets');
result.runtime_tmpfs = fs.statfsSync('/runtime').type === 0x01021994;
// Observe the launch boundary without making this control an authenticated model call.
// A preload can read code from noexec tmpfs; no temporary executable is required.
const preload = '/runtime/launch-probe.cjs';
fs.writeFileSync(preload, `
const fs = require('node:fs');
const child = require('node:child_process');
child.spawnSync = (command, args, options) => {
  const auth = JSON.parse(fs.readFileSync('/runtime/codex/auth.json', 'utf8'));
  console.log(JSON.stringify({
    codex_only: command === 'codex',
    private_file: (fs.statSync('/runtime/codex/auth.json').mode & 0o777) === 0o600,
    private_directory: (fs.statSync('/runtime/codex').mode & 0o777) === 0o700,
    prompt_only: options.input === 'benign-launch-control',
    credentials_received: auth.auth_mode === 'apikey' && auth.OPENAI_API_KEY === 'probe-only-do-not-use',
    no_credential_environment: !options.env.OPENAI_API_KEY && !options.env.CODEX_API_KEY,
  }));
  return {status: 0};
};
require('node:module').syncBuiltinESMExports();
`, {mode: 0o600});
const launch = spawnSync('node', ['/opt/graph-eval/worker_launch.mjs'], {
  input: JSON.stringify({auth: {auth_mode: 'apikey', OPENAI_API_KEY: 'probe-only-do-not-use'},
    prompt: 'benign-launch-control'}),
  encoding: 'utf8', env: {...process.env, NODE_OPTIONS: '--require=' + preload},
});
result.stdin_credentials_private = launch.status === 0 &&
  Object.values(JSON.parse(launch.stdout)).every(value => value === true);
result.credentials_removed_after_exit = !fs.existsSync('/runtime/codex/auth.json');
const invalid = spawnSync('node', ['/opt/graph-eval/worker_launch.mjs'], {
  input: '{"auth":{"OPENAI_API_KEY":"probe-only-do-not-use"},"prompt":', encoding: 'utf8',
});
result.invalid_envelope_redacted = invalid.status !== 0 &&
  !(invalid.stdout + invalid.stderr).includes('probe-only-do-not-use');
const messages = [
  {jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-03-26',
    capabilities:{},clientInfo:{name:'probe',version:'1'}}},
  {jsonrpc:'2.0',id:2,method:'tools/call',params:{name:'source_read',
    arguments:{path:'../secrets/auth.json',start_line:1,end_line:1}}},
  {jsonrpc:'2.0',id:3,method:'tools/call',params:{name:'source_read',
    arguments:{path:'control.txt',start_line:1,end_line:1}}},
];
const rpc = spawnSync('node',['/opt/graph-eval/source_reader.mjs'],{
  input:messages.map(x=>JSON.stringify(x)).join('\n')+'\n',encoding:'utf8',
  env:{...process.env,SOURCE_ROOT:'/source',SOURCE_AUDIT:'/output/probe-source-reads.jsonl'},
});
const responses = rpc.stdout.trim().split('\n').map(x=>JSON.parse(x));
result.source_tool_rejects_escape = responses.find(x=>x.id===2)?.result?.isError === true;
const read = responses.find(x=>x.id===3)?.result;
result.source_tool_reads_source = read?.isError !== true &&
  read?.content[0]?.text.includes('source-view-control');
result.direct_network_blocked = await new Promise(resolve=>{
  const socket = net.connect({host:'1.1.1.1',port:443});
  socket.once('connect',()=>{socket.destroy();resolve(false)});
  socket.once('error',()=>resolve(true));
  socket.setTimeout(1200,()=>{socket.destroy();resolve(true)});
});
function connect(host) {
  return new Promise(resolve=>{
    const req = http.request({hostname:'egress',port:8080,method:'CONNECT',path:host+':443'});
    req.on('connect',(res,socket)=>{socket.destroy();resolve(res.statusCode)});
    req.on('response',res=>{res.resume();resolve(res.statusCode)});
    req.on('error',()=>resolve(0));
    req.setTimeout(15000,()=>{req.destroy();resolve(0)});
    req.end();
  });
}
result.unrelated_egress_denied = (await connect('example.com')) === 403;
result.provider_egress_available = (await connect('api.openai.com')) === 200;
result.cli_version = spawnSync('codex',['--version'],{encoding:'utf8'}).stdout.trim();
console.log(JSON.stringify(result));
process.exit(Object.entries(result).every(([key,value])=>key==='cli_version'||value===true)?0:1);
