#!/usr/bin/env node
/**
 * install.mjs — Cross-platform installer & MCP/Skill generator for anki-exam-forge.
 *
 * Usage:
 *   node scripts/install.mjs [--print-mcp]
 *   node scripts/install.mjs --antigravity-global
 *   node scripts/install.mjs --antigravity-workspace <dir>
 *   node scripts/install.mjs --skill-only <skills-dir>
 */

import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const REPO_ROOT = resolve(__dirname, '..');

const PLUGIN_COPY_ITEMS = [
  'plugin.json',
  'LICENSE',
  'package.json',
  'assets',
  'skills',
  'engine',
  'server',
  'scripts',
  'examples',
];

const SKIP_NAMES = new Set([
  '.venv',
  'node_modules',
  '__pycache__',
  '.pytest_cache',
  '.git',
  'out',
  'tmp',
]);

function shouldCopy(p) {
  const base = p.split(/[\\/]/).pop() || '';
  if (SKIP_NAMES.has(base)) return false;
  if (base.endsWith('.pyc') || base.endsWith('.apkg')) return false;
  return true;
}

function parseArgs(argv) {
  const opts = {
    mode: 'print-mcp',
    targetDir: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--antigravity-global') {
      opts.mode = 'antigravity-global';
    } else if (arg === '--antigravity-workspace') {
      opts.mode = 'antigravity-workspace';
      opts.targetDir = argv[++i] || '';
      if (!opts.targetDir) {
        throw new Error('--antigravity-workspace requires a directory path');
      }
    } else if (arg === '--skill-only') {
      opts.mode = 'skill-only';
      opts.targetDir = argv[++i] || '';
      if (!opts.targetDir) {
        throw new Error('--skill-only requires a destination skills directory path');
      }
    } else if (arg === '--print-mcp') {
      opts.mode = 'print-mcp';
    } else if (arg === '--help' || arg === '-h') {
      opts.mode = 'help';
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return opts;
}

function printHelp() {
  console.log([
    'Usage: node scripts/install.mjs [options]',
    '',
    'Options:',
    '  --print-mcp                     Print MCP JSON snippets & skill install examples (default)',
    '  --antigravity-global            Install plugin into ~/.gemini/config/plugins/anki-exam-forge/',
    '  --antigravity-workspace <dir>   Install plugin into <dir>/.agents/plugins/anki-exam-forge/',
    '  --skill-only <dir>              Install self-contained Skill + Python engine into <dir>/anki-exam-forge/',
    '  -h, --help                      Show this help message',
  ].join('\n'));
}

async function installPluginBundle(targetDir) {
  await mkdir(targetDir, { recursive: true });
  for (const item of PLUGIN_COPY_ITEMS) {
    await cp(join(REPO_ROOT, item), join(targetDir, item), {
      recursive: true,
      force: true,
      filter: shouldCopy,
    });
  }

  const mcpServerPath = join(targetDir, 'server', 'mcp_server.mjs');
  const mcpConfig = {
    mcpServers: {
      'anki-exam-forge': {
        command: 'node',
        args: [mcpServerPath],
      },
    },
  };
  await writeFile(join(targetDir, 'mcp_config.json'), `${JSON.stringify(mcpConfig, null, 2)}\n`, 'utf8');

  const manifest = JSON.parse(await readFile(join(targetDir, 'plugin.json'), 'utf8'));
  return { targetDir, mcpServerPath, name: manifest.name, version: manifest.version };
}

async function installSkillOnly(skillsRoot) {
  const destDir = join(resolve(skillsRoot), 'anki-exam-forge');
  await mkdir(destDir, { recursive: true });
  await cp(join(REPO_ROOT, 'skills', 'anki-exam-forge'), destDir, {
    recursive: true,
    force: true,
    filter: shouldCopy,
  });
  await cp(join(REPO_ROOT, 'engine'), join(destDir, 'engine'), {
    recursive: true,
    force: true,
    filter: shouldCopy,
  });
  await cp(join(REPO_ROOT, 'LICENSE'), join(destDir, 'LICENSE'), { force: true });
  return {
    destDir,
    engineCli: join(destDir, 'engine', 'cli.py'),
    skillFile: join(destDir, 'SKILL.md'),
  };
}

function buildMcpPayload() {
  const mcpServerPath = join(REPO_ROOT, 'server', 'mcp_server.mjs');
  return {
    mcpServers: {
      'anki-exam-forge': {
        command: 'node',
        args: [mcpServerPath],
      },
    },
    clients: {
      antigravityGlobalPlugin: 'node scripts/install.mjs --antigravity-global',
      antigravityWorkspacePlugin: 'node scripts/install.mjs --antigravity-workspace <workspace-dir>',
      claudeCodeMcp: `claude mcp add anki-exam-forge -- node "${mcpServerPath}"`,
      cursorMcp: 'Copy `mcpServers` above into .cursor/mcp.json',
      geminiCliMcp: 'Merge `mcpServers` above into ~/.gemini/settings.json',
      codexMcp: `codex mcp add anki-exam-forge -- node "${mcpServerPath}"`,
      universalSkillOnly: 'node scripts/install.mjs --skill-only <agent-skills-dir>',
    },
    diagnostics: 'node server/cli.mjs doctor',
  };
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.mode === 'help') {
    printHelp();
    return 0;
  }
  if (opts.mode === 'print-mcp') {
    console.log(JSON.stringify(buildMcpPayload(), null, 2));
    return 0;
  }
  if (opts.mode === 'skill-only') {
    const info = await installSkillOnly(opts.targetDir);
    console.log([
      `Installed self-contained skill anki-exam-forge -> ${info.destDir}`,
      `- Skill guide: ${info.skillFile}`,
      `- Standalone engine CLI: python "${info.engineCli}" doctor`,
    ].join('\n'));
    return 0;
  }

  const targetDir = opts.mode === 'antigravity-global'
    ? join(homedir(), '.gemini', 'config', 'plugins', 'anki-exam-forge')
    : join(resolve(opts.targetDir), '.agents', 'plugins', 'anki-exam-forge');

  const info = await installPluginBundle(targetDir);
  console.log([
    `Installed ${info.name} v${info.version} -> ${info.targetDir}`,
    `- MCP server: node "${info.mcpServerPath}"`,
    `- Run diagnostics: node "${join(info.targetDir, 'server', 'cli.mjs')}" doctor`,
  ].join('\n'));
  return 0;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error(`Error: ${err?.message ?? err}`);
    process.exit(1);
  });
