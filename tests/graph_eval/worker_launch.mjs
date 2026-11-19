import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
fs.mkdirSync('/runtime/codex',{recursive:true,mode:0o700});
if(fs.existsSync('/secrets/auth.json'))fs.copyFileSync('/secrets/auth.json','/runtime/codex/auth.json');
if(fs.existsSync('/secrets/api-key'))process.env.OPENAI_API_KEY=fs.readFileSync('/secrets/api-key','utf8').trim();
const result=spawnSync('codex',process.argv.slice(2),{stdio:['inherit','inherit','inherit'],env:process.env});
process.exit(result.status ?? 1);
