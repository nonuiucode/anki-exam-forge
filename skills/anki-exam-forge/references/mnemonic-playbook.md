# Mnemonic Playbook (For Stuck & Leech Cards)

> **Core Principle:** Mnemonics solve **initial encoding** (turning a 3-day brute-force grind into a 20-minute lock-in). Anki spaced repetition solves **long-term retrievability**. Keep mnemonics strictly in the `Mnemonic` field without distorting the factual answer.

When `anki_inspect_apkg` surfaces high-lapse (`minLapses >= 3`) or `leech`-tagged cards, diagnose *why* the learner keeps failing recall and pick **one** matching technique:

| Technique Key (`technique`) | Best For | Pattern & Example |
|---|---|---|
| `acronym` | Ordered lists, nerve branches, criteria, drug groups | Take 1-character keywords into a pronounceable phrase.<br>*Example (12 对脑神经)*: **“一嗅二视三动眼，四滑五叉六外展，七面八听九舌咽，十迷十一副舌下全。”** |
| `rhyme` | Symptom clusters, anatomical boundaries, contraindications | Short rhythmic couplet (`<= 2` lines) where every word maps 1-to-1 to a real item. |
| `homophone` | Foreign drug suffixes, obscure Latin/Greek terms, rare enzyme names | Sound-alike Chinese/English hook tied directly to the clinical effect.<br>*Example*: **阿托品（“阿拖平”→ 拖住副交感、平息平滑肌痉挛，口干瞳大心率快）。** |
| `mechanism` | Physiology/pathology steps that feel random when memorized raw | Compress into a 3-step `A -> B -> C` causal arrow instead of memorizing isolated endpoints.<br>*Example*: **醛固酮 → 保钠排钾留水 → 血压↑、血钾↓。** |
| `contrast` | Two easily swapped diseases, signs, or drugs (most common leech cause!) | Side-by-side single-dimension anchor.<br>*Example*: **Crohn 病“全层跳跃鹅卵石”；溃疡性结肠炎“黏膜连续杯状少”。** |
| `visual` / `loci` | Spatial anatomy, brachial plexus, cranial foramina, pathway layers | Anchor sequentially along a familiar visual path or anatomical body route (top-to-bottom / superficial-to-deep). |

## Quality Rules for Generated Mnemonics

1. **Never invent fake medical/scientific facts** inside a mnemonic; clearly separate the memory cue from the actual definition.
2. **Every character in an acronym must map explicitly** (include the mapping in parentheses if non-obvious so the learner isn't left wondering what a word stands for).
3. **Keep it under 100–140 characters** so it flashes cleanly on the card back without slowing review rhythm.
4. **Target the specific confusion pair** when a card is a `leech` (often `contrast` beats clever puns when two similar concepts interfere).
