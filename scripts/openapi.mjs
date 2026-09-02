import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const projectRoot = resolve(fileURLToPath(new URL("../", import.meta.url)));
const webRoot = join(projectRoot, "web");
const generatedPath = join(webRoot, "src", "generated", "api.ts");
const checkOnly = process.argv.includes("--check");
const temporaryRoot = mkdtempSync(join(tmpdir(), "contextox-openapi-"));
const schemaPath = join(temporaryRoot, "openapi.json");
const candidatePath = join(temporaryRoot, "api.ts");

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    stdio: "inherit",
    env: {
      ...process.env,
      PYTHONPATH: join(projectRoot, "src"),
    },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

try {
  run("uv", ["run", "--locked", "python", "-m", "contextox", "openapi", "--output", schemaPath]);
  run(join(webRoot, "node_modules", ".bin", "openapi-typescript"), [schemaPath, "--output", candidatePath]);
  const candidate = readFileSync(candidatePath);
  if (checkOnly) {
    const current = readFileSync(generatedPath);
    if (!candidate.equals(current)) {
      console.error("Generated OpenAPI types are stale. Run npm run generate:api in web/.");
      process.exitCode = 1;
    } else {
      console.log("OpenAPI types are up to date.");
    }
  } else {
    const current = existsSync(generatedPath) ? readFileSync(generatedPath) : null;
    if (!current || !candidate.equals(current)) {
      writeFileSync(generatedPath, candidate);
      console.log(`Generated ${generatedPath}`);
    } else {
      console.log("OpenAPI types unchanged.");
    }
  }
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
}
