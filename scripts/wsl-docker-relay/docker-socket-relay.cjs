// Private, unfiltered engine connection for the trusted WSL launcher only.
// This is separate from the restricted Docker Socket Proxy exposed to AI tools.
// run-relay.sh invokes this CLI: serve relays bytes; check probes /_ping.
'use strict';
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const { setTimeout: delay } = require('node:timers/promises');

function socketPath(value) {
    // Linux sockaddr_un has 108 bytes, including the terminating NUL.
    if (!value || !value.startsWith('/') || value.includes('\0') || Buffer.byteLength(value) > 107) {
        throw new Error('Expected an absolute Unix socket path of at most 107 bytes.');
    }
    return value;
}

function ping(path) {
    return new Promise((resolve, reject) => {
        const request = http.get({ socketPath: path, path: '/_ping', agent: false }, (response) => {
            let body = '';
            response.setEncoding('utf8');
            response.on('data', (chunk) => {
                body += chunk;
                if (body.length > 64) request.destroy(new Error('Unexpected Docker ping response.'));
            });
            response.on('error', reject);
            response.on('end', () => {
                if (response.statusCode === 200 && body.trim() === 'OK') resolve();
                else reject(new Error('Docker ping did not return OK.'));
            });
        });
        request.on('error', reject);
        // Also bound total time when the peer keeps sending partial responses.
        const timer = setTimeout(() => request.destroy(new Error('Docker ping timed out.')), 1000);
        request.on('close', () => clearTimeout(timer));
    });
}

async function check(path, timeout) {
    const deadline = Date.now() + timeout;
    do {
        try { await ping(path); return; }
        catch (error) {
            if (Date.now() >= deadline) throw new Error(`Docker relay check failed: ${error.message}`);
        }
        await delay(100);
    } while (true);
}

function serve(path, upstream) {
    if (path === upstream) throw new Error('Relay and upstream sockets must differ.');
    // Fail on an existing path instead of unlinking another process's socket.
    if (fs.existsSync(path)) throw new Error('Relay socket path already exists.');
    process.umask(0o077);
    const peers = new Set();
    const server = net.createServer({ allowHalfOpen: true }, (client) => {
        const engine = net.createConnection({ path: upstream, allowHalfOpen: true });
        peers.add(client);
        peers.add(engine);
        const abort = () => { client.destroy(); engine.destroy(); };
        client.on('error', abort);
        engine.on('error', abort);
        for (const socket of [client, engine]) {
            socket.on('close', () => {
                peers.delete(socket);
                if (!socket.readableEnded || !socket.writableFinished) abort();
            });
        }
        // Raw duplex pipes preserve HTTP upgrade/hijack and stdin half-close.
        // Node streams apply backpressure in both directions.
        client.pipe(engine);
        engine.pipe(client);
    });
    server.on('error', (error) => {
        console.error(`Docker relay failed: ${error.code || error.message}`);
        for (const socket of peers) socket.destroy();
        process.exitCode = 1;
    });
    server.listen(path, () => fs.chmodSync(path, 0o600));
    const stop = () => {
        server.close();
        for (const socket of peers) socket.destroy();
    };
    process.on('SIGTERM', stop);
    process.on('SIGINT', stop);
}

async function main() {
    const [mode, path, option] = process.argv.slice(2);
    socketPath(path);
    if (mode === 'serve') serve(path, socketPath(option));
    else if (mode === 'check') {
        const timeout = option === undefined ? 10000 : Number(option);
        if (!Number.isInteger(timeout) || timeout < 1 || timeout > 60000) throw new Error('Invalid check timeout.');
        await check(path, timeout);
    } else throw new Error('Usage: docker-socket-relay.cjs serve SOCKET UPSTREAM | check SOCKET [TIMEOUT_MS]');
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
