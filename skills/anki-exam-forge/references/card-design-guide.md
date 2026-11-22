# Card Design Guide

Design flashcards for **fast, unambiguous active recall** — not passive textbook reading.

## 1. Choosing the Right Card Type

| Type | Model Name | Best For | Avoid When |
|---|---|---|---|
| `basic` | `ExamForge-Basic` | Definitions, diagnostic criteria, clinical signs, first-line treatments, short Q&A | The answer is a 10-line textbook paragraph |
| `mcq` | `ExamForge-MCQ` | Past-exam multiple choice drills where distractors matter (exam recognition & trap training) | Pure fact recall with no meaningful distractors |
| `cloze` | `ExamForge-Cloze` | Fill-in-the-blank mechanisms, numerical thresholds, anatomical relations, enumerations with 2–4 slots | Cloze blanks hide >50% of the sentence with zero context |

## 2. Atomicity & Minimum Information Principle

- **One testable question per card.** If a past-exam topic asks for *both* etiology, triad of symptoms, and first-line therapy, split it into 2–3 focused cards sharing the same `topic` and linked by `concept_key` suffix (e.g., `...::症状`, `...::治疗`), or use `c1`/`c2` cloze deletions.
- **Keep Front/Stem concise (`<= 120` chars ideal, warn above `300`).** Strip polite filler and redundant phrasing.
- **Keep Back concise (`<= 250` chars ideal, warn above `650`).** Put detailed differential explanations, exam traps, and variant phrasings in `Explanation`, **not** in `Back`.

## 3. Clean Cloze Authoring

- Always start numbering at `c1`: `{{c1::答案}}`.
- Add short **semantic hints** when the prompt could accept multiple categories:
  - Good: `交感神经节后纤维大多释放{{c1::去甲肾上腺素::神经递质}}，但支配汗腺的节后纤维释放{{c2::乙酰胆碱::神经递质}}。`
  - Weak: `{{c1::交感神经节后纤维}}大多释放{{c2::去甲肾上腺素}}。`
- Keep simultaneous cloze groups (`c1`, `c2`, `c3`) to at most 3–4 per note so review load stays predictable.

## 4. Handling Diagrams, Anatomy Figures & Slide Tables

- Extract diagrams directly via `anki_extract_source(..., extractImages: true)` and attach filenames in `media: ["slide_012_img_01.png"]`.
- Never embed raw base64 data URIs inside card fields — always reference local image filenames and pass `mediaDir` to `anki_validate_deck` / `anki_build_apkg`.
- For medical anatomy or pathology slides: ask specifically what the highlighted structure/arrow indicates (`Front`: `"图中箭号所指解剖结构及其主要神经支配？"`, `media`: `["anatomy_01.png"]`).

## 5. Authoring Initial AI Mnemonics (Default Practice)

Do **not** ship blank `mnemonic` fields waiting for a card to become a leech: every generated card should carry a short (`20–80` Chinese chars), targeted memory cue from day one so initial encoding happens immediately alongside FSRS spaced repetition:

- Pick the single sharpest hook from the [Mnemonic Playbook](./mnemonic-playbook.md) and prefix it clearly: `【口诀】`, `【数值锚点】`, `【对比辨析】`, `【机制推导】`, `【图像锚点】`, or `【谐音/首字母】`.
- Anchor the *exact* cutoff, differential trap, or waveform/anatomical clue tested on that card — never write vague filler like `"理解并熟记本题解析"`.

## 6. Quality Checklist Before Packaging

1. Can each card be answered in under **10 seconds** during review?
2. Does every card carry a tailored initial `mnemonic` (`mnemonic_coverage == 1.0`)?
3. Does `MCQ` include a short rationale in `Explanation` explaining why the main distractor is wrong?
4. Are inequality symbols (`<`, `>`) and units (`mmHg`, `mmol/L`) intact and unambiguous?
5. Do all cards carry a clean `topic` tag/label for chapter filtering?
