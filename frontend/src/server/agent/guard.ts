// Prompt-injection screen for worker answers. Pure code, no model: it never decides
// quality, it only flags text that tries to steer the agent. A flag can only make the
// outcome more cautious (the floor escalates instead of paying); it never costs money.
// References: OWASP LLM01 and its prevention cheat sheet, Microsoft "spotlighting".

const MAX_FIELD_CHARS = 500;
const MAX_ANSWER_CHARS = 5_000;

// Format characters (zero-width, bidi controls, Unicode tag characters) and variation
// selectors are invisible to people but read by models: a classic smuggling channel.
const INVISIBLE = /[\p{Cf}\u{FE00}-\u{FE0F}\u{E0100}-\u{E01EF}]/gu;

// Look-alike letters from other scripts mapped to Latin, so "ignоre" (Cyrillic o) matches.
const CONFUSABLES: Record<string, string> = {
  а: "a", е: "e", о: "o", р: "p", с: "c", х: "x", у: "y", і: "i", ѕ: "s", ј: "j", ԁ: "d", һ: "h",
  α: "a", ε: "e", ι: "i", ο: "o", ρ: "p", ν: "v", κ: "k", τ: "t", ı: "i",
};

const LEET: Record<string, string> = { "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", $: "s" };

// Words attackers scramble ("ignroe all prevoius instructions"); scrambled forms are mapped back.
const KEYWORDS = [
  "ignore", "disregard", "forget", "previous", "instructions", "instruction", "system", "prompt",
  "override", "reveal", "developer", "assistant", "approve", "ignora", "olvida", "instrucciones",
  "anteriores", "sistema", "reglas",
];

