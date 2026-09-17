#!/usr/bin/env node
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const start = path.join(root, 'start.ps1');
const mode = process.argv[2] || 'remote';

function run(command, args) {
  return spawnSync(command, args, { stdio: 'inherit', shell: false });
}

function hasPython() {
  const r = run('py', ['-3.14', '--version']);
  return r.status === 0;
}

function installPython() {
  console.log('[BOOT] Python 3.14 not found. Installing with Windows Package Manager...');
  const r = run('winget', ['install', '--id', 'Python.Python.3.14', '--exact', '--accept-source-agreements', '--accept-package-agreements']);
  return r.status === 0;
}

console.log('============================================================');
console.log('                 NEXORA HANDS');
console.log('============================================================');
console.log(`[BOOT] Mode: ${mode}`);
console.log(`[BOOT] Node.js: ${process.version}`);
if (mode !== 'remote') {
  console.error('[ERROR] Unknown command. Use: nexora-hands remote');
  process.exit(2);
}

if (!hasPython() && !installPython()) {
  console.error('[ERROR] Python 3.14 could not be installed automatically.');
  process.exit(1);
}

if (!fs.existsSync(start)) {
  console.error(`[ERROR] Launcher payload is incomplete: ${start}`);
  process.exit(1);
}

console.log('[BOOT] Python runtime ready.');
console.log('[BOOT] Starting NEXORA Hands in this PowerShell console...');

const r = spawnSync('powershell.exe', [
  '-NoProfile',
  '-ExecutionPolicy', 'Bypass',
  '-File', start
], { stdio: 'inherit', cwd: root, shell: false });

process.exit(r.status ?? 1);
