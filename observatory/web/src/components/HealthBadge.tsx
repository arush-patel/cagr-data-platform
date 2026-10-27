import { useQuery } from "@tanstack/react-query";

import { getHealth } from "../api/client";

// Shows whether the API can read the database, so an empty page is never mistaken for an
// empty queue.
export function HealthBadge() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 60_000,
  });
  if (isError) return <span className="badge bad">API unreachable</span>;
  if (!data) return <span className="badge">Checking…</span>;
  return data.database ? (
    <span className="badge good">Database connected</span>
  ) : (
    <span className="badge bad" title={data.detail ?? undefined}>
      Database unreachable
    </span>
  );
}
