import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell } from "./AppShell";
import * as systemApi from "../../api/system";
import * as projectsApi from "../../api/projects";
import type { HostReadiness } from "../../types/system";

const readiness: HostReadiness = {
  depicted_image_providers: [], procedural_images: true,
  search_provider: null, search_available: false, narration_available: true,
  narration: { available: true, engine: "piper", voice: "en_US-libritts_r-medium", detail: "local Piper narration" },
  depicted_imagery: { state: "worker_offline", detail: "home-gpu-01 is offline", starts_now: false, worker_id: "home-gpu-01" },
  remote_gpu: {
    state: "worker_offline", detail: "home-gpu-01 is offline", worker_id: "home-gpu-01",
    workers: [],
    queue: { queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0 },
  },
};

function renderShell(overrides: Partial<HostReadiness> = {}) {
  vi.spyOn(systemApi, "getReadiness").mockResolvedValue({ ...readiness, ...overrides });
  vi.spyOn(systemApi, "listRecentRuns").mockResolvedValue({ active: [], recent: [] });
  vi.spyOn(projectsApi, "listProjects").mockResolvedValue([]);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <AppShell>
          <p>content</p>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppShell system lights", () => {
  it("reports narration as available only when the host says so", async () => {
    renderShell();
    await waitFor(() => expect(screen.getByText("piper")).toBeInTheDocument());
    expect(screen.getByTitle(/local Piper narration/)).toBeInTheDocument();
  });

  // This light used to be a literal green "synth · piper" that could not go
  // out, on a host with neither the voice model nor piper-tts installed.
  it("reports narration as unavailable when the host cannot narrate", async () => {
    renderShell({
      narration_available: false,
      narration: {
        available: false, engine: "piper", voice: "en_US-libritts_r-medium",
        detail: "piper-tts is not installed. Install with: .venv/bin/pip install piper-tts",
      },
    });
    await waitFor(() => expect(screen.getByText("unavailable")).toBeInTheDocument());
    expect(screen.getByTitle(/piper-tts is not installed/)).toBeInTheDocument();
  });

  it("shows the research provider the host actually has", async () => {
    renderShell({ search_provider: "brave", search_available: true });
    await waitFor(() => expect(screen.getByText("brave")).toBeInTheDocument());
  });
});
