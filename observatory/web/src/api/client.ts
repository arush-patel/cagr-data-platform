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
