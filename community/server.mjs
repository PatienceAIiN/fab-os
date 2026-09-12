import { createReadStream, promises as fs } from 'node:fs';
import { createServer } from 'node:http';
import { DatabaseSync } from 'node:sqlite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomBytes } from 'node:crypto';
import { GetObjectCommand, PutObjectCommand, S3Client } from '@aws-sdk/client-s3';

const here = path.dirname(fileURLToPath(import.meta.url));
const websiteRoot = path.resolve(here, '../website');
const port = Number(process.env.PORT || 4180);
const maxBody = 2_500_000;
const db = new DatabaseSync(process.env.COMMUNITY_DB || path.join(here, 'community.sqlite'));
db.exec(`PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT NOT NULL, email TEXT,
  body TEXT NOT NULL DEFAULT '', image_key TEXT, owner_token_hash TEXT NOT NULL,
  reshared_post_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL, parent_id INTEGER,
  display_name TEXT NOT NULL, email TEXT, body TEXT NOT NULL, owner_token_hash TEXT NOT NULL,
  created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS reactions (
  post_id INTEGER NOT NULL, visitor_id TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(post_id, visitor_id, kind)
);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL, reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);`);

const r2 = process.env.R2_ACCESS_KEY_ID && process.env.R2_SECRET_ACCESS_KEY && process.env.R2_ENDPOINT
  ? new S3Client({ endpoint: process.env.R2_ENDPOINT, region: 'auto', credentials: { accessKeyId: process.env.R2_ACCESS_KEY_ID, secretAccessKey: process.env.R2_SECRET_ACCESS_KEY } })
  : null;
const bucket = process.env.R2_BUCKET || 'fab-os-community';
const rate = new Map();
const mime = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml', '.xml': 'application/xml; charset=utf-8', '.txt': 'text/plain; charset=utf-8' };

