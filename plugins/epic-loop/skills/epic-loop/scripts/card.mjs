// The goal card of spec 2.13: its one validator and its issue body. epic.mjs and evals/check-card.mjs both use this file.

const text = (v) => typeof v === 'string' && v.trim() !== '';

// Every problem of a parsed card, as one short sentence each. An empty list means a valid card.
export function cardProblems(card) {
  if (card === null || typeof card !== 'object' || Array.isArray(card)) return ['the card is not a JSON object'];
  const out = [];
  if (card.v !== 1) out.push('v must be 1');
  if (!text(card.title)) out.push('title must be a text');
  if (!Array.isArray(card.rows) || !card.rows.length) out.push('rows must hold at least one row');
  else {
    const ids = new Set();
    card.rows.forEach((row, i) => {
      for (const field of ['id', 'outcome', 'oracle', 'threshold']) if (!text(row?.[field])) out.push(`rows[${i}].${field} must be a text`);
      if (ids.has(row?.id)) out.push(`rows[${i}].id repeats ${row.id}`);
      ids.add(row?.id);
    });
  }
  if (!(Number.isFinite(card.size?.planWindows) && card.size.planWindows > 0)) out.push('size.planWindows must be a number above 0');
  if (card.door !== 'two-way' && card.door !== 'one-way') out.push('door must be two-way or one-way');
  if (!Array.isArray(card.next) || !card.next.length || !card.next.every(text)) out.push('next must hold at least one text');
  // A fence inside a value makes the reader of the issue body see a second block.
  if (JSON.stringify(card).includes('```')) out.push('a value holds a code fence');
  return out;
}

export const cardBody = (card) => `\`\`\`json\n${JSON.stringify(card, null, 2)}\n\`\`\`\n`;

// The text of each fenced json block in an issue body.
export const jsonBlocks = (body) => [...String(body ?? '').matchAll(/^```json[ \t]*\r?\n([\s\S]*?)^```[ \t]*$/gm)].map((m) => m[1]);
