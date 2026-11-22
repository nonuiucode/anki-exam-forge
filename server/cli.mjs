#!/usr/bin/env node
/**
 * cli.mjs — Portable Node.js CLI wrapper around engine/cli.py.
 *
 * Subcommands:
 *   doctor | extract | freq | validate | build | inspect | diff | mnemonic | help
 */

import { spawn } from 'node:child_process';
import { ENGINE_CLI, resolvePythonSync } from './adapter.mjs';

function printHelp() {
  console.log([
    'Usage: node server/cli.mjs <command> [options]',
    '',
    'Commands:',
    '  doctor                              Check Python, SQLite, OOXML, and PDF backend',
    '  extract <source> [options]          Extract text/pages/media from PDF/PPTX/DOCX/text/images',
    '  freq <input.json> [options]         Analyze exam frequencies and generate Markdown report + deck JSON',
    '  validate <deck.json> [options]      Validate card syntax, IDs/GUIDs, and media paths',
    '  build <deck.json> --out <file.apkg> Compile deck JSON into a deterministic-GUID Anki .apkg',
    '  inspect <file.apkg> [options]       Inspect .apkg contents and filter by tag/deck/query/lapses',
    '  diff --base <f> --candidates <c>    Diff supplement candidates against existing deck/.apkg',
    '  mnemonic --base <f> --patches <p>   Apply mnemonics back while preserving original GUIDs',
    '  help                                Show this help message',
  ].join('\n'));
}

async function main() {
  const argv = process.argv.slice(2);
  const cmd = argv[0];
  if (!cmd || cmd === 'help' || cmd === '-h' || cmd === '--help') {
    printHelp();
    return 0;
  }

  const py = resolvePythonSync();
  const code = await new Promise((resolveExit, reject) => {
    const child = spawn(py.bin, [...py.prefix, ENGINE_CLI, ...argv], {
      stdio: 'inherit',
      env: {
        ...process.env,
        PYTHONUTF8: '1',
        PYTHONIOENCODING: 'utf-8',
      },
      windowsHide: true,
    });
    child.on('error', reject);
    child.on('close', (c) => resolveExit(c ?? 0));
  });
  return code;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error(`Error: ${err?.message ?? err}`);
    process.exit(1);
  });
