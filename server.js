const http = require('http');
const https = require('https');
const { URL } = require('url');

const PORT = process.env.PORT || 3000;
const TARGET = process.env.TARGET || 'http://example.com';

const targetUrl = new URL(TARGET);
const targetClient = targetUrl.protocol === 'https:' ? https : http;

const colors = {
  reset: '\x1b[0m',
  dim: '\x1b[2m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  magenta: '\x1b[35m',
  cyan: '\x1b[36m',
};

function colorStatus(status) {
  if (status >= 500) return colors.red;
  if (status >= 400) return colors.yellow;
  if (status >= 300) return colors.cyan;
  if (status >= 200) return colors.green;
  return colors.dim;
}

function colorMethod(method) {
  switch (method) {
    case 'GET': return colors.blue;
    case 'POST': return colors.green;
    case 'PUT': return colors.yellow;
    case 'DELETE': return colors.red;
    case 'HEAD': return colors.magenta;
    default: return colors.cyan;
  }
}

let reqCounter = 0;

function logIncoming(id, req) {
  const ts = new Date().toISOString();
  const m = colorMethod(req.method);
  console.log(
    `${colors.dim}[${ts}]${colors.reset} ${colors.dim}#${id}${colors.reset} ` +
    `${m}${req.method.padEnd(6)}${colors.reset} ${req.url} ` +
    `${colors.dim}from ${req.socket.remoteAddress}${colors.reset}`
  );
}

function logOutgoing(id, status, durationMs, note = '') {
  const c = colorStatus(status);
  const extra = note ? ` ${colors.dim}(${note})${colors.reset}` : '';
  console.log(
    `${colors.dim}      └─ #${id}${colors.reset} ` +
    `${c}${status}${colors.reset} ${colors.dim}${durationMs}ms${colors.reset}${extra}`
  );
}

const server = http.createServer((req, res) => {
  const id = ++reqCounter;
  const start = Date.now();
  logIncoming(id, req);

  if (req.method === 'HEAD') {
    res.writeHead(200);
    res.end();
    logOutgoing(id, 200, Date.now() - start, 'HEAD short-circuit');
    return;
  }

  const proxyReq = targetClient.request(
    {
      protocol: targetUrl.protocol,
      hostname: targetUrl.hostname,
      port: targetUrl.port || (targetUrl.protocol === 'https:' ? 443 : 80),
      method: req.method,
      path: req.url,
      headers: { ...req.headers, host: targetUrl.host },
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode, proxyRes.headers);
      proxyRes.pipe(res);
      proxyRes.on('end', () => {
        logOutgoing(id, proxyRes.statusCode, Date.now() - start, `proxied → ${targetUrl.host}`);
      });
    }
  );

  proxyReq.on('error', (err) => {
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'text/plain' });
      res.end('Bad Gateway');
    }
    logOutgoing(id, 502, Date.now() - start, `proxy error: ${err.message}`);
  });

  req.pipe(proxyReq);
});

server.listen(PORT, () => {
  console.log(
    `${colors.green}▶ proxy${colors.reset} listening on ${colors.cyan}http://localhost:${PORT}${colors.reset} ` +
    `→ forwarding to ${colors.cyan}${TARGET}${colors.reset}`
  );
});
