// A small fetch client for the Observatory API. The types are generated from the API's OpenAPI
// schema (npm run api-types), so the UI can't drift from the API.
import type { components } from "./schema";

type Schemas = components["schemas"];
export type Health = Schemas["Health"];
export type Item = Schemas["Item"];
export type AdjustmentDetail = Schemas["AdjustmentDetail"];
export type ChangeDetail = Schemas["ChangeDetail"];
export type Listing = Schemas["Listing"];
export type Bar = Schemas["Bar"];
export type ItemKind = Item["kind"];

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${path}: ${response.status} ${response.statusText}`);
  return (await response.json()) as T;
}

export const getHealth = () => getJson<Health>("/api/health");

export const getItems = (status: "pending" | "decided") =>
  getJson<Item[]>(`/api/review/items?status=${status}`);

export const getAdjustment = (id: number) =>
  getJson<AdjustmentDetail>(`/api/review/items/adjustment/${id}`);

export const getChange = (id: number) => getJson<ChangeDetail>(`/api/review/items/change/${id}`);

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    // The API says why a decision was refused in plain words; show that, not the status.
    const detail = await response
      .json()
      .then((b: { detail?: unknown }) => (typeof b.detail === "string" ? b.detail : null))
      .catch(() => null);
    throw new Error(detail ?? `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export type Approve = Schemas["Approve"];

export const approve = (kind: ItemKind, id: number, body: Approve) =>
  postJson<Item>(`/api/review/items/${kind}/${id}/approve`, body);

export const reject = (kind: ItemKind, id: number, note: string) =>
  postJson<Item>(`/api/review/items/${kind}/${id}/reject`, { note });
