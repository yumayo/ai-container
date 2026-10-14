'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const toolDirectory = '/opt/aicontainer/bin';

// 起動設定だけを読み、RPC・ユーザー設定の保存・シェルでの再解釈は行わない。
let profile;
let tool;
let baseArguments;
try {
    const [filename, requestedTool, ...providedArguments] = process.argv.slice(2);
    if (requestedTool !== 'claude' && requestedTool !== 'codex') throw new Error();
    const contents = fs.readFileSync(filename);
    if (contents.length > 1048576) throw new Error();
    profile = JSON.parse(contents.toString('utf8'));
    if (profile === null || Array.isArray(profile) || typeof profile !== 'object') throw new Error();
    if (Object.keys(profile).sort().join(',') !== 'arguments,environment') throw new Error();
    if (!Array.isArray(profile.arguments) || profile.arguments.length > 1000 || profile.arguments.some(value => typeof value !== 'string' || value.includes('\0'))) throw new Error();
    if (profile.environment === null || Array.isArray(profile.environment) || typeof profile.environment !== 'object' || Object.keys(profile.environment).length > 1000) throw new Error();
    for (const [name, value] of Object.entries(profile.environment)) {
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || typeof value !== 'string' || value.includes('\0')) throw new Error();
    }
    tool = requestedTool;
    baseArguments = providedArguments;
} catch {
    // JSON の構文エラーも内容を含むことがあるため、秘密値や原文は出力しない。
    process.stderr.write('aicontainer: 起動プロファイルが無効か、読み込めません。\n');
    process.exit(1);
}

const child = spawn(path.join(toolDirectory, tool), [...baseArguments, ...profile.arguments], {
    stdio: 'inherit',
    env: { ...process.env, ...profile.environment },
});
// TTY の割込み・切断は同じ前景プロセスグループの子へも直接届くため、再送しない。
for (const signal of ['SIGINT', 'SIGHUP']) {
    process.on(signal, () => {
        if (!process.stdin.isTTY) child.kill(signal);
    });
}
// コンテナ停止など、親 PID だけへの終了要求は子へ伝える。
process.on('SIGTERM', () => child.kill('SIGTERM'));
child.on('error', () => {
    process.stderr.write('aicontainer: AI ツールを起動できません。\n');
    process.exitCode = 1;
});
child.on('exit', (code, signal) => {
    if (signal) {
        process.removeAllListeners(signal);
        process.kill(process.pid, signal);
    }
    else process.exitCode = code;
});
