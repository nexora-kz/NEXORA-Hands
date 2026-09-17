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

function pythonCandidates() {
  const local = process.env.LOCALAPPDATA || '';
  return [
    path.join(local, 'Programs', 'Python', 'Python314', 'python.exe'),
    path.join(local, 'Programs', 'Python', 'Python313', 'python.exe'),
    'C:\\Python314\\python.exe',
    'C:\\Python313\\python.exe'
  ];
}

function findPython() {
  refreshPath();
  for (const candidate of pythonCandidates()) {
    if (candidate.length > 3 && fs.existsSync(candidate)) return candidate;
  }
  let r = run('python', ['--version']);
  if (r.status === 0) return 'python';
  r = run('py', ['--version']);
  if (r.status === 0) return 'py';
  return null;
}

function installPython() {
  const version = '3.14.7';
  const installer = path.join(process.env.TEMP || '.', `NEXORA-Python-${version}-amd64.exe`);
  const url = `https://www.python.org/ftp/python/${version}/python-${version}-amd64.exe`;
  console.log('[BOOT] Python not found. Downloading official Python installer...');

  const download = run('powershell.exe', [
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    `$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri '${url}' -OutFile '${installer}'`
  ]);
  if (download.status !== 0 || !fs.existsSync(installer)) return false;

  console.log('[BOOT] Installing Python for the current Windows user...');
  const install = run(installer, [
    '/quiet',
    'InstallAllUsers=0',
    'PrependPath=1',
    'Include_pip=1',
    'Include_launcher=1',
    'SimpleInstall=1'
  ]);
  try { fs.unlinkSync(installer); } catch {}
  if (install.status !== 0) return false;

  refreshPath();
  return !!findPython();
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

let Python = findPython();
if (!Python) {
  if (!installPython()) {
    console.error('[ERROR] Python could not be installed automatically.');
    console.error('[ERROR] The official Python installer did not produce a usable runtime.');
    process.exit(1);
  }
  Python = findPython();
}

if (!Python) {
  console.error('[ERROR] Python installation finished, but Python is not available.');
  process.exit(1);
}

if (!fs.existsSync(start)) {
  console.error(`[ERROR] Launcher payload is incomplete: ${start}`);
  process.exit(1);
}

console.log(`[BOOT] Python runtime ready: ${Python}`);
console.log('[BOOT] Starting NEXORA Hands in this PowerShell console...');

const r = spawnSync('powershell.exe', [
  '-NoProfile',
  '-ExecutionPolicy', 'Bypass',
  '-File', start
], { stdio: 'inherit', cwd: root, shell: false });

process.exit(r.status ?? 1);
