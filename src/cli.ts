#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import process from "node:process";

const MINIMUM_NODE_VERSION = "22.19.0";
const EXPECTED_PI_VERSION = "0.84.4";
const SQLITE_PROBE_SCRIPT = `
import { DatabaseSync } from "node:sqlite";

const summarizeError = (error) =>
  error instanceof Error ? error.name + ": " + error.message : String(error);
const result = {
  sqlite: { status: "blocked", detail: "SQLite probe did not run." },
  fts5: { status: "blocked", detail: "FTS5 probe did not run." },
};
let database;

try {
  database = new DatabaseSync(":memory:");
  database.exec("CREATE TABLE doctor_sqlite_probe (value TEXT NOT NULL)");
  database
    .prepare("INSERT INTO doctor_sqlite_probe(value) VALUES (?)")
    .run("contextox");
  const sqliteRow = database
    .prepare("SELECT count(*) AS count FROM doctor_sqlite_probe WHERE value = ?")
    .get("contextox");
  if (sqliteRow?.count !== 1) {
    throw new Error("SQLite probe returned an unexpected result.");
  }
  result.sqlite = {
    status: "success",
    detail: "In-memory SQLite read and write probe passed.",
  };
} catch (error) {
  result.sqlite = { status: "blocked", detail: summarizeError(error) };
  result.fts5 = {
    status: "blocked",
    detail: "FTS5 was not tested because SQLite initialization failed.",
  };
  database?.close();
  process.stdout.write(JSON.stringify(result));
  process.exit(0);
}

try {
  database.exec("CREATE VIRTUAL TABLE doctor_fts5_probe USING fts5(body)");
  database
    .prepare("INSERT INTO doctor_fts5_probe(body) VALUES (?)")
    .run("governed context");
  const fts5Row = database
    .prepare(
      "SELECT count(*) AS count FROM doctor_fts5_probe WHERE doctor_fts5_probe MATCH ?",
    )
    .get("governed");
  if (fts5Row?.count !== 1) {
    throw new Error("FTS5 probe returned an unexpected result.");
  }
  result.fts5 = {
    status: "success",
    detail: "In-memory FTS5 create, write, and query probe passed.",
  };
} catch (error) {
  result.fts5 = { status: "blocked", detail: summarizeError(error) };
} finally {
  database.close();
}

process.stdout.write(JSON.stringify(result));
`;

type ImplementedCheckStatus = "success" | "blocked";
type DoctorStatus = "partial" | "blocked";

type RuntimeCheck = {
  status: ImplementedCheckStatus;
  detail: string;
};

type NodeCheck = RuntimeCheck & {
  actual: string;
  required: string;
};

type PackageCheck = RuntimeCheck & {
  actual: string | null;
  expected: string;
};

type PendingCheck = {
  status: "pending";
  detail: string;
};

type DoctorChecks = {
  node: NodeCheck;
  sqlite: RuntimeCheck;
  fts5: RuntimeCheck;
  pi_packages: {
    status: ImplementedCheckStatus;
    agent_core: PackageCheck;
    ai: PackageCheck;
  };
  schema: PendingCheck;
  data_directory: PendingCheck;
  provider_configuration: PendingCheck;
};

type DoctorEnvelope = {
  status: DoctorStatus;
  data: {
    scope: "c1";
    checks: DoctorChecks;
  };
  error: null;
  ids: {
    workspace_id: null;
    mission_id: null;
    run_id: null;
    approval_id: null;
  };
};

const emptyIds = {
  workspace_id: null,
  mission_id: null,
  run_id: null,
  approval_id: null,
} as const;

function summarizeError(error: unknown): string {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  return String(error);
}

function summarizePackageError(error: unknown, packageName: string): string {
  let code = "PACKAGE_UNAVAILABLE";
  if (typeof error === "object" && error !== null && "code" in error) {
    const candidate = (error as { code?: unknown }).code;
    if (typeof candidate === "string") code = candidate;
  }
  return `${code}: ${packageName} is unavailable or unreadable.`;
}

