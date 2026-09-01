import { spawnSync } from "node:child_process";
import { globSync } from "node:fs";
import process from "node:process";
import { fileURLToPath } from "node:url";

const projectRoot = fileURLToPath(new URL("../", import.meta.url));
const testFiles = globSync("test/**/*.test.ts", { cwd: projectRoot }).sort();

if (testFiles.length === 0) {
  process.stderr.write("No test/**/*.test.ts files found\n");
  process.exitCode = 1;
} else {
  process.stdout.write(`matched test files: ${testFiles.length}\n`);
  const result = spawnSync(
    process.execPath,
    ["--test", "--test-reporter=tap", ...testFiles],
    { cwd: projectRoot, encoding: "utf8" },
  );
  if (result.error) throw result.error;

  const stdout = result.stdout ?? "";
  process.stdout.write(stdout);
  process.stderr.write(result.stderr ?? "");

  const summaries = [...stdout.matchAll(/^# tests (\d+)$/gm)];
  const testCount = Number(summaries.at(-1)?.[1] ?? 0);
  if (result.status !== 0) {
    process.exitCode = result.status ?? 1;
  } else if (testCount === 0) {
    process.stderr.write("Test runner reported zero tests\n");
    process.exitCode = 1;
  }
}
