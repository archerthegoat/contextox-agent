import { describe, expect, it, vi } from "vitest";
import { createLatestScopedRead } from "./Path2Workbench";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {resolve = yes; reject = no;});
  return { promise, resolve, reject };
}

describe("overlapping mission readback", () => {
  it.each([true, false])("returns the newest same-task snapshot when newest settles first=%s", async newestFirst => {
    const read = createLatestScopedRead<string | null>();
    const old = deferred<string | null>(); const next = deferred<string | null>();
    const getOld = vi.fn(() => old.promise); const getNext = vi.fn(() => next.promise);
    const a = read("workspace/mission", getOld);
    const b = read("workspace/mission", getNext);
    if (newestFirst) {next.resolve("current snapshot"); await b; old.resolve(null);}
    else {old.resolve(null); await Promise.resolve(); next.resolve("current snapshot");}
    expect(await a).toBe("current snapshot"); expect(await b).toBe("current snapshot");
    expect(getOld).toHaveBeenCalledTimes(1); expect(getNext).toHaveBeenCalledTimes(1);
  });

  it.each(["workspace-b/mission", "workspace-a/other-mission"])("does not borrow the result from %s", async otherScope => {
    const read = createLatestScopedRead<string | null>();
    const old = deferred<string | null>(); const next = deferred<string | null>();
    const a = read("workspace-a/mission", () => old.promise);
    const b = read(otherScope, () => next.promise);
    next.resolve("other workspace"); old.resolve(null);
    expect(await a).toBeNull(); expect(await b).toBe("other workspace");
  });

  it("preserves actual latest failure rather than falling back to an old success", async () => {
    const read = createLatestScopedRead<string | null>();
    const old = deferred<string | null>(); const next = deferred<string | null>();
    const a = read("w/m", () => old.promise); const b = read("w/m", () => next.promise);
    next.resolve(null); old.resolve("obsolete success");
    expect(await a).toBeNull(); expect(await b).toBeNull();
  });

  it("propagates a current transport rejection to every same-scope waiter", async () => {
    const read = createLatestScopedRead<string | null>();
    const old = deferred<string | null>(); const next = deferred<string | null>();
    const a = read("w/m", () => old.promise); const b = read("w/m", () => next.promise);
    const results = Promise.allSettled([a, b]);
    next.reject(new Error("offline")); old.resolve(null);
    expect(await results).toEqual([
      {status:"rejected", reason:new Error("offline")}, {status:"rejected", reason:new Error("offline")},
    ]);
  });
});
