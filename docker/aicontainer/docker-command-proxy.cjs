#!/usr/bin/env node
'use strict';

// Node.js標準ライブラリだけで、制限されたDocker Socket Proxyへ接続する。
const http = require('node:http');
const { once } = require('node:events');

class DockerError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function request(socketPath, method, path, body, upgrade = false) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? '' : JSON.stringify(body);
    const req = http.request({
      socketPath, method, path, agent: false,
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data),
        ...(upgrade ? { Connection: 'Upgrade', Upgrade: 'tcp' } : {}),
      },
    });
    // 接続・API応答だけにタイムアウトを適用。実行時間は制限しない。
    const timer = setTimeout(() => req.destroy(new Error('Docker proxy request timed out')), 10000);
    const clearTimer = () => clearTimeout(timer);
    req.once('error', (error) => { clearTimer(); reject(error); });
    req.once('upgrade', (res, socket, head) => {
      clearTimer();
      if (res.statusCode !== 101) {
        socket.destroy();
        reject(new DockerError(res.statusCode, 'Unexpected Docker upgrade response'));
        return;
      }
      socket.allowHalfOpen = true;
      if (head.length) socket.unshift(head);
      resolve({ stream: socket, socket });
    });
    req.once('response', async (res) => {
      clearTimer();
      if (upgrade && res.statusCode === 200) {
        res.socket.allowHalfOpen = true;
        resolve({ stream: res, socket: res.socket });
        return;
      }
      try {
        const chunks = [];
        for await (const chunk of res) chunks.push(chunk);
        const text = Buffer.concat(chunks).toString();
        let result;
        try { result = text ? JSON.parse(text) : null; }
        catch { throw new DockerError(res.statusCode, text || 'Invalid Docker response'); }
        if (res.statusCode >= 300) {
          throw new DockerError(res.statusCode, result?.message || `Docker HTTP ${res.statusCode}`);
        }
        resolve(result);
      } catch (error) { reject(error); }
    });
    req.end(data);
  });
}

async function write(output, data) {
  if (!output.write(data)) await once(output, 'drain');
}

async function copyOutput(stream, tty) {
  // Dockerの非TTY出力は、stdout/stderrごとに8バイトのヘッダーを持つ。
  const header = Buffer.alloc(8);
  let headerSize = 0;
  let remaining = 0;
  let output;
  for await (const chunk of stream) {
    if (tty) {
      await write(process.stdout, chunk);
      continue;
    }
    let offset = 0;
    while (offset < chunk.length) {
      if (!remaining) {
        const size = Math.min(8 - headerSize, chunk.length - offset);
        chunk.copy(header, headerSize, offset, offset + size);
        headerSize += size;
        offset += size;
        if (headerSize < 8) continue;
        if (![1, 2].includes(header[0]) || header[1] || header[2] || header[3]) {
          throw new Error('Invalid Docker output stream');
        }
        output = header[0] === 2 ? process.stderr : process.stdout;
        remaining = header.readUInt32BE(4);
        headerSize = 0;
      }
      const size = Math.min(remaining, chunk.length - offset);
      await write(output, chunk.subarray(offset, offset + size));
      remaining -= size;
      offset += size;
    }
  }
  if (headerSize || remaining) throw new Error('Truncated Docker output stream');
}

