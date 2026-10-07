import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getIdentity, type Identity } from "../api/auth";
import { ApiError } from "../api/client";
import { LoginPage } from "../features/auth/LoginPage";
import { InlineSpinner } from "../components/ui/States";
import styles from "./AuthGate.module.css";

/**
 * Decides, once per boot, whether to render the app or the sign-in screen.
 *
 * A 403 from /auth/me/ is the normal "not signed in" answer, not an error
 * to report - every other failure still surfaces, because "the API is down"
 * and "you are logged out" must not look the same.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const identity = useQuery({
    queryKey: ["identity"],
    queryFn: getIdentity,
    retry: false,
    staleTime: Infinity,
  });

  if (identity.isLoading) {
    return (
      <div className={styles.boot}>
        <InlineSpinner label="Loading your control center" />
      </div>
    );
  }

  const loggedOut = identity.error instanceof ApiError && identity.error.status === 403;

  if (loggedOut || (!identity.data && identity.error)) {
    return (
      <LoginPage
        onSignedIn={(who: Identity) => {
          queryClient.setQueryData(["identity"], who);
          // Anything fetched while logged out failed; start clean.
          queryClient.invalidateQueries();
        }}
      />
    );
  }

  return <>{children}</>;
}
