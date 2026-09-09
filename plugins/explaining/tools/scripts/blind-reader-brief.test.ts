import { describe, expect, test } from 'bun:test';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const TEMPLATE = resolve(SCRIPT_DIR, '..', 'references', 'blind-reader-brief.md');

describe('blind-reader brief template', () => {
  const text = readFileSync(TEMPLATE, 'utf8');

  test('marks the renderable region with BRIEF-START and BRIEF-END', () => {
    const start = text.indexOf('BRIEF-START');
    const end = text.indexOf('BRIEF-END');
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
  });

  test('carries exactly the three allowed slots, each once', () => {
    const slots = [...text.matchAll(/\{\{([a-z_]+)\}\}/g)].map((m) => m[1]);
    expect(slots.sort()).toEqual(['artifact_path', 'audience', 'language']);
  });

  test('every slot sits inside the renderable region', () => {
    const region = text.slice(text.indexOf('BRIEF-START'), text.indexOf('BRIEF-END'));
    for (const slot of ['artifact_path', 'audience', 'language']) {
      expect(region).toContain(`{{${slot}}}`);
    }
  });

  test('instructs the terminal verdict line the rule parses', () => {
    expect(text).toContain('READER: PASS');
    expect(text).toContain('READER: FAIL');
  });

  test('asks for the hardest passage even on a clean read (spec risk 1)', () => {
    expect(text.toLowerCase()).toContain('hardest passage');
  });

  test('names the reader model knob outside the renderable region', () => {
    const notes = text.slice(text.indexOf('BRIEF-END'));
    expect(notes).toContain('sonnet');
  });

  test('never names the forbidden inputs inside the renderable region', () => {
    const region = text.slice(text.indexOf('BRIEF-START'), text.indexOf('BRIEF-END'));
    for (const banned of ['user prompt', 'the author', 'source', 'reasoning', 'previous round']) {
      expect(region.toLowerCase()).not.toContain(banned);
    }
  });
});
