import { readFile, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const directory = dirname(fileURLToPath(import.meta.url));
const engineering = resolve(directory, '../../../engineering');
const sourcePath = resolve(engineering, 'src/utils/processReviewForm.ts');
const before = await readFile(sourcePath);
const names = ['finite_values', 'exclusive_boundary', 'range_order', 'required_modes', 'legacy_display_shape'];
const only = process.argv.indexOf('--only');
const indices = only === -1 ? names.map((_, index) => index) : [Number(process.argv[only + 1])];
if (indices.some(index => !Number.isInteger(index) || !names[index])) throw new Error('Unknown mutation');
let cases = only === -1 ? [] : JSON.parse(await readFile(resolve(directory, 'frontend-mutations.json'), 'utf8')).cases;
for (const index of indices) {
  const result = spawnSync(process.execPath, ['node_modules/vitest/vitest.mjs', 'run', 'src/utils/__tests__/processApplicabilityForm.test.ts', '--config', 'tests/manual/vitest.applicability.ts'],
    { cwd: engineering, env: { ...process.env, APPLICABILITY_MUTATION: String(index) }, timeout: 180000, encoding: 'utf8' });
  const output = (result.stdout ?? '') + (result.stderr ?? '');
  await writeFile(resolve(directory, `mutation-ui-${names[index]}.log`), output);
  cases = cases.filter(item => item.name !== names[index]);
  cases.push({ name: names[index], caught: result.status === 1 && output.includes('AssertionError'), exit_code: result.status });
  console.log(JSON.stringify(cases.at(-1)));
}
const unchanged = before.equals(await readFile(sourcePath));
await writeFile(resolve(directory, 'frontend-mutations.json'), JSON.stringify({ source_unchanged: unchanged, cases }, null, 2));
process.exitCode = unchanged && cases.every(item => item.caught) ? 0 : 1;