const PHRASES: { name: string; re: RegExp }[] = [
  { name: "ignore instructions", re: /\b(ignore|disregard|forget|skip|bypass)\b.{0,40}\b(instructions?|rules|prompt|guidelines|above|previous)\b/ },
  { name: "ignore instructions (es)", re: /\b(ignora|ignore|olvida|omite|salta)\b.{0,40}\b(instrucciones|reglas|indicaciones|anteriores|prompt)\b/ },
  { name: "role switch", re: /\b(you are now|act as|pretend to be|from now on you|developer mode|jailbreak|dan mode)\b/ },
  { name: "role switch (es)", re: /\b(ahora eres|actua como|act[uú]a como|finge ser|modo desarrollador)\b/ },
  { name: "new instructions", re: /\b(new|updated|real|actual) (instructions?|rules|task)\b|\bnuevas? (instrucciones|reglas|tarea)\b/ },
  { name: "fake role marker", re: /(^|\n)\s*(system|assistant|developer|user)\s*:|<\|?\s*(im_start|im_end|system|endoftext)\b|\[\s*(system|inst)\s*\]|#{2,}\s*(system|instruction)/ },
  { name: "fake tag", re: /<\s*\/?\s*(worker_answer|other_answer|system|instructions?|answer)\b/ },
  { name: "payment command", re: /\b(pay|approve|accept|reward)\s+(me|this (worker|submission|answer)|all workers|everyone)\b|"action"\s*:\s*"(pay|reject|escalate|wait)"/ },
  { name: "payment command (es)", re: /\b(p[aá]game|paga(le)?s?|aprueba|acepta)\b.{0,20}\b(esta|este|a todos|todo|entrega|respuesta|trabajo)\b/ },
  { name: "target other workers", re: /\b(reject|deny|refuse)\b.{0,30}\b(other|others|everyone|the rest)\b|\brechaza\b.{0,30}\b(otros?|otras?|dem[aá]s|todos)\b/ },
  { name: "prompt extraction", re: /\b(reveal|repeat|print|show|output)\b.{0,30}\b(system prompt|your (instructions|prompt|rules))\b|\b(revela|repite|muestra)\b.{0,30}\b(instrucciones|prompt)\b/ },
  { name: "forget everything", re: /\b(forget|olvida|olvidate)\b.{0,20}\b(everything|all|todo)\b/ },
  { name: "ignore instructions (other languages)", re: /\b(ignoriere|vergiss)\b.{0,40}\b(anweisungen|regeln)\b|\b(ignore|ignora|esqueca|ignorare)\b.{0,40}\b(instrucoes|istruzioni|regras|regole)\b/ },
  { name: "talks to the reviewer", re: /\b(note|message|instructions?) (to|for) (the )?(reviewer|ai|agent|model|assistant|grader|checker|llm)\b|\b(dear|hey|hi|attention) (reviewer|ai|agent|model|assistant|llm)\b|(^|\n)\s*(ai|reviewer|llm)\s*:|\bas an ai\b|\bnota (para|al) (el |la )?(revisor|agente|modelo|ia)\b/ },
  { name: "approval command", re: /\b(approve|mark)\s+(it|this|as (correct|verified|valid|approved))\b|\baccept this (answer|submission|transcription)\b|\btrust (me|my (numbers|answer|transcription|work))\b|\bconf[ií]a en (mi|m[ií]s)\b/ },
  { name: "key-value command", re: /\b(action|decision|verdict)\s*[=:]\s*"?(pay|approve|reject|escalate)\b|\boverride\s*:/ },
];

// Spaced-out or punctuated attacks ("i g n o r e  p r e v i o u s") survive as letters only.
const COLLAPSED = [
  "ignorepreviousinstructions", "ignoreallinstructions", "ignoreyourinstructions", "ignoretheinstructions",
  "ignoreallpreviousinstructions", "disregardpreviousinstructions", "systemprompt", "youarenow", "developermode",
  "ignoralasinstrucciones", "ignoratusinstrucciones", "olvidalasinstrucciones", "nuevasinstrucciones",
];

export interface CleanText {
  text: string;
  hidden: number; // invisible characters removed
}

/** NFKC (fullwidth and compatibility forms), invisible characters removed, look-alikes mapped to Latin. */
export function normalizeText(raw: string): CleanText {
  const nfkc = raw.normalize("NFKC");
  const visible = nfkc.replace(INVISIBLE, "");
  return { text: visible, hidden: [...nfkc].length - [...visible].length };
}

function canonicalForm(text: string): string {
  const lower = [...text.toLowerCase()].map((ch) => CONFUSABLES[ch] ?? ch).join("");
  // Leetspeak ("1gn0re") only inside words that mix letters and digits, so amounts stay untouched.
  const unleet = lower.replace(/[\p{L}\d@$]+/gu, (w) =>
    /\p{L}/u.test(w) && /[\d@$]/.test(w) ? w.replace(/[013457@$]/g, (c) => LEET[c] ?? c) : w,
  );
  const unscrambled = unleet.replace(/\p{L}+/gu, (word) => unscramble(word));
  return unscrambled.normalize("NFD").replace(/\p{M}/gu, "").replace(/[^\S\n]+/g, " ");
}

function unscramble(word: string): string {
  if (word.length < 4 || KEYWORDS.includes(word)) return word;
  const middle = (w: string) => [...w.slice(1, -1)].sort().join("");
  const match = KEYWORDS.find(
    (k) => k.length === word.length && k[0] === word[0] && k.at(-1) === word.at(-1) && middle(k) === middle(word),
  );
  return match ?? word;
}

function scanPhrases(text: string): string[] {
  const form = canonicalForm(text); // keeps line breaks, for line-start markers like "system:"
  const flat = form.replace(/\s+/g, " "); // one line, so an attack split across lines still matches
  const found = PHRASES.filter((p) => p.re.test(form) || p.re.test(flat)).map((p) => p.name);
  const letters = form.replace(/[^a-z]/g, "");
  if (COLLAPSED.some((c) => letters.includes(c))) found.push("spaced-out instructions");
  return found;
}

// Hidden payloads: decode long base64 / hex runs and scan what they say.
function scanEncoded(text: string): string[] {
  const found: string[] = [];
  for (const blob of text.match(/[A-Za-z0-9+/]{24,}={0,2}/g) ?? []) {
    const decoded = Buffer.from(blob, "base64").toString("utf8");
    if (scanPhrases(decoded).length) found.push("instructions hidden in base64");
  }
  for (const blob of text.match(/\b(?:[0-9a-fA-F]{2}){16,}\b/g) ?? []) {
    const decoded = Buffer.from(blob, "hex").toString("utf8");
    if (scanPhrases(decoded).length) found.push("instructions hidden in hex");
  }
  return found;
}

/** Findings for one piece of text. Empty means nothing suspicious was found. */
export function scanText(raw: string): string[] {
  const { text, hidden } = normalizeText(raw);
  const found: string[] = [];
  if (hidden > 0) found.push(`${hidden} hidden character(s)`);
  if ([...text].length > MAX_FIELD_CHARS) found.push(`field longer than ${MAX_FIELD_CHARS} characters`);
  found.push(...scanPhrases(text), ...scanEncoded(text));
  return [...new Set(found)];
}

/** Scans every string (keys included) in an answer of any shape. */
export function scanAnswer(answer: unknown): string[] {
  const found: string[] = [];
  const visit = (value: unknown): void => {
    if (typeof value === "string") found.push(...scanText(value));
    else if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === "object") {
      for (const [key, v] of Object.entries(value)) {
        found.push(...scanText(key));
        visit(v);
      }
    }
  };
  visit(answer);
  let size = 0;
  try {
    size = JSON.stringify(answer)?.length ?? 0;
  } catch {
    found.push("answer is not plain JSON");
  }
  if (size > MAX_ANSWER_CHARS) found.push(`answer longer than ${MAX_ANSWER_CHARS} characters`);
  return [...new Set(found)];
}
