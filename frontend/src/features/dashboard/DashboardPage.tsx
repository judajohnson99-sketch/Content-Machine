import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../../api/projects";
import { StatusBadge } from "../../components/StatusBadge";

export function DashboardPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    refetchInterval: 20_000, // live-computed on the server, no cache (plan §9)
  });

  if (isLoading) return <p>Loading projects…</p>;
  if (isError) return <p>Failed to load projects: {(error as Error).message}</p>;

  return (
    <div>
      <h1>Content Machine — Dashboard</h1>
      <table cellPadding={8} style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
            <th>Video ID</th>
            <th>Title</th>
            <th>Niche</th>
            <th>Status</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((p) => (
            <tr key={p.video_id} style={{ borderBottom: "1px solid #eee" }}>
              <td>
                <Link to={`/projects/${p.video_id}`}>{p.video_id}</Link>
              </td>
              <td>{p.selected_title ?? "—"}</td>
              <td>{p.niche ?? "—"}</td>
              <td>
                <StatusBadge status={p.overall_status} />
              </td>
              <td>{p.created_utc ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