function now() { return new Date().toISOString(); }
function token() { return randomBytes(24).toString('base64url'); }
function hash(value) { return createHash('sha256').update(value).digest('hex'); }
function clean(value, max) { return String(value ?? '').trim().slice(0, max); }
function validEmail(value) { return !value || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value); }
function json(response, status, data) { response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }); response.end(JSON.stringify(data)); }
function error(response, status, message) { json(response, status, { error: message }); }
function clientIp(request) { return String(request.headers['x-forwarded-for'] || request.socket.remoteAddress || 'unknown').split(',')[0].trim(); }
function allow(request, response, bucketName, limit = 20, windowMs = 60_000) {
  const key = `${bucketName}:${clientIp(request)}`;
  const current = rate.get(key) || { count: 0, at: Date.now() };
  if (Date.now() - current.at > windowMs) { current.count = 0; current.at = Date.now(); }
  current.count += 1; rate.set(key, current);
  if (current.count > limit) { error(response, 429, 'Please slow down and try again shortly.'); return false; }
  return true;
}
async function readJson(request, response) {
  let raw = '';
  for await (const chunk of request) {
    raw += chunk;
    if (raw.length > maxBody) { error(response, 413, 'This submission is too large.'); return null; }
  }
  try { return JSON.parse(raw || '{}'); } catch { error(response, 400, 'Invalid request.'); return null; }
}
function publicPost(row) {
  const comments = db.prepare('SELECT id,parent_id,display_name,body,created_at FROM comments WHERE post_id = ? AND deleted_at IS NULL ORDER BY id ASC').all(row.id);
  const reactions = db.prepare('SELECT kind,COUNT(*) count FROM reactions WHERE post_id = ? GROUP BY kind').all(row.id);
  const counts = Object.fromEntries(reactions.map(item => [item.kind, Number(item.count)]));
  return { id: row.id, displayName: row.display_name, body: row.body, imageUrl: row.image_key ? `/api/community/media/${encodeURIComponent(row.image_key)}` : null, resharedPostId: row.reshared_post_id, resharedBy: row.reshared_post_id ? row.display_name : null, createdAt: row.created_at, updatedAt: row.updated_at, likeCount: counts.like || 0, reshareCount: counts.reshare || 0, comments: comments.map(item => ({ id: item.id, parentId: item.parent_id, displayName: item.display_name, body: item.body, createdAt: item.created_at })) };
}
async function uploadImage(data, id) {
  if (!data) return null;
  const match = /^data:image\/(webp|jpeg|png);base64,([A-Za-z0-9+/=]+)$/.exec(data);
  if (!match) throw new Error('Use a WebP, JPEG, or PNG image.');
  const body = Buffer.from(match[2], 'base64');
  if (body.length > 1_800_000) throw new Error('Compressed images must be under 1.8 MB.');
  if (!r2) throw new Error('Image storage is not configured.');
  const ext = match[1] === 'jpeg' ? 'jpg' : match[1];
  const key = `posts/${new Date().toISOString().slice(0, 10)}/${id}.${ext}`;
  await r2.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: body, ContentType: `image/${match[1]}`, CacheControl: 'public, max-age=31536000, immutable' }));
  return key;
}
async function notify(email, subject, text) {
  if (!email || !process.env.BREVO_API_KEY || !process.env.BREVO_SENDER_EMAIL) return;
  try {
    await fetch('https://api.brevo.com/v3/smtp/email', { method: 'POST', headers: { 'api-key': process.env.BREVO_API_KEY, 'content-type': 'application/json' }, body: JSON.stringify({ sender: { email: process.env.BREVO_SENDER_EMAIL, name: process.env.BREVO_SENDER_NAME || 'Patience AI' }, to: [{ email }], subject, textContent: text }) });
  } catch { /* notification failure must not break community actions */ }
}
async function api(request, response, url) {
  if (request.method === 'GET' && url.pathname === '/api/community/posts') {
    const posts = db.prepare('SELECT * FROM posts WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 100').all();
    return json(response, 200, { posts: posts.map(publicPost) });
  }
  if (request.method === 'POST' && url.pathname === '/api/community/posts') {
    if (!allow(request, response, 'new-post', 8, 300_000)) return;
    const body = await readJson(request, response); if (!body) return;
    const text = clean(body.body, 5000); const name = clean(body.displayName, 60) || 'Anonymous'; const email = clean(body.email, 160);
    if (!text && !body.imageData) return error(response, 400, 'Add a message or an image.');
    if (!validEmail(email)) return error(response, 400, 'Enter a valid email or leave it blank.');
    const ownerToken = token(); const timestamp = now();
    const result = db.prepare('INSERT INTO posts(display_name,email,body,owner_token_hash,created_at,updated_at) VALUES(?,?,?,?,?,?)').run(name, email || null, text, hash(ownerToken), timestamp, timestamp);
    const id = Number(result.lastInsertRowid);
    try {
      const imageKey = await uploadImage(body.imageData, id);
      if (imageKey) db.prepare('UPDATE posts SET image_key = ? WHERE id = ?').run(imageKey, id);
    } catch (uploadError) {
      db.prepare('DELETE FROM posts WHERE id = ?').run(id);
      return error(response, 400, uploadError.message);
    }
    return json(response, 201, { post: publicPost(db.prepare('SELECT * FROM posts WHERE id = ?').get(id)), ownerToken });
  }
  const match = url.pathname.match(/^\/api\/community\/posts\/(\d+)(?:\/(comments|react|reshare|report))?$/);
  if (!match) return error(response, 404, 'Community route not found.');
  const id = Number(match[1]);
  const action = match[2] || '';
  const post = db.prepare('SELECT * FROM posts WHERE id = ? AND deleted_at IS NULL').get(id);
  if (!post) return error(response, 404, 'Post not found.');
  if (!action && request.method === 'PATCH') {
    if (hash(String(request.headers['x-owner-token'] || bodyToken(request))) !== post.owner_token_hash) return error(response, 403, 'This private edit link is not valid.');
    const body = await readJson(request, response); if (!body) return;
    const text = clean(body.body, 5000); if (!text && !post.image_key) return error(response, 400, 'A post needs text or an image.');
    db.prepare('UPDATE posts SET body = ?,updated_at = ? WHERE id = ?').run(text, now(), id);
    return json(response, 200, { post: publicPost(db.prepare('SELECT * FROM posts WHERE id = ?').get(id)) });
  }
  if (!action && request.method === 'DELETE') {
    if (hash(String(request.headers['x-owner-token'] || bodyToken(request))) !== post.owner_token_hash) return error(response, 403, 'This private delete link is not valid.');
    db.prepare('UPDATE posts SET deleted_at = ? WHERE id = ?').run(now(), id); return json(response, 200, { ok: true });
  }
  if (!allow(request, response, action || 'post', action === 'react' ? 60 : 20)) return;
  const body = await readJson(request, response); if (!body) return;
  if (action === 'comments') {
    const text = clean(body.body, 1200); const name = clean(body.displayName, 60) || 'Anonymous'; const email = clean(body.email, 160);
    if (!text || !validEmail(email)) return error(response, 400, 'Add a message and a valid email if you want notifications.');
    const commentToken = token(); const timestamp = now();
    const result = db.prepare('INSERT INTO comments(post_id,parent_id,display_name,email,body,owner_token_hash,created_at) VALUES(?,?,?,?,?,?,?)').run(id, Number(body.parentId) || null, name, email || null, text, hash(commentToken), timestamp);
    void notify(post.email, 'Someone replied to your Fab OS community post', `${name} replied:\n\n${text}\n\nView the discussion: https://fabos.patienceai.in/community/`);
    return json(response, 201, { comment: { id: Number(result.lastInsertRowid), displayName: name, body: text, createdAt: timestamp }, ownerToken: commentToken });
  }
  if (action === 'react') {
    const kind = body.kind === 'reshare' ? 'reshare' : 'like'; const visitor = clean(body.visitorId, 120);
    if (!visitor) return error(response, 400, 'A browser visitor id is required.');
    const inserted = db.prepare('INSERT OR IGNORE INTO reactions(post_id,visitor_id,kind,created_at) VALUES(?,?,?,?)').run(id, hash(visitor), kind, now());
    if (Number(inserted.changes) && kind === 'like') void notify(post.email, 'Someone liked your Fab OS community post', 'Someone liked your Fab OS community post.');
    return json(response, 200, publicPost(post));
  }
  if (action === 'reshare') {
    const name = clean(body.displayName, 60) || 'Anonymous'; const email = clean(body.email, 160); if (!validEmail(email)) return error(response, 400, 'Enter a valid email or leave it blank.');
    const ownerToken = token(); const timestamp = now(); const result = db.prepare('INSERT INTO posts(display_name,email,body,owner_token_hash,reshared_post_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)').run(name, email || null, '', hash(ownerToken), id, timestamp, timestamp);
    db.prepare('INSERT OR IGNORE INTO reactions(post_id,visitor_id,kind,created_at) VALUES(?,?,?,?)').run(id, hash(ownerToken), 'reshare', timestamp);
    void notify(post.email, 'Someone reshared your Fab OS community post', `${name} reshared your post. View it: https://fabos.patienceai.in/community/`);
    return json(response, 201, { post: publicPost(db.prepare('SELECT * FROM posts WHERE id = ?').get(Number(result.lastInsertRowid))), ownerToken });
  }
  if (action === 'report') {
    const reason = clean(body.reason, 500); if (!reason) return error(response, 400, 'Add a report reason.');
    db.prepare('INSERT INTO reports(post_id,reason,created_at) VALUES(?,?,?)').run(id, reason, now()); return json(response, 201, { ok: true });
  }
  return error(response, 405, 'Unsupported community action.');
}
function bodyToken(request) { return request.headers['x-owner-token'] || ''; }
async function media(response, key) {
  if (!r2 || !key.startsWith('posts/')) return error(response, 404, 'Image not found.');
  try { const result = await r2.send(new GetObjectCommand({ Bucket: bucket, Key: key })); response.writeHead(200, { 'Content-Type': result.ContentType || 'image/webp', 'Cache-Control': 'public, max-age=31536000, immutable' }); result.Body.pipe(response); } catch { error(response, 404, 'Image not found.'); }
}
function staticFile(response, pathname) {
  const relative = pathname === '/' ? '/index.html' : pathname.endsWith('/') ? `${pathname}index.html` : pathname;
  const filename = path.resolve(websiteRoot, `.${relative}`); if (!filename.startsWith(websiteRoot)) return error(response, 403, 'Forbidden.');
  fs.stat(filename).then(stat => { if (!stat.isFile()) throw new Error(); response.writeHead(200, { 'Content-Type': mime[path.extname(filename)] || 'application/octet-stream', 'Cache-Control': 'no-store' }); createReadStream(filename).pipe(response); }).catch(() => error(response, 404, 'Not found.'));
}
createServer(async (request, response) => {
  const url = new URL(request.url || '/', `http://${request.headers.host || 'localhost'}`);
  if (url.pathname.startsWith('/api/community/media/')) return media(response, decodeURIComponent(url.pathname.slice('/api/community/media/'.length)));
  if (url.pathname.startsWith('/api/community/')) return api(request, response, url);
  return staticFile(response, url.pathname);
}).listen(port, '127.0.0.1', () => console.log(`Fab OS community: http://127.0.0.1:${port}/community/`));
