import { describe, expect, test } from 'bun:test';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE = resolve(SCRIPT_DIR, '..', 'evals', 'evals.json');

describe('evals case 4 — the blind-reader review case', () => {
  const data = JSON.parse(readFileSync(FIXTURE, 'utf8'));
  const case4 = data.evals.find((c: { id: number }) => c.id === 4);

  test('the existing cases are untouched and case 4 is appended', () => {
    expect(data.evals.map((c: { id: number }) => c.id)).toEqual([1, 2, 3]. concat([4]));
    expect(case4).toBeDefined();
  });

  test('the prompt never names the behavior under test', () => {
    const prompt = case4.prompt.toLowerCase();
    for (const word of ['review', 'reader', 'subagent', 'agent', 'blind', 'critique',
                         'diagram', 'mermaid', 'html', 'file', 'disk']) {
      expect(prompt).not.toContain(word);
    }
  });

  test('the prompt carries no single quote, which would break the check command', () => {
    expect(case4.prompt).not.toContain("'");
  });

  test('the check runs the review-log checker from the skill directory', () => {
    expect(case4.checks).toHaveLength(1);
    const command: string = case4.checks[0].command;
    expect(command).toContain('{skill_dir}/scripts/check-review-log.ts');
    expect(command).toContain('--log-glob');
  });

  test('the prompt the check is given is the case prompt, byte for byte', () => {
    const command: string = case4.checks[0].command;
    const match = command.match(/--prompt '([^']*)'$/);
    expect(match).not.toBeNull();
    expect(match![1]).toBe(case4.prompt);
  });

  test('the draft and the log are both preserved as artifacts', () => {
    expect(case4.artifacts).toContain('*.review.jsonl');
    expect(case4.artifacts).toContain('*.md');
  });

  test('the prompt is long enough in scope to cross the 600-word threshold', () => {
    expect(case4.prompt.split(/\s+/).length).toBeGreaterThan(30);
  });
});
