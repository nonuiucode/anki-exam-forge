/**
 * adapter.mjs — Bridges MCP tool schemas and calls to the pure-Python engine (`engine/cli.py`).
 */

import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const runFile = promisify(execFile);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

export const REPO_ROOT = resolve(__dirname, '..');
export const ENGINE_CLI = join(REPO_ROOT, 'engine', 'cli.py');

function findOnPathSync(name, env = process.env) {
  const dirs = (env.PATH || env.Path || '').split(delimiter).filter(Boolean);
  const exts = process.platform === 'win32'
    ? (env.PATHEXT || '.EXE;.CMD;.BAT').split(';').filter(Boolean)
    : [''];
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = join(dir, name + ext);
      if (existsSync(candidate)) return candidate;
    }
  }
  return null;
}

export function resolvePythonSync(env = process.env) {
  const explicit = env.ANKI_FORGE_PYTHON;
  if (explicit && existsSync(explicit)) {
    return { bin: explicit, prefix: [] };
  }

  for (const name of ['python3', 'python']) {
    const found = findOnPathSync(name, env);
    if (found) return { bin: found, prefix: [] };
  }

  if (process.platform === 'win32') {
    const pyLauncher = findOnPathSync('py', env);
    if (pyLauncher) return { bin: pyLauncher, prefix: ['-3'] };

    const localPrograms = env.LOCALAPPDATA
      ? join(env.LOCALAPPDATA, 'Programs', 'Python')
      : join(homedir(), 'AppData', 'Local', 'Programs', 'Python');
    for (const ver of ['Python313', 'Python312', 'Python311', 'Python310', 'Python39']) {
      const candidate = join(localPrograms, ver, 'python.exe');
      if (existsSync(candidate)) return { bin: candidate, prefix: [] };
    }
  }

  return { bin: 'python', prefix: [] };
}

export async function runEngineCli(cliArgs, { env = process.env, timeoutMs = 180_000 } = {}) {
  const py = resolvePythonSync(env);
  const { stdout } = await runFile(py.bin, [...py.prefix, ENGINE_CLI, ...cliArgs], {
    env: {
      ...env,
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8',
    },
    timeout: timeoutMs,
    maxBuffer: 32 * 1024 * 1024,
    windowsHide: true,
  });
  return JSON.parse(stdout);
}

async function withTempJson(payload, fn) {
  const dir = await mkdtemp(join(tmpdir(), 'anki-exam-forge-'));
  const filePath = join(dir, 'payload.json');
  try {
    await writeFile(filePath, JSON.stringify(payload, null, 2), 'utf8');
    return await fn(filePath);
  } finally {
    await rm(dir, { recursive: true, force: true }).catch(() => {});
  }
}

