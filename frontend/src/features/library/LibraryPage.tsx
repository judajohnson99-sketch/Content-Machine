import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { listLibrary, libraryConnections, retrieveAsset, setOwnerMedia, analyzeAsset } from "../../api/library";
import { listProjects } from "../../api/projects";
import { AssetCard, ScanFolder } from "../../components/OwnMediaPanel";
import { DiscoverMedia } from "../../components/DiscoverMedia";
import { PageHeader } from "../../components/ui/PageHeader";
import { Button } from "../../components/ui/Button";
import { EmptyState, ErrorState, SkeletonRows } from "../../components/ui/States";
import { ROLE_LABEL, type MediaKind, type OwnerMediaRole } from "../../types/library";
import styles from "./LibraryPage.module.css";

export function LibraryPage() {
  const client = useQueryClient();
  const [params] = useSearchParams();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<MediaKind | "">("");
  const [origin, setOrigin] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [production, setProduction] = useState(params.get("project") ?? "");
  const [role, setRole] = useState<OwnerMediaRole>("visuals");
  const [notice, setNotice] = useState("");
  const library = useQuery({ queryKey: ["library", query, kind], queryFn: () => listLibrary({ query, kind: kind || undefined }), refetchInterval: 15000 });
  const connections = useQuery({ queryKey: ["library-connections"], queryFn: libraryConnections, refetchInterval: 5000 });
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const refresh = () => client.invalidateQueries({ queryKey: ["library"] });
  const analyze = useMutation({ mutationFn: (id: string) => analyzeAsset(id), onSuccess: () => { refresh(); setNotice("Content analyzed. Search can now match this media by its picture or sound."); } });
  const retrieve = useMutation({ mutationFn: (id: string) => retrieveAsset(id), onSuccess: () => { client.invalidateQueries({ queryKey: ["library-connections"] }); setNotice("Retrieval requested. Keep your computer connected; the library updates when the file arrives."); } });
  const assign = useMutation({ mutationFn: () => setOwnerMedia(production, { [role]: selected }), onSuccess: () => { setNotice("Media assigned. Produce the video to apply your selection."); client.invalidateQueries({ queryKey: ["owner-media", production] }); } });
  const assets = (library.data?.assets ?? []).filter(a => !origin || a.origin === origin);
  return <div className={styles.page}>
    <PageHeader eyebrow="Your creative collection" title="Media library" description="Pictures, footage and sound. Find the right atmosphere, then make it part of your film."
      actions={<Link to="/system/image-lab" className={styles.primary}>Create images ↗</Link>} />
    <div className={styles.toolbar}>
      <input type="search" aria-label="Search media" placeholder="Describe the scene or sound you need…" value={query} onChange={e => setQuery(e.target.value)} />
      <select aria-label="Media type" value={kind} onChange={e => setKind(e.target.value as MediaKind | "")}><option value="">All media</option><option value="image">Images</option><option value="video">Video</option><option value="audio">Audio</option></select>
      <select aria-label="Media source" value={origin} onChange={e => setOrigin(e.target.value)}><option value="">All sources</option><option value="owner">My media</option><option value="generated">Generated</option><option value="unknown">Other sources</option></select>
      <ScanFolder onScanned={refresh} />
    </div>
    <details className={styles.connections}><summary>Discover external media</summary><DiscoverMedia initialQuery={query} /></details>
    <details className={styles.connections}><summary>Connected media sources</summary>
      {connections.isError && <p role="alert">Could not check your connected computer. Retry by refreshing.</p>}
      {connections.data?.sources.map(source => <p key={source.id}><strong>{source.name}</strong> · {source.state === "ONLINE" ? "Connected" : "Offline — requests wait safely"}. {source.connected ? source.detail : "Media access needs the updated computer companion and an approved media folder."}</p>)}
      {connections.data?.sources.length === 0 && <p>No computer connected yet. Your uploaded and generated library remains available.</p>}
    </details>
    {notice && <p className={styles.notice} role="status">{notice}{assign.isSuccess && <Link to={`/projects/${production}?step=Media`}> Open production →</Link>}</p>}
    {(retrieve.isError || assign.isError || analyze.isError) && <ErrorState title="Couldn’t complete that action" description={(retrieve.error || assign.error || analyze.error)?.message} />}
    {library.isLoading && <SkeletonRows count={6} />}
    {library.isError && <ErrorState title="Couldn’t load your media" description={library.error.message} action={<Button onClick={() => library.refetch()}>Retry</Button>} />}
    {library.data && assets.length === 0 && <EmptyState title="Make room for inspiration" description="Try a different description, connect your own media, or create an image in Image Lab." />}
    <div className={styles.grid}>{assets.map(asset => {
      const transfer = connections.data?.transfers.find(t => t.request.asset_ids.includes(asset.id) && t.request.mode === "source");
      const pending = transfer && ["WAITING", "TRANSFERRING"].includes(transfer.state);
      const analysisAvailable = asset.kind === "audio" ? connections.data?.audio_analysis_available : connections.data?.visual_analysis_available;
      return <ul key={asset.id} className={styles.tile}><AssetCard asset={asset} selected={selected.includes(asset.id)} position={0}
        onToggle={() => { setNotice(""); setSelected(current => current.includes(asset.id) ? current.filter(id => id !== asset.id) : [...current, asset.id]); }}
        annotating={editing === asset.id} onAnnotate={() => setEditing(editing === asset.id ? null : asset.id)} onAnnotated={() => { setEditing(null); refresh(); }} />
        {asset.available && <li className={styles.transfer}>{asset.analysis?.available ? <span>Content analyzed · meaning-based search available</span> : <><Button size="sm" disabled={analyze.isPending || !analysisAvailable} onClick={() => analyze.mutate(asset.id)}>{analyze.isPending ? "Analyzing…" : "Analyze content"}</Button>{!analysisAvailable && <p>Local analysis for this media type is not installed.</p>}</>}</li>}
        {!asset.available && <li className={styles.transfer}><p>{transfer?.message || "This file is on your connected computer."}</p><Button size="sm" disabled={!!pending || retrieve.isPending} onClick={() => retrieve.mutate(asset.id)}>{pending ? "Retrieval in progress" : transfer?.state === "FAILED" ? "Retry retrieval" : "Retrieve from computer"}</Button></li>}
      </ul>;
    })}</div>
    {selected.length > 0 && <div className={styles.selection}>
      <strong>{selected.length} selected</strong>
      <select aria-label="Target production" value={production} onChange={e => setProduction(e.target.value)}><option value="">Choose production</option>{projects.data?.map(p => <option key={p.video_id} value={p.video_id}>{p.selected_title || p.video_id}</option>)}</select>
      <select aria-label="Assign media role" value={role} onChange={e => setRole(e.target.value as OwnerMediaRole)}>{Object.entries(ROLE_LABEL).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
      <Button variant="primary" disabled={!production || assign.isPending} onClick={() => assign.mutate()}>Use in production</Button>
      {!production && <span>Choose a production to use this media.</span>}
      <Button variant="ghost" onClick={() => setSelected([])}>Clear</Button>
    </div>}
  </div>;
}
