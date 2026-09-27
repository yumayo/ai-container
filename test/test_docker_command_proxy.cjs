'use strict';

// DockerなしでUnix socket、HTTP upgrade、Bashのフォールバックを検証する。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { once } = require('node:events');

const client = path.resolve(__dirname, '../docker/aicontainer/docker-command-proxy.cjs');
const hook = path.resolve(__dirname, '../docker/aicontainer/command-not-found.bash');
const missing = 'aicontainer-missing-command-for-test';

function frame(channel, text) {
  const data = Buffer.from(text);
  const header = Buffer.alloc(8);
  header[0] = channel;
  header.writeUInt32BE(data.length, 4);
  return Buffer.concat([header, data]);
}

async function fixture(t, options = {}) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'command-proxy-'));
  const socketPath = path.join(dir, 'docker.sock');
  const calls = [];
  const sockets = new Set();
  const execs = new Map();
  const containers = options.containers || ['first', 'second'];
  const json = (res, code, value) => {
    res.writeHead(code, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(value));
  };
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks)) : null;
    calls.push({ method: req.method, path: req.url, body });
    if (req.url === '/containers/json') {
      json(res, 200, containers.map((name) => ({ Names: [`/${name}`] })));
    } else if (/^\/containers\/[^/]+\/exec$/.test(req.url)) {
      if (options.forbidden) return json(res, 403, { message: 'Forbidden: command is not allowed' });
      const container = decodeURIComponent(req.url.split('/')[2]);
      const id = `exec${execs.size}`;
      execs.set(id, container);
      json(res, 201, { Id: id });
    } else if (/^\/exec\/\w+\/json$/.test(req.url)) {
      json(res, 200, { Running: false, ExitCode: options.exitCode || 0 });
    } else if (req.url.includes('/resize?')) {
      json(res, 200, null);
    } else {
      json(res, 403, { message: `Forbidden: ${req.url}` });
    }
  });
  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.once('close', () => sockets.delete(socket));
  });
  server.on('upgrade', async (req, socket, head) => {
    socket.allowHalfOpen = true;
    socket.on('error', () => {});
    const length = Number(req.headers['content-length']);
    let body = head;
    while (body.length < length) {
      const [chunk] = await once(socket, 'data');
      body = Buffer.concat([body, chunk]);
    }
    const start = JSON.parse(body.subarray(0, length));
    calls.push({ method: req.method, path: req.url, body: start });
    const container = execs.get(req.url.split('/')[2]);
    if ((options.absent || []).includes(container) || options.startError) {
      const message = options.startError || `exec: "${missing}": executable file not found in $PATH: unknown`;
      const data = JSON.stringify({ message });
      socket.end(`HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nContent-Length: ${Buffer.byteLength(data)}\r\nConnection: close\r\n\r\n${data}`);
      return;
    }
    socket.write(options.http200
      ? 'HTTP/1.1 200 OK\r\nContent-Type: application/vnd.docker.raw-stream\r\nConnection: close\r\n\r\n'
      : 'HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n');
    if (options.echoInput) {
      const input = [body.subarray(length)];
      socket.on('data', (chunk) => input.push(chunk));
      socket.on('end', () => socket.end(frame(1, Buffer.concat(input))));
    } else {
      const data = options.output || (start.Tty ? Buffer.from('tty output\n')
        : Buffer.concat([frame(1, 'stdout\n'), frame(2, 'stderr\n')]));
      // ヘッダーとペイロードを途中で分割する。
      socket.write(data.subarray(0, 3));
      setImmediate(() => socket.end(data.subarray(3)));
    }
  });
  await new Promise((resolve) => server.listen(socketPath, resolve));
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => server.close(resolve));
    await fs.rm(dir, { recursive: true, force: true });
  });
  const run = async (args = [missing], config = {}) => {
    const quote = (value) => `'${value.replaceAll("'", "'\\''")}'`;
    const binary = config.tty ? 'script' : (config.bash ? 'bash' : process.execPath);
    const command = config.tty ? ['-q', '-e', '-c', [process.execPath, client, ...args].map(quote).join(' '), '/dev/null']
      : (config.bash ? ['--noprofile', '--norc', '-c', config.bash] : [client, ...args]);
    const child = spawn(binary, command, {
        cwd: dir,
        env: { ...process.env, BASH_ENV: hook, DOCKER_HOST: `unix://${socketPath}`,
          DOCKER_PROXY_CONTAINERS: containers.join('\n'), ...config.env },
        stdio: ['pipe', 'pipe', 'pipe'],
      });
    const stdout = [], stderr = [];
    child.stdout.on('data', (chunk) => stdout.push(chunk));
    child.stderr.on('data', (chunk) => stderr.push(chunk));
    child.stdin.on('error', () => {});
    child.stdin.end(config.input || '');
    const [code, signal] = await once(child, 'close');
    assert.equal(signal, null);
    return { code, stdout: Buffer.concat(stdout).toString(), stderr: Buffer.concat(stderr).toString() };
  };
  return { run, calls, dir };
}

