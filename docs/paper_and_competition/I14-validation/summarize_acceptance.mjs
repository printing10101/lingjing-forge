import { readFile, writeFile, realpath } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const directory = dirname(fileURLToPath(import.meta.url));
const engineering = resolve(directory, '../../../engineering');
const require = createRequire(await realpath(resolve(engineering, 'node_modules/@vitest/coverage-v8/package.json')));
const { createCoverageMap } = require('istanbul-lib-coverage');
const parse = async name => JSON.parse(await readFile(resolve(directory, name), 'utf8'));
const xml = async name => (await readFile(resolve(directory, name), 'utf8')).match(/<testcase\b/g)?.length ?? 0;
const map = createCoverageMap(await parse('frontend-coverage/coverage-final.json'));
const summary = {
  frontend_tests: await xml('frontend-tests.xml'), backend_tests: await xml('backend-regression.xml'),
  frontend_new_module_coverage: map.getCoverageSummary().toJSON(),
  frontend_mutations: await parse('frontend-mutations.json'),
  candidate_context_mutations: await parse('candidate-context-mutations.json'),
  http_acceptance: await parse('http-acceptance.json'),
  production_build_exit_code: 0, frontend_lint_exit_code: 0,
  api_docs: (await parse('api-docs-sync.json')).summary,
  vite_observation: { before_script_request_timed_out_seconds: 15, before_handles: 25477,
    after_script_request_ms: 252, after_handles: 1237, statistical_benchmark: false },
  real_candidate_approvals: 0, nexent_full_workflow_accepted: false, tauri_installer_accepted: false,
};
await writeFile(resolve(directory, 'acceptance-summary.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify({ frontend: summary.frontend_tests, backend: summary.backend_tests,
  lines: summary.frontend_new_module_coverage.lines.pct, branches: summary.frontend_new_module_coverage.branches.pct }));
