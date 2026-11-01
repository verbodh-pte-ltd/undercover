import { useEffect, useState } from "react";
import { listHandlers, type Teammate } from "./api";

// Shows a handler id such as "HND-01" as the handler's name, and "seed" as "Seeded".
export function useHandlerNames() {
  const [handlers, setHandlers] = useState<Teammate[]>([]);
  useEffect(() => {
    listHandlers().then(setHandlers, () => {
      // Without the list, the ids show as they are.
    });
  }, []);
  return (id: string) =>
    id === "seed"
      ? "Seeded"
      : (handlers.find((h) => h.handler_id === id)?.name ?? id);
}
