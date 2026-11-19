// Only the Codex provider/authentication endpoints can leave the internal network.
import http from 'node:http';
import net from 'node:net';
const allowed = new Set(['api.openai.com','chatgpt.com','auth.openai.com']);
const server = http.createServer((_req,res) => {res.writeHead(403);res.end('CONNECT only');});
server.on('connect',(req,client,head) => {
  let target;
  try {target = new URL('https://' + req.url);} catch {client.end('HTTP/1.1 403 Forbidden\r\n\r\n');return;}
  if (!allowed.has(target.hostname) || target.port && target.port !== '443' || target.username || target.password || target.pathname !== '/') {client.end('HTTP/1.1 403 Forbidden\r\n\r\n');return;}
  const upstream=net.connect({host:target.hostname,port:443},()=>{client.write('HTTP/1.1 200 Connection Established\r\n\r\n');if(head.length)upstream.write(head);upstream.pipe(client);client.pipe(upstream);});
  upstream.setTimeout(900000,()=>upstream.destroy());
  upstream.on('error',()=>client.destroy());client.on('error',()=>upstream.destroy());client.on('close',()=>upstream.destroy());
});
server.listen(8080,'0.0.0.0');
