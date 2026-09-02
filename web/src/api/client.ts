import createClient from "openapi-fetch";
import type { paths } from "../generated/api";

const client = createClient<paths>({ baseUrl: "" });

export type WorkbenchSnapshot = NonNullable<
  paths["/api/workbench"]["get"]["responses"][200]["content"]["application/json"]
>;

export async function fetchWorkbench(): Promise<WorkbenchSnapshot> {
  const result = await client.GET("/api/workbench");
  if (!result.response.ok || !result.data) {
    throw new Error(`Workbench snapshot unavailable (${result.response.status}).`);
  }
  return result.data;
}
