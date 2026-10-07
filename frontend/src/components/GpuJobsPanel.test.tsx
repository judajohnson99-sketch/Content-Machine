import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "../test/renderWithClient";
import { GpuJobsPanel } from "./GpuJobsPanel";
import * as systemApi from "../api/system";
import type { GpuJob } from "../types/system";

function job(overrides: Partial<GpuJob>): GpuJob {
  return {
    job_id: "0b46f3a443c1b1ec", state: "QUEUED", wait_reason: null, attempt: 0,
    max_attempts: 3, required_capabilities: ["comfyui"], project_id: "vid",
    label: "s08: moonlit glasshouse", worker_id: null, lease_expires_in_seconds: null,
    assets: [], error: null, failure_category: null, created_at: null, updated_at: null,
    provider_job_id: null, last_transition: { at: null, to: null, detail: null },
    completed_at: null, ...overrides,
  };
}

describe("GpuJobsPanel", () => {
  it("offers Retry only on a failed or cancelled job and requeues through the API", async () => {
    const failed = job({ state: "FAILED", error: "CUDA out of memory", failure_category: "capacity",
                         worker_id: "home-gpu-01", attempt: 1 });
    const running = job({ job_id: "deadbeefcafef00d", state: "RUNNING", label: "s01", attempt: 1 });
    const list = vi.spyOn(systemApi, "listProjectGpuJobs").mockResolvedValue([failed, running]);
    const requeue = vi.spyOn(systemApi, "requeueGpuJob").mockResolvedValue(job({ state: "QUEUED" }));

    renderWithClient(<GpuJobsPanel videoId="vid" />);

    expect(await screen.findByText("hardware capacity")).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: /^retry/i });
    expect(buttons).toHaveLength(1);
    await userEvent.click(buttons[0]);

    expect(requeue).toHaveBeenCalledWith("0b46f3a443c1b1ec");
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });

  it("surfaces a refused retry instead of swallowing it", async () => {
    vi.spyOn(systemApi, "listProjectGpuJobs").mockResolvedValue([job({ state: "CANCELLED" })]);
    vi.spyOn(systemApi, "requeueGpuJob").mockRejectedValue(new Error("job is RUNNING"));

    renderWithClient(<GpuJobsPanel videoId="vid" />);

    await userEvent.click(await screen.findByRole("button", { name: /^retry/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("job is RUNNING");
  });
});