async function execute(socketPath, id, tty) {
  const { stream, socket } = await request(socketPath, 'POST', `/exec/${id}/start`,
    { Detach: false, Tty: tty }, true);
  const wasRaw = process.stdin.isRaw;
  const resize = () => {
    request(socketPath, 'POST', `/exec/${id}/resize?h=${process.stdout.rows || 24}&w=${process.stdout.columns || 80}`)
      .catch(() => {});
  };
  const onInputEnd = () => socket.end();
  const onSocketError = () => {}; // copyOutputがストリームのエラーを報告する。
  const onSignal = (signal) => {
    if (tty) process.stdin.setRawMode(Boolean(wasRaw));
    socket.destroy();
    process.exit(signal === 'SIGINT' ? 130 : 143);
  };
  const onInterrupt = () => onSignal('SIGINT');
  const onTerminate = () => onSignal('SIGTERM');
  // HTTP 200の場合、出力Readableと入力Socketは別オブジェクトになる。
  socket.on('error', onSocketError);
  process.on('SIGINT', onInterrupt);
  process.on('SIGTERM', onTerminate);
  if (tty) {
    process.stdin.setRawMode(true);
    process.stdout.on('resize', resize);
    resize();
  }
  process.stdin.pipe(socket, { end: false });
  process.stdin.once('end', onInputEnd);
  if (process.stdin.readableEnded) onInputEnd();
  try {
    await copyOutput(stream, tty);
  } finally {
    process.stdin.unpipe(socket);
    process.stdin.pause();
    process.stdin.removeListener('end', onInputEnd);
    process.removeListener('SIGINT', onInterrupt);
    process.removeListener('SIGTERM', onTerminate);
    if (tty) {
      process.stdin.setRawMode(Boolean(wasRaw));
      process.stdout.removeListener('resize', resize);
    }
    socket.destroy();
    socket.removeListener('error', onSocketError);
  }
  const info = await request(socketPath, 'GET', `/exec/${id}/json`);
  if (info.Running || !Number.isInteger(info.ExitCode) || info.ExitCode < 0 || info.ExitCode > 255) {
    throw new Error('Docker did not return a completed command exit code');
  }
  return info.ExitCode;
}

function isMissingExecutable(error) {
  // 実行済みコマンドの終了コード127や、作業ディレクトリの不存在では再試行しない。
  return error instanceof DockerError && error.status >= 400
    && /executable file not found in \$PATH/.test(error.message);
}

async function main() {
  const cmd = process.argv.slice(2);
  if (!cmd.length) {
    console.error('Usage: docker-command-proxy COMMAND [ARG ...]');
    return 2;
  }
  if (cmd[0].includes('/')) {
    console.error(`${cmd[0]}: command not found`);
    return 127;
  }
  const host = process.env.DOCKER_HOST || '';
  if (!host.startsWith('unix:///')) {
    console.error(`${cmd[0]}: command not found`);
    return 127;
  }
  const socketPath = host.slice('unix://'.length);
  const containers = await request(socketPath, 'GET', '/containers/json');
  if (!Array.isArray(containers)) throw new Error('Invalid Docker containers response');
  // 一覧はプロキシが許可済み・起動中コンテナだけにフィルタする。
  const visible = new Set(containers.flatMap((c) => c.Names || []).map((n) => n.replace(/^\//, '')));
  const configured = (process.env.DOCKER_PROXY_CONTAINERS || '')
    .replaceAll('\r\n', '\n')
    .replaceAll('\r', '\n')
    .split('\n').map((n) => n.trim()).filter(Boolean);
  const targets = [...new Set(configured.length ? configured : visible)].filter((n) => visible.has(n));
  const tty = Boolean(process.stdin.isTTY && process.stdout.isTTY && process.stderr.isTTY);
  for (const container of targets) {
    const exec = await request(socketPath, 'POST', `/containers/${encodeURIComponent(container)}/exec`, {
      Cmd: cmd, WorkingDir: process.cwd(),
      AttachStdin: true, AttachStdout: true, AttachStderr: true, Tty: tty,
    });
    if (!exec || !/^[a-zA-Z0-9]+$/.test(exec.Id)) throw new Error('Invalid Docker exec ID');
    try {
      return await execute(socketPath, exec.Id, tty);
    } catch (error) {
      if (!isMissingExecutable(error)) throw error;
    }
  }
  console.error(`${cmd[0]}: command not found in registered running containers`);
  return 127;
}

main().then((code) => { process.exitCode = code; }).catch((error) => {
  console.error(`docker-command-proxy: ${error.message}`);
  process.exitCode = error instanceof DockerError && error.status === 403 ? 126 : 125;
});
