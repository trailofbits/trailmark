// Credentials arrive only over private stdin and exist only in the runtime tmpfs.
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';

try {
  const envelope = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (!envelope || typeof envelope.prompt !== 'string' || !envelope.auth ||
      typeof envelope.auth !== 'object' || Array.isArray(envelope.auth)) {
    throw new Error('Invalid worker input');
  }
  // Refuse to create credential files on a persistent filesystem, even if mislaunched.
  if (fs.statfsSync('/runtime').type !== 0x01021994) {
    throw new Error('Worker runtime must be tmpfs');
  }
  const directory = '/runtime/codex';
  const authPath = directory + '/auth.json';
  fs.mkdirSync(directory, {recursive: true, mode: 0o700});
  fs.writeFileSync(authPath, JSON.stringify(envelope.auth), {mode: 0o600, flag: 'wx'});
  delete envelope.auth;
  const env = {...process.env, HOME: '/runtime', CODEX_HOME: directory};
  delete env.OPENAI_API_KEY;
  delete env.CODEX_API_KEY;
  try {
    const result = spawnSync('codex', process.argv.slice(2), {
      input: envelope.prompt, stdio: ['pipe', 'inherit', 'inherit'], env,
    });
    process.exitCode = result.status ?? 1;
  } finally {
    fs.rmSync(authPath, {force: true});
  }
} catch {
  // Exceptions may contain input fragments. Never print them or the envelope.
  process.stderr.write('Worker launch failed; authentication input was not logged.\n');
  process.exitCode = 1;
}
