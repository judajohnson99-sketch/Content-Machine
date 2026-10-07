import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GeneratePage } from "./GeneratePage";
import { ApiError } from "../../api/client";
import * as systemApi from "../../api/system";
import type { HostReadiness, GpuJob } from "../../types/system";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/generate"]}>
        <Routes>
          <Route path="/generate" element={<GeneratePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const readiness: HostReadiness = {
  depicted_image_providers: [], procedural_images: true,
  search_provider: null, search_available: false, narration_available: true,
  narration: { available: true, engine: "piper", voice: "en_US-libritts_r-medium", detail: "local Piper narration" },
  depicted_imagery: { state: "worker_offline", detail: "home-gpu-01 is offline", starts_now: false, worker_id: "home-gpu-01" },
  remote_gpu: {
    state: "worker_offline", detail: "home-gpu-01 is offline", worker_id: "home-gpu-01",
    workers: [
      {
        worker_id: "home-gpu-01", state: "OFFLINE", capabilities: ["comfyui", "sd15"],
        revoked: false, enrolled_at: null, last_heartbeat_at: null, heartbeat_age_seconds: null,
        heartbeats: 0, status: { checkpoints: ["DreamShaper_8_pruned.safetensors"] },
      },
    ],
    queue: { queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0 },
  },
};

const queuedJob: GpuJob = {
  job_id: "abc123", state: "QUEUED", wait_reason: "WAITING_FOR_CAPABLE_WORKER",
  attempt: 0, max_attempts: 3, required_capabilities: ["comfyui"], project_id: null,
  label: "a lit window", worker_id: null, lease_expires_in_seconds: null, assets: [],
  error: null, failure_category: null, created_at: null, updated_at: null,
  provider_job_id: null,
  last_transition: { at: null, to: null, detail: null }, completed_at: null,
};

describe("GeneratePage", () => {
  it("disables submit until a prompt is entered", async () => {
    vi.spyOn(systemApi, "getReadiness").mockResolvedValue(readiness);
    renderPage();
    expect(await screen.findByRole("button", { name: /generate/i })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^prompt$/i), "a lit window at night");
    expect(screen.getByRole("button", { name: /generate/i })).toBeEnabled();
  });

  it("submits and renders the queued job even though the worker is offline", async () => {
    vi.spyOn(systemApi, "getReadiness").mockResolvedValue(readiness);
    const enqueue = vi.spyOn(systemApi, "enqueueGpuJob").mockResolvedValue(queuedJob);
    vi.spyOn(systemApi, "listGpuJobs").mockResolvedValue([queuedJob]);
    renderPage();

    await userEvent.type(await screen.findByLabelText(/^prompt$/i), "a lit window at night");
    await userEvent.click(screen.getByRole("button", { name: /generate/i }));

    expect(enqueue).toHaveBeenCalledWith(
      expect.objectContaining({ prompt: "a lit window at night", width: 512, height: 512 }),
    );
    await waitFor(() => expect(document.querySelector('[data-status="QUEUED"]')).not.toBeNull());
  });

  it("shows the API error message when queueing fails", async () => {
    vi.spyOn(systemApi, "getReadiness").mockResolvedValue(readiness);
    vi.spyOn(systemApi, "enqueueGpuJob").mockRejectedValue(new ApiError(400, "prompt is required"));
    renderPage();

    await userEvent.type(await screen.findByLabelText(/^prompt$/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /generate/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("prompt is required");
  });
});
