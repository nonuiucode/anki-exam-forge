# Subject Presets & Tagging Conventions

Select the closest preset when running `anki_analyze_frequency` (`--preset`), or adapt the tagging convention to your course.

## 1. `clinical-medicine` — Clinical Medicine & Medical School Finals

Covers high-density memorization courses:
- **Basic preclinical**: 系统解剖学 (Systematic Anatomy), 组织学与胚胎学 (Histology & Embryology), 生理学 (Physiology), 生物化学 (Biochemistry), 病理学 (Pathology), 病理生理学 (Pathophysiology), 药理学 (Pharmacology).
- **Clinical rotations & finals**: 内科学, 外科学, 妇产科学, 儿科学, 皮肤性病学, 传染病学, 诊断学.

**Recommended Strategy:**
- **Recognition first for anatomy/histology/dermatology finals**: convert past-exam questions directly into `mcq` or short `basic` recall cards first — exam papers test whether you can *identify and distinguish* structures/lesions under time pressure.
- **Causal mechanism chains for physiology/pathology/internal medicine**: pair `cloze` cards for critical mechanisms (e.g., RAAS axis, Starling forces, acid-base compensation) with `mcq` traps.
- **Image attachment**: keep lecture PPT pathology/rash/ECG figures in `media` so visual pattern recognition reinforces text recall.
- **Tag hierarchy**: `subject::med`, `course::<科目>`, `topic::<章节>`, `freq::high|medium|low`.

## 2. `postgrad-exam` — Postgraduate & Licensing Exams (考研 / 执业医师)

- **Multi-year exam merging**: identical clinical vignettes or mechanism tests often reappear with slightly altered lab values or patient ages across 10 years of exams. Normalize `concept_key` to the **core tested rule**, keep all years in `source_years`, and log variant phrasings in `Explanation`.
- **High-frequency first**: use `subdeckStrategy: "tier"` (`01-高频必背` -> `02-中频常考` -> `03-真题常规`) so limited pre-exam days focus strictly on `>= 2x` / `>= 3x` concepts before touching rare outliers.

## 3. `law-exam` — Bar Exam / Civil Service / Regulations (法考 / 公考)

- Focus `cloze` cards on **statutory conditions, numerical time limits, and exception clauses** (`但书`).
- Focus `basic` cards on **element comparison** (e.g., 抢劫罪 vs 抢夺罪, 行政复议 vs 行政诉讼期限).

## 4. `general` — General STEM & Humanities

- For math/physics/engineering formulas: test **applicability conditions & boundary cases**, not blind symbol substitution.
- For history/biology/languages: test **cause-effect contrast pairs** and **exact terminology**.
