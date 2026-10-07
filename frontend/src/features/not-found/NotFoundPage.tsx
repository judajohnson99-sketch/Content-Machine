import { Link, useLocation } from "react-router-dom";
import { EmptyState } from "../../components/ui/States";
import { SearchIcon } from "../../components/ui/icons";

// Without a catch-all, a mistyped or stale URL rendered the shell around an
// empty page, which reads as "this project has nothing in it" rather than
// "this address does not exist".
export function NotFoundPage() {
  const { pathname } = useLocation();
  return (
    <EmptyState
      icon={<SearchIcon width={24} height={24} />}
      title="No such page"
      description={`Nothing is routed at ${pathname}. The link may be stale, or a project id may have changed.`}
      action={<Link to="/">Back to the dashboard</Link>}
    />
  );
}
