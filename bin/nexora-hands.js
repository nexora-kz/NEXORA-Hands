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

function refreshPath() {
  const machine = process.env.Path || process.env.PATH || '';
  const user = process.env.Path || process.env.PATH || '';
  process.env.Path = `${machine};${user}`;
}

function hasUsablePython() {
  let r = run('python', ['--version']);
  if (r.status === 0) return true;
  r = run('py', ['--version']);
  return r.status === 0;
}

function installPython() {
  console.log('[BOOT] Python not found. Installing Python automatically...');
  const wingetArgs = [
    'install', '--id', 'Python.Python.3.13', '--exact', '--source', 'winget',
    '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity'
  ];
  let r = run('winget', wingetArgs);
  if (r.status === 0) return true;

  console.log('[BOOT] Python 3.13 install failed. Trying Python 3.14...');
  r = run('winget', [
    'install', '--id', 'Python.Python.3.14', '--exact', '--source', 'winget',
    '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity'
  ]);
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

if (!hasUsablePython() && !installPython()) {
  console.error('[ERROR] Python could not be installed automatically.');
  console.error('[ERROR] Windows Package Manager (winget) did not install a usable Python runtime.');
  process.exit(1);
}

refreshPath();
if (!hasUsablePython()) {
  console.error('[ERROR] Python installation finished, but Python is not available in this process.');
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
