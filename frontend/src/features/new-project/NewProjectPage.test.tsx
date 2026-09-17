import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NewProjectPage } from "./NewProjectPage";
import { ApiError } from "../../api/client";
import * as conceptsApi from "../../api/concepts";
import * as pipelineApi from "../../api/pipeline";
import type { ConceptCatalog } from "../../types/concepts";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/projects/new"]}>
        <Routes>
          <Route path="/projects/new" element={<NewProjectPage />} />
          <Route path="/projects/:videoId" element={<p>workspace for {"{videoId}"}</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const catalog: ConceptCatalog = {
  capabilities: {
    depicted_image_providers: [], procedural_images: true,
    search_provider: null, search_available: false, narration_available: true,
    depicted_imagery: { state: "worker_offline", detail: "home-gpu-01 is offline", starts_now: false, worker_id: "home-gpu-01" },
    remote_gpu: {
      state: "worker_offline", detail: "home-gpu-01 is offline", worker_id: "home-gpu-01", workers: [],
      queue: { queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0 },
    },
  },
  concepts: [
    {
      id: "sleep-brown-noise-dark", niche: "adult_sleep", title_pattern: "Brown Noise | 8 Hours",
      content_format: "Static dark screen with brown noise", target_audience: "adults",
      video_length_minutes: 480, visual_concept: "dark plate", audio_concept: "brown noise",
      audio_requirement: "synthesisable_now", procedural_visuals_acceptable: true,
      requires_subject_research: false, production_complexity: 1, risks: [], score: 9,
      readiness: { images: "ok", audio: "ok", research: "n/a", runnable_now: true, notes: [] },
    },
    {
      id: "story-sleepy-history-adult", niche: "bedtime_stories", title_pattern: "Sleepy History",
      content_format: "Narrated monotone history", target_audience: "adults",
      video_length_minutes: 60, visual_concept: "archival stills", audio_concept: "narration",
      audio_requirement: "tts_required", procedural_visuals_acceptable: false,
      requires_subject_research: true, production_complexity: 3, risks: [], score: 5,
      readiness: {
        images: "blocked", audio: "ok", research: "blocked", runnable_now: false,
        notes: ["Needs source-backed subject research; no SEARCH_PROVIDER is configured."],
      },
    },
  ],
};

catalog.concepts.push({
  id: "ref-moonlit-victorian-glasshouse", kind: "reference", niche: "immersive_ambience",
  title_pattern: "Moonlit Victorian Glasshouse", tagline: "A glass conservatory at midnight.",
  creative_intent: "Peaceful, cinematic, slightly surreal.",
  visual_direction: { atmosphere: "peaceful", palette: ["deep indigo", "aqua"], motifs: ["condensation"], avoid: ["people"], prompt_core: "x", negative: "text" },
  narration: "none", preview_seconds: 60,
  content_format: "3-5 dreamlike stills", target_audience: "adults",
  video_length_minutes: 120, visual_concept: "glasshouse", audio_concept: "low drone",
  audio_requirement: "synthesisable_now", procedural_visuals_acceptable: false,
  requires_subject_research: false, production_complexity: 2, risks: [], score: 7,
  readiness: { images: "partial", images_via: "gpu-worker", audio: "ok", research: "n/a", runnable_now: false, waits_for_gpu: true, notes: ["Depicted imagery will queue for the GPU worker"] },
});

describe("NewProjectPage", () => {
  it("leads with the reference catalogue and lets the operator switch to experiments", async () => {
    vi.spyOn(conceptsApi, "listConcepts").mockResolvedValue(catalog);
    renderPage();
    await screen.findByText("Moonlit Victorian Glasshouse");
    expect(screen.getByText("waits for GPU")).toBeInTheDocument();
    expect(screen.queryByText("Brown Noise | 8 Hours")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Experiments" }));
    expect(await screen.findByText("Brown Noise | 8 Hours")).toBeInTheDocument();
  });

  it("sends the preview length instead of the full duration when quality preview is on", async () => {
    vi.spyOn(conceptsApi, "listConcepts").mockResolvedValue(catalog);
    const create = vi.spyOn(conceptsApi, "createProject").mockResolvedValue({
      video_id: "glasshouse-1", selected_title: null, concept_id: "ref-moonlit-victorian-glasshouse",
      niche: "immersive_ambience", overall_status: "DRAFT", created_utc: null,
    });
    vi.spyOn(pipelineApi, "triggerStage").mockResolvedValue({
      id: 1, video_id: "glasshouse-1", stage: "produce", client_request_id: "x", celery_task_id: null,
      status: "QUEUED", params: {}, exit_code: null, message: "", data: {}, log_tail: "",
      created_at: "", started_at: null, finished_at: null,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Moonlit Victorian Glasshouse/ }));
    await userEvent.click(screen.getByLabelText(/Quality preview/));
    await userEvent.click(screen.getByRole("button", { name: "Create & Produce" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ duration: 60 })));
  });

  it("lists concepts with their host readiness and explains what blocks one", async () => {
    vi.spyOn(conceptsApi, "listConcepts").mockResolvedValue(catalog);
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Experiments" }));
    await screen.findByText("Brown Noise | 8 Hours");
    expect(screen.getByText("runnable now")).toBeInTheDocument();
    expect(screen.getByText("needs setup")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Sleepy History/ }));
    expect(screen.getByText(/no SEARCH_PROVIDER is configured/)).toBeInTheDocument();
  });

  it("creates the project, starts produce with the chosen image mode, and lands in the workspace", async () => {
    vi.spyOn(conceptsApi, "listConcepts").mockResolvedValue(catalog);
    const create = vi.spyOn(conceptsApi, "createProject").mockResolvedValue({
      video_id: "calm-001", selected_title: null, concept_id: "sleep-brown-noise-dark",
      niche: "adult_sleep", overall_status: "DRAFT", created_utc: null,
    });
    const trigger = vi.spyOn(pipelineApi, "triggerStage").mockResolvedValue({
      id: 1, video_id: "calm-001", stage: "produce", client_request_id: "x", celery_task_id: null,
      status: "QUEUED", params: {}, exit_code: null, message: "", data: {}, log_tail: "",
      created_at: "", started_at: null, finished_at: null,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Experiments" }));
    await userEvent.click(await screen.findByRole("button", { name: /Brown Noise/ }));

    const id = screen.getByLabelText(/Project id/);
    await userEvent.clear(id);
    await userEvent.type(id, "calm-001");
    const minutes = screen.getByLabelText(/Duration/);
    await userEvent.clear(minutes);
    await userEvent.type(minutes, "2");
    await userEvent.selectOptions(screen.getByLabelText(/Image mode/), "scenes");
    await userEvent.click(screen.getByRole("button", { name: "Create & Produce" }));

    await waitFor(() => expect(create).toHaveBeenCalledWith({
      video_id: "calm-001", concept_id: "sleep-brown-noise-dark", duration: 120,
    }));
    await waitFor(() => expect(trigger).toHaveBeenCalledTimes(1));
    const [videoId, stage, , params] = trigger.mock.calls[0];
    expect([videoId, stage, params]).toEqual(["calm-001", "produce", { scenes: true }]);
    expect(await screen.findByText(/workspace for/)).toBeInTheDocument();
  });

  it("can create without starting, and refuses a malformed id before calling the API", async () => {
    vi.spyOn(conceptsApi, "listConcepts").mockResolvedValue(catalog);
    const create = vi.spyOn(conceptsApi, "createProject");
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Experiments" }));
    await userEvent.click(await screen.findByRole("button", { name: /Brown Noise/ }));
    await userEvent.click(screen.getByLabelText(/Start Produce immediately/));
    const id = screen.getByLabelText(/Project id/);
    await userEvent.clear(id);
    await userEvent.type(id, "Not Valid");
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
  });

  it("explains a taken id instead of failing opaquely", async () => {
    vi.spyOn(conceptsApi, "listConcepts").mockResolvedValue(catalog);
    vi.spyOn(conceptsApi, "createProject").mockRejectedValue(new ApiError(409, "project already exists"));
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Experiments" }));
    await userEvent.click(await screen.findByRole("button", { name: /Brown Noise/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create & Produce" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists — pick another id/);
  });
});
