import { strict as assert } from "node:assert";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const projectRoot = fileURLToPath(new URL("../", import.meta.url));
const cliPath = fileURLToPath(new URL("../src/cli.ts", import.meta.url));

function runCli(...args: string[]) {
  return spawnSync(process.execPath, [cliPath, ...args], {
    cwd: projectRoot,
    encoding: "utf8",
  });
}

test("help exposes only the C1 command surface", () => {
  const result = runCli("--help");

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /contextox doctor \[--json\]/);
  assert.match(result.stdout, /Only the C1 doctor command is implemented\./);
  assert.equal(result.stderr, "");
});

test("doctor reports implemented checks and pending later checkpoints", () => {
  const result = runCli("doctor", "--json");

  assert.equal(result.status, 8, result.stderr);
  const envelope: unknown = JSON.parse(result.stdout);
  assert.equal(typeof envelope, "object");
  assert.notEqual(envelope, null);

  const output = envelope as {
    status: unknown;
    data: {
      scope: unknown;
      checks: {
        node: { status: unknown };
        sqlite: { status: unknown };
        fts5: { status: unknown };
        pi_packages: {
          status: unknown;
          agent_core: { actual: unknown; status: unknown };
          ai: { actual: unknown; status: unknown };
        };
        schema: { status: unknown };
        data_directory: { status: unknown };
        provider_configuration: { status: unknown };
      };
    };
  };

  assert.equal(output.status, "partial");
  assert.equal(output.data.scope, "c1");
  assert.equal(output.data.checks.node.status, "success");
  assert.equal(output.data.checks.sqlite.status, "success");
  assert.equal(output.data.checks.fts5.status, "success");
  assert.equal(output.data.checks.pi_packages.status, "success");
  assert.equal(output.data.checks.pi_packages.agent_core.status, "success");
  assert.equal(output.data.checks.pi_packages.agent_core.actual, "0.84.4");
  assert.equal(output.data.checks.pi_packages.ai.status, "success");
  assert.equal(output.data.checks.pi_packages.ai.actual, "0.84.4");
  assert.equal(output.data.checks.schema.status, "pending");
  assert.equal(output.data.checks.data_directory.status, "pending");
  assert.equal(output.data.checks.provider_configuration.status, "pending");
  assert.equal(result.stderr, "");
});

test("doctor text output preserves the JSON status", () => {
  const result = runCli("doctor");

  assert.equal(result.status, 8, result.stderr);
  assert.match(result.stdout, /^status: partial$/m);
  assert.match(result.stdout, /^sqlite: success$/m);
  assert.match(result.stdout, /^fts5: success$/m);
  assert.match(result.stdout, /^schema: pending$/m);
  assert.equal(result.stderr, "");
});

test("unknown commands fail closed", () => {
  const result = runCli("unknown", "--json");

  assert.equal(result.status, 2, result.stderr);
  const envelope: unknown = JSON.parse(result.stdout);
  assert.equal(typeof envelope, "object");
  assert.notEqual(envelope, null);
  assert.equal((envelope as { status: unknown }).status, "error");
  assert.equal(result.stderr, "");
});
