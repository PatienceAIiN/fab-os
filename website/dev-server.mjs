import { createReadStream, promises as fs } from 'node:fs';
import { createServer } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 4173);
const clients = new Set();
const mime = { '.css': 'text/css; charset=utf-8', '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.txt': 'text/plain; charset=utf-8', '.xml': 'application/xml; charset=utf-8' };

function safePath(requestUrl) {
  const pathname = decodeURIComponent(new URL(requestUrl, `http://localhost:${port}`).pathname);
  const clean = pathname === '/' ? '/index.html' : pathname.endsWith('/') ? `${pathname}index.html` : pathname;
  const resolved = path.resolve(root, `.${clean}`);
  return resolved.startsWith(root) ? resolved : null;
}

const server = createServer(async (request, response) => {
  if (request.url === '/__fabos_live') {
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive', 'Access-Control-Allow-Origin': '*' });
    response.write('event: connected\ndata: ready\n\n');
    clients.add(response);
    request.on('close', () => clients.delete(response));
    return;
  }
  const file = safePath(request.url || '/');
  if (!file) { response.writeHead(403); response.end('Forbidden'); return; }
  try {
    const stat = await fs.stat(file);
    if (!stat.isFile()) throw new Error('not a file');
    response.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    if (path.extname(file) === '.html') {
      let html = await fs.readFile(file, 'utf8');
      if (file === path.join(root, 'index.html')) html = html.replace('</body>', '<script>new EventSource("/__fabos_live").onmessage=()=>location.reload();</script></body>');
      response.end(html);
    } else createReadStream(file).pipe(response);
  } catch { response.writeHead(404); response.end('Not found'); }
});

try {
  const watcher = (await import('node:fs')).watch(root, { recursive: true });
  watcher.on('change', () => { for (const client of clients) client.write('data: reload\n\n'); });
} catch { /* File watching is optional; the server still serves the site. */ }

server.listen(port, '127.0.0.1', () => console.log(`Fab OS local preview: http://127.0.0.1:${port}`));
