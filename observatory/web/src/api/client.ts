// A small fetch client for the Observatory API. Types are generated from the API's
// OpenAPI schema once the review endpoints exist (spec 0005, section 5.1).

export type Health = { version: string; database: boolean; detail: string | null };

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${path}: ${response.status} ${response.statusText}`);
  return (await response.json()) as T;
}

export const getHealth = () => getJson<Health>("/api/health");