const TOOL_SPECS = [
  {
    name: 'anki_doctor',
    description: 'Inspect local Python, SQLite, OOXML (PPTX/DOCX), and PDF extraction readiness plus available subject presets.',
    inputSchema: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'anki_extract_source',
    description: 'Extract text, speaker notes, rendered PNG pages (for scanned PDFs), and embedded media from .pdf, .pptx, .docx, .md/.txt, or images.',
    inputSchema: {
      type: 'object',
      properties: {
        sourcePath: { type: 'string', description: 'Path to local PDF, PPTX, DOCX, Markdown/text file, or image directory.' },
        outDir: { type: 'string', description: 'Optional directory to write extracted markdown, manifest.json, pages/, and media/.' },
        fromPage: { type: 'integer', minimum: 1, default: 1, description: '1-based start page/slide/chunk index.' },
        toPage: { type: 'integer', minimum: 1, description: 'Optional inclusive end page/slide/chunk index.' },
        maxPages: { type: 'integer', minimum: 1, default: 20, description: 'Maximum pages/slides to process when toPage is omitted.' },
        renderMode: { type: 'string', enum: ['auto', 'always', 'never'], default: 'auto', description: 'PDF page rasterization mode.' },
        dpi: { type: 'integer', minimum: 72, maximum: 300, default: 150, description: 'DPI for rendered PDF page PNGs.' },
        extractImages: { type: 'boolean', default: true, description: 'Extract embedded slide/document images into media/.' },
      },
      required: ['sourcePath'],
    },
  },
  {
    name: 'anki_analyze_frequency',
    description: 'Group past-exam questions by canonical concept key, count exact appearances deterministically, generate a Markdown frequency report, and build a prioritized deck JSON.',
    inputSchema: {
      type: 'object',
      properties: {
        inputPath: { type: 'string', description: 'Path to JSON/JSONL file of raw exam items (provide inputPath or items).' },
        items: { type: 'array', description: 'Inline array of exam item objects (alternative to inputPath).', items: { type: 'object' } },
        deckName: { type: 'string', description: 'Target root Anki deck name (e.g. "临床医学::系统解剖学").' },
        subjectPreset: { type: 'string', enum: ['general', 'clinical-medicine', 'postgrad-exam', 'law-exam'], default: 'general' },
        highMin: { type: 'integer', minimum: 1, description: 'Minimum appearances for high-frequency tier (default 3).' },
        mediumMin: { type: 'integer', minimum: 1, description: 'Minimum appearances for medium-frequency tier (default 2).' },
        reportPath: { type: 'string', description: 'Optional output path for Markdown frequency report.' },
        deckOutPath: { type: 'string', description: 'Optional output path for prioritized deck JSON.' },
      },
    },
  },
  {
    name: 'anki_validate_deck',
    description: 'Validate a deck JSON (schema, non-empty prompts/answers, valid {{c1::...}} cloze syntax, >=2 MCQ options, unique IDs/GUIDs, and media file existence).',
    inputSchema: {
      type: 'object',
      properties: {
        deckPath: { type: 'string', description: 'Path to deck JSON file (provide deckPath or deck).' },
        deck: { type: 'object', description: 'Inline deck object with deck_name and cards array.' },
        mediaDir: { type: 'string', description: 'Optional base directory for resolving relative media references.' },
      },
    },
  },
  {
    name: 'anki_build_apkg',
    description: 'Compile a deck JSON (Basic, MCQ, Cloze + media) into a deterministic-GUID Anki .apkg archive with priority due ordering and optional frequency subdecks.',
    inputSchema: {
      type: 'object',
      properties: {
        deckPath: { type: 'string', description: 'Path to deck JSON file (provide deckPath or deck).' },
        deck: { type: 'object', description: 'Inline deck object (alternative to deckPath).' },
        outputPath: { type: 'string', description: 'Destination .apkg file path.' },
        mediaDir: { type: 'string', description: 'Optional directory containing referenced images/media.' },
        subdeckStrategy: { type: 'string', enum: ['tier', 'topic', 'flat'], description: 'How cards are organized into subdecks.' },
      },
      required: ['outputPath'],
    },
  },
  {
    name: 'anki_inspect_apkg',
    description: 'Inspect an existing .apkg archive (preferring collection.anki21), extract cards/GUIDs/media, and filter by tag (e.g. leech), deck, query, or min lapses.',
    inputSchema: {
      type: 'object',
      properties: {
        apkgPath: { type: 'string', description: 'Path to .apkg file.' },
        tagFilter: { type: 'string', description: 'Filter notes matching tag or tag prefix (e.g. "leech").' },
        deckFilter: { type: 'string', description: 'Substring filter on deck name.' },
        query: { type: 'string', description: 'Substring search across card fields and tags.' },
        minLapses: { type: 'integer', minimum: 0, description: 'Only return notes whose max card lapses >= minLapses.' },
        extractMediaDir: { type: 'string', description: 'Optional directory to extract bundled media files.' },
        outJsonPath: { type: 'string', description: 'Optional path to save full inspection JSON.' },
      },
      required: ['apkgPath'],
    },
  },
  {
    name: 'anki_diff_supplement',
    description: 'Diff lecture PPT/notes candidate cards against an existing deck JSON or .apkg using exact + bigram similarity matching, adding only new concepts while keeping all original GUIDs intact.',
    inputSchema: {
      type: 'object',
      properties: {
        basePath: { type: 'string', description: 'Path to existing deck JSON or .apkg file.' },
        candidatesPath: { type: 'string', description: 'Path to candidate cards JSON/JSONL (provide candidatesPath or candidates).' },
        candidates: { type: 'array', description: 'Inline candidate card objects.', items: { type: 'object' } },
        nearThreshold: { type: 'number', minimum: 0, maximum: 1, default: 0.62, description: 'Similarity threshold for flagging near-duplicates.' },
        mergeNear: { type: 'boolean', default: false, description: 'Append supplemental notes onto matched near-duplicate cards.' },
        outDeckPath: { type: 'string', description: 'Optional path to write merged deck JSON.' },
      },
      required: ['basePath'],
    },
  },
  {
    name: 'anki_apply_mnemonics',
    description: 'Write memory-technique mnemonics back into matching cards in a deck JSON or .apkg while keeping original GUIDs for zero-progress-loss in-place Anki updates.',
    inputSchema: {
      type: 'object',
      properties: {
        basePath: { type: 'string', description: 'Path to base deck JSON or .apkg file.' },
        patchesPath: { type: 'string', description: 'Path to mnemonic patch JSON/JSONL (provide patchesPath or patches).' },
        patches: { type: 'array', description: 'Inline array of { guid | card_id | concept_key, mnemonic, technique }.', items: { type: 'object' } },
        mode: { type: 'string', enum: ['replace', 'append'], default: 'replace' },
        outDeckPath: { type: 'string', description: 'Optional path to write updated full deck JSON.' },
        outApkgPath: { type: 'string', description: 'Optional path to build an updated .apkg package.' },
        updateOnly: { type: 'boolean', default: true, description: 'When building outApkgPath, include only patched cards (true) or full deck (false).' },
        mediaDir: { type: 'string', description: 'Optional directory containing referenced media files.' },
      },
      required: ['basePath'],
    },
  },
];

