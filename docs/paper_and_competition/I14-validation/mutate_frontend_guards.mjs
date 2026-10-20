import { readFile, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const evidence = dirname(fileURLToPath(import.meta.url));
const engineering = resolve(evidence, '../../../engineering');
const mutations = [
  ['approval_confirmation', 'src/utils/__tests__/processReviewForm.test.ts', 'rejects incomplete approval'],
  ['https_source', 'src/utils/__tests__/processReviewForm.test.ts', 'rejects incomplete approval'],
  ['unsaved_parameter', 'src/utils/__tests__/processReviewForm.test.ts', 'never approves a displayed'],
  ['stale_revision', 'src/composables/__tests__/useProcessReview.test.ts', 'fails honestly on HTTP 409'],
  ['double_submission', 'src/composables/__tests__/useProcessReview.test.ts', 'blocks double submissions'],
  ['engineer_identity', 'src/stores/__tests__/auth.test.ts', 'restores engineer'],
];
const paths = ['src/utils/processReviewForm.ts', 'src/composables/useProcessReview.ts', 'src/stores/auth.ts'];
const before = await Promise.all(paths.map(path => readFile(resolve(engineering, path))));
const results = [];
for (let index = 0; index < mutations.length; index++) {
  const [name, target, pattern] = mutations[index];
  const run = spawnSync(process.execPath, ['node_modules/vitest/vitest.mjs', 'run', target,
    '--config', 'tests/manual/vitest.review-mutations.ts', '--testNamePattern', pattern],
    { cwd: engineering, env: { ...process.env, REVIEW_MUTATION: String(index) }, timeout: 60000, encoding: 'utf8' });
  const output = (run.stdout ?? '') + (run.stderr ?? '');
  await writeFile(resolve(evidence, `mutation-${name}.log`), output);
  results.push({ name, caught: run.status === 1 && output.includes('AssertionError'), exit_code: run.status });
  console.log(JSON.stringify(results.at(-1)));
}
const after = await Promise.all(paths.map(path => readFile(resolve(engineering, path))));
const unchanged = before.every((buffer, index) => buffer.equals(after[index]));
await writeFile(resolve(evidence, 'frontend-mutations.json'), JSON.stringify({ source_files_unchanged: unchanged, cases: results }, null, 2));
process.exitCode = unchanged && results.every(row => row.caught) ? 0 : 1;
