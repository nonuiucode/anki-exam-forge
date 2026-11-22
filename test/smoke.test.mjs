import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { resolvePythonSync } from '../server/adapter.mjs';

const runFile = promisify(execFile);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const REPO_ROOT = resolve(__dirname, '..');

test('MCP stdio JSON-RPC exposes all 8 tools and runs full freq -> build -> inspect loop', async () => {
  const tmpDir = await mkdtemp(join(tmpdir(), 'aef-mcp-test-'));
  const sampleExamPath = join(REPO_ROOT, 'examples', 'sample-medical-exams.json');
  const deckOutPath = join(tmpDir, 'deck_v1.json');
  const reportOutPath = join(tmpDir, 'freq.md');
  const apkgOutPath = join(tmpDir, 'med_v1.apkg');

  try {
    const res = await new Promise((resolveRun, reject) => {
      const child = spawn(process.execPath, [join(REPO_ROOT, 'server', 'mcp_server.mjs')], {
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      });
      let out = '';
      let err = '';
      child.stdout.on('data', (d) => { out += d.toString(); });
      child.stderr.on('data', (d) => { err += d.toString(); });
      child.on('error', reject);
      child.on('close', (code) => resolveRun({ code, out, err }));

      const send = (obj) => child.stdin.write(`${JSON.stringify(obj)}\n`);
      send({
        jsonrpc: '2.0',
        id: 1,
        method: 'initialize',
        params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'smoke', version: '1' } },
      });
      send({ jsonrpc: '2.0', method: 'notifications/initialized' });
      send({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} });
      send({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'anki_doctor', arguments: {} } });
      send({
        jsonrpc: '2.0',
        id: 4,
        method: 'tools/call',
        params: {
          name: 'anki_analyze_frequency',
          arguments: {
            inputPath: sampleExamPath,
            subjectPreset: 'clinical-medicine',
            reportPath: reportOutPath,
            deckOutPath,
          },
        },
      });
      send({
        jsonrpc: '2.0',
        id: 5,
        method: 'tools/call',
        params: {
          name: 'anki_build_apkg',
          arguments: {
            deckPath: deckOutPath,
            outputPath: apkgOutPath,
          },
        },
      });
      send({
        jsonrpc: '2.0',
        id: 6,
        method: 'tools/call',
        params: {
          name: 'anki_inspect_apkg',
          arguments: {
            apkgPath: apkgOutPath,
            tagFilter: 'freq::high',
          },
        },
      });
      child.stdin.end();
    });

    assert.equal(res.code, 0);
    assert.equal(res.err, '', `Unexpected stderr: ${res.err}`);

    const messages = res.out.trim().split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
    assert.equal(messages.length, 6);
    assert.equal(messages[0]?.result?.serverInfo?.name, 'anki-exam-forge');

    const tools = messages[1]?.result?.tools || [];
    const expectedTools = [
      'anki_doctor',
      'anki_extract_source',
      'anki_analyze_frequency',
      'anki_validate_deck',
      'anki_build_apkg',
      'anki_inspect_apkg',
      'anki_diff_supplement',
      'anki_apply_mnemonics',
    ];
    assert.deepEqual(tools.map((t) => t.name), expectedTools);
    for (const tool of tools) {
      assert.equal(tool.inputSchema?.type, 'object');
      assert.equal(typeof tool.inputSchema?.properties, 'object');
    }

    const docPayload = JSON.parse(messages[2]?.result?.content?.[0]?.text || '{}');
    assert.equal(docPayload.ok, true);
    assert.equal(docPayload.stdlib_ready, true);

    const freqPayload = JSON.parse(messages[3]?.result?.content?.[0]?.text || '{}');
    assert.equal(freqPayload.stats?.unique_concepts, 5);
    assert.equal(existsSync(reportOutPath), true);

    const buildPayload = JSON.parse(messages[4]?.result?.content?.[0]?.text || '{}');
    assert.equal(buildPayload.note_count, 5);
    assert.equal(buildPayload.card_count, 6);
    assert.equal(existsSync(apkgOutPath), true);

    const inspectPayload = JSON.parse(messages[5]?.result?.content?.[0]?.text || '{}');
    assert.equal(inspectPayload.matched_count, 2);
  } finally {
    await rm(tmpDir, { recursive: true, force: true }).catch(() => {});
  }
});

test('Node CLI doctor and freq commands return valid JSON', async () => {
  const { stdout: docOut } = await runFile(
    process.execPath,
    [join(REPO_ROOT, 'server', 'cli.mjs'), 'doctor', '--json'],
    { timeout: 20_000, windowsHide: true },
  );
  const doc = JSON.parse(docOut);
  assert.equal(doc.ok, true);

  const { stdout: freqOut } = await runFile(
    process.execPath,
    [
      join(REPO_ROOT, 'server', 'cli.mjs'),
      'freq',
      join(REPO_ROOT, 'examples', 'sample-medical-exams.json'),
      '--preset',
      'clinical-medicine',
    ],
    { timeout: 20_000, windowsHide: true },
  );
  const freq = JSON.parse(freqOut);
  assert.equal(freq.stats?.total_appearances, 11);
  assert.equal(freq.stats?.unique_concepts, 5);
});

test('install.mjs supports --print-mcp, --antigravity-workspace, and self-contained --skill-only', async () => {
  const { stdout: mcpOut } = await runFile(
    process.execPath,
    [join(REPO_ROOT, 'scripts', 'install.mjs'), '--print-mcp'],
    { timeout: 15_000, windowsHide: true },
  );
  const mcpPayload = JSON.parse(mcpOut);
  assert.equal(mcpPayload.mcpServers?.['anki-exam-forge']?.command, 'node');

  const tmpDir = await mkdtemp(join(tmpdir(), 'aef-install-'));
  try {
    const wsDir = join(tmpDir, 'workspace');
    await runFile(
      process.execPath,
      [join(REPO_ROOT, 'scripts', 'install.mjs'), '--antigravity-workspace', wsDir],
      { timeout: 20_000, windowsHide: true },
    );
    assert.equal(existsSync(join(wsDir, '.agents', 'plugins', 'anki-exam-forge', 'plugin.json')), true);
    assert.equal(existsSync(join(wsDir, '.agents', 'plugins', 'anki-exam-forge', 'mcp_config.json')), true);

    const skillsDir = join(tmpDir, 'skills');
    await runFile(
      process.execPath,
      [join(REPO_ROOT, 'scripts', 'install.mjs'), '--skill-only', skillsDir],
      { timeout: 20_000, windowsHide: true },
    );
    const skillCli = join(skillsDir, 'anki-exam-forge', 'engine', 'cli.py');
    assert.equal(existsSync(join(skillsDir, 'anki-exam-forge', 'SKILL.md')), true);
    assert.equal(existsSync(skillCli), true);

    const py = resolvePythonSync();
    const { stdout: pyDocOut } = await runFile(
      py.bin,
      [...py.prefix, skillCli, 'doctor'],
      {
        env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
        timeout: 20_000,
        windowsHide: true,
      },
    );
    const pyDoc = JSON.parse(pyDocOut);
    assert.equal(pyDoc.ok, true);
    assert.equal(pyDoc.stdlib_ready, true);
  } finally {
    await rm(tmpDir, { recursive: true, force: true }).catch(() => {});
  }
});