export function listMcpTools() {
  return TOOL_SPECS;
}

export async function callMcpTool(name, args = {}) {
  let result;

  if (name === 'anki_doctor') {
    result = await runEngineCli(['doctor']);
  } else if (name === 'anki_extract_source') {
    if (!args.sourcePath) throw new Error('sourcePath is required');
    const cliArgs = ['extract', String(args.sourcePath)];
    if (args.outDir) cliArgs.push('--out-dir', String(args.outDir));
    if (args.fromPage !== undefined) cliArgs.push('--from-page', String(args.fromPage));
    if (args.toPage !== undefined) cliArgs.push('--to-page', String(args.toPage));
    if (args.maxPages !== undefined) cliArgs.push('--max-pages', String(args.maxPages));
    if (args.renderMode) cliArgs.push('--render-mode', String(args.renderMode));
    if (args.dpi !== undefined) cliArgs.push('--dpi', String(args.dpi));
    if (args.extractImages === false) cliArgs.push('--no-images');
    result = await runEngineCli(cliArgs);
  } else if (name === 'anki_analyze_frequency') {
    const runWithInput = async (inputPath) => {
      const cliArgs = ['freq', inputPath];
      if (args.deckName) cliArgs.push('--deck-name', String(args.deckName));
      if (args.subjectPreset) cliArgs.push('--preset', String(args.subjectPreset));
      if (args.highMin !== undefined) cliArgs.push('--high-min', String(args.highMin));
      if (args.mediumMin !== undefined) cliArgs.push('--medium-min', String(args.mediumMin));
      if (args.reportPath) cliArgs.push('--report-out', String(args.reportPath));
      if (args.deckOutPath) cliArgs.push('--deck-out', String(args.deckOutPath));
      return await runEngineCli(cliArgs);
    };
    if (args.inputPath) {
      result = await runWithInput(String(args.inputPath));
    } else if (Array.isArray(args.items)) {
      result = await withTempJson(
        { deck_name: args.deckName, subject_preset: args.subjectPreset, items: args.items },
        runWithInput,
      );
    } else {
      throw new Error('Either inputPath or items array is required');
    }
  } else if (name === 'anki_validate_deck') {
    const runWithDeck = async (deckPath) => {
      const cliArgs = ['validate', deckPath];
      if (args.mediaDir) cliArgs.push('--media-dir', String(args.mediaDir));
      return await runEngineCli(cliArgs);
    };
    if (args.deckPath) {
      result = await runWithDeck(String(args.deckPath));
    } else if (args.deck && typeof args.deck === 'object') {
      result = await withTempJson(args.deck, runWithDeck);
    } else {
      throw new Error('Either deckPath or deck object is required');
    }
  } else if (name === 'anki_build_apkg') {
    if (!args.outputPath) throw new Error('outputPath is required');
    const runWithDeck = async (deckPath) => {
      const cliArgs = ['build', deckPath, '--out', String(args.outputPath)];
      if (args.mediaDir) cliArgs.push('--media-dir', String(args.mediaDir));
      if (args.subdeckStrategy) cliArgs.push('--subdeck-strategy', String(args.subdeckStrategy));
      return await runEngineCli(cliArgs);
    };
    if (args.deckPath) {
      result = await runWithDeck(String(args.deckPath));
    } else if (args.deck && typeof args.deck === 'object') {
      result = await withTempJson(args.deck, runWithDeck);
    } else {
      throw new Error('Either deckPath or deck object is required');
    }
  } else if (name === 'anki_inspect_apkg') {
    if (!args.apkgPath) throw new Error('apkgPath is required');
    const cliArgs = ['inspect', String(args.apkgPath)];
    if (args.tagFilter) cliArgs.push('--tag', String(args.tagFilter));
    if (args.deckFilter) cliArgs.push('--deck', String(args.deckFilter));
    if (args.query) cliArgs.push('--query', String(args.query));
    if (args.minLapses !== undefined) cliArgs.push('--min-lapses', String(args.minLapses));
    if (args.extractMediaDir) cliArgs.push('--extract-media', String(args.extractMediaDir));
    if (args.outJsonPath) cliArgs.push('--out-json', String(args.outJsonPath));
    result = await runEngineCli(cliArgs);
  } else if (name === 'anki_diff_supplement') {
    if (!args.basePath) throw new Error('basePath is required');
    const runWithCandidates = async (candPath) => {
      const cliArgs = ['diff', '--base', String(args.basePath), '--candidates', candPath];
      if (args.nearThreshold !== undefined) cliArgs.push('--threshold', String(args.nearThreshold));
      if (args.mergeNear) cliArgs.push('--merge-near');
      if (args.outDeckPath) cliArgs.push('--out-deck', String(args.outDeckPath));
      return await runEngineCli(cliArgs);
    };
    if (args.candidatesPath) {
      result = await runWithCandidates(String(args.candidatesPath));
    } else if (Array.isArray(args.candidates)) {
      result = await withTempJson(args.candidates, runWithCandidates);
    } else {
      throw new Error('Either candidatesPath or candidates array is required');
    }
  } else if (name === 'anki_apply_mnemonics') {
    if (!args.basePath) throw new Error('basePath is required');
    const runWithPatches = async (patchesPath) => {
      const cliArgs = ['mnemonic', '--base', String(args.basePath), '--patches', patchesPath];
      if (args.mode) cliArgs.push('--mode', String(args.mode));
      if (args.outDeckPath) cliArgs.push('--out-deck', String(args.outDeckPath));
      if (args.outApkgPath) cliArgs.push('--out-apkg', String(args.outApkgPath));
      if (args.updateOnly === false) cliArgs.push('--full-deck');
      if (args.mediaDir) cliArgs.push('--media-dir', String(args.mediaDir));
      return await runEngineCli(cliArgs);
    };
    if (args.patchesPath) {
      result = await runWithPatches(String(args.patchesPath));
    } else if (Array.isArray(args.patches)) {
      result = await withTempJson(args.patches, runWithPatches);
    } else {
      throw new Error('Either patchesPath or patches array is required');
    }
  } else {
    throw new Error(`Unknown tool: ${name}`);
  }

  return {
    content: [
      {
        type: 'text',
        text: JSON.stringify(result, null, 2),
      },
    ],
  };
}
