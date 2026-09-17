import { QueryClient } from "@tanstack/react-query";

// Server state dominates this app (project/gate/job status), so TanStack
// Query is the only state layer - no Redux/Zustand (architecture plan §11).
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: true,
    },
  },
});