function nodeVersionIsSupported(version: string): boolean {
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(version);
  if (!match) return false;

  const major = Number(match[1]);
  const minor = Number(match[2]);
  return major > 22 || (major === 22 && minor >= 19);
}

function checkNode(): NodeCheck {
  const actual = process.versions.node;
  const supported = nodeVersionIsSupported(actual);
  return {
    status: supported ? "success" : "blocked",
    actual,
    required: `>=${MINIMUM_NODE_VERSION}`,
    detail: supported
      ? "Node version satisfies the C1 minimum."
      : "Install a supported Node runtime before continuing.",
  };
}

function checkSqliteAndFts5(): {
  sqlite: RuntimeCheck;
  fts5: RuntimeCheck;
} {
  const probe = spawnSync(
    process.execPath,
    [
      "--disable-warning=ExperimentalWarning",
      "--input-type=module",
      "--eval",
      SQLITE_PROBE_SCRIPT,
    ],
    { encoding: "utf8", maxBuffer: 64 * 1024, timeout: 5_000 },
  );

  if (probe.error || probe.status !== 0 || probe.stderr !== "") {
    const detail = probe.error
      ? summarizeError(probe.error)
      : probe.stderr.trim() || `SQLite probe exited with status ${probe.status}.`;
    return {
      sqlite: { status: "blocked", detail },
      fts5: {
        status: "blocked",
        detail: "FTS5 was not tested because the SQLite probe failed.",
      },
    };
  }

  try {
    const parsed: unknown = JSON.parse(probe.stdout);
    if (typeof parsed !== "object" || parsed === null) {
      throw new Error("SQLite probe returned a non-object result.");
    }

    const result = parsed as {
      sqlite?: { status?: unknown; detail?: unknown };
      fts5?: { status?: unknown; detail?: unknown };
    };
    const validStatus = (
      status: unknown,
    ): status is ImplementedCheckStatus =>
      status === "success" || status === "blocked";
    const sqliteStatus = result.sqlite?.status;
    const sqliteDetail = result.sqlite?.detail;
    const fts5Status = result.fts5?.status;
    const fts5Detail = result.fts5?.detail;
    if (
      !validStatus(sqliteStatus) ||
      typeof sqliteDetail !== "string" ||
      !validStatus(fts5Status) ||
      typeof fts5Detail !== "string"
    ) {
      throw new Error("SQLite probe returned an invalid result shape.");
    }

    return {
      sqlite: {
        status: sqliteStatus,
        detail: sqliteDetail,
      },
      fts5: {
        status: fts5Status,
        detail: fts5Detail,
      },
    };
  } catch (error) {
    const detail = summarizeError(error);
    return {
      sqlite: { status: "blocked", detail },
      fts5: { status: "blocked", detail },
    };
  }
}

function readPackageVersion(packageName: string): string {
  const entryUrl = import.meta.resolve(packageName);
  const manifestUrl = new URL("../package.json", entryUrl);
  const parsed: unknown = JSON.parse(readFileSync(manifestUrl, "utf8"));

  if (typeof parsed !== "object" || parsed === null || !("version" in parsed)) {
    throw new Error(`${packageName} package manifest has no version.`);
  }

  const version = (parsed as { version?: unknown }).version;
  if (typeof version !== "string") {
    throw new Error(`${packageName} package version is not a string.`);
  }

  return version;
}

function checkPackage(packageName: string): PackageCheck {
  try {
    const actual = readPackageVersion(packageName);
    const matched = actual === EXPECTED_PI_VERSION;
    return {
      status: matched ? "success" : "blocked",
      actual,
      expected: EXPECTED_PI_VERSION,
      detail: matched
        ? "Installed package matches the approved exact version."
        : "Installed package does not match the approved exact version.",
    };
  } catch (error) {
    return {
      status: "blocked",
      actual: null,
      expected: EXPECTED_PI_VERSION,
      detail: summarizePackageError(error, packageName),
    };
  }
}

