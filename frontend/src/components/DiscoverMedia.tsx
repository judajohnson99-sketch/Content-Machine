import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "../api/client";
import { Button } from "./ui/Button";
import styles from "../features/library/LibraryPage.module.css";

interface FoundAsset { id: string; title: string; creator: string; thumbnail: string; url: string; license: string; license_url: string; foreign_landing_url: string }
export function DiscoverMedia({ initialQuery = "" }: { initialQuery?: string }) {
  const client = useQueryClient();
  const [query, setQuery] = useState(initialQuery);
  const [kind, setKind] = useState("image");
  const [resultKind, setResultKind] = useState("image");
  const search = useMutation({ mutationFn: () => apiGet<{ assets: FoundAsset[] }>(`/library/discover/?${new URLSearchParams({ query, kind })}`), onSuccess: () => setResultKind(kind) });
  const imported = useMutation({ mutationFn: (id: string) => apiPost("/library/discover/", { id, kind: resultKind }), onSuccess: () => client.invalidateQueries({ queryKey: ["library"] }) });
  return <section className={styles.discovery} aria-label="Discover external media">
    <h2>Find the missing piece</h2>
    <p>Explore openly licensed media. Inspect the original source and confirm its terms before production use.</p>
    <form className={styles.toolbar} onSubmit={e => { e.preventDefault(); search.mutate(); }}>
      <input aria-label="Describe external media" placeholder="Moonlit forest, gentle rain, quiet piano…" value={query} onChange={e => setQuery(e.target.value)} required />
      <select aria-label="External media type" value={kind} onChange={e => setKind(e.target.value)}><option value="image">Images</option><option value="audio">Music, ambience & effects</option></select>
      <Button type="submit" variant="primary" disabled={search.isPending}>{search.isPending ? "Searching…" : "Find media"}</Button>
    </form>
    {(search.isError || imported.isError) && <p role="alert">{(search.error || imported.error)?.message}</p>}
    {imported.isSuccess && <p role="status">Added to the library. Review the source licence in the asset’s details to enable production use.</p>}
    {search.isSuccess && search.data.assets.length === 0 && <p>No matches. Try a broader description.</p>}
    <div className={styles.grid}>{search.data?.assets.map(asset => <article key={asset.id} className={styles.discovered}>
      {resultKind === "image" && <img src={asset.thumbnail} alt={asset.title} loading="lazy" referrerPolicy="no-referrer" />}
      {resultKind === "audio" && <audio controls preload="none" src={asset.url} />}
      <h3>{asset.title || "Untitled"}</h3><p>{asset.creator || "Creator not listed"} · {asset.license.toUpperCase()}</p>
      <a href={asset.foreign_landing_url} target="_blank" rel="noreferrer">Inspect source & licence ↗</a>
      <Button size="sm" onClick={() => imported.mutate(asset.id)} disabled={imported.isPending}>Import to library</Button>
    </article>)}</div>
  </section>;
}