test('missing command tries configured containers and preserves argv, cwd, streams and exit code', async (t) => {
  const { run, calls, dir } = await fixture(t, { containers: ['first', 'second', 'third'], absent: ['second', 'third'], exitCode: 7 });
  const args = [missing, 'argument with spaces', '', '$(touch injected)', '"quoted"'];
  const result = await run(args, { env: { DOCKER_PROXY_CONTAINERS: ' second \r third \r\n\n first ' } });
  assert.deepEqual(result, { code: 7, stdout: 'stdout\n', stderr: 'stderr\n' });
  const creates = calls.filter((c) => c.path.startsWith('/containers/') && c.path.endsWith('/exec'));
  assert.deepEqual(creates.map((c) => c.path), ['/containers/second/exec', '/containers/third/exec', '/containers/first/exec']);
  assert.deepEqual(creates[2].body, {
    Cmd: args, WorkingDir: dir, AttachStdin: true, AttachStdout: true, AttachStderr: true, Tty: false,
  });
  await assert.rejects(fs.stat(path.join(dir, 'injected')), { code: 'ENOENT' });
});

test('stdin is preserved after an absent executable and EOF reaches the remote command', async (t) => {
  const { run } = await fixture(t, { absent: ['first'], echoInput: true });
  const input = '入力\n'.repeat(100000);
  const result = await run([missing], { input });
  assert.equal(result.code, 0);
  assert.equal(result.stdout, input);
  assert.equal(result.stderr, '');
});

test('HTTP 200 raw-stream response also preserves stdin and stdout', async (t) => {
  const { run } = await fixture(t, { http200: true, echoInput: true });
  const result = await run([missing], { input: 'http200 input\n' });
  assert.deepEqual(result, { code: 0, stdout: 'http200 input\n', stderr: '' });
});

test('interactive terminal requests TTY and reads unframed output', {
  skip: spawnSync('script', ['--version']).status !== 0,
}, async (t) => {
  const { run, calls } = await fixture(t, { exitCode: 3 });
  const result = await run([missing], { tty: true });
  assert.equal(result.code, 3);
  assert.match(result.stdout, /tty output/);
  assert.equal(result.stderr, '');
  assert.equal(calls.find((c) => c.path.endsWith('/exec')).body.Tty, true);
  assert.equal(calls.find((c) => c.path.endsWith('/start')).body.Tty, true);
});

test('executed command exit code 127 never triggers a second execution', async (t) => {
  const { run, calls } = await fixture(t, { exitCode: 127 });
  assert.equal((await run()).code, 127);
  assert.equal(calls.filter((c) => c.path.endsWith('/start')).length, 1);
});

test('allow-list denial is reported without starting or retrying the command', async (t) => {
  const { run, calls } = await fixture(t, { forbidden: true });
  const result = await run();
  assert.equal(result.code, 126);
  assert.match(result.stderr, /Forbidden/);
  assert.equal(calls.filter((c) => c.path.endsWith('/exec')).length, 1);
  assert.equal(calls.filter((c) => c.path.endsWith('/start')).length, 0);
});

test('a missing working directory stops execution rather than trying another container', async (t) => {
  const { run, calls } = await fixture(t, { startError: 'chdir to cwd: no such file or directory' });
  assert.equal((await run()).code, 125);
  assert.equal(calls.filter((c) => c.path.endsWith('/start')).length, 1);
});

test('all absent executables return 127', async (t) => {
  const { run } = await fixture(t, { absent: ['first', 'second'] });
  const result = await run();
  assert.equal(result.code, 127);
  assert.match(result.stderr, /command not found/);
});

test('an empty allowed container list returns 127 without creating an exec', async (t) => {
  const { run, calls } = await fixture(t, { containers: [] });
  assert.equal((await run()).code, 127);
  assert.equal(calls.length, 1);
});

test('disconnected proxy returns an actionable error', async (t) => {
  const { run, dir } = await fixture(t);
  const result = await run([missing], { env: { DOCKER_HOST: `unix://${dir}/missing.sock` } });
  assert.equal(result.code, 125);
  assert.match(result.stderr, /ENOENT/);
});

test('Bash keeps local commands local and does not proxy explicit paths or a disabled proxy', async (t) => {
  const { run, calls } = await fixture(t);
  const local = await run([], { bash: 'printf local' });
  assert.deepEqual(local, { code: 0, stdout: 'local', stderr: '' });
  const disabled = await run([], { bash: missing, env: { DOCKER_HOST: '' } });
  assert.equal(disabled.code, 127);
  const explicit = await run([`./${missing}`]);
  assert.equal(explicit.code, 127);
  assert.equal(calls.length, 0);
});

test('Bash forwards missing commands from a noninteractive shell', async (t) => {
  // hook内のインストールパスだけを一時コピーで置き換える。
  const { run, dir, calls } = await fixture(t, { exitCode: 9 });
  const localHook = path.join(dir, 'hook.bash');
  await fs.writeFile(localHook, (await fs.readFile(hook, 'utf8'))
    .replace('/usr/local/lib/aicontainer/docker-command-proxy.cjs', JSON.stringify(client)));
  const result = await run([], { bash: `${missing} 'two words' ''`, env: { BASH_ENV: localHook } });
  assert.deepEqual(result, { code: 9, stdout: 'stdout\n', stderr: 'stderr\n' });
  assert.deepEqual(calls.find((c) => c.path.endsWith('/exec')).body.Cmd, [missing, 'two words', '']);
});

test('truncated output is an error and never executes on a second container', async (t) => {
  const { run, calls } = await fixture(t, { output: frame(1, 'payload').subarray(0, 10) });
  const result = await run();
  assert.equal(result.code, 125);
  assert.match(result.stderr, /Truncated/);
  assert.equal(calls.filter((c) => c.path.endsWith('/start')).length, 1);
});