function runDoctor(): DoctorEnvelope {
  const node = checkNode();
  const runtime = checkSqliteAndFts5();
  const agentCore = checkPackage("@earendil-works/pi-agent-core");
  const ai = checkPackage("@earendil-works/pi-ai");
  const piStatus =
    agentCore.status === "success" && ai.status === "success"
      ? "success"
      : "blocked";
  const implementedStatuses: ImplementedCheckStatus[] = [
    node.status,
    runtime.sqlite.status,
    runtime.fts5.status,
    piStatus,
  ];
  const status: DoctorStatus = implementedStatuses.includes("blocked")
    ? "blocked"
    : "partial";

  return {
    status,
    data: {
      scope: "c1",
      checks: {
        node,
        sqlite: runtime.sqlite,
        fts5: runtime.fts5,
        pi_packages: {
          status: piStatus,
          agent_core: agentCore,
          ai,
        },
        schema: {
          status: "pending",
          detail: "C2 schema is not implemented.",
        },
        data_directory: {
          status: "pending",
          detail: "C2 data directory is not implemented.",
        },
        provider_configuration: {
          status: "pending",
          detail: "C3 provider configuration is not implemented.",
        },
      },
    },
    error: null,
    ids: emptyIds,
  };
}

function printHelp(): void {
  process.stdout.write(
    [
      "ContextOx Agent CLI (C1 scaffold)",
      "",
      "Usage:",
      "  contextox --help",
      "  contextox doctor [--json]",
      "",
      "Only the C1 doctor command is implemented.",
      "",
    ].join("\n"),
  );
}

function printDoctorText(result: DoctorEnvelope): void {
  const checks = result.data.checks;
  const packageChecks = checks.pi_packages;
  process.stdout.write(
    [
      `status: ${result.status}`,
      `scope: ${result.data.scope}`,
      `node: ${checks.node.status} (${checks.node.actual})`,
      `sqlite: ${checks.sqlite.status}`,
      `fts5: ${checks.fts5.status}`,
      `pi-agent-core: ${packageChecks.agent_core.status}`,
      `pi-ai: ${packageChecks.ai.status}`,
      `schema: ${checks.schema.status}`,
      `data_directory: ${checks.data_directory.status}`,
      `provider_configuration: ${checks.provider_configuration.status}`,
      "",
    ].join("\n"),
  );
}

function printInvalidInput(message: string, json: boolean): void {
  if (json) {
    process.stdout.write(
      `${JSON.stringify(
        {
          status: "error",
          data: {},
          error: {
            code: "INVALID_INPUT",
            message,
            next: "contextox --help",
          },
          ids: emptyIds,
        },
        null,
        2,
      )}\n`,
    );
  } else {
    process.stderr.write(`error: ${message}\nnext: contextox --help\n`);
  }
  process.exitCode = 2;
}

function main(args: string[]): void {
  if (args.length === 0 || args[0] === "--help" || args[0] === "-h") {
    if (args.length > 1) {
      printInvalidInput("--help does not accept additional arguments.", false);
      return;
    }
    printHelp();
    return;
  }

  if (args[0] !== "doctor") {
    printInvalidInput(`Unknown command: ${args[0]}`, args.includes("--json"));
    return;
  }

  const doctorArgs = args.slice(1);
  const json = doctorArgs.includes("--json");
  const invalidDoctorArgs = doctorArgs.filter((argument) => argument !== "--json");
  if (invalidDoctorArgs.length > 0 || doctorArgs.length > 1) {
    printInvalidInput(
      "doctor accepts only one optional --json flag.",
      json,
    );
    return;
  }

  const result = runDoctor();
  if (json) {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } else {
    printDoctorText(result);
  }
  process.exitCode = result.status === "blocked" ? 6 : 8;
}

main(process.argv.slice(2));
